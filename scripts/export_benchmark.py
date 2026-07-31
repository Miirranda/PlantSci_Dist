#!/usr/bin/env python3
"""从 annotation_draft 清洗导出评测用 benchmark.json。

规则：
  - 仅保留已人工确认的样本（``human_verified`` 为 true）
    （可用 --include-unverified 导出全部，便于调试）
  - 终稿只保留评测字段；去掉 system_retrieval / analysis
    （可用 --keep-analysis 保留 analysis）

草稿字段与终稿保持同名（gold_retrieval / gold_classification），
同时兼容早期草稿的 gold / labels / annotation_meta 结构。

Usage:
    python scripts/export_benchmark.py \\
        --draft data/annotations/P001/P001_A001_annotation_draft.json \\
        --output data/annotations/P001/P001_A001_benchmark.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent

_DEFAULT_DRAFT = "data/annotations/P001/P001_A001_annotation_draft.json"
_DEFAULT_OUTPUT = "data/annotations/P001/P001_A001_benchmark.json"


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def _is_verified(sample: dict[str, Any]) -> bool:
    if "human_verified" in sample:
        return bool(sample.get("human_verified"))
    meta = sample.get("annotation_meta") or {}
    return bool(meta.get("human_verified"))


def _sentence_ids(raw: Any) -> list[int]:
    ids: list[int] = []
    for item in raw or []:
        try:
            sid = int(item)
        except (TypeError, ValueError):
            continue
        if sid >= 0 and sid not in ids:
            ids.append(sid)
    return ids


def _ids_from_evidences(evidences: Any) -> list[int]:
    ids: list[int] = []
    for item in evidences or []:
        if not isinstance(item, dict):
            continue
        try:
            sid = int(item.get("sentence_id", -1))
        except (TypeError, ValueError):
            continue
        if sid >= 0 and sid not in ids:
            ids.append(sid)
    return ids


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _clean_sample(sample: dict[str, Any], *, keep_analysis: bool) -> dict[str, Any]:
    retrieval = sample.get("gold_retrieval") or {}
    classification = sample.get("gold_classification") or {}
    gold = sample.get("gold") or {}
    labels = sample.get("labels") or {}

    sentence_ids = _sentence_ids(
        _first(retrieval.get("sentence_ids"), gold.get("sentence_ids"))
    )
    if not sentence_ids:
        sentence_ids = _ids_from_evidences(
            _first(retrieval.get("evidences"), gold.get("sentences"))
        )

    evidence_level = _first(
        classification.get("evidence_level"),
        gold.get("evidence_level"),
        labels.get("evidence_level"),
    )
    is_answerable = _first(
        retrieval.get("is_answerable"),
        bool(sentence_ids) or str(evidence_level or "") != "No_Evidence",
    )

    row: dict[str, Any] = {
        "sample_id": str(
            _first(
                sample.get("sample_id"),
                sample.get("id"),
                sample.get("claim_id"),
                "",
            )
        ),
        "paper_id": str(sample.get("paper_id") or ""),
        "article_id": str(sample.get("article_id") or ""),
        "article_source_type": str(sample.get("article_source_type") or ""),
        "claim_zh": str(
            _first(sample.get("claim_zh"), sample.get("claim_text"), "")
        ).strip(),
        "gold_retrieval": {
            "sentence_ids": sentence_ids,
            "is_answerable": bool(is_answerable),
        },
        "gold_classification": {
            "evidence_level": evidence_level,
            "primary_type": _first(
                classification.get("primary_type"),
                gold.get("primary_type"),
                labels.get("primary_hallucination_type"),
            ),
            "secondary_types": list(
                _first(
                    classification.get("secondary_types"),
                    gold.get("secondary_types"),
                    labels.get("secondary_hallucination_types"),
                )
                or []
            ),
            "is_accurate": _first(
                classification.get("is_accurate"),
                gold.get("is_accurate"),
                labels.get("is_accurate"),
            ),
            "severity": _first(
                classification.get("severity"),
                gold.get("severity"),
                labels.get("severity"),
            ),
        },
    }
    if keep_analysis and sample.get("analysis"):
        row["analysis"] = sample["analysis"]
    return row


def export_benchmark(
    draft: dict[str, Any],
    *,
    include_unverified: bool = False,
    keep_analysis: bool = False,
) -> dict[str, Any]:
    samples_in = list(draft.get("samples") or draft.get("claims") or [])
    selected: list[dict[str, Any]] = []
    for sample in samples_in:
        if not isinstance(sample, dict):
            continue
        if not include_unverified and not _is_verified(sample):
            continue
        selected.append(_clean_sample(sample, keep_analysis=keep_analysis))

    return {
        "schema_version": "1.1",
        "status": "benchmark",
        "paper_id": draft.get("paper_id", ""),
        "article_id": draft.get("article_id", ""),
        "article_source_type": draft.get("article_source_type", ""),
        "index_version": draft.get("index_version", ""),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "sample_count": len(selected),
        "samples": selected,
    }


def _pending_review(draft: dict[str, Any]) -> list[str]:
    pending: list[str] = []
    for sample in draft.get("samples") or draft.get("claims") or []:
        if not isinstance(sample, dict) or _is_verified(sample):
            continue
        analysis = sample.get("analysis") or {}
        if analysis.get("needs_manual_review"):
            pending.append(str(sample.get("sample_id") or sample.get("claim_id") or ""))
    return pending


def main() -> int:
    parser = argparse.ArgumentParser(description="annotation_draft → benchmark.json")
    parser.add_argument("--draft", default=_DEFAULT_DRAFT, help="标注初稿路径")
    parser.add_argument("--output", default=_DEFAULT_OUTPUT, help="终稿输出路径")
    parser.add_argument(
        "--include-unverified",
        action="store_true",
        help="包含尚未 human_verified 的样本（调试用）",
    )
    parser.add_argument(
        "--keep-analysis",
        action="store_true",
        help="终稿中保留 analysis 字段",
    )
    args = parser.parse_args()

    draft_path = _resolve(args.draft)
    out_path = _resolve(args.output)
    if not draft_path.is_file():
        print("草稿不存在: %s" % draft_path)
        return 1

    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    bench = export_benchmark(
        draft,
        include_unverified=args.include_unverified,
        keep_analysis=args.keep_analysis,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(bench, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("已导出 benchmark: %s" % out_path)
    print("  samples: %d" % bench["sample_count"])

    pending = _pending_review(draft)
    if pending:
        print("  待复核（needs_manual_review 且未确认）: %d" % len(pending))
        print(
            "    %s" % ", ".join(pending[:10]) + (" ..." if len(pending) > 10 else "")
        )
    if bench["sample_count"] == 0 and not args.include_unverified:
        print(
            "  提示: 当前无 human_verified=true；可用 --include-unverified 导出全部调试"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
