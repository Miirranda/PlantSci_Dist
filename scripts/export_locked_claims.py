#!/usr/bin/env python3
"""从审 A JSON 导出锁定观点句名单（只保留 review_decision=keep）。

Usage:
  python scripts/export_locked_claims.py
  python scripts/export_locked_claims.py --review data/annotations/P001/P001_A001_claims_for_review.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_ARAG = _PROJECT_ROOT / "arag-main"
for path in (_ARAG, _ARAG / "src", _PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from retrieval_adaptor.claim_extractor import (  # noqa: E402
    export_locked_claims_from_review,
    save_claims_json,
    save_claims_jsonl,
)


def _fill_empty_keep(review_path: Path) -> int:
    """空 review_decision → keep。返回填充条数。"""
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    samples = payload.get("samples") or []
    changed = 0
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        if not str(sample.get("review_decision") or "").strip():
            sample["review_decision"] = "keep"
            changed += 1

    if not changed:
        return 0

    payload["instructions"] = (
        "review_decision 默认 keep；发现不该进 Benchmark 时改为 drop 或 merge。"
        "不要改写 claim_zh。"
    )
    payload["sample_count"] = len(samples)
    review_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="从审 A 导出锁定观点句名单")
    parser.add_argument(
        "--review",
        default=str(
            _PROJECT_ROOT
            / "data"
            / "annotations"
            / "P001"
            / "P001_A001_claims_for_review.json"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="默认与审 A JSON 同目录",
    )
    parser.add_argument(
        "--no-fill-empty-keep",
        action="store_true",
        help="不把空的 review_decision 自动填成 keep",
    )
    args = parser.parse_args()

    review_path = Path(args.review)
    if not review_path.is_file():
        print("找不到审 A 文件: %s" % review_path)
        return 1

    if not args.no_fill_empty_keep:
        n = _fill_empty_keep(review_path)
        if n:
            print("已将 %d 条空 review_decision 填为 keep" % n)

    payload = json.loads(review_path.read_text(encoding="utf-8"))
    paper_id = str(payload.get("paper_id") or "P001")
    article_id = str(payload.get("article_id") or "A001")
    out_dir = Path(args.output_dir) if args.output_dir else review_path.parent

    source_path = out_dir / ("%s_%s_claims.jsonl" % (paper_id, article_id))
    source_claims = None
    if source_path.is_file():
        source_claims = [
            json.loads(line)
            for line in source_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    locked = export_locked_claims_from_review(review_path, source_claims=source_claims)
    jsonl_path = out_dir / ("%s_%s_claims.jsonl" % (paper_id, article_id))
    json_path = out_dir / ("%s_%s_claims.json" % (paper_id, article_id))
    save_claims_jsonl(locked, jsonl_path)
    save_claims_json(locked, json_path)

    dropped = merged = 0
    for sample in payload.get("samples") or []:
        decision = str(sample.get("review_decision") or "keep").strip().lower()
        if decision == "drop":
            dropped += 1
        elif decision == "merge":
            merged += 1

    print("锁定名单 JSONL: %s" % jsonl_path)
    print("锁定名单 JSON: %s" % json_path)
    print("保留: %d | drop: %d | merge: %d" % (len(locked), dropped, merged))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
