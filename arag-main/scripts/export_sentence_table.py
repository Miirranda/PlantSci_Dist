#!/usr/bin/env python3
"""从句级索引导出 CSV 句表，供人工标注勾选 sentence_id。

Usage:
    python scripts/export_sentence_table.py
    python scripts/export_sentence_table.py --index-dir data/index --paper-id P001 \\
        -o ../data/annotations/P001_sentences.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from retrieval_adaptor.index_store import IndexStore  # noqa: E402
from retrieval_adaptor.paper_registry import canonical_paper_id, layout_for  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="导出索引句表 CSV")
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=None,
        help="索引目录（默认 data/index/<paper_id>/）",
    )
    parser.add_argument("--paper-id", default="", help="短论文 id，如 P001")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="输出 CSV 路径",
    )
    args = parser.parse_args()

    paper_id = canonical_paper_id(args.paper_id)
    if args.index_dir is not None:
        index_dir = args.index_dir
    elif paper_id:
        index_dir = layout_for(paper_id).index_dir
    else:
        parser.error("请提供 --paper-id 或 --index-dir")
        return 2

    store = IndexStore(index_dir)
    paper_id = paper_id or str(getattr(store, "paper_id", "") or "")
    output = args.output
    if output is None:
        if paper_id:
            output = layout_for(paper_id).sentences_csv
        else:
            output = Path(index_dir) / "index_sentences.csv"

    path = store.export_sentence_table(output, paper_id=paper_id)
    print("句表: %d 句 -> %s" % (len(store), path))
    print("索引信息: %s" % store.describe())
    if getattr(store, "index_version", ""):
        print("index_version: %s" % store.index_version)
    if store.built_at:
        print("built_at: %s" % store.built_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
