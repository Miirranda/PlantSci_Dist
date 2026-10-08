#!/usr/bin/env python3
"""把拆分两阶段之前、已经人工审完的失真结果迁成文件2。

只读文件1 ``*_annotation_draft.json``。不调用千问，也不补翻译。
人工标签来自 ``human_reviews``；AI 初稿来自样本上的 ``analysis`` /
``gold_classification``，写入 ``generated_analysis`` 供失真界面展示。

用法::

    python scripts/migrate_legacy_distortion.py --draft data/annotations/P001/P001_A001_annotation_draft.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

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
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from hallu.config import DISTORTION_LABELS, NO_DISTORTION  # noqa: E402
from regenerate_analysis import (  # noqa: E402
    _claim_zh,
    _file1_claim_original,
    atomic_write,
    derive_output_path,
    load_sentence_table,
)

_DISTORTION_FIELDS = (
    "evidence_level",
    "primary_level2",
    "secondary_level2",
    "severity",
    "uncovered_phenomenon",
    "note",
    "human_verified",
)


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def _slug(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("level2") or "").strip()
    return str(raw or "").strip()


def _label_obj(raw: Any) -> dict[str, str] | None:
    slug = _slug(raw)
    if not slug:
        return None
    if slug == NO_DISTORTION:
        return {"level1": "", "level2": slug}
    info = DISTORTION_LABELS.get(slug) or {}
    return {"level1": str(info.get("level1") or ""), "level2": slug}


def _evidence_map(sample: dict[str, Any]) -> dict[int, dict[str, str]]:
    sr = sample.get("system_retrieval") or {}
    found: dict[int, dict[str, str]] = {}
    for evidence in sr.get("review_evidences") or []:
        sid = evidence.get("sentence_id")
        if sid is None:
            continue
        found[int(sid)] = {
            "text": str(evidence.get("text") or ""),
            "text_zh": str(evidence.get("text_zh") or ""),
        }
    for evidence in sr.get("classify_evidences") or []:
        sid = evidence.get("sentence_id")
        if sid is None or int(sid) in found:
            continue
        found[int(sid)] = {"text": str(evidence.get("text") or ""), "text_zh": ""}
    return found


def _gold_evidences(
    record: dict[str, Any],
    sample: dict[str, Any],
    sentence_table: dict[int, str],
    missing: list[str],
) -> list[dict[str, str]]:
    ev_map = _evidence_map(sample)
    gold: list[dict[str, str]] = []
    sample_id = str(sample.get("sample_id") or "")
    for raw_sid in record.get("gold_sentence_ids") or []:
        sid = int(raw_sid)
        if sid in ev_map:
            item = {"sentence_id": str(sid), **ev_map[sid]}
        elif sentence_table.get(sid):
            item = {"sentence_id": str(sid), "text": sentence_table[sid], "text_zh": ""}
        else:
            missing.append("%s gold id=%s 找不到原文" % (sample_id, sid))
            continue
        if item["text"] and not item["text_zh"]:
            missing.append("%s gold id=%s 缺少中文" % (sample_id, sid))
        gold.append(item)
    return gold


def _manual_paragraphs(record: dict[str, Any], sample_id: str, missing: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for paragraph in record.get("manual_retrieved_paragraphs") or []:
        text = str((paragraph or {}).get("text") or "").strip()
        if not text:
            continue
        zh = str((paragraph or {}).get("text_zh") or "").strip()
        if not zh:
            missing.append("%s 人工段落缺少中文" % sample_id)
        out.append({"text": text, "text_zh": zh})
    return out


def _generated_analysis(sample: dict[str, Any], migrated_at: str) -> dict[str, Any]:
    analysis = sample.get("analysis") if isinstance(sample.get("analysis"), dict) else {}
    gold = sample.get("gold_classification") if isinstance(sample.get("gold_classification"), dict) else {}
    differences = analysis.get("key_differences") if isinstance(analysis.get("key_differences"), list) else []
    return {
        "evidence_level": str(gold.get("evidence_level") or "").strip(),
        "primary_label": _label_obj(gold.get("primary_label")),
        "secondary_label": _label_obj(gold.get("secondary_label")),
        "severity": str(gold.get("severity") or "").strip(),
        "classification_reason": str(analysis.get("classification_reason") or "").strip(),
        "evidence_judgement": str(analysis.get("evidence_judgement") or "").strip(),
        "key_differences": [item for item in differences if isinstance(item, dict)],
        "ai_confidence": str(analysis.get("ai_confidence") or "").strip().lower(),
        "generated_at": migrated_at,
        "source": "legacy_sample_analysis",
    }


def _is_dropped(record: dict[str, Any]) -> bool:
    if record.get("weak_evidence"):
        return True
    return str(record.get("review_decision") or "").strip().lower() == "drop"


def migrate(draft_path: Path, out_path: Path) -> dict[str, Any]:
    doc = json.loads(draft_path.read_text(encoding="utf-8"))
    samples = {str(s.get("sample_id") or ""): s for s in (doc.get("samples") or [])}
    sentence_table = load_sentence_table(str(doc.get("paper_id") or ""))
    migrated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    reviews: dict[str, Any] = {}
    kept_pairing: dict[str, str] = {}
    kept_original: dict[str, str] = {}
    missing: list[str] = []
    stats = {"records": 0, "dropped": 0, "verified": 0, "with_gold": 0, "with_manual": 0}

    for reviewer, records in (doc.get("human_reviews") or {}).items():
        for sample_id, record in (records or {}).items():
            if not isinstance(record, dict):
                continue
            if "primary_level2" not in record and not record.get("human_verified"):
                continue
            sample = samples.get(str(sample_id))
            if not sample:
                missing.append("%s 在 samples 里不存在" % sample_id)
                continue
            gold = _gold_evidences(record, sample, sentence_table, missing)
            manual = _manual_paragraphs(record, str(sample_id), missing)
            original = _file1_claim_original(sample, record)
            working = _claim_zh(sample, record)
            migrated: dict[str, Any] = {
                "gold_sentence_ids": [int(x) for x in (record.get("gold_sentence_ids") or [])],
                "gold_evidences": gold,
                "recall_note": str(record.get("recall_note") or ""),
                "manual_retrieved_paragraphs": manual,
                "recall_reviewed": bool(record.get("recall_reviewed")),
                "recall_updated_at": record.get("recall_updated_at") or "",
                "claim_zh_original": original,
                "claim_zh": working,
                "claim_changed": working != original,
                "weak_evidence": bool(record.get("weak_evidence")),
                "review_decision": "drop" if _is_dropped(record) else "keep",
                "generated_analysis": _generated_analysis(sample, migrated_at),
            }
            revised = str(record.get("claim_zh_revised") or "").strip()
            if revised:
                migrated["claim_zh_revised"] = revised
            for key in _DISTORTION_FIELDS:
                if key in record:
                    migrated[key] = record[key]
            if record.get("updated_at"):
                migrated["updated_at"] = record["updated_at"]
            reviews.setdefault(reviewer, {})[sample_id] = migrated
            if str(sample_id) not in kept_pairing:
                kept_pairing[str(sample_id)] = working
                kept_original[str(sample_id)] = original
            stats["records"] += 1
            stats["dropped"] += int(_is_dropped(record))
            stats["verified"] += int(bool(record.get("human_verified")))
            stats["with_gold"] += int(bool(gold))
            stats["with_manual"] += int(bool(manual))

    out_samples = []
    for sample in doc.get("samples") or []:
        sid = str(sample.get("sample_id") or "")
        if sid in kept_pairing:
            out_samples.append({
                "sample_id": sid,
                "claim_zh": kept_pairing[sid],
                "claim_zh_original": kept_original[sid],
            })

    out_doc = {
        "schema_version": "1.3",
        "kind": "distortion_review",
        "paper_id": doc.get("paper_id") or "",
        "article_id": doc.get("article_id") or "",
        "source_draft": draft_path.name,
        "migration": {
            "from": "legacy_annotation_draft",
            "migrated_at": migrated_at,
            "note": "未调用模型。generated_analysis 来自样本上的旧 analysis / gold_classification。",
        },
        "samples": out_samples,
        "human_reviews": reviews,
    }
    atomic_write(out_path, out_doc)
    stats["samples"] = len(out_samples)
    stats["missing"] = missing
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="旧版单文件失真结果迁移为 distortion_review.json")
    parser.add_argument("--draft", required=True, help="文件1 *_annotation_draft.json")
    parser.add_argument("--out", default="", help="文件2 路径，缺省按命名推导")
    parser.add_argument("--force", action="store_true", help="覆盖已有文件2")
    args = parser.parse_args()

    draft_path = _resolve(args.draft)
    if not draft_path.exists():
        raise SystemExit("找不到文件1: %s" % draft_path)
    out_path = _resolve(args.out) if args.out else derive_output_path(draft_path)
    if out_path.exists() and not args.force:
        raise SystemExit("文件2 已存在，未覆盖: %s（确认要重迁再加 --force）" % out_path)

    stats = migrate(draft_path, out_path)
    print("文件1 = %s" % draft_path)
    print("文件2 = %s" % out_path)
    print(
        "迁入 %d 条（样本 %d，已核 %d，drop %d，有 gold %d，有人工段落 %d）"
        % (
            stats["records"],
            stats["samples"],
            stats["verified"],
            stats["dropped"],
            stats["with_gold"],
            stats["with_manual"],
        )
    )
    for line in stats["missing"]:
        print("  [缺] %s" % line)
    if not stats["records"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
