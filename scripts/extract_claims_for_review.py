#!/usr/bin/env python3
"""按当前抽取规则抽公众号观点句，写出审 A 文件。

审 A 的 review_decision 默认 keep；人工改 drop/merge 后，用
scripts/export_locked_claims.py 导出锁定名单。

Usage:
  python scripts/extract_claims_for_review.py
  python scripts/extract_claims_for_review.py --skip-llm
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_ARAG = _PROJECT_ROOT / "arag-main"
for path in (_ARAG, _ARAG / "src", _PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from retrieval_adaptor.claim_extractor import (  # noqa: E402
    extract_claims_from_article,
    save_claims_for_review,
    save_claims_json,
    save_claims_jsonl,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="抽取观点句供审 A")
    parser.add_argument(
        "--article",
        default=str(
            _PROJECT_ROOT
            / "data"
            / "articles"
            / "high_quality"
            / "P001_A001_黄瓜下位子房的发育机制.md"
        ),
    )
    parser.add_argument("--paper-id", default="P001")
    parser.add_argument("--article-id", default="A001")
    parser.add_argument(
        "--output-dir",
        default="",
        help="默认 data/annotations/<paper_id>/",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="只跑规则筛除（无 API 时用）",
    )
    args = parser.parse_args()

    article = Path(args.article)
    out_dir = Path(args.output_dir) if args.output_dir else (
        _PROJECT_ROOT / "data" / "annotations" / args.paper_id
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    claims = extract_claims_from_article(article, skip_llm_verify=args.skip_llm)
    stem = "%s_%s_claims_for_review" % (args.paper_id, args.article_id)
    review_json = out_dir / ("%s.json" % stem)
    save_claims_for_review(
        claims,
        review_json,
        paper_id=args.paper_id,
        article_id=args.article_id,
    )
    # 抽取后的 claims.* 为「默认全 keep」的临时稿；人工改审 A 后请再跑
    # scripts/export_locked_claims.py 覆盖为真正锁定名单。
    save_claims_jsonl(claims, out_dir / ("%s_%s_claims.jsonl" % (args.paper_id, args.article_id)))
    save_claims_json(claims, out_dir / ("%s_%s_claims.json" % (args.paper_id, args.article_id)))
    print("审 A JSON: %s" % review_json)
    print("临时 claims（默认 keep）: %d 条" % len(claims))
    print("人工改 drop/merge 后请运行: python scripts/export_locked_claims.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
