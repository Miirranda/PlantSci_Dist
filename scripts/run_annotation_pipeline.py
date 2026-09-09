#!/usr/bin/env python3
"""标注全流程编排：论文+公众号 → 草稿 → 审A → 二次生成 → 审B → 合并。

薄包装：只推导路径 + 顺序调用现有脚本（subprocess），不重写业务逻辑。
断点可重入：每个子命令跑完自动段即退出，人工审核另起 review_server（或 --open-review 阻塞启动）。

用法:
  python scripts/run_annotation_pipeline.py stage1 --paper P002 --article A001
  python scripts/run_annotation_pipeline.py stage1 --paper P002 --article A001 --retrieve --article-md data/articles/high_quality/P002_A001.md
  python scripts/run_annotation_pipeline.py stage2 --paper P002 --article A001
  python scripts/run_annotation_pipeline.py merge  --paper P002 --article A001 --export
"""

from __future__ import annotations

import argparse
import subprocess
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

# 各阶段脚本（都在 scripts/ 下，用 sys.executable 而非裸 python）
_ENSURE_INDEX = _SCRIPT_DIR / "ensure_index.py"
_RUN = _SCRIPT_DIR / "run.py"
_GEN_DRAFT = _SCRIPT_DIR / "generate_draft_from_pairs.py"
_REGENERATE = _SCRIPT_DIR / "regenerate_analysis.py"
_MERGE = _SCRIPT_DIR / "merge_reviews.py"
_EXPORT = _SCRIPT_DIR / "export_benchmark.py"
_REVIEW_SERVER = _SCRIPT_DIR / "review_server.py"


# ---------------------------------------------------------------------------
# 路径推导（与各脚本默认约定一致）
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    return s.strip().upper()


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def _rel(p: Path) -> Path:
    try:
        return p.relative_to(_PROJECT_ROOT)
    except ValueError:
        return p


def pairs_path(paper: str, article: str) -> Path:
    return _PROJECT_ROOT / "outputs" / paper / article / "claim_evidence_pairs.jsonl"


def draft_path(paper: str, article: str) -> Path:
    return (
        _PROJECT_ROOT
        / "data"
        / "annotations"
        / paper
        / ("%s_%s_annotation_draft.json" % (paper, article))
    )


def distortion_path(paper: str, article: str) -> Path:
    return (
        _PROJECT_ROOT
        / "data"
        / "annotations"
        / paper
        / ("%s_%s_distortion_review.json" % (paper, article))
    )


def benchmark_path(paper: str, article: str) -> Path:
    return (
        _PROJECT_ROOT
        / "data"
        / "annotations"
        / paper
        / ("%s_%s_benchmark.json" % (paper, article))
    )


def article_md_path(paper: str, article: str, source_type: str) -> Path:
    return _PROJECT_ROOT / "data" / "articles" / source_type / ("%s_%s.md" % (paper, article))


# ---------------------------------------------------------------------------
# 调用辅助
# ---------------------------------------------------------------------------


def run_script(script: Path, args: list[str]) -> int:
    print("\n$ python %s %s" % (_rel(script), " ".join(args)))
    print("-" * 60)
    return subprocess.run([sys.executable, str(script), *args]).returncode


def _open_review(draft: Path) -> None:
    print("\n启动审核服务器（完成审核后 Ctrl+C 返回）…")
    try:
        subprocess.run([sys.executable, str(_REVIEW_SERVER), "--draft", str(draft)])
    except KeyboardInterrupt:
        print("\n审核服务器已停止。")


def _banner(title: str) -> None:
    print("\n" + "=" * 60)
    print("  " + title)
    print("=" * 60)


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------


def cmd_stage1(args: argparse.Namespace) -> int:
    """阶段一：检索（可选）→ 生成草稿 → 审A。"""
    paper = _norm(args.paper)
    article = _norm(args.article)
    source_type = (args.source_type or "high_quality").strip()
    pairs = pairs_path(paper, article)
    draft = draft_path(paper, article)

    if args.retrieve:
        md = (
            _resolve(args.article_md)
            if args.article_md
            else article_md_path(paper, article, source_type)
        )
        if not md.exists():
            print("找不到公众号文章 md: %s（用 --article-md 指定）" % md)
            return 1
        rc = run_script(_ENSURE_INDEX, ["--paper-id", paper])
        if rc != 0:
            return rc
        rc = run_script(_RUN, ["--article", str(md), "--paper-id", paper])
        if rc != 0:
            return rc
    elif not pairs.exists():
        print("找不到 pairs: %s" % pairs)
        print("请先产出 pairs（加 --retrieve 重跑检索，或按现有分布执行方式生成）。")
        return 1

    # 固定 --output 到规范名：--limit 只切条数（可续跑补齐），不改变文件名，
    # 保证 stage2/merge 按 draft_path() 找得到同一份文件。
    gen_args = [
        "--paper", paper,
        "--article", article,
        "--source-type", source_type,
        "--output", str(draft),
    ]
    if args.limit is not None:
        gen_args += ["--limit", str(args.limit)]
    if args.batch_size > 0:
        gen_args += ["--batch-size", str(args.batch_size)]
    if args.dry_run:
        gen_args += ["--dry-run"]
    rc = run_script(_GEN_DRAFT, gen_args)
    if rc != 0:
        return rc

    if args.dry_run:
        print("\n[dry-run] 未调 API、未落盘。去掉 --dry-run 正式生成。")
        return 0

    _banner("阶段一完成：草稿已生成，进入审A（召回审核）")
    print("  文件: %s" % _rel(draft))
    print("  命令: python scripts/review_server.py --draft %s" % _rel(draft))
    print("  审A 全部勾选「召回审核已完成」后，再跑 stage2。")
    if args.open_review:
        _open_review(draft)
    return 0


