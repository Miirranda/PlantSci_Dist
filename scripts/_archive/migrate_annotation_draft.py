#!/usr/bin/env python3
"""将旧版 P001_claims.json 升级为 annotation_draft 结构。

保留全部解释性字段（analysis 等）与原有 labels；仅增补：
  - gold.sentence_ids / gold 分类位
  - system_suggestion（可空）
  - 文档级 index_version / status

不与流水线 LLM 抽句合并。

Usage:
    python scripts/migrate_annotation_draft.py \\
        --input data/annotations/P001_claims.json \\
        --output data/annotations/P001_A001_annotation_draft.json

    # 可选：用索引把旧 gold 文本解析成 sentence_ids
    python scripts/migrate_annotation_draft.py ... --index-dir arag-main/data/index
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def _try_load_index(index_dir: Path | None):
    if index_dir is None:
        return None
    index_dir = Path(index_dir)
    if not (index_dir / "sentence_index.pkl").exists():
        print("  [warn] 索引不存在，跳过 sentence_id 解析: %s" % index_dir)
        return None
    arag_root = _PROJECT_ROOT / "arag-main"
    if str(arag_root) not in sys.path:
        sys.path.insert(0, str(arag_root))
    from retrieval_adaptor.index_store import IndexStore

    return IndexStore(index_dir)


def _resolve_ids(store, texts: list[str]) -> list[int]:
    if store is None:
        return []
    ids: list[int] = []
    seen: set[int] = set()
    for text in texts:
        sid = store.resolve_sentence_id(text)
        if sid >= 0 and sid not in seen:
            seen.add(sid)
            ids.append(sid)
    return ids


def migrate_claim(claim: dict[str, Any], store) -> dict[str, Any]:
    """单条旧 claim → draft sample；保留原字段并增补 gold / system_suggestion。"""
    labels = dict(claim.get("labels") or {})
    gold_texts = list(claim.get("gold_evidence_sentences") or [])
    resolved = _resolve_ids(store, [str(t) for t in gold_texts])

    sample = dict(claim)  # 浅拷贝，保留 analysis / annotation_meta / 全部原键
    sample["sample_id"] = str(
        claim.get("id") or claim.get("sample_id") or claim.get("claim_id") or ""
    )
    sample["claim_zh"] = str(
        claim.get("claim_text") or claim.get("claim_zh") or ""
    )

    sample["system_suggestion"] = {
        "sentence_ids": [],
        "primary_type": None,
        "secondary_types": [],
        "evidence_level": None,
    }
    sample["gold"] = {
        "sentence_ids": resolved,
        "sentences": gold_texts,  # 文本备份，便于对照句表人工补 id
        "primary_type": labels.get("primary_hallucination_type"),
        "secondary_types": list(labels.get("secondary_hallucination_types") or []),
        "evidence_level": labels.get("evidence_level"),
        "is_accurate": labels.get("is_accurate"),
        "severity": labels.get("severity"),
    }
    return sample


def migrate_document(
    data: dict[str, Any],
    store=None,
    *,
    index_version: str = "",
) -> dict[str, Any]:
    claims = list(data.get("claims") or [])
    samples = [migrate_claim(c, store) for c in claims if isinstance(c, dict)]

    article_ids = {
        str(s.get("article_id") or "") for s in samples if s.get("article_id")
    }
    article_id = sorted(article_ids)[0] if len(article_ids) == 1 else (
        sorted(article_ids)[0] if article_ids else ""
    )
    source_types = {
        str(s.get("article_source_type") or "")
        for s in samples
        if s.get("article_source_type")
    }
    source_type = sorted(source_types)[0] if len(source_types) == 1 else (
        sorted(source_types)[0] if source_types else ""
    )

    if store is not None and not index_version:
        index_version = str(getattr(store, "built_at", "") or "")

    draft = {
        "schema_version": "1.0",
        "status": "draft",
        "paper_id": str(data.get("paper_id") or ""),
        "article_id": article_id,
        "article_source_type": source_type,
        "paper_title": data.get("paper_title", ""),
        "paper_journal": data.get("paper_journal", ""),
        "paper_year": data.get("paper_year"),
        "paper_doi": data.get("paper_doi", ""),
        "index_version": index_version,
        "generated_date": data.get("generated_date", ""),
        "migrated_at": datetime.now().isoformat(timespec="seconds"),
        "_description": (
            "标注初稿：人工在 samples[].gold 与 annotation_meta 中审核；"
            "保留 analysis 等解释性字段；评测请导出 benchmark.json。"
        ),
        "samples": samples,
    }
    # 保留旧顶层未知键（除 claims）
    for key, value in data.items():
        if key in draft or key == "claims":
            continue
        if key.startswith("_") and key not in ("_description",):
            draft[key] = value
    return draft


def main() -> int:
    parser = argparse.ArgumentParser(description="旧标注 → annotation_draft")
    parser.add_argument(
        "--input",
        default="data/annotations/P001_claims.json",
        help="旧版 claims 标注 JSON",
    )
    parser.add_argument(
        "--output",
        default="data/annotations/P001_A001_annotation_draft.json",
        help="草稿输出路径",
    )
    parser.add_argument(
        "--index-dir",
        default="",
        help="可选：arag 索引目录，用于把 gold 文本解析为 sentence_ids",
    )
    parser.add_argument(
        "--index-version",
        default="",
        help="写入 draft 的 index_version（默认可取索引 built_at）",
    )
    args = parser.parse_args()

    inp = _resolve(args.input)
    out = _resolve(args.output)
    if not inp.is_file():
        print("输入不存在: %s" % inp)
        return 1

    data = json.loads(inp.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print("输入必须是 JSON 对象")
        return 1

    index_dir = _resolve(args.index_dir) if args.index_dir else None
    store = _try_load_index(index_dir)
    draft = migrate_document(
        data,
        store,
        index_version=str(args.index_version or ""),
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    n = len(draft["samples"])
    with_ids = sum(1 for s in draft["samples"] if s.get("gold", {}).get("sentence_ids"))
    print("已写入草稿: %s" % out)
    print("  samples: %d  | 已解析到 sentence_ids 的条数: %d" % (n, with_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
