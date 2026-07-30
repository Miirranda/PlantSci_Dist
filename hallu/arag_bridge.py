"""调用独立 arag-main 模块：LLM 抽句 + 跨语言检索。

hallu 不内嵌提取/检索实现；本模块只负责子进程调用与结果落盘。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from hallu.config import ARAG_ROOT


def _arag_env() -> dict[str, str]:
    """构造含 arag-main/src 的子进程环境。"""
    env = dict(os.environ)
    parts = [str(ARAG_ROOT / "src"), str(ARAG_ROOT)]
    old = env.get("PYTHONPATH", "")
    if old:
        parts.append(old)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def run_arag_article_pipeline(
    article_path: Path,
    output_dir: Path,
    *,
    workers: int = 1,
    limit: int | None = None,
    skip_extract: bool = False,
    skip_retrieval: bool = False,
    resume: bool = True,
    verbose: bool = False,
) -> tuple[Path, Path]:
    """文章 → LLM 观点句 → 检索证据。

    Returns:
        (claims_jsonl, evidences_jsonl)
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    claims_jsonl = output_dir / "claims.jsonl"
    claims_json = output_dir / "claims.json"
    evidences_path = output_dir / "evidences.jsonl"

    if skip_extract and not claims_jsonl.exists() and not claims_json.exists():
        raise FileNotFoundError(
            "skip_extract 需要已有 claims.jsonl，未找到: %s" % claims_jsonl
        )

    if skip_retrieval:
        if not evidences_path.exists():
            raise FileNotFoundError("skip_retrieval 需要已有 evidences.jsonl")
        if not claims_jsonl.exists() and claims_json.exists():
            # 从 json 补 jsonl
            _json_to_jsonl(claims_json, claims_jsonl)
        return claims_jsonl, evidences_path

    batch_script = ARAG_ROOT / "batch_retrieval.py"
    if not batch_script.exists():
        raise FileNotFoundError("找不到 arag 入口: %s" % batch_script)

    cmd = [
        sys.executable,
        str(batch_script),
        "--workers",
        str(workers),
    ]
    if limit is not None:
        cmd.extend(["--limit", str(limit)])
    if verbose:
        cmd.append("--verbose")

    if skip_extract:
        if not claims_jsonl.exists() and claims_json.exists():
            _json_to_jsonl(claims_json, claims_jsonl)
        cmd.extend(["--claims", str(claims_jsonl)])
    else:
        cmd.extend(
            [
                "--article",
                str(article_path),
                "--claims-out",
                str(claims_jsonl),
            ]
        )

    if resume and evidences_path.exists() and evidences_path.stat().st_size > 0:
        cmd.extend(["--resume", str(evidences_path)])
    else:
        run_parent = output_dir / "_arag_run"
        run_parent.mkdir(parents=True, exist_ok=True)
        cmd.extend(["--output", str(run_parent)])

    env = _arag_env()
    print("  [arag] 工作目录: %s" % ARAG_ROOT)
    print("  [arag] 命令: %s" % " ".join(cmd))

    proc = subprocess.run(cmd, cwd=str(ARAG_ROOT), env=env, check=False)
    if proc.returncode != 0:
        raise RuntimeError("arag 流水线失败，exit code=%s" % proc.returncode)

    if not (resume and evidences_path.exists() and evidences_path.stat().st_size > 0):
        run_parent = output_dir / "_arag_run"
        candidates = sorted(
            run_parent.glob("*/evidences.jsonl"),
            key=lambda p: p.stat().st_mtime,
        )
        if not candidates:
            if evidences_path.exists():
                return claims_jsonl, evidences_path
            raise FileNotFoundError("arag 未产出 evidences.jsonl，检查 %s" % run_parent)
        shutil.copy2(candidates[-1], evidences_path)
        print("  [arag] 已复制: %s → %s" % (candidates[-1], evidences_path))

    if not claims_jsonl.exists() and claims_json.exists():
        _json_to_jsonl(claims_json, claims_jsonl)

    return claims_jsonl, evidences_path


def run_arag_retrieval(
    claims_jsonl: Path,
    output_evidences: Path,
    *,
    workers: int = 1,
    resume: bool = True,
    verbose: bool = False,
) -> Path:
    """仅检索：已有 claims.jsonl → evidences.jsonl。"""
    output_dir = Path(output_evidences).parent
    _, evidences = run_arag_article_pipeline(
        article_path=Path("."),  # unused when skip_extract
        output_dir=output_dir,
        workers=workers,
        skip_extract=True,
        resume=resume,
        verbose=verbose,
    )
    if Path(evidences) != Path(output_evidences):
        shutil.copy2(evidences, output_evidences)
        return Path(output_evidences)
    return Path(evidences)


def clean_arag_output(evidences_path: Path, pairs_path: Path) -> Path:
    """调用 ``clean_retrieval_output.py`` 生成精简对照表。"""
    clean_script = ARAG_ROOT / "clean_retrieval_output.py"
    if not clean_script.exists():
        raise FileNotFoundError("找不到清洗脚本: %s" % clean_script)

    pairs_path = Path(pairs_path)
    pairs_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(clean_script),
        str(evidences_path),
        "-o",
        str(pairs_path),
    ]
    print("  [arag] 清洗: %s" % " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ARAG_ROOT), env=_arag_env(), check=False)
    if proc.returncode != 0:
        raise RuntimeError("arag 清洗失败，exit code=%s" % proc.returncode)
    return pairs_path


def _json_to_jsonl(claims_json: Path, claims_jsonl: Path) -> None:
    import json

    data = json.loads(claims_json.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("claims", [])
    with claims_jsonl.open("w", encoding="utf-8") as handle:
        for row in data:
            handle.write(
                json.dumps(
                    {
                        "claim_id": row.get("id") or row.get("claim_id"),
                        "claim_zh": row.get("claim_text") or row.get("claim_zh") or "",
                        "context_before": row.get("context_before", ""),
                        "context_after": row.get("context_after", ""),
                        "section": row.get("section", ""),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