def cmd_stage2(args: argparse.Namespace) -> int:
    """阶段二：二次生成失真分析 → 审B。"""
    paper = _norm(args.paper)
    article = _norm(args.article)
    draft = draft_path(paper, article)
    if not draft.exists():
        print("找不到文件1（召回草稿）: %s" % draft)
        print("请先运行 stage1 生成草稿并完成审A。")
        return 1

    regen_args = ["--draft", str(draft)]
    if args.reviewer:
        regen_args += ["--reviewer", args.reviewer]
    if args.force:
        regen_args += ["--force"]
    rc = run_script(_REGENERATE, regen_args)
    if rc != 0:
        return rc

    distortion = distortion_path(paper, article)
    if not distortion.exists():
        print("\n未产出文件2：可能还没有 recall_reviewed=true 的记录，请先完成审A。")
        return 0

    _banner("阶段二：文件2 已生成，进入审B（失真审核）")
    print("  文件: %s" % _rel(distortion))
    print("  命令: python scripts/review_server.py --draft %s" % _rel(distortion))
    print(
        "  审B 完成后运行: python scripts/run_annotation_pipeline.py merge "
        "--paper %s --article %s --export" % (paper, article)
    )
    if args.open_review:
        _open_review(distortion)
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    """收尾：合并 file2 → file1，可选导出 benchmark。"""
    paper = _norm(args.paper)
    article = _norm(args.article)
    draft = draft_path(paper, article)
    if not draft.exists():
        print("找不到文件1: %s" % draft)
        return 1

    rc = run_script(_MERGE, ["--draft", str(draft)])
    if rc != 0:
        return rc

    if args.export:
        rc = run_script(
            _EXPORT,
            ["--draft", str(draft), "--output", str(benchmark_path(paper, article))],
        )
        if rc != 0:
            return rc
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="标注全流程编排（论文+公众号 → 草稿 → 审A → 审B → 合并）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp1 = sub.add_parser("stage1", help="阶段一：检索(可选) → 草稿 → 审A")
    sp1.add_argument("--paper", required=True, help="如 P002")
    sp1.add_argument("--article", required=True, help="如 A001")
    sp1.add_argument("--source-type", default="high_quality", help="写入每条 sample")
    sp1.add_argument("--limit", type=int, default=None, help="只生成前 N 条（冒烟用；同一文件，之后可不带 --limit 续跑补齐）")
    sp1.add_argument("--batch-size", type=int, default=0, help="默认用脚本自身默认 3")
    sp1.add_argument("--retrieve", action="store_true", help="重跑检索（ensure_index + run.py）")
    sp1.add_argument("--article-md", default="", help="公众号文章 md（--retrieve 时用，缺省按路径推导）")
    sp1.add_argument("--dry-run", action="store_true", help="透传给草稿脚本，只打印批不调 API")
    sp1.add_argument("--open-review", action="store_true", help="草稿生成后阻塞启动审A 服务器")
    sp1.set_defaults(func=cmd_stage1)

    sp2 = sub.add_parser("stage2", help="阶段二：二次生成失真分析 → 审B")
    sp2.add_argument("--paper", required=True)
    sp2.add_argument("--article", required=True)
    sp2.add_argument("--reviewer", default="", help="只处理指定审核人（缺省全部）")
    sp2.add_argument("--force", action="store_true", help="强制重生成全部失真分析")
    sp2.add_argument("--open-review", action="store_true", help="文件2 生成后阻塞启动审B 服务器")
    sp2.set_defaults(func=cmd_stage2)

    sp3 = sub.add_parser("merge", help="收尾：合并 file2→file1，可选导出 benchmark")
    sp3.add_argument("--paper", required=True)
    sp3.add_argument("--article", required=True)
    sp3.add_argument("--export", action="store_true", help="合并后导出 benchmark")
    sp3.set_defaults(func=cmd_merge)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
