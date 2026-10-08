#!/usr/bin/env python3
"""统一构建 RAG / 召回审核草稿：锁定观点句 → 检索 → 清洗 pairs → 生成草稿 → 翻译。

一条命令跑完全程（不含启动 review_server）：

    python scripts/build_rag_review.py --paper P004 --article A001

分步停在某一环（--until retrieve|clean|draft|translate，默认 translate）：

    python scripts/build_rag_review.py --paper P004 --article A001 --until clean
    python scripts/build_rag_review.py --paper P004 --article A001 --skip-analysis

等价于：
    1. cd arag-main && python batch_retrieval.py --paper-id P004 --claims <锁定句> --output ...
    2. python arag-main/clean_retrieval_output.py <ts>/evidences.jsonl -o outputs/P004/A001/claim_evidence_pairs.jsonl
    3. python scripts/generate_draft_from_pairs.py --paper P004 --article A001 --source-type high_quality
    4. python scripts/translate_text_fields.py data/annotations/P004/P004_A001_annotation_draft.json

断点可重入：
    - 检索已跑过（outputs/<P>/<A>/evidences.jsonl 已存在）会 --resume 跳过已完成观点句；
    - 草稿 / 翻译各自按 sample_id / 原文去重跳过，重复运行安全。

前置：论文已建库（python scripts/ensure_index.py --paper-id P004），
观点句已审并导出终稿（scripts/extract_claims_for_review.py → 人工改 → scripts/export_locked_claims.py）。
"""

from __future__ import annotations

import argparse
import os
import shutil
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
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from hallu.config import ARAG_ROOT, ensure_env  # noqa: E402

ensure_env()

_BATCH_RETRIEVAL = ARAG_ROOT / "batch_retrieval.py"
_CLEAN = ARAG_ROOT / "clean_retrieval_output.py"
_GEN_DRAFT = _SCRIPT_DIR / "generate_draft_from_pairs.py"
_TRANSLATE = _SCRIPT_DIR / "translate_text_fields.py"


# ---------------------------------------------------------------------------
# 路径推导
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return s.strip().upper()


def locked_claims_path(paper: str, article: str) -> Path:
    d = _PROJECT_ROOT / "data" / "annotations" / paper
    jl = d / ("%s_%s_claims.jsonl" % (paper, article))
    if jl.exists():
        return jl
    return d / ("%s_%s_claims.json" % (paper, article))


def output_dir(paper: str, article: str) -> Path:
    return _PROJECT_ROOT / "outputs" / paper / article


def pairs_path(paper: str, article: str) -> Path:
    return output_dir(paper, article) / "claim_evidence_pairs.jsonl"


def evidences_path(paper: str, article: str) -> Path:
    return output_dir(paper, article) / "evidences.jsonl"


def draft_path(paper: str, article: str) -> Path:
    return (
        _PROJECT_ROOT
        / "data"
        / "annotations"
        / paper
        / ("%s_%s_annotation_draft.json" % (paper, article))
    )


def index_ready(paper: str) -> bool:
    meta = _PROJECT_ROOT / "data" / "index" / paper / "index_meta.json"
    if not meta.exists():
        return False
    try:
        import json

        payload = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(payload.get("embedded")) or (
        _PROJECT_ROOT / "data" / "index" / paper / "sentence_index.pkl"
    ).exists()


# ---------------------------------------------------------------------------
# 子进程辅助
# ---------------------------------------------------------------------------

def _arag_env() -> dict[str, str]:
    """PYTHONPATH = arag-main/src + arag-main，并继承已加载 .env 的 os.environ。"""
    env = dict(os.environ)
    parts = [str(ARAG_ROOT / "src"), str(ARAG_ROOT)]
    old = env.get("PYTHONPATH", "")
    if old:
        parts.append(old)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def run_cmd(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("\n$ " + " ".join(map(str, cmd)))
    print("-" * 60)
    rc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env).returncode
    if rc != 0:
        raise SystemExit("命令失败 (exit=%d): %s" % (rc, " ".join(map(str, cmd))))


_KNOWN_RETRIEVAL_PYTHONS = (
    "D:/Anaconda/envs/Hallucination_detection/python.exe",
    "C:/python3/python.exe",
)


