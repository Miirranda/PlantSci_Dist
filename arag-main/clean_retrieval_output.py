#!/usr/bin/env python3
"""从完整检索结果中清洗出精简「claim ↔ 英文证据句」对照表。

不改动检索链路。完整 ``evidences.jsonl``（含 sentence_id / verdict / 元数据等）继续保留，
本脚本只做后处理。

同一次检索，两套用途：
  - classify_evidences：幻觉分类用 top-5
  - review_evidences：人工审核池固定 10 条（包含 top-5）

输出每行::

    {
      "claim_id": "C01",
      "claim_zh": "<公众号观点句>",
      "classify_evidences": [
        {"rank": 1, "sentence_id": 42, "text": "<论文原句>"}
      ],
      "review_evidences": [
        {"rank": 1, "sentence_id": 42, "text": "<论文原句>"}
      ],
      "evidences": [ ... ]   # 兼容旧字段：等同 review_evidences（无 rank 亦可）
    }

Usage:
    python clean_retrieval_output.py results/.../evidences.jsonl
    python clean_retrieval_output.py results/.../evidences.jsonl -o claim_evidence_pairs.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CLASSIFY_TOP_K = 5
REVIEW_POOL_SIZE = 10


def _sentence_from_evidence(item: dict[str, Any]) -> str:
    """优先取匹配句本身；evidence_en 为空时回退到 context.target_text。"""
    text = str(item.get("evidence_en") or item.get("text") or item.get("sentence") or "").strip()
    if text:
        return text
    context = item.get("context") or {}
    return str(context.get("target_text") or "").strip()


def _sentence_id_from_evidence(item: dict[str, Any]) -> int:
    raw = item.get("sentence_id", -1)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


def _ranked_evidences(items: list[Any], *, limit: int) -> list[dict[str, Any]]:
    """去重保序，截到 limit，并写入 rank / sentence_id / text。"""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if len(rows) >= limit:
            break
        if isinstance(item, str):
            sentence = item.strip()
            sid = -1
        elif isinstance(item, dict):
            sentence = _sentence_from_evidence(item)
            sid = _sentence_id_from_evidence(item)
        else:
            continue
        if not sentence or sentence in seen:
            continue
        seen.add(sentence)
        rows.append(
            {
                "rank": len(rows) + 1,
                "sentence_id": sid,
                "text": sentence,
            }
        )
    return rows


def clean_record(
    record: dict[str, Any],
    *,
    classify_top_k: int = CLASSIFY_TOP_K,
    review_pool_size: int = REVIEW_POOL_SIZE,
) -> dict[str, Any]:
    """把一条完整检索结果压成 claim + 分类用 top-k + 审核池。"""
    claim = str(record.get("claim_zh") or "").strip()
    claim_id = str(record.get("claim_id") or record.get("id") or "").strip()

    raw_items = list(record.get("evidences") or record.get("review_evidences") or [])
    if not raw_items and record.get("paper_sentences"):
        raw_items = list(record.get("paper_sentences") or [])

    review = _ranked_evidences(raw_items, limit=review_pool_size)
    classify = review[:classify_top_k]

    row: dict[str, Any] = {
        "claim_zh": claim,
        "classify_evidences": classify,
        "review_evidences": review,
        # 兼容：旧代码读 evidences 时拿到完整审核池
        "evidences": [
            {"sentence_id": ev["sentence_id"], "text": ev["text"]} for ev in review
        ],
        "paper_sentences": [ev["text"] for ev in review],
    }
    if claim_id:
        row["claim_id"] = claim_id
    verdict = record.get("verdict")
    if verdict:
        row["verdict"] = verdict
    return row


def clean_file(input_path: Path, output_path: Path) -> int:
    """逐行清洗并落盘，返回写出条数。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with (
        input_path.open("r", encoding="utf-8") as src,
        output_path.open("w", encoding="utf-8") as dst,
    ):
        for line_no, line in enumerate(src, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "%s 第 %d 行不是合法 JSON: %s" % (input_path, line_no, exc)
                ) from exc
            if not isinstance(record, dict):
                raise ValueError("%s 第 %d 行不是 JSON 对象" % (input_path, line_no))
            cleaned = clean_record(record)
            if not cleaned["claim_zh"]:
                continue
            dst.write(json.dumps(cleaned, ensure_ascii=False) + "\n")
            count += 1
    return count


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name("claim_evidence_pairs.jsonl")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="清洗检索结果：分类 top-5 + 审核池 10 条（含 sentence_id）",
    )
    parser.add_argument("input", type=Path, help="完整 evidences.jsonl 路径")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="精简输出路径（默认同目录 claim_evidence_pairs.jsonl）",
    )
    args = parser.parse_args()

    input_path = args.input
    if not input_path.is_file():
        print("输入文件不存在: %s" % input_path, file=sys.stderr)
        return 1

    output_path = args.output or default_output_path(input_path)
    count = clean_file(input_path, output_path)
    print("已清洗 %d 条 -> %s" % (count, output_path))
    print("  classify_top_k=%d, review_pool_size=%d" % (CLASSIFY_TOP_K, REVIEW_POOL_SIZE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
