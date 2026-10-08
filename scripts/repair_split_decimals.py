#!/usr/bin/env python3
"""把人工召回译文里被拆开的小数和基因号接回去。

只改 P011、P012 的 manual_retrieved_paragraphs.text_zh。
专有名词沿用旧译文，不整段换成新的逐句翻译。
原文留在 text_zh_before_number_repair。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ("P011", "P012")

# 小数点被当成句号，数字被拆到句子两端。整句词序只在这几处挪回，名词仍用旧译文。
EXPLICIT = (
    (
        "在铁( Fe-EDTA )浓度为0、0的水培装置中，向大豆参考品种Williams 82 ( Wm82 )接种固氮根瘤菌USDA110。4、40和400 μ M在低氮和充足光照条件下。",
        "在铁( Fe-EDTA )浓度为0、0.4、40和400 μM的水培装置中，在低氮和充足光照条件下，向大豆参考品种Williams 82 ( Wm82 )接种固氮根瘤菌USDA110。",
    ),
    (
        "我们识别并验证了1。Copia LTR - RT插入到毛竹CCS基因的单个编码外显子中1 - kb",
        "我们识别并验证了1.1-kb Copia LTR - RT插入到毛竹CCS基因的单个编码外显子中",
    ),
)


def _decimals(text: str) -> set[str]:
    return set(re.findall(r"\d+\.\d+", text or ""))


def _loci(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z][A-Za-z0-9]*\.\d+g\d+", text or ""))


def repair_split_numbers(zh: str, sources: list[str]) -> tuple[str, list[str]]:
    """返回 (新译文, 改动说明)。sources 含英文原段和逐句新译文。"""
    notes: list[str] = []
    blob = "\n".join(sources)
    decimals = sorted(_decimals(blob), key=len, reverse=True)
    for src, dst in EXPLICIT:
        if src in zh:
            zh = zh.replace(src, dst, 1)
            notes.append("explicit")

    for dec in decimals:
        whole, frac = dec.split(".", 1)
        # 只匹配被空格或句号拆开的形式，已经写对的 0.4 不会再次命中。
        adjacent = re.compile(
            r"(?<!\d)"
            + re.escape(whole)
            + r"(?:\s+[.．]\s*|\s*[.．]\s+|\s*。\s*)"
            + re.escape(frac)
            + r"(?!\d)"
        )
        updated, count = adjacent.subn(dec, zh)
        if count:
            zh = updated
            notes.append(dec)
        gapped = re.compile(
            r"(?<!\d)"
            + re.escape(whole)
            + r"([^\d.。．]{1,16}?)[.。．]\s*"
            + re.escape(frac)
            + r"(?!\d)(\s*%)?"
        )
        while True:
            match = gapped.search(zh)
            if not match:
                break
            percent = "%" if match.group(2) else ""
            zh = zh[: match.start()] + dec + percent + match.group(1) + zh[match.end() :]
            notes.append("gap " + dec)

    for locus in sorted(_loci(blob), key=len, reverse=True):
        left, right = locus.split(".", 1)
        adjacent = re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(left) + r"\s*[.。．]\s*" + re.escape(right)
        )
        updated, count = adjacent.subn(locus, zh)
        if count:
            zh = updated
            notes.append(locus)
        gapped = re.compile(
            r"(?<![A-Za-z0-9])"
            + re.escape(left)
            + r"([^\d.。．]{1,8}?)[.。．]\s*"
            + re.escape(right)
        )
        while True:
            match = gapped.search(zh)
            if not match:
                break
            middle = match.group(1)
            after = zh[match.end() :]
            if middle.endswith("为") and after.startswith("为"):
                middle = middle[:-1]
            zh = zh[: match.start()] + locus + middle + after
            notes.append("gap " + locus)

    spaced = re.compile(r"(?<!\d)(\d+)\s+\.\s+(\d+)(?!\d)")

    def _join_spaced(match: re.Match[str]) -> str:
        return match.group(1) + "." + match.group(2)

    updated, count = spaced.subn(_join_spaced, zh)
    if count:
        zh = updated
        notes.append("spaced-decimal")

    if "μM" in blob or "µM" in blob:
        updated = re.sub(r"[μµ]\s+M", "μM", zh)
        if updated != zh:
            notes.append("μM")
            zh = updated
    return zh, notes


def _sources_for(paragraph: dict, units: list, index: int) -> list[str]:
    sources = [str(paragraph.get("text") or "")]
    for unit in units:
        if not isinstance(unit, dict):
            continue
        if unit.get("source") == "manual" and unit.get("manual_index") == index:
            sources.append(str(unit.get("text") or ""))
            sources.append(str(unit.get("text_zh") or ""))
    return sources


def repair_draft(draft: dict) -> list[str]:
    changes: list[str] = []
    for reviewer, records in (draft.get("human_reviews") or {}).items():
        if not isinstance(records, dict):
            continue
        for sample_id, record in records.items():
            if not isinstance(record, dict):
                continue
            units = (record.get("recall_normalization") or {}).get("evidence_units") or []
            for index, paragraph in enumerate(record.get("manual_retrieved_paragraphs") or []):
                if not isinstance(paragraph, dict):
                    continue
                old = str(paragraph.get("text_zh") or "")
                if not old:
                    continue
                updated, notes = repair_split_numbers(old, _sources_for(paragraph, units, index))
                if updated == old:
                    continue
                if "text_zh_before_number_repair" not in paragraph:
                    paragraph["text_zh_before_number_repair"] = old
                paragraph["text_zh"] = updated
                changes.append(
                    "%s %s para %d: %s" % (reviewer, sample_id, index, ", ".join(notes))
                )
    return changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    for paper in PAPERS:
        path = ROOT / "data" / "annotations" / paper / ("%s_A001_annotation_draft.json" % paper)
        draft = json.loads(path.read_text(encoding="utf-8"))
        changes = repair_draft(draft)
        print("== %s (%d)" % (paper, len(changes)))
        for line in changes:
            print(" ", line)
        if args.write and changes:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