def _can_load_index_pickle(python: str, paper: str) -> bool:
    """该 Python 能否反序列化 data/index/<paper>/sentence_index.pkl。"""
    pkl = _PROJECT_ROOT / "data" / "index" / paper / "sentence_index.pkl"
    if not pkl.exists():
        return False
    code = "import pickle,sys; pickle.load(open(sys.argv[1], 'rb'))"
    r = subprocess.run(
        [python, "-c", code, str(pkl)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return r.returncode == 0


def find_retrieval_python(paper: str) -> str:
    """返回能加载该篇索引 pkl 的 Python（索引由 numpy>=2 序列化，numpy 1.x 反序列化会崩）。

    优先当前解释器，否则回退到已知项目环境（Hallucination_detection / C:\\python3）。
    """
    if _can_load_index_pickle(sys.executable, paper):
        return sys.executable
    for cand in _KNOWN_RETRIEVAL_PYTHONS:
        if os.path.exists(cand) and _can_load_index_pickle(cand, paper):
            return cand
    return sys.executable


# ---------------------------------------------------------------------------
# 四步
# ---------------------------------------------------------------------------

def step_retrieve(paper: str, article: str, locked: Path, out_dir: Path, *, workers: int, limit: int | None) -> Path:
    """检索锁定观点句 → 返回用于清洗的 evidences.jsonl 路径。"""
    top = evidences_path(paper, article)
    run_parent = out_dir / "_arag_run"
    run_parent.mkdir(parents=True, exist_ok=True)

    retrieval_py = find_retrieval_python(paper)
    if retrieval_py != sys.executable:
        print("\n  [env] 当前解释器无法加载索引 pkl（numpy 版本不匹配），检索改用: %s" % retrieval_py)

    cmd = [
        retrieval_py,
        str(_BATCH_RETRIEVAL),
        "--paper-id", paper,
        "--claims", str(locked),
        "--workers", str(workers),
    ]
    if limit:
        cmd += ["--limit", str(limit)]

    resumed = top.exists() and top.stat().st_size > 0
    if resumed:
        cmd += ["--resume", str(top)]
    else:
        cmd += ["--output", str(run_parent)]

    run_cmd(cmd, cwd=ARAG_ROOT, env=_arag_env())

    if not resumed:
        candidates = sorted(
            run_parent.glob("*/evidences.jsonl"),
            key=lambda p: p.stat().st_mtime,
        )
        if not candidates:
            raise SystemExit("检索未产出 evidences.jsonl，检查 %s" % run_parent)
        shutil.copy2(candidates[-1], top)
        print("  已复制: %s -> %s" % (candidates[-1], top))
    return top


def step_clean(evidences: Path, pairs: Path) -> None:
    run_cmd(
        [
            sys.executable,
            str(_CLEAN),
            str(evidences),
            "-o",
            str(pairs),
        ]
    )


def step_draft(
    paper: str,
    article: str,
    source_type: str,
    *,
    refresh: bool = False,
    skip_analysis: bool = False,
) -> None:
    cmd = [
        sys.executable,
        str(_GEN_DRAFT),
        "--paper", paper,
        "--article", article,
        "--source-type", source_type,
    ]
    if refresh:
        cmd.append("--overwrite-unverified")
    if skip_analysis:
        cmd.append("--skip-analysis")
    run_cmd(cmd)


def step_translate(draft: Path) -> None:
    run_cmd([sys.executable, str(_TRANSLATE), str(draft)])


_STEPS = ("retrieve", "clean", "draft", "translate")


def _step_rank(name: str) -> int:
    return _STEPS.index(name)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="构建 RAG / 召回审核草稿（检索 → 清洗 pairs → 生成草稿 → 翻译，不含启动服务器）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  只检索+清洗:      python scripts/build_rag_review.py --paper P004 --article A001 --until clean\n"
            "  召回草稿(无失真): python scripts/build_rag_review.py --paper P004 --article A001 --skip-analysis\n"
            "  从已有检索续跑:   python scripts/build_rag_review.py --paper P004 --article A001 --skip-retrieve --skip-analysis\n"
        ),
    )
    parser.add_argument("--paper", required=True, help="如 P004")
    parser.add_argument("--article", required=True, help="如 A001")
    parser.add_argument("--source-type", default="high_quality", help="写入每条 sample")
    parser.add_argument("--workers", type=int, default=1, help="检索并发数（默认 1）")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条（调试/冒烟）")
    parser.add_argument(
        "--until",
        choices=_STEPS,
        default="translate",
        help="做到哪一步停：retrieve / clean / draft / translate（默认跑完）",
    )
    parser.add_argument(
        "--skip-retrieve",
        action="store_true",
        help="跳过检索，复用已有 evidences.jsonl / _arag_run",
    )
    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="草稿只打包 top-10，不调模型写失真标签（召回审核用）",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="重生成 human_verified=false 的草稿样本（重跑检索后用它把 top-10 刷新到最新结果）",
    )
    args = parser.parse_args()

    paper = _norm(args.paper)
    article = _norm(args.article)
    locked = locked_claims_path(paper, article)
    out_dir = output_dir(paper, article)
    pairs = pairs_path(paper, article)
    draft = draft_path(paper, article)
    stop = _step_rank(args.until)

    if not locked.exists():
        raise SystemExit(
            "找不到锁定观点句: %s\n"
            "请先运行:\n"
            "  python scripts/extract_claims_for_review.py --article data/articles/high_quality/%s_%s.md --paper-id %s --article-id %s\n"
            "人工在 claims_for_review.json 里标 drop/merge 后:\n"
            "  python scripts/export_locked_claims.py --review data/annotations/%s/%s_%s_claims_for_review.json"
            % (locked, paper, article, paper, article, paper, paper, article)
        )

    if not args.skip_retrieve and not index_ready(paper):
        raise SystemExit(
            "论文索引未就绪: data/index/%s/\n"
            "请先运行: python scripts/ensure_index.py --paper-id %s" % (paper, paper)
        )

    print("=" * 60)
    print("  RAG / 召回审核草稿构建")
    print("=" * 60)
    print("  论文/文章 : %s / %s" % (paper, article))
    print("  锁定观点句: %s" % locked)
    print("  输出目录  : %s" % out_dir)
    print("  做到      : %s" % args.until)
    if args.skip_analysis:
        print("  草稿模式  : 只召回（skip-analysis）")
    print("=" * 60)

    evidences = None
    if stop >= _step_rank("retrieve"):
        if args.skip_retrieve:
            top = evidences_path(paper, article)
            if not (top.exists() and top.stat().st_size > 0):
                run_parent = out_dir / "_arag_run"
                cands = sorted(
                    run_parent.glob("*/evidences.jsonl"),
                    key=lambda p: p.stat().st_mtime,
                )
                if not cands:
                    raise SystemExit("找不到已有检索结果（evidences.jsonl / _arag_run 下都没有）")
                top = cands[-1]
            print("\n[跳过检索] 复用: %s" % top)
            evidences = top
        else:
            evidences = step_retrieve(
                paper, article, locked, out_dir, workers=args.workers, limit=args.limit
            )

    if stop < _step_rank("clean"):
        print("\n" + "=" * 60)
        print("  已停在 retrieve")
        print("=" * 60)
        print("  检索: %s" % evidences)
        print(
            "  下一步: python scripts/build_rag_review.py --paper %s --article %s --until clean --skip-retrieve"
            % (paper, article)
        )
        return 0

    step_clean(evidences, pairs)
    if not pairs.exists():
        raise SystemExit("清洗未产出 pairs: %s" % pairs)

    if stop < _step_rank("draft"):
        print("\n" + "=" * 60)
        print("  已停在 clean（纯 RAG 对照表，不含失真分析）")
        print("=" * 60)
        print("  pairs: %s" % pairs)
        print(
            "  下一步（召回草稿）: python scripts/build_rag_review.py --paper %s --article %s --skip-retrieve --skip-analysis"
            % (paper, article)
        )
        return 0

    step_draft(
        paper,
        article,
        args.source_type,
        refresh=args.refresh,
        skip_analysis=args.skip_analysis,
    )
    if not draft.exists():
        raise SystemExit("未产出草稿: %s" % draft)

    if stop < _step_rank("translate"):
        print("\n" + "=" * 60)
        print("  已停在 draft")
        print("=" * 60)
        print("  草稿: %s" % draft)
        print(
            "  下一步: python scripts/build_rag_review.py --paper %s --article %s --skip-retrieve --until translate%s"
            % (paper, article, " --skip-analysis" if args.skip_analysis else "")
        )
        return 0

    step_translate(draft)

    print("\n" + "=" * 60)
    if args.skip_analysis:
        print("  完成：召回审核草稿已就绪（top-10 已翻译，未写失真分析）")
    else:
        print("  完成：RAG 审核草稿已就绪（含 top-10 中文翻译）")
    print("=" * 60)
    print("  草稿: %s" % draft)
    print("  下一步（自己启动）: python scripts/review_server.py --draft %s" % draft)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
