#!/usr/bin/env python3
"""按 paper_id 为单篇论文建句级向量索引。

推荐::

    python scripts/build_index.py --paper-id P001
    python scripts/build_index.py --paper-id P001 --rebuild
    python scripts/build_index.py --paper-id P001 --from-sentences

已有 ``data/index/P001/sentence_index.pkl`` 且 meta 完整时直接复用。
禁止把整个 papers 目录打进同一套 sentence_id。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from retrieval_adaptor.index_builder import (  # noqa: E402
    build_index,
    build_index_from_sentences,
    embed_sentences,
    ensure_index,
    load_chunks,
)
from retrieval_adaptor.paper_registry import canonical_paper_id, infer_ids  # noqa: E402
from retrieval_adaptor.pdf_ingest import split_english_sentences  # noqa: E402

# 兼容旧测试名
split_sentences = split_english_sentences


def main() -> int:
    parser = argparse.ArgumentParser(description="为单篇论文建立语义检索索引")
    parser.add_argument(
        "--paper-id",
        default="",
        help="短论文 id，如 P001；决定 data/index/P001/ 与注册表中的 PDF",
    )
    parser.add_argument(
        "--pdf",
        default="",
        help="覆盖注册表中的 PDF 路径",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="即使已有完整索引也重建",
    )
    parser.add_argument(
        "--chunks",
        "-c",
        default="",
        help="已有 chunks.json 时跳过 PDF（仍须 --paper-id 或能从 chunks 推断）",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="",
        help="覆盖默认索引目录 data/index/<paper_id>/",
    )
    parser.add_argument(
        "--papers",
        "-p",
        default=None,
        help="单个 PDF 路径；若传入目录且含多篇 PDF 会拒绝",
    )
    parser.add_argument(
        "--target-chars", type=int, default=1200, help="单个 chunk 的目标字符数"
    )
    parser.add_argument(
        "--min-sentence-chars", type=int, default=30, help="入索引的句子最小字符数"
    )
    parser.add_argument(
        "--skip-embed",
        action="store_true",
        help="只切句并写 index_meta.json / 句表，不调用向量化",
    )
    parser.add_argument(
        "--sentences-out",
        default="",
        help="句表 CSV；默认 data/annotations/<paper_id>/<paper_id>_sentences.csv",
    )
    parser.add_argument(
        "--from-sentences",
        action="store_true",
        help="从句表建库，不重新切 PDF，也不覆盖句表",
    )
    parser.add_argument(
        "--sentences",
        default="",
        help="句表路径；配合 --from-sentences，默认 data/annotations/<id>/<id>_sentences.csv",
    )
    parser.add_argument("--model", "-m", default=None, help="（已弃用）")
    parser.add_argument("--device", "-d", default=None, help="（已弃用）")
    parser.add_argument("--batch-size", "-b", type=int, default=None, help="（已弃用）")
    args = parser.parse_args()

    if args.model or args.device or args.batch_size:
        print(
            "提示：--model/--device/--batch-size 在在线向量化模式下已弃用，"
            "请在 .env 中配置 SILICONFLOW_* 相关项。"
        )

    paper_id = canonical_paper_id(args.paper_id)
    if not paper_id and args.papers:
        paper_id, _ = infer_ids(args.papers)
    if not paper_id and args.pdf:
        paper_id, _ = infer_ids(args.pdf)

    from_sentences: bool | str = False
    if args.sentences:
        from_sentences = args.sentences
    elif args.from_sentences:
        from_sentences = True

    if from_sentences and paper_id and not args.output:
        ensure_index(
            paper_id,
            rebuild=args.rebuild,
            skip_embed=args.skip_embed,
            pdf=args.pdf or args.papers,
            target_chars=args.target_chars,
            min_sentence_chars=args.min_sentence_chars,
            from_sentences=from_sentences,
        )
        return 0

    if from_sentences:
        csv_path = args.sentences or ""
        if not csv_path:
            parser.error("--from-sentences 配合 --output 时请同时给 --sentences")
        result = build_index_from_sentences(
            csv_path,
            args.output or "data/index",
            paper_id=paper_id,
            chunks_file=args.chunks or None,
            skip_embed=args.skip_embed,
            min_sentence_chars=args.min_sentence_chars,
        )
        if result.get("index_version"):
            print("index_version: %s" % result["index_version"])
        return 0

    if paper_id and not args.chunks and not args.output:
        ensure_index(
            paper_id,
            rebuild=args.rebuild,
            skip_embed=args.skip_embed,
            pdf=args.pdf or args.papers,
            target_chars=args.target_chars,
            min_sentence_chars=args.min_sentence_chars,
        )
        return 0

    if not args.chunks and not args.papers and not paper_id:
        parser.error("请提供 --paper-id Pxxx（推荐），或 --papers <单个PDF> / --chunks")

    result = build_index(
        chunks_file=args.chunks or "data/corpus/chunks.json",
        output_dir=args.output or "data/index",
        papers_dir=args.papers or args.pdf or None,
        target_chars=args.target_chars,
        min_sentence_chars=args.min_sentence_chars,
        skip_embed=args.skip_embed,
        paper_id=paper_id,
        sentences_out=args.sentences_out or None,
    )
    if result.get("index_version"):
        print("index_version: %s" % result["index_version"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
