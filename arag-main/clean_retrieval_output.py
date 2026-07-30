#!/usr/bin/env python3
"""从完整检索结果中清洗出精简「claim ↔ 英文证据句」对照表。

不改动检索链路。完整 ``evidences.jsonl``（含 sentence_id / verdict / 元数据等）继续保留，
本脚本只做后处理。

输出每行::

    {
      "claim_id": "C01",
      "claim_zh": "<公众号观点句>",
      "evidences": [
        {"sentence_id": 42, "text": "<论文原句>"}
      ]
    }

为兼容旧调用，仍接受输出文件名 ``claim_paper_pairs.jsonl``；
推荐新名 ``claim_evidence_pairs.jsonl``。

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


def _sentence_from_evidence(item: dict[str, Any]) -> str:
    """优先取匹配句本身；evidence_en 为空时回退到 context.target_text。"""
    text = str(item.get("evidence_en") or "").strip()
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


def clean_record(record: dict[str, Any]) -> dict[str, Any]:
    """把一条完整检索结果压成 claim + 带 sentence_id 的证据列表。"""
    claim = str(record.get("claim_zh") or "").strip()
    claim_id = str(record.get("claim_id") or record.get("id") or "").strip()
    evidences: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in record.get("evidences") or []:
        if not isinstance(item, dict):
            continue
        sentence = _sentence_from_evidence(item)
        if not sentence or sentence in seen:
            continue
        seen.add(sentence)
        evidences.append(
            {
                "sentence_id": _sentence_id_from_evidence(item),
                "text": sentence,
            }
        )
    row: dict[str, Any] = {
        "claim_zh": claim,
        "evidences": evidences,
    }
    if claim_id:
        row["claim_id"] = claim_id
    # 兼容旧 hallu 适配：仍提供 paper_sentences 纯文本列表
    row["paper_sentences"] = [ev["text"] for ev in evidences]
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
                raise ValueError("%s 第 %d 行不是合法 JSON: %s" % (input_path, line_no, exc)) from exc
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
        description="清洗检索结果：观点句 + 带 sentence_id 的论文原句",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="完整 evidences.jsonl 路径",
    )
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
