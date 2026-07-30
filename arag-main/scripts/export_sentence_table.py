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

from retrieval_adaptor.config import INDEX_DIR  # noqa: E402
from retrieval_adaptor.index_store import IndexStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="导出索引句表 CSV")
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=INDEX_DIR,
        help="索引目录（含 sentence_index.pkl）",
    )
    parser.add_argument("--paper-id", default="", help="写入 CSV 的 paper_id 列（可选）")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="输出 CSV 路径",
    )
    args = parser.parse_args()

    store = IndexStore(args.index_dir)
    paper_id = str(args.paper_id or "").strip()
    output = args.output
    if output is None:
        name = "%s_sentences.csv" % (paper_id or "index")
        output = PROJECT_ROOT.parent / "data" / "annotations" / name
        if not output.parent.exists():
            output = Path(args.index_dir) / name

    path = store.export_sentence_table(output, paper_id=paper_id)
    print("句表: %d 句 -> %s" % (len(store), path))
    print("索引信息: %s" % store.describe())
    if store.built_at:
        print("built_at: %s" % store.built_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
