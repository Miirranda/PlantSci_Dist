#!/usr/bin/env python3
"""把文件2（失真审核）的结果合并回文件1，完成闭环。

读文件2 ``*_distortion_review.json`` 的 ``human_reviews``，把失真标注字段 +
``generated_analysis`` + 补好翻译的 ``manual_retrieved_paragraphs`` 写回文件1
``*_annotation_draft.json`` 对应 record。只写失真相关字段，不覆盖文件1 的召回字段
（gold_sentence_ids / recall_note / recall_reviewed / recall_updated_at）。

用法::

    python scripts/merge_reviews.py --draft data/annotations/P001/P001_A001_annotation_draft.json
    python scripts/merge_reviews.py --draft ... --distortion data/annotations/P001/P001_A001_distortion_review.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent

# 从文件2 写回文件1 的字段（失真 + AI 分析 + 补好翻译的段落）
_MERGE_FIELDS = (
    "generated_analysis",
    "evidence_level",
    "primary_level2",
    "secondary_level2",
    "severity",
    "uncovered_phenomenon",
    "note",
    "human_verified",
    "manual_retrieved_paragraphs",
)


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def derive_distortion_path(draft_path: Path) -> Path:
    """文件1 -> 文件2 命名：<prefix>_annotation_draft.json -> <prefix>_distortion_review.json。"""
    name = draft_path.name
    if name.endswith("_annotation_draft.json"):
        name = name[: -len("_annotation_draft.json")] + "_distortion_review.json"
    else:
        name = draft_path.stem + "_distortion_review.json"
    return draft_path.with_name(name)


def atomic_write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="文件2 失真审核结果合并回文件1")
    parser.add_argument("--draft", default="", help="召回审核文件（文件1）JSON 路径")
    parser.add_argument("--distortion", default="", help="失真审核文件（文件2）路径，缺省按命名推导")
    args = parser.parse_args()

    if not args.draft:
        raise SystemExit("请用 --draft 指定文件1（*_annotation_draft.json）")
    draft_path = _resolve(args.draft)
    if not draft_path.exists():
        raise SystemExit("找不到文件1: %s" % draft_path)

    dist_path = _resolve(args.distortion) if args.distortion else derive_distortion_path(draft_path)
    if not dist_path.exists():
        raise SystemExit("找不到文件2: %s（请先运行 regenerate_analysis.py）" % dist_path)

    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    dist = json.loads(dist_path.read_text(encoding="utf-8"))

    draft_reviews = draft.setdefault("human_reviews", {})
    dist_reviews = dist.get("human_reviews") or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    merged = 0
    for reviewer, recs in dist_reviews.items():
        for sample_id, dist_rec in (recs or {}).items():
            rec = (draft_reviews.get(reviewer) or {}).get(sample_id)
            if rec is None:
                # 文件1 里没有该记录（理论不应发生），新建
                rec = {}
                draft_reviews.setdefault(reviewer, {})[sample_id] = rec
            for key in _MERGE_FIELDS:
                if key in dist_rec:
                    rec[key] = dist_rec[key]
            rec["updated_at"] = now
            merged += 1

    atomic_write(draft_path, draft)
    print("合并 %d 条（%s -> %s）" % (merged, dist_path, draft_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
