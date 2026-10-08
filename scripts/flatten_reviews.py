#!/usr/bin/env python3
"""把 human_reviews 的审核结果回填到 samples[] 层，供 export_benchmark.py 导出终稿。

审核前端把结果写到顶层 ``human_reviews``（含段落级人工召回），但导出脚本
``export_benchmark.py`` 读的是旧 ``samples[]`` 层（gold_retrieval 句子级 +
human_verified），两条路径没接通。本脚本把每个审核人的记录摊回对应 sample：

    gold_sentence_ids           -> samples[].gold_retrieval.sentence_ids
    manual_retrieved_paragraphs -> samples[].gold_retrieval.paragraphs
    evidence_level              -> samples[].gold_classification.evidence_level
                                  samples[].gold_retrieval.is_answerable (=With_Evidence)
    primary_level2 / secondary_level2 -> gold_classification.primary_label / secondary_label
                                  非 With_Evidence 时按 HANDOVER 规则清空
    severity / uncovered_phenomenon / note -> 对应字段 / human_note
    human_verified              -> samples[].human_verified = true
    claim_zh_original           -> samples[].claim_zh（公众号原文，不写成修改版）
                                  samples[].claim_zh_original / claim_zh_revised / claim_changed
    review_decision=drop / weak_evidence -> 跳过，不回填（不进 benchmark）

用法::

    python scripts/flatten_reviews.py --draft data/annotations/P001/P001_A001_annotation_draft.json
    python scripts/flatten_reviews.py --draft ... --reviewer 牟德兰 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
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

_DEFAULT_DRAFT = "data/annotations/P001/P001_A001_annotation_draft.json"


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def atomic_write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def _as_int_ids(raw: Any) -> list[int]:
    ids: list[int] = []
    for item in raw or []:
        try:
            sid = int(item)
        except (TypeError, ValueError):
            continue
        if sid >= 0 and sid not in ids:
            ids.append(sid)
    return ids


def _as_paragraphs(raw: Any) -> list[dict[str, str]]:
    paras: list[dict[str, str]] = []
    for p in raw or []:
        if not isinstance(p, dict):
            continue
        text = str(p.get("text") or "").strip()
        if not text:
            continue
        paras.append({"text": text, "text_zh": str(p.get("text_zh") or "")})
    return paras


def flatten(doc: dict[str, Any], reviewer: str = "") -> tuple[int, int]:
    """把 human_reviews 回填进 samples[]，返回 (写回条数, 覆盖样本数)。"""
    samples = doc.get("samples") or []
    by_id: dict[str, dict[str, Any]] = {}
    for s in samples:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("sample_id") or s.get("claim_id") or "")
        if sid:
            by_id[sid] = s

    human_reviews = doc.get("human_reviews") or {}
    written = 0
    for rv, recs in human_reviews.items():
        if reviewer and rv != reviewer:
            continue
        for sample_id, rec in (recs or {}).items():
            sample = by_id.get(sample_id)
            if sample is None:
                continue
            if rec.get("weak_evidence") or str(rec.get("review_decision") or "").strip().lower() == "drop":
                continue
            evidence_level = str(rec.get("evidence_level") or "")
            is_with = evidence_level == "With_Evidence"

            # 非 With_Evidence 时清空失真标签（HANDOVER：Weak/No ⇒ primary_label null）
            primary = str(rec.get("primary_level2") or "") if is_with else ""
            secondary = str(rec.get("secondary_level2") or "") if is_with else ""

            sample["gold_retrieval"] = {
                "sentence_ids": _as_int_ids(rec.get("gold_sentence_ids")),
                "paragraphs": _as_paragraphs(rec.get("manual_retrieved_paragraphs")),
                "is_answerable": bool(is_with),
            }
            sample["gold_classification"] = {
                "evidence_level": evidence_level,
                "primary_label": primary,
                "secondary_label": secondary,
                "severity": str(rec.get("severity") or ""),
                "uncovered_phenomenon": str(rec.get("uncovered_phenomenon") or ""),
                # 标记新 schema（normalize 会据此推导 has_distortion），
                # 同时让 export 的 Weak/No 样本也走新字段分支而非旧扁平分支。
                "has_distortion": None,
            }
            sample["human_verified"] = bool(rec.get("human_verified", True))
            if rec.get("note"):
                sample["human_note"] = rec["note"]
            original = str(rec.get("claim_zh_original") or sample.get("claim_zh") or "").strip()
            revised = str(rec.get("claim_zh_revised") or "").strip()
            if "claim_changed" in rec:
                changed = bool(rec.get("claim_changed"))
            else:
                edited = str(rec.get("claim_zh") or "").strip()
                changed = bool(edited and original and edited != original)
                if changed:
                    revised = edited
            if changed and not revised:
                revised = str(rec.get("claim_zh") or "").strip()
            if original:
                sample["claim_zh"] = original
            sample["claim_zh_original"] = original
            sample["claim_zh_revised"] = revised if changed else ""
            sample["claim_changed"] = changed
            written += 1
    return written, len(by_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="human_reviews 回填到 samples[]")
    parser.add_argument("--draft", default=_DEFAULT_DRAFT, help="文件1（召回审核）JSON 路径")
    parser.add_argument("--reviewer", default="", help="只回填指定审核人；缺省回填全部")
    parser.add_argument("--dry-run", action="store_true", help="只预览统计，不写回")
    args = parser.parse_args()

    draft_path = _resolve(args.draft)
    if not draft_path.is_file():
        print("找不到文件: %s" % draft_path)
        return 1

    doc = json.loads(draft_path.read_text(encoding="utf-8"))
    written, total = flatten(doc, reviewer=args.reviewer)

    if args.dry_run:
        print("[dry-run] 将回填 %d 条 / 共 %d 样本（不写回）" % (written, total))
        return 0

    atomic_write(draft_path, doc)
    print("已回填 %d 条 / 共 %d 样本 -> %s" % (written, total, draft_path))
    print("下一步: python scripts/export_benchmark.py --draft %s --output data/annotations/P001/P001_A001_benchmark.json" % args.draft)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
