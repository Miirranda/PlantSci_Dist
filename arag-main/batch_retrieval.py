#!/usr/bin/env python3
"""批量跨语言检索：输入中文语段列表，输出结构化英文论文证据。

原创代码（非 A-RAG 开源部分）。

输入可以是：
* 单篇公众号文章（``--article``）：LLM 筛选事实性科学断言（推荐）
* JSON / JSONL / 纯文本断言文件（``--claims``）
* 命令行直接给的一条或多条断言（``--claim``）
* 旧规则切句（``--wechat --legacy-split``，仅调试）

输出：``results/<时间戳>/evidences.jsonl``，每行一条 ``RetrievalOutput`` 的 JSON，
字段结构固定，可直接送入下游幻觉判定模块。

Usage:
    # 单篇文章：LLM 抽句 + 检索（推荐）
    python batch_retrieval.py --article path/to/article.md --claims-out claims.jsonl

    # 已有断言文件
    python batch_retrieval.py --claims data/claims.jsonl --workers 1

    # 旧规则切句（调试）
    python batch_retrieval.py --wechat data/wechat --legacy-split
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Any

_ARAG_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ARAG_ROOT))
sys.path.insert(0, str(_ARAG_ROOT / "src"))

from tqdm import tqdm

from retrieval_adaptor import (
    VERDICT_INCONCLUSIVE,
    VERDICT_NO_EVIDENCE,
    VERDICT_SUPPORTED,
    CrossLingualRetrievalPipeline,
    RetrievalConfig,
    load_claims_from_file,
)


def collect_claims(args: argparse.Namespace) -> list[dict[str, Any]]:
    """按优先级解析输入：--claim > --claims > --article(LLM) > --wechat(--legacy-split)。"""
    if args.claim:
        return [
            {"claim_id": "cli#%d" % index, "claim_zh": text}
            for index, text in enumerate(args.claim)
        ]

    if args.claims:
        claims = load_claims_from_file(args.claims)
        if not claims:
            raise ValueError("%s 中没有解析出任何断言" % args.claims)
        return claims

    if getattr(args, "article", None):
        from retrieval_adaptor.claim_extractor import (
            extract_claims_from_article,
            save_claims_json,
            save_claims_jsonl,
        )

        claims = extract_claims_from_article(args.article)
        if not claims:
            raise ValueError("LLM 未从文章中提取到任何观点句: %s" % args.article)
        claims_out = getattr(args, "claims_out", None)
        if claims_out:
            out = Path(claims_out)
            save_claims_jsonl(claims, out)
            json_path = out.with_name("claims.json") if out.suffix == ".jsonl" else out.with_suffix(".json")
            save_claims_json(claims, json_path)
            print("  [batch] 观点句已保存: %s / %s" % (out, json_path))
        return claims

    if getattr(args, "legacy_split", False):
        from retrieval_adaptor.pdf_ingest import load_wechat_claims

        print("  [batch] 警告: 使用已废弃的规则切句 (--legacy-split)")
        return load_wechat_claims(Path(args.wechat), min_chars=args.min_chars)

    raise SystemExit(
        "请指定输入来源之一:\n"
        "  --article <文章.md>     LLM 筛选观点句（推荐）\n"
        "  --claims <claims.jsonl> 已有断言文件\n"
        "  --claim <文本>          单条断言\n"
        "  --wechat <目录> --legacy-split  旧规则切句（调试）"
    )


def claim_key(item: dict[str, Any]) -> str:
    """断点续跑用的稳定键：优先 claim_id，否则用原文。"""
    return str(item.get("claim_id") or "").strip() or str(item.get("claim_zh") or "").strip()


def load_completed_keys(path: Path) -> set[str]:
    """从已有 evidences.jsonl 读取已完成的 claim_id / claim_zh。"""
    done: set[str] = set()
    if not path.is_file():
        return done
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "续跑文件 %s 第 %d 行不是合法 JSON: %s" % (path, line_no, exc)
                ) from exc
            if not isinstance(record, dict):
                continue
            key = str(record.get("claim_id") or "").strip() or str(
                record.get("claim_zh") or ""
            ).strip()
            if key:
                done.add(key)
    return done


def load_records(path: Path) -> list[dict[str, Any]]:
    """读取已有 JSONL 记录，供汇总统计。"""
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            records.append(json.loads(stripped))
    return records


class ResultWriter:
    """线程安全的 JSONL 增量落盘，跑一半中断也不会丢已完成的结果。"""

    def __init__(self, output_file: Path) -> None:
        self.output_file = output_file
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self.count = 0

    def write(self, record: dict[str, Any]) -> None:
        with self._lock:
            with self.output_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
            self.count += 1


def summarize(records: list[dict[str, Any]]) -> None:
    counts = {VERDICT_SUPPORTED: 0, VERDICT_INCONCLUSIVE: 0, VERDICT_NO_EVIDENCE: 0}
    total_evidence = 0
    total_cost = 0.0
    errors = 0

    for record in records:
        verdict = record.get("verdict", "")
        if verdict in counts:
            counts[verdict] += 1
        total_evidence += record.get("evidence_count", 0)
        stats = record.get("stats") or {}
        total_cost += float(stats.get("agent_cost_cny", 0.0) or 0.0)
        if stats.get("error"):
            errors += 1

    print("\n" + "=" * 78)
    print("批量检索汇总")
    print("=" * 78)
    print("断言总数      : %d" % len(records))
    print("SUPPORTED     : %d" % counts[VERDICT_SUPPORTED])
    print("INCONCLUSIVE  : %d" % counts[VERDICT_INCONCLUSIVE])
    print("NO_EVIDENCE   : %d" % counts[VERDICT_NO_EVIDENCE])
    print("证据条数合计  : %d" % total_evidence)
    print("Qwen 费用合计 : %.4f 元" % total_cost)
    if errors:
        print("失败断言      : %d（详见 stats.error）" % errors)


def build_record(item: dict[str, Any], output: Any) -> dict[str, Any]:
    record = output.to_dict()
    record["claim_id"] = item.get("claim_id", "")
    if item.get("source_file"):
        record["source_file"] = item["source_file"]
    return record


def run_claims(
    pipeline: CrossLingualRetrievalPipeline,
    claims: list[dict[str, Any]],
    writer: ResultWriter,
    *,
    workers: int,
) -> list[dict[str, Any]]:
    """逐条检索并立即落盘；workers=1 时串行，便于观察进度与降低限流。"""
    records: list[dict[str, Any]] = []
    workers = max(1, int(workers))

    with tqdm(total=len(claims), desc="Retrieving", unit="claim", mininterval=0.5) as bar:
        if workers == 1:
            for item in claims:
                output = pipeline.retrieve(item["claim_zh"])
                record = build_record(item, output)
                writer.write(record)
                records.append(record)
                bar.set_postfix(
                    claim=str(item.get("claim_id") or "")[:18],
                    verdict=record.get("verdict", ""),
                    refresh=False,
                )
                bar.update(1)
            return records

        # 并发时用 as_completed，谁先完成谁先写盘，避免整批结束后才看见文件
        with ThreadPoolExecutor(max_workers=min(workers, len(claims))) as pool:
            futures = {
                pool.submit(pipeline.retrieve, item["claim_zh"]): item for item in claims
            }
            for future in as_completed(futures):
                item = futures[future]
                try:
                    output = future.result()
                except Exception as exc:
                    from retrieval_adaptor.evidence_board import EvidenceBoard

                    board = EvidenceBoard(claim_zh=item["claim_zh"], gate=pipeline.gate)
                    output = board.empty_output("pipeline_error")
                    output.stats["error"] = "%s: %s" % (type(exc).__name__, exc)
                record = build_record(item, output)
                writer.write(record)
                records.append(record)
                bar.set_postfix(
                    claim=str(item.get("claim_id") or "")[:18],
                    verdict=record.get("verdict", ""),
                    refresh=False,
                )
                bar.update(1)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(
        description="批量跨语言检索：中文语段 -> 英文论文结构化证据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_argument_group("输入来源（择一）")
    source.add_argument("--article", "-a", help="公众号文章 Markdown：LLM 筛选观点句（推荐）")
    source.add_argument("--claim", action="append", help="直接给出中文断言，可重复")
    source.add_argument("--claims", help="断言文件（.json / .jsonl / .txt）")
    source.add_argument(
        "--wechat",
        default=None,
        help="公众号文章目录（需配合 --legacy-split，已废弃）",
    )
    source.add_argument(
        "--legacy-split",
        action="store_true",
        help="对 --wechat 使用旧规则切句（仅调试；生产请用 --article）",
    )
    source.add_argument(
        "--claims-out",
        default=None,
        help="--article 时把 LLM 观点句写到该 JSONL 路径",
    )

    parser.add_argument("--output", "-o", default="results", help="输出根目录")
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="已有 evidences.jsonl：跳过其中 claim_id，并追加写入该文件",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=1,
        help="并发数（默认 1，降低限流；需要加速可设 2）",
    )
    parser.add_argument("--limit", "-l", type=int, default=None, help="只跑前 N 条")
    parser.add_argument("--min-chars", type=int, default=12, help="规则切句最短句长（仅 legacy）")
    parser.add_argument("--index-dir", default=None, help="索引目录，默认取 .env / data/index")
    parser.add_argument("--chunks", default=None, help="chunks.json 路径")
    parser.add_argument("--high", type=float, default=None, help="覆盖高阈值")
    parser.add_argument("--low", type=float, default=None, help="覆盖低阈值")
    parser.add_argument("--verbose", "-v", action="store_true", help="打印 Agent 推理过程")

    args = parser.parse_args()

    if args.wechat and not args.legacy_split and not (args.claim or args.claims or args.article):
        raise SystemExit(
            "已废弃：单独使用 --wechat 规则切句。\n"
            "请改用: --article <文章.md>\n"
            "或显式: --wechat <目录> --legacy-split"
        )
    if args.legacy_split and not args.wechat:
        args.wechat = "data/wechat"

    claims = collect_claims(args)
    if args.limit:
        claims = claims[: args.limit]

    config = RetrievalConfig.from_env()
    if args.index_dir:
        config.index_dir = Path(args.index_dir)
    if args.chunks:
        config.chunks_file = Path(args.chunks)
    if args.high is not None:
        config.thresholds.high = args.high
    if args.low is not None:
        config.thresholds.low = args.low
    config.thresholds.validate()

    if args.resume:
        output_file = Path(args.resume)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        done = load_completed_keys(output_file)
        before = len(claims)
        claims = [item for item in claims if claim_key(item) not in done]
        print("续跑模式  : 已完成 %d，待跑 %d / 原 %d" % (len(done), len(claims), before))
        print("追加写入  : %s" % output_file)
    else:
        run_dir = Path(args.output) / time.strftime("%Y%m%d_%H%M%S")
        output_file = run_dir / "evidences.jsonl"
        done = set()

    writer = ResultWriter(output_file)

    print("=" * 78)
    print("批量跨语言检索")
    print("=" * 78)
    print("断言数量 : %d" % len(claims))
    print("索引目录 : %s" % config.index_dir)
    print(
        "双阈值   : high=%.2f / low=%.2f / min_hits=%d"
        % (config.thresholds.high, config.thresholds.low, config.thresholds.min_hits)
    )
    print("并发      : %d（完成一条即落盘）" % args.workers)
    print("输出      : %s" % writer.output_file)
    print("=" * 78)

    if not claims:
        print("没有待跑断言（可能已全部完成）。")
        summarize(load_records(output_file))
        print("\n结果已写入: %s" % writer.output_file)
        return 0

    with CrossLingualRetrievalPipeline(config=config, verbose=args.verbose) as pipeline:
        new_records = run_claims(pipeline, claims, writer, workers=args.workers)

    # 汇总时合并续跑前已有记录，避免统计只覆盖本轮
    all_records = load_records(output_file) if args.resume else new_records
    if args.resume and not all_records:
        all_records = new_records
    summarize(all_records)
    print("\n本轮新完成  : %d" % len(new_records))
    print("结果已写入: %s" % writer.output_file)
    print("清洗命令  : python clean_retrieval_output.py %s" % writer.output_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
