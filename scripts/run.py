#!/usr/bin/env python3
"""植物科学公众号信息失真检测 — 统一编排入口。

流程:
  文章 → [arag] 规则分句+LLM核验观点句 + RAG+Agent检索
       → 同一次检索拆成分类 top-5 / 审核池 10
       → [hallu] 信息失真分类（只用 top-5）→ 证据链 JSON

用法:
    python scripts/run.py \\
    --article data/articles/high_quality/P001_A001_黄瓜下位子房的发育机制.md \\
    --paper-id P001
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from hallu.config import ARAG_ROOT, QWEN_MODEL, ensure_env  # noqa: E402

ensure_env()

from api_client import QwenClient  # noqa: E402

from hallu.adapters.from_arag import (  # noqa: E402
    arag_pairs_to_retrieval_results,
    evidences_to_pairs,
    load_claim_paper_pairs,
    load_evidences_jsonl,
    save_pairs_jsonl,
)
from hallu.arag_bridge import clean_arag_output, run_arag_article_pipeline  # noqa: E402
from hallu.classifier import classify_all  # noqa: E402
from hallu.evidence_chain import build_final_output  # noqa: E402
from retrieval_adaptor.index_builder import ensure_index  # noqa: E402
from retrieval_adaptor.paper_registry import (  # noqa: E402
    canonical_paper_id,
    infer_ids,
    layout_for,
    resolve_pdf,
)


def _resolve(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def _infer_ids(article_path: Path, paper_path: Path) -> tuple[str, str]:
    paper_id, article_id = "PXXX", "AXXX"
    m = re.search(r"(P\d+)", paper_path.stem, re.I)
    if m:
        paper_id = m.group(1).upper()
    m = re.search(r"(P\d+)[_\-]?(A\d+)", article_path.stem, re.I)
    if m:
        paper_id = m.group(1).upper()
        article_id = m.group(2).upper()
    else:
        m = re.search(r"(A\d+)", article_path.stem, re.I)
        if m:
            article_id = m.group(1).upper()
    return paper_id, article_id


def _load_claims(output_dir: Path) -> list[dict]:
    jsonl = output_dir / "claims.jsonl"
    js = output_dir / "claims.json"
    claims: list[dict] = []
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            claims.append(
                {
                    "id": row.get("claim_id") or row.get("id"),
                    "claim_text": row.get("claim_zh") or row.get("claim_text") or "",
                    "context_before": row.get("context_before", ""),
                    "context_after": row.get("context_after", ""),
                    "section": row.get("section", ""),
                }
            )
        return claims
    if js.exists():
        data = json.loads(js.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return data.get("claims", [])
    raise FileNotFoundError("未找到 claims 文件: %s" % jsonl)


def _print_summary(classification_results: list[dict]) -> None:
    total = len(classification_results)
    level_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    distortion_n = 0
    unverifiable_n = 0
    for r in classification_results:
        clf = r.get("classification", {})
        lvl = clf.get("evidence_level", "Unknown")
        level_counts[lvl] = level_counts.get(lvl, 0) + 1
        ptype = clf.get("primary_type") or ""
        if not ptype:
            pl = clf.get("primary_label") or {}
            if isinstance(pl, dict):
                ptype = str(pl.get("level2") or "")
        if ptype:
            type_counts[ptype] = type_counts.get(ptype, 0) + 1
        has_d = clf.get("has_distortion")
        if has_d is True:
            distortion_n += 1
        elif has_d is None and clf.get("evidence_level") in (
            "No_Evidence",
            "Weak_Evidence",
        ):
            unverifiable_n += 1

    print("\n" + "=" * 60)
    print("  运行摘要")
    print("=" * 60)
    print("  观点句总数: %d" % total)
    print("  With_Evidence: %d" % level_counts.get("With_Evidence", 0))
    print("  Weak_Evidence: %d" % level_counts.get("Weak_Evidence", 0))
    print("  No_Evidence:   %d" % level_counts.get("No_Evidence", 0))
    print("  no_distortion: %d" % type_counts.get("no_distortion", 0))
    print("  存在失真:      %d" % distortion_n)
    print("  未判定失真类型: %d" % unverifiable_n)
    if distortion_n:
        print("  失真分布:")
        for ptype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            if ptype in ("no_distortion", "accurate"):
                continue
            print("    - %s: %d" % (ptype, count))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="植物科学公众号科普文章信息失真检测 Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--article", "-a", required=True, help="公众号文章 Markdown 路径")
    parser.add_argument(
        "--paper",
        "-p",
        default="",
        help="参考论文 PDF；可省略，由 --paper-id 与 papers_index.json 解析",
    )
    parser.add_argument("--paper-id", default="", help="短论文 id，如 P001；决定检索哪一座索引")
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="强制重建该篇索引（默认已有则复用）",
    )
    parser.add_argument(
        "--no-ensure-index",
        action="store_true",
        help="不自动建库；索引不存在时直接失败",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=None,
        help="输出目录（默认 outputs/<paper_id>/<article_id>）",
    )
    parser.add_argument("--model", default=None, help="分类用 LLM（默认 %s）" % QWEN_MODEL)
    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="跳过 LLM 抽句，复用已有 claims.jsonl",
    )
    parser.add_argument(
        "--skip-retrieval",
        action="store_true",
        help="跳过 arag 检索，复用已有 evidences/pairs",
    )
    parser.add_argument(
        "--from-step",
        choices=["arag", "classify", "chain"],
        default="arag",
        help="从指定阶段开始",
    )
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 条观点句")
    parser.add_argument("--workers", type=int, default=1, help="arag 检索并发数")
    parser.add_argument("--verbose", "-v", action="store_true", help="arag Agent 详细日志")
    parser.add_argument("--paper-title", default="", help="论文标题（可选）")
    parser.add_argument("--article-title", default="", help="文章标题（可选）")

    args = parser.parse_args()

    article_path = _resolve(args.article)
    if not article_path.exists():
        print("文章不存在: %s" % article_path)
        return 1
    if not ARAG_ROOT.exists():
        print("找不到 arag-main: %s" % ARAG_ROOT)
        return 1

    paper_id = canonical_paper_id(args.paper_id)
    inferred_paper, article_id = infer_ids(article_path, args.paper)
    paper_id = paper_id or inferred_paper
    if not paper_id:
        paper_id, article_id = _infer_ids(article_path, Path(args.paper or article_path))
    if not paper_id:
        print("无法确定 paper_id，请传入 --paper-id Pxxx")
        return 1

    if args.paper:
        paper_path = _resolve(args.paper)
    else:
        try:
            paper_path = resolve_pdf(paper_id)
        except FileNotFoundError as exc:
            print(str(exc))
            return 1
    if not paper_path.exists():
        print("论文不存在: %s" % paper_path)
        return 1

    layout = layout_for(paper_id, pdf=paper_path)
    output_dir = (
        _resolve(args.output_dir)
        if args.output_dir
        else _PROJECT_ROOT / "outputs" / paper_id / article_id
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    do_arag = args.from_step == "arag"
    do_classify = args.from_step in ("arag", "classify")
    do_chain = True
    if args.from_step == "classify":
        do_arag = False
    if args.from_step == "chain":
        do_arag = False
        do_classify = False

    skip_extract = args.skip_extract
    skip_retrieval = args.skip_retrieval
    if not do_arag:
        skip_extract = True
        skip_retrieval = True

    need_index = do_arag and not skip_retrieval
    if need_index:
        if args.no_ensure_index:
            if not layout.index_file.is_file():
                print("索引不存在: %s" % layout.index_file)
                print("请先运行: python scripts/ensure_index.py --paper-id %s" % paper_id)
                return 1
        else:
            ensure_index(
                paper_id,
                rebuild=args.rebuild_index,
                pdf=paper_path,
            )

    model = args.model or QWEN_MODEL
    pairs_path = output_dir / "claim_evidence_pairs.jsonl"
    legacy_pairs_path = output_dir / "claim_paper_pairs.jsonl"
    clf_path = output_dir / "classification.json"
    result_path = output_dir / "result.json"
    report_path = output_dir / "report.md"

    print("=" * 60)
    print("  植物科学公众号信息失真检测 Pipeline")
    print("=" * 60)
    print("  文章: %s" % article_path)
    print("  论文: %s" % paper_path)
    print("  样本: %s/%s" % (paper_id, article_id))
    print("  索引: %s" % layout.index_dir)
    print("  输出: %s" % output_dir)
    print("  模型: %s" % model)
    print("  arag: %s" % ARAG_ROOT)
    print("=" * 60)

    # ------------------------------------------------------------------
    # Phase 1–2: arag（LLM 抽句 + 检索）
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("  Phase 1–2/4: arag — LLM观点句 + RAG检索")
    print("=" * 60)

    if do_arag and not (
        skip_extract
        and skip_retrieval
        and (pairs_path.exists() or legacy_pairs_path.exists())
    ):
        if skip_retrieval and (pairs_path.exists() or legacy_pairs_path.exists()):
            print("  跳过 arag，复用 pairs")
        else:
            run_arag_article_pipeline(
                article_path=article_path,
                output_dir=output_dir,
                workers=args.workers,
                limit=args.limit,
                skip_extract=skip_extract,
                skip_retrieval=skip_retrieval,
                resume=True,
                verbose=args.verbose,
                paper_id=paper_id,
            )

    claims = _load_claims(output_dir)
    if args.limit and len(claims) > args.limit:
        claims = claims[: args.limit]
        print("  --limit=%d，下游使用 %d 条" % (args.limit, len(claims)))

    evidences_path = output_dir / "evidences.jsonl"
    active_pairs = (
        pairs_path
        if pairs_path.exists()
        else legacy_pairs_path
        if legacy_pairs_path.exists()
        else pairs_path
    )
    if active_pairs.exists() and skip_retrieval:
        pairs = load_claim_paper_pairs(active_pairs)
    elif evidences_path.exists():
        evidences = load_evidences_jsonl(evidences_path)
        pairs = evidences_to_pairs(evidences)
        for i, pair in enumerate(pairs):
            if not pair.get("claim_id") and i < len(claims):
                pair["claim_id"] = claims[i].get("id")
        save_pairs_jsonl(pairs, pairs_path)
        try:
            clean_arag_output(evidences_path, pairs_path)
        except Exception as exc:
            print("  [warn] clean 可选失败: %s" % exc)
        print("  已保存: %s" % pairs_path)
    elif active_pairs.exists():
        pairs = load_claim_paper_pairs(active_pairs)
    else:
        print("找不到检索结果: %s 或 %s" % (evidences_path, pairs_path))
        return 1

    # 对齐 claims 顺序
    by_text = {p.get("claim_zh", "").strip(): p for p in pairs}
    by_id = {str(p.get("claim_id") or ""): p for p in pairs}
    aligned = []
    for c in claims:
        cid = str(c.get("id") or "")
        text = (c.get("claim_text") or "").strip()
        pair = by_id.get(cid) or by_text.get(text) or {
            "claim_id": cid,
            "claim_zh": text,
            "paper_sentences": [],
        }
        aligned.append(pair)
    # 分类只用 top-5；pairs 中另保留 review_evidences（10 条）供标注审核
    retrieval_results = arag_pairs_to_retrieval_results(aligned, claims=claims, top_k=5)
    with_ev = sum(1 for r in retrieval_results if r.get("evidence_sentences"))
    review_n = sum(len(r.get("review_evidences") or []) for r in retrieval_results)
    print("  检索对齐: %d/%d 条有分类证据（top-5）" % (with_ev, len(retrieval_results)))
    if retrieval_results:
        print(
            "  审核池: 平均 %.1f 条/claim（目标 10）"
            % (review_n / float(len(retrieval_results)))
        )

    # ------------------------------------------------------------------
    # Phase 3: 信息失真细分类
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("  Phase 3/4: hallu — 信息失真细分类")
    print("=" * 60)

    if do_classify:
        client = QwenClient(verbose=False)
        classification_results = classify_all(
            retrieval_results=retrieval_results,
            client=client,
            model=model,
        )
        clf_path.write_text(
            json.dumps(classification_results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("  已保存: %s" % clf_path)
    else:
        if not clf_path.exists():
            print("找不到分类结果: %s" % clf_path)
            return 1
        classification_results = json.loads(clf_path.read_text(encoding="utf-8"))
        print("  复用分类结果: %d 条" % len(classification_results))

    # ------------------------------------------------------------------
    # Phase 4: 证据链
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("  Phase 4/4: hallu — 证据链生成")
    print("=" * 60)

    if do_chain:
        paper_title = args.paper_title or paper_path.name
        article_title = args.article_title or article_path.name
        final_output = build_final_output(
            classification_results=classification_results,
            paper_title=paper_title,
            article_title=article_title,
            output_path=str(result_path),
        )
        report_path.write_text(
            final_output.get("evidence_chain_markdown") or "",
            encoding="utf-8",
        )
        final_output["meta"]["paper_id"] = paper_id
        final_output["meta"]["article_id"] = article_id
        final_output["meta"]["article_path"] = str(article_path)
        final_output["meta"]["paper_path"] = str(paper_path)
        final_output["meta"]["generated_at"] = datetime.now().isoformat()
        result_path.write_text(
            json.dumps(final_output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("  已保存: %s" % result_path)
        print("  已保存: %s" % report_path)

    _print_summary(classification_results)
    print("\n  完整结果: %s" % result_path)
    print("  中间产物: %s/" % output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
