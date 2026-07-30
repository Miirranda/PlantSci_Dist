#!/usr/bin/env python3
"""从 annotation_draft 清洗导出评测用 benchmark.json。

规则：
  - 仅保留 annotation_meta.human_verified == true 的样本
    （可用 --include-unverified 导出全部，便于调试）
  - 终稿只保留评测字段；解释性 analysis 默认去掉（可用 --keep-analysis）

Usage:
    python scripts/export_benchmark.py \\
        --draft data/annotations/P001_A001_annotation_draft.json \\
        --output data/annotations/P001_A001_benchmark.json
"""

from __future__ import annotations

import argparse
import json
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


def _is_verified(sample: dict[str, Any]) -> bool:
    meta = sample.get("annotation_meta") or {}
    return bool(meta.get("human_verified"))


def _clean_sample(sample: dict[str, Any], *, keep_analysis: bool) -> dict[str, Any]:
    gold = dict(sample.get("gold") or {})
    labels = sample.get("labels") or {}
    # 若 gold 分类为空，回退到旧 labels
    primary = gold.get("primary_type")
    if primary is None:
        primary = labels.get("primary_hallucination_type")
    secondary = gold.get("secondary_types")
    if secondary is None:
        secondary = list(labels.get("secondary_hallucination_types") or [])
    evidence_level = gold.get("evidence_level")
    if evidence_level is None:
        evidence_level = labels.get("evidence_level")

    sentence_ids = []
    for item in gold.get("sentence_ids") or []:
        try:
            sid = int(item)
        except (TypeError, ValueError):
            continue
        if sid >= 0:
            sentence_ids.append(sid)

    row: dict[str, Any] = {
        "sample_id": str(
            sample.get("sample_id") or sample.get("id") or sample.get("claim_id") or ""
        ),
        "paper_id": str(sample.get("paper_id") or ""),
        "article_id": str(sample.get("article_id") or ""),
        "article_source_type": str(sample.get("article_source_type") or ""),
        "claim_zh": str(
            sample.get("claim_zh") or sample.get("claim_text") or ""
        ).strip(),
        "gold_retrieval": {
            "sentence_ids": sentence_ids,
            "is_answerable": bool(sentence_ids)
            or str(evidence_level or "") != "No_Evidence",
        },
        "gold_classification": {
            "evidence_level": evidence_level,
            "primary_type": primary,
            "secondary_types": list(secondary or []),
            "is_accurate": gold.get("is_accurate", labels.get("is_accurate")),
            "severity": gold.get("severity", labels.get("severity")),
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
        "schema_version": "1.0",
        "status": "benchmark",
        "paper_id": draft.get("paper_id", ""),
        "article_id": draft.get("article_id", ""),
        "article_source_type": draft.get("article_source_type", ""),
        "index_version": draft.get("index_version", ""),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "sample_count": len(selected),
        "samples": selected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="annotation_draft → benchmark.json")
    parser.add_argument(
        "--draft",
        default="data/annotations/P001_A001_annotation_draft.json",
        help="标注初稿路径",
    )
    parser.add_argument(
        "--output",
        default="data/annotations/P001_A001_benchmark.json",
        help="终稿输出路径",
    )
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
    out_path.write_text(json.dumps(bench, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已导出 benchmark: %s" % out_path)
    print("  samples: %d" % bench["sample_count"])
    if bench["sample_count"] == 0 and not args.include_unverified:
        print("  提示: 当前无 human_verified=true；可用 --include-unverified 导出全部调试")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
