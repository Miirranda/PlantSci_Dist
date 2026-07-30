#!/usr/bin/env python3
"""从完整检索结果中清洗出「观点句 + 多条论文原句」。

不改动检索链路。完整 ``evidences.jsonl``（含 verdict / 元数据 / stats 等）继续保留，
本脚本只做后处理，生成一份现阶段需要的精简对照表。

输入：``batch_retrieval.py`` 产出的 JSONL（每行一条 RetrievalOutput）
输出：同目录或指定路径下的 ``claim_paper_pairs.jsonl``，每行仅含::

    {
      "claim_zh": "<公众号观点句>",
      "paper_sentences": ["<相关论文原句1>", "<相关论文原句2>", ...]
    }

Usage:
    python clean_retrieval_output.py results/20260727_191416/evidences.jsonl
    python clean_retrieval_output.py results/20260727_191416/evidences.jsonl -o results/pairs.jsonl
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


def clean_record(record: dict[str, Any]) -> dict[str, Any]:
    """把一条完整检索结果压成观点句 + 去重后的论文原句列表。"""
    claim = str(record.get("claim_zh") or "").strip()
    sentences: list[str] = []
    seen: set[str] = set()
    for item in record.get("evidences") or []:
        if not isinstance(item, dict):
            continue
        sentence = _sentence_from_evidence(item)
        if not sentence or sentence in seen:
            continue
        seen.add(sentence)
        sentences.append(sentence)
    return {"claim_zh": claim, "paper_sentences": sentences}


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
    return input_path.with_name("claim_paper_pairs.jsonl")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="清洗检索结果：只保留观点句与相关论文原句",
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
        help="精简输出路径（默认写到同目录 claim_paper_pairs.jsonl）",
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
