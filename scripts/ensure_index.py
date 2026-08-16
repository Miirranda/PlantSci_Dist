#!/usr/bin/env python3
"""为单篇论文确保句级向量索引存在：已有则复用，没有才重建。

Usage:
  python scripts/ensure_index.py --paper-id P001
  python scripts/ensure_index.py --paper-id P001 --rebuild
  python scripts/ensure_index.py --paper-id P001 --skip-embed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_ARAG = _PROJECT_ROOT / "arag-main"
for path in (_PROJECT_ROOT, _ARAG):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
# retrieval_adaptor 必须优先于仓库根下可能重名的包
sys.path.insert(0, str(_ARAG))

from hallu.config import ensure_env  # noqa: E402

ensure_env()

from retrieval_adaptor.index_builder import ensure_index  # noqa: E402
from retrieval_adaptor.paper_registry import canonical_paper_id, load_registry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="按 paper_id 确保单篇论文索引")
    parser.add_argument("--paper-id", default="", help="如 P001；省略则列出注册表")
    parser.add_argument("--pdf", default="", help="覆盖注册表中的 PDF")
    parser.add_argument("--rebuild", action="store_true", help="强制重建")
    parser.add_argument("--skip-embed", action="store_true", help="只切句，不向量化")
    args = parser.parse_args()

    paper_id = canonical_paper_id(args.paper_id)
    if not paper_id:
        registry = load_registry()
        if not registry:
            print("注册表为空或不存在: data/papers/papers_index.json")
            return 1
        print("已登记论文：")
        for record in registry.values():
            print("  %s  %s" % (record.paper_id, record.title or record.pdf or "(无标题)"))
        print("用法: python scripts/ensure_index.py --paper-id P001")
        return 0

    result = ensure_index(
        paper_id,
        rebuild=args.rebuild,
        skip_embed=args.skip_embed,
        pdf=args.pdf or None,
    )
    print("paper_id: %s" % paper_id)
    print("reused: %s" % result.get("reused"))
    if result.get("index_dir"):
        print("index_dir: %s" % result["index_dir"])
    if result.get("index_version"):
        print("index_version: %s" % result["index_version"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
