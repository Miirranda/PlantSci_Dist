#!/usr/bin/env python3
"""对 Agent+RAG 召回初稿做证据再定位，写出独立的 recall_review 文件。

不覆盖召回初稿，不改句表，不读取 PDF。PDF 路径只记在结果文件头，供人工核对。

用法::

    python scripts/run_recall_review.py --paper P006
    python scripts/run_recall_review.py --papers P006 P011 --limit 2
    python scripts/run_recall_review.py --paper P006 --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from hallu.config import ensure_env  # noqa: E402

ensure_env()

from api_client import QwenClient  # noqa: E402
from retrieval_adaptor.recall_review import output_path_for_draft, run_review  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="召回审核：从论文原文重新提取支撑观点句的证据")
    parser.add_argument("--paper", action="append", default=[], help="论文编号，可重复，如 P006")
    parser.add_argument("--papers", nargs="+", default=[], help="多个论文编号")
    parser.add_argument("--limit", type=int, default=None, help="每篇初稿本次最多新处理的样本数")
    parser.add_argument("--force", action="store_true", help="忽略已有成功结果，全部重跑")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit 不能为负数")

    paper_ids: list[str] = []
    for raw in list(args.paper) + list(args.papers):
        paper_id = str(raw).strip()
        if paper_id and paper_id not in paper_ids:
            paper_ids.append(paper_id)
    if not paper_ids:
        parser.error("请用 --paper 或 --papers 指定论文")

    client = QwenClient(verbose=False)
    failed = False
    try:
        for paper_id in paper_ids:
            try:
                drafts = _drafts_for_paper(_PROJECT_ROOT, paper_id)
            except FileNotFoundError as exc:
                print(str(exc), file=sys.stderr)
                failed = True
                continue
            sentence_path = _PROJECT_ROOT / "data" / "annotations" / paper_id / (
                "%s_sentences.csv" % paper_id
            )
            if not sentence_path.is_file():
                print("找不到句表: %s" % sentence_path, file=sys.stderr)
                failed = True
                continue
            pdf_path = _PROJECT_ROOT / "data" / "papers" / ("%s.pdf" % paper_id)
            for draft_path in drafts:
                document = run_review(
                    client,
                    draft_path,
                    sentence_path,
                    output_path_for_draft(draft_path),
                    pdf_path=pdf_path,
                    project_root=_PROJECT_ROOT,
                    force=args.force,
                    limit=args.limit,
                )
                errors = sum(1 for sample in document["samples"] if sample.get("error"))
                print(
                    "%s %s  %d 条 -> %s"
                    % (
                        document["paper_id"] or paper_id,
                        document["article_id"],
                        document["sample_count"],
                        output_path_for_draft(draft_path).relative_to(_PROJECT_ROOT).as_posix(),
                    )
                )
                if errors:
                    print("  其中 %d 条调用失败，已记入说明" % errors, file=sys.stderr)
                    failed = True
    finally:
        client.close()
    return 1 if failed else 0


def _drafts_for_paper(root: Path, paper_id: str) -> list[Path]:
    folder = root / "data" / "annotations" / paper_id
    drafts = sorted(folder.glob("%s_*_annotation_draft.json" % paper_id))
    if not drafts:
        raise FileNotFoundError("找不到召回初稿: %s" % folder)
    return drafts


if __name__ == "__main__":
    raise SystemExit(main())
