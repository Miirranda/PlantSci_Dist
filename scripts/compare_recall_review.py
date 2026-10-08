#!/usr/bin/env python3
"""把优化召回的证据区间和已完成的人工召回对齐，只评估找句。

不调用模型，不改初稿、句表和 recall_review 文件。
drop 与规范句是否改写只记入对照，不改变找句分类。

用法::

    python scripts/compare_recall_review.py --paper P011 --limit 15
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
_ARAG_ROOT = _PROJECT_ROOT / "arag-main"
if str(_ARAG_ROOT) not in sys.path:
    sys.path.insert(0, str(_ARAG_ROOT))

from retrieval_adaptor.recall_review import (  # noqa: E402
    load_sentence_table,
    normalize_match_text,
)

_MIN_MATCH_CHARS = 8
_CLASS_SAME = "找句一致"
_CLASS_WIDER = "模型更宽"
_CLASS_MISS = "未能替代"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="比对优化召回与人工召回的找句结果")
    parser.add_argument("--paper", default="P011", help="论文编号，默认 P011")
    parser.add_argument("--reviewer", default="牟德兰", help="人工审核人，默认牟德兰")
    parser.add_argument("--limit", type=int, default=15, help="只比初稿前 N 条，默认 15")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit 必须为正数")

    paper_id = args.paper.strip()
    folder = _PROJECT_ROOT / "data" / "annotations" / paper_id
    draft_path = folder / ("%s_A001_annotation_draft.json" % paper_id)
    review_path = folder / ("%s_A001_recall_review.json" % paper_id)
    sentence_path = folder / ("%s_sentences.csv" % paper_id)
    output_path = folder / ("%s_A001_recall_smoke.json" % paper_id)
    for path in (draft_path, review_path, sentence_path):
        if not path.is_file():
            print("找不到文件: %s" % path, file=sys.stderr)
            return 1

    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    table = load_sentence_table(sentence_path)
    records = ((draft.get("human_reviews") or {}).get(args.reviewer) or {})
    if not isinstance(records, dict) or not records:
        print("初稿中没有审核人 %s 的召回记录" % args.reviewer, file=sys.stderr)
        return 1

    review_by_id = {}
    for sample in review.get("samples") or []:
        if isinstance(sample, dict) and sample.get("sample_id"):
            review_by_id[str(sample["sample_id"])] = sample

    rows = []
    for sample in (draft.get("samples") or [])[: args.limit]:
        if not isinstance(sample, dict):
            continue
        rows.append(_compare_sample(sample, records, review_by_id, table))

    summary = _summarize(rows)
    document = {
        "paper_id": paper_id,
        "article_id": "A001",
        "reviewer": args.reviewer,
        "sample_count": len(rows),
        "source_draft": _relative(draft_path),
        "source_review": _relative(review_path),
        "summary": summary,
        "conclusion": _conclusion(summary),
        "samples": rows,
    }
    output_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(summary["counts"])
    print(document["conclusion"])
    print("报告: %s" % _relative(output_path))
    return 0


def sentences_in_paragraph(table: Any, paragraph: str) -> list[int]:
    """把手工段落对回句表：kept 原句规范化后整句出现在段落里才算。"""
    needle = normalize_match_text(paragraph)
    if len(needle) < _MIN_MATCH_CHARS:
        return []
    found: list[int] = []
    for sentence in table.sentences:
        hay = normalize_match_text(sentence.text)
        if len(hay) >= _MIN_MATCH_CHARS and hay in needle:
            found.append(sentence.sentence_id)
    return found


def model_sentence_ids(sample: dict[str, Any] | None, table: Any) -> list[int]:
    if not isinstance(sample, dict):
        return []
    seen: set[int] = set()
    ordered: list[int] = []
    for item in sample.get("evidences") or []:
        if not isinstance(item, dict):
            continue
        start = _as_int(item.get("sentence_id_start"))
        end = _as_int(item.get("sentence_id_end"))
        if start is None or end is None:
            continue
        if end < start:
            start, end = end, start
        for sentence_id in table.order:
            if sentence_id < start or sentence_id > end or sentence_id in seen:
                continue
            seen.add(sentence_id)
            ordered.append(sentence_id)
    return ordered


def locate_class(human_ids: set[int], model_ids: set[int]) -> str:
    missed = human_ids - model_ids
    extra = model_ids - human_ids
    if missed:
        return _CLASS_MISS
    if extra:
        return _CLASS_WIDER
    return _CLASS_SAME


def _compare_sample(
    sample: dict[str, Any],
    records: dict[str, Any],
    review_by_id: dict[str, dict[str, Any]],
    table: Any,
) -> dict[str, Any]:
    sample_id = str(sample.get("sample_id") or "")
    record = records.get(sample_id) if isinstance(records.get(sample_id), dict) else {}
    model = review_by_id.get(sample_id)
    gold_ids = _id_list(record.get("gold_sentence_ids"))
    manual_ids: list[int] = []
    seen_manual: set[int] = set()
    for paragraph in record.get("manual_retrieved_paragraphs") or []:
        text = ""
        if isinstance(paragraph, dict):
            text = str(paragraph.get("text") or "")
        elif isinstance(paragraph, str):
            text = paragraph
        for sentence_id in sentences_in_paragraph(table, text):
            if sentence_id not in seen_manual:
                seen_manual.add(sentence_id)
                manual_ids.append(sentence_id)
    human_ids = _union(gold_ids, manual_ids)
    predicted = model_sentence_ids(model, table)
    human_set = set(human_ids)
    model_set = set(predicted)
    hit = [sentence_id for sentence_id in human_ids if sentence_id in model_set]
    missed = [sentence_id for sentence_id in human_ids if sentence_id not in model_set]
    extra = [sentence_id for sentence_id in predicted if sentence_id not in human_set]
    original = str(record.get("claim_zh_original") or sample.get("claim_zh") or "").strip()
    revised = str(record.get("claim_zh_revised") or record.get("claim_zh") or original).strip()
    claim_changed = bool(record.get("claim_changed")) or (bool(revised) and revised != original)
    dropped = _is_dropped(record)
    verdict = str((model or {}).get("verdict") or "")
    return {
        "sample_id": sample_id,
        "claim": str(sample.get("claim_zh") or ""),
        "claim_changed": claim_changed,
        "dropped": dropped,
        "verdict": verdict,
        "need_human_review": bool((model or {}).get("need_human_review")),
        "model_missing": model is None,
        "human_gold_ids": gold_ids,
        "manual_sentence_ids": manual_ids,
        "model_sentence_ids": predicted,
        "hit_ids": hit,
        "missed_ids": missed,
        "extra_ids": extra,
        "location_class": locate_class(human_set, model_set),
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {_CLASS_SAME: 0, _CLASS_WIDER: 0, _CLASS_MISS: 0}
    for row in rows:
        counts[row["location_class"]] = counts.get(row["location_class"], 0) + 1
    return {
        "counts": counts,
        "human_drop": sum(1 for row in rows if row["dropped"]),
        "claim_changed": sum(1 for row in rows if row["claim_changed"]),
        "model_only": sum(
            1
            for row in rows
            if row["location_class"] == _CLASS_WIDER and not row["human_gold_ids"] and not row["manual_sentence_ids"]
        ),
        "with_human_sentences": sum(
            1 for row in rows if row["human_gold_ids"] or row["manual_sentence_ids"]
        ),
    }


def _conclusion(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    located = summary["with_human_sentences"]
    missed = counts.get(_CLASS_MISS, 0)
    covered = located - missed
    model_only = summary["model_only"]
    if located == 0 or missed == 0:
        return (
            "人工找出的论文句都被终版证据盖住。"
            "审核人主要把关多出的句子，并继续做 drop 和改句。"
        )
    if missed * 2 < located:
        return (
            "人工实际找出论文句的 %d 条里，%d 条已被盖住，%d 条没盖全。"
            "多数手工找句可以被该板块完成；另有 %d 条人工没有认定句子，模型仍给出了证据，drop 仍要人把关。"
            % (located, covered, missed, model_only)
        )
    return (
        "人工实际找出论文句的 %d 条里，只有 %d 条被终版证据盖全，%d 条没盖全，"
        "还不能大部分代替手工找句。另有 %d 条人工没有认定句子，模型仍给出了证据，drop 和改句仍要人把关。"
        % (located, covered, missed, model_only)
    )


def _is_dropped(record: dict[str, Any]) -> bool:
    if record.get("weak_evidence"):
        return True
    return str(record.get("review_decision") or "").strip().lower() == "drop"


def _id_list(raw: Any) -> list[int]:
    found: list[int] = []
    seen: set[int] = set()
    for value in raw or []:
        number = _as_int(value)
        if number is None or number in seen:
            continue
        seen.add(number)
        found.append(number)
    return found


def _union(left: list[int], right: list[int]) -> list[int]:
    found: list[int] = []
    seen: set[int] = set()
    for sentence_id in list(left) + list(right):
        if sentence_id in seen:
            continue
        seen.add(sentence_id)
        found.append(sentence_id)
    return found


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return None


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(_PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
