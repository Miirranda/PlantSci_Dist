"""召回审核：把初步召回的候选句重新定位到论文原文，提取连续证据句。

``sentence_id`` 是冻结锚点。本模块只读取句表，不重编号、不重切句，
也不改写召回初稿。证据原文一律由句表回拼，不采纳模型改写。
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "recall-review-v1"
WINDOW_RADIUS = 10
MAX_ANCHORS = 3

_ZERO_RECALL = "召回句为空，无法从论文原文定位证据。"
_UNLOCATED = "定位句在句表中均未找到，无法从论文原文提取证据。"
_NONE_IN_WINDOW = "已在定位句上下文中检索，未找到能够支撑该观点句的连续原文证据。"
_FINAL_REJECTED = "终版判断未保留任何能够支撑该观点句的证据。"
_PARTIAL_FALLBACK = "现有证据只覆盖了观点句的一部分，其余部分在当前原文窗口中没有对应的连续句子。"
_SUPPORTED_FALLBACK = "上述连续原文句子支撑该观点句。"
_INVALID_VERDICT = "模型未给出有效判定，现有证据需人工确认是否完整支撑该观点句。"

_DRAFT_NAME = re.compile(r"^(?P<paper>.+)_(?P<article>A\d+)_annotation_draft\.json$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_SPACE = re.compile(r"\s+")
_MIN_MATCH_CHARS = 8

_ANCHOR_SYSTEM = (
    "你是植物科学论文的证据定位助手。\n"
    "给定一条中文观点句，以及若干条初步召回的英文候选句。\n"
    "请选出最可能靠近真正证据的定位句，最多 3 条。\n"
    "只能从给定候选的 sentence_id 中选择，不能编造新的句子编号。\n"
    "只返回一个 JSON 对象，不要 Markdown。\n"
    '格式：{"anchor_sentence_ids": [12, 4, 30]}'
)

_EXTRACT_SYSTEM = (
    "你是植物科学论文的证据提取助手。\n"
    "给定一条中文观点句，以及一段按论文阅读顺序排列的英文原文窗口。\n"
    "窗口里每句都有冻结的 sentence_id。请提取能支撑该观点句的证据。\n"
    "规则：\n"
    "1. 只抽取原文里写出了观点句命题的连续句子。只因为同在讨论铁、根瘤或固氮，不能抽取。\n"
    "2. 证据可以有多处相互独立的连续片段。每一处内部必须连续，读下来是同一件事。\n"
    "3. 位置不连续的句子必须拆成多条，不能拼成一条。\n"
    "4. 只能用完整句子，用 sentence_id 闭区间表示，不能截成半句，不能改写原文。\n"
    "5. 每条证据给出 0 到 1 的置信度。\n"
    "6. 区间必须落在给定窗口内。没有写出命题的证据时 spans 为空数组。\n"
    "若要跳过窗口中的某一句，请改用 sentence_ids 列出保留的编号，不要用闭区间把中间句包进去。\n"
    "只返回一个 JSON 对象，不要 Markdown。\n"
    "格式："
    '{"spans": [{"sentence_id_start": 1, "sentence_id_end": 3, "confidence": 0.8}]}'
)

_FINAL_SYSTEM = (
    "你是植物科学论文的证据综合助手。\n"
    "给定一条中文观点句，以及已经去重、合并后的候选证据片段。\n"
    "这些片段来自论文原文。请选出能代替人工找句的终版证据。\n"
    "留句标准：\n"
    "1. 只留原文已经写出的命题。产量、品质、重要意义、首要因子等，原文没有写出就不是证据。\n"
    "2. 不能用“因此可以推出”补上观点句里的话。话题相近但没写出该命题的片段不要选。\n"
    "3. 每一条证据都要能顺着读完，通常是连续的 2 到 4 句。"
    "只有缺了就读不成句时，才多留紧邻的一句。\n"
    "4. 同一命题在多处出现时，只留最清楚的一处。\n"
    "5. 只能通过 index 选择给定候选，不能新增候选外的句子，不能改写原文。\n"
    "6. 用 sentence_id_start 和 sentence_id_end 把该候选裁到上述短段，闭区间必须落在该候选内部。\n"
    "7. 裁后的原文写出了观点句的各个命题时，verdict 为 supported。\n"
    "8. 只写出其中一部分时，verdict 为 partial，explanation 写明已覆盖哪些命题、缺哪些命题。\n"
    "9. 没有任何原文写出该命题时，selected 为空数组，verdict 为 no_evidence，"
    "explanation 只说明为什么不能支撑。\n"
    "10. 若说明认定某几条候选写出了命题，selected 必须列出对应 index，不能留空。\n"
    "每条保留证据给出 0 到 1 的最终置信度。\n"
    "只返回一个 JSON 对象，不要 Markdown。\n"
    "格式："
    '{"selected": [{"index": 0, "sentence_id_start": 2, "sentence_id_end": 3, "confidence": 0.9}], '
    '"verdict": "supported", "explanation": "中文说明"}'
)

_FINAL_RETRY_SYSTEM = (
    "上一次 selected 为空，但说明仍在论证候选能够支撑观点句。这是无效输出。\n"
    "请重新只返回一个 JSON 对象。\n"
    "如果某几条候选的原文确实写出了观点句中的命题，selected 必须列出 index，"
    "并用 sentence_id_start、sentence_id_end 裁到最短的连续可读片段，区间必须落在该候选内部。\n"
    "如果原文只是话题相关、并没有写出该命题，selected 为空，verdict 为 no_evidence，"
    "explanation 只说明为什么不能支撑，不要再写这些候选如何支撑。\n"
    "格式与终版相同："
    '{"selected": [{"index": 0, "sentence_id_start": 2, "sentence_id_end": 3, "confidence": 0.9}], '
    '"verdict": "partial", "explanation": "中文说明"}'
)


@dataclass
class KeptSentence:
    sentence_id: int
    text: str


@dataclass
class SentenceTable:
    """按阅读顺序排列的 kept 句。``order_index`` 把 sentence_id 映射到序号。"""

    sentences: list[KeptSentence]
    by_id: dict[int, KeptSentence]
    order: list[int]
    order_index: dict[int, int]


@dataclass
class ContextWindow:
    sentence_ids: list[int]
    anchor_ids: list[int]


@dataclass
class EvidenceSpan:
    sentence_ids: list[int]
    confidence: float
    anchor_source: list[int]


def normalize_match_text(text: str) -> str:
    """去掉控制字符、做 NFKC、压缩空白，供子串定位。"""
    cleaned = unicodedata.normalize("NFKC", text or "")
    cleaned = _CONTROL.sub("", cleaned)
    return _SPACE.sub(" ", cleaned).strip().casefold()


def load_sentence_table(path: str | Path) -> SentenceTable:
    """读取句表。必须用 utf-8-sig，否则首列会变成 ``\\ufeffsentence_id``。"""
    sentences: list[KeptSentence] = []
    seen: set[int] = set()
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            status = str(row.get("status") or "").strip().lower()
            if status != "kept":
                continue
            sentence_id = _as_nonneg_int(row.get("sentence_id"))
            if sentence_id is None or sentence_id in seen:
                continue
            seen.add(sentence_id)
            sentences.append(KeptSentence(sentence_id=sentence_id, text=str(row.get("text") or "")))
    sentences.sort(key=lambda item: item.sentence_id)
    order = [item.sentence_id for item in sentences]
    return SentenceTable(
        sentences=sentences,
        by_id={item.sentence_id: item for item in sentences},
        order=order,
        order_index={sentence_id: index for index, sentence_id in enumerate(order)},
    )


def load_draft(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("召回初稿必须是 JSON 对象: %s" % path)
    return data


def article_id_from_draft_name(name: str) -> str:
    match = _DRAFT_NAME.match(name)
    if match is None:
        return "A001"
    return match.group("article")


def output_path_for_draft(draft_path: str | Path) -> Path:
    path = Path(draft_path)
    suffix = "_annotation_draft.json"
    if path.name.endswith(suffix):
        stem = path.name[: -len(suffix)]
        return path.with_name(stem + "_recall_review.json")
    return path.with_name(path.stem + "_recall_review.json")


def parse_candidates(sample: dict[str, Any]) -> list[dict[str, Any]]:
    retrieval = sample.get("system_retrieval") or {}
    if not isinstance(retrieval, dict):
        return []
    raw = retrieval.get("review_evidences") or []
    if not isinstance(raw, list):
        return []
    candidates: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sentence_id = _as_nonneg_int(item.get("sentence_id"))
        text = str(item.get("text") or "")
        if sentence_id is None and not text.strip():
            continue
        candidates.append(
            {
                "rank": _as_int(item.get("rank")),
                "sentence_id": sentence_id,
                "text": text,
                "text_zh": str(item.get("text_zh") or ""),
            }
        )
    return candidates


def locate_anchor(
    table: SentenceTable,
    sentence_id: int | None,
    text: str,
) -> tuple[int | None, str]:
    """先按 sentence_id 定位；句表没有该编号时，再用规范化子串匹配。"""
    if sentence_id is not None and sentence_id in table.by_id:
        return sentence_id, "id"
    matched = find_sentence_by_text(table, text)
    if matched is None:
        return None, "missing"
    return matched, "text"


def find_sentence_by_text(table: SentenceTable, text: str) -> int | None:
    needle = normalize_match_text(text)
    if len(needle) < _MIN_MATCH_CHARS:
        return None
    exact: list[int] = []
    needle_in_sentence: list[int] = []
    sentence_in_needle: list[int] = []
    for sentence in table.sentences:
        hay = normalize_match_text(sentence.text)
        if not hay:
            continue
        if hay == needle:
            exact.append(sentence.sentence_id)
        elif needle in hay:
            needle_in_sentence.append(sentence.sentence_id)
        elif len(hay) >= _MIN_MATCH_CHARS and hay in needle:
            sentence_in_needle.append(sentence.sentence_id)
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    pool = needle_in_sentence or sentence_in_needle
    if len(pool) == 1:
        return pool[0]
    return None


def window_sentence_ids(
    table: SentenceTable,
    anchor_id: int,
    *,
    radius: int = WINDOW_RADIUS,
) -> list[int]:
    """在 kept 序列上取锚点前后各 ``radius`` 句，含锚点，到边界即停。"""
    position = table.order_index.get(anchor_id)
    if position is None:
        return []
    start = max(0, position - radius)
    end = min(len(table.order), position + radius + 1)
    return list(table.order[start:end])


def merge_overlapping_windows(windows: list[ContextWindow]) -> list[ContextWindow]:
    """sentence_id 集合有交集的窗口并成一段；不相交的保持独立。"""
    usable = [window for window in windows if window.sentence_ids]
    parent = list(range(len(usable)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    id_sets = [set(window.sentence_ids) for window in usable]
    for left in range(len(usable)):
        for right in range(left + 1, len(usable)):
            if id_sets[left].intersection(id_sets[right]):
                union(left, right)

    buckets: dict[int, list[int]] = {}
    for index in range(len(usable)):
        buckets.setdefault(find(index), []).append(index)

    merged: list[ContextWindow] = []
    for root in sorted(buckets, key=lambda item: min(buckets[item])):
        sentence_ids: set[int] = set()
        anchor_ids: list[int] = []
        for index in buckets[root]:
            sentence_ids.update(usable[index].sentence_ids)
            for anchor_id in usable[index].anchor_ids:
                if anchor_id not in anchor_ids:
                    anchor_ids.append(anchor_id)
        merged.append(
            ContextWindow(sentence_ids=sorted(sentence_ids), anchor_ids=anchor_ids)
        )
    return merged


def contiguous_runs(sentence_ids: list[int], order_index: dict[int, int]) -> list[list[int]]:
    """把编号按 kept 顺序拆成若干连续段。中间缺一句就断开。"""
    ordered: list[int] = []
    seen: set[int] = set()
    for sentence_id in sentence_ids:
        if sentence_id not in order_index or sentence_id in seen:
            continue
        seen.add(sentence_id)
        ordered.append(sentence_id)
    ordered.sort(key=lambda sentence_id: order_index[sentence_id])
    if not ordered:
        return []
    runs: list[list[int]] = [[ordered[0]]]
    for sentence_id in ordered[1:]:
        previous = runs[-1][-1]
        if order_index[sentence_id] == order_index[previous] + 1:
            runs[-1].append(sentence_id)
        else:
            runs.append([sentence_id])
    return runs


def integrate_spans(spans: list[EvidenceSpan], order_index: dict[int, int]) -> list[EvidenceSpan]:
    """去掉重复句，并把原文上相邻或重叠的片段合并。隔开的片段保持独立。"""
    usable = [span for span in spans if span.sentence_ids]
    usable.sort(key=lambda span: order_index.get(span.sentence_ids[0], 10**9))
    folded: list[EvidenceSpan] = []
    for span in usable:
        span_ids = [sentence_id for sentence_id in span.sentence_ids if sentence_id in order_index]
        if not span_ids:
            continue
        span_ids = sorted(set(span_ids), key=lambda sentence_id: order_index[sentence_id])
        if not folded:
            folded.append(
                EvidenceSpan(
                    sentence_ids=span_ids,
                    confidence=span.confidence,
                    anchor_source=list(span.anchor_source),
                )
            )
            continue
        previous = folded[-1]
        previous_end = order_index[previous.sentence_ids[-1]]
        next_start = order_index[span_ids[0]]
        if next_start <= previous_end + 1:
            union = sorted(
                set(previous.sentence_ids).union(span_ids),
                key=lambda sentence_id: order_index[sentence_id],
            )
            anchors = list(previous.anchor_source)
            for anchor_id in span.anchor_source:
                if anchor_id not in anchors:
                    anchors.append(anchor_id)
            folded[-1] = EvidenceSpan(
                sentence_ids=union,
                confidence=max(previous.confidence, span.confidence),
                anchor_source=anchors,
            )
        else:
            folded.append(
                EvidenceSpan(
                    sentence_ids=span_ids,
                    confidence=span.confidence,
                    anchor_source=list(span.anchor_source),
                )
            )
    normalized: list[EvidenceSpan] = []
    for span in folded:
        for run in contiguous_runs(span.sentence_ids, order_index):
            normalized.append(
                EvidenceSpan(
                    sentence_ids=run,
                    confidence=span.confidence,
                    anchor_source=list(span.anchor_source),
                )
            )
    return normalized


def fill_anchor_ids(
    allowed: list[int],
    model_ids: list[int],
    *,
    limit: int = MAX_ANCHORS,
) -> list[int]:
    """保留模型选出的合法编号，不足时按召回顺序补齐。"""
    allowed_set = set(allowed)
    picked: list[int] = []
    for sentence_id in model_ids:
        if sentence_id in allowed_set and sentence_id not in picked:
            picked.append(sentence_id)
        if len(picked) >= limit:
            return picked
    for sentence_id in allowed:
        if sentence_id not in picked:
            picked.append(sentence_id)
        if len(picked) >= limit:
            break
    return picked


def evidence_text_for(table: SentenceTable, sentence_ids: list[int]) -> str:
    parts: list[str] = []
    for sentence_id in sentence_ids:
        sentence = table.by_id.get(sentence_id)
        if sentence is None:
            continue
        text = sentence.text.strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def parse_extracted_spans(
    raw: Any,
    window: ContextWindow,
    table: SentenceTable,
) -> list[EvidenceSpan]:
    """把模型区间收成窗口内的连续片段。不连续的编号拆开，原文稍后由句表回拼。"""
    if not isinstance(raw, dict):
        return []
    items = raw.get("spans")
    if not isinstance(items, list):
        return []
    window_set = set(window.sentence_ids)
    spans: list[EvidenceSpan] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        selected = _ids_from_span_item(item, window.sentence_ids, window_set)
        confidence = clamp_confidence(item.get("confidence"))
        for run in contiguous_runs(selected, table.order_index):
            spans.append(
                EvidenceSpan(
                    sentence_ids=run,
                    confidence=confidence,
                    anchor_source=list(window.anchor_ids),
                )
            )
    return spans


def review_sample(client: Any, sample: dict[str, Any], table: SentenceTable) -> dict[str, Any]:
    """审核一条样本。单条失败写入说明，不向外抛出。"""
    sample_id = str(sample.get("sample_id") or "")
    claim = str(sample.get("claim_zh") or "").strip()
    try:
        return _review_sample(client, sample_id, claim, sample, table)
    except Exception as exc:
        message = "%s: %s" % (type(exc).__name__, exc)
        return _result(
            sample_id,
            claim,
            [],
            "no_evidence",
            "召回审核失败：%s" % message,
            error=message,
        )


def review_samples(
    client: Any,
    samples: list[Any],
    table: SentenceTable,
    *,
    existing: dict[str, Any] | None = None,
    force: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """按初稿顺序审核。已成功的样本默认跳过；``limit`` 只限制本次新处理的条数。"""
    done = _successful_by_id(existing, force=force)
    results: list[dict[str, Any]] = []
    processed = 0
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        sample_id = str(sample.get("sample_id") or "")
        cached = done.get(sample_id)
        if cached is not None:
            results.append(cached)
            continue
        if limit is not None and processed >= limit:
            continue
        results.append(review_sample(client, sample, table))
        processed += 1
    return results


def build_document(
    *,
    paper_id: str,
    article_id: str,
    source_draft: str,
    sentence_table: str,
    pdf_path: str,
    samples: list[dict[str, Any]],
    generated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_id": paper_id,
        "article_id": article_id,
        "source_draft": source_draft,
        "sentence_table": sentence_table,
        "pdf_path": pdf_path,
        "generated_at": generated_at or _now(),
        "sample_count": len(samples),
        "samples": samples,
    }


def run_review(
    client: Any,
    draft_path: str | Path,
    sentence_path: str | Path,
    output_path: str | Path,
    *,
    pdf_path: str | Path | None = None,
    project_root: str | Path | None = None,
    force: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """读初稿和句表，把审核结果写入新的 recall_review 文件。"""
    draft_file = Path(draft_path)
    sentence_file = Path(sentence_path)
    output_file = Path(output_path)
    _reject_protected_output(output_file, draft_file, sentence_file)

    draft = load_draft(draft_file)
    table = load_sentence_table(sentence_file)
    existing = _load_existing(output_file, force=force)
    raw_samples = draft.get("samples") or []
    if not isinstance(raw_samples, list):
        raise ValueError("召回初稿的 samples 必须是数组: %s" % draft_file)
    samples = review_samples(
        client,
        raw_samples,
        table,
        existing=existing,
        force=force,
        limit=limit,
    )
    root = Path(project_root) if project_root is not None else None
    paper_id = str(draft.get("paper_id") or "")
    if pdf_path is None and paper_id:
        pdf_path = "data/papers/%s.pdf" % paper_id
    document = build_document(
        paper_id=paper_id,
        article_id=str(draft.get("article_id") or article_id_from_draft_name(draft_file.name)),
        source_draft=_display_path(draft_file, root),
        sentence_table=_display_path(sentence_file, root),
        pdf_path=_display_path(Path(pdf_path), root) if pdf_path else "",
        samples=samples,
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return document


def clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return round(max(0.0, min(1.0, number)), 4)


def _review_sample(
    client: Any,
    sample_id: str,
    claim: str,
    sample: dict[str, Any],
    table: SentenceTable,
) -> dict[str, Any]:
    candidates = parse_candidates(sample)
    if not candidates:
        return _result(sample_id, claim, [], "no_evidence", _ZERO_RECALL)

    chosen = _select_anchor_candidates(client, claim, candidates)
    windows: list[ContextWindow] = []
    for candidate in chosen:
        located, _how = locate_anchor(table, candidate["sentence_id"], candidate["text"])
        if located is None:
            continue
        sentence_ids = window_sentence_ids(table, located)
        if not sentence_ids:
            continue
        windows.append(ContextWindow(sentence_ids=sentence_ids, anchor_ids=[located]))
    if not windows:
        return _result(sample_id, claim, [], "no_evidence", _UNLOCATED)

    merged = merge_overlapping_windows(windows)
    extracted = _extract_windows(client, claim, merged, table)
    integrated = integrate_spans(extracted, table.order_index)
    if not integrated:
        return _result(sample_id, claim, [], "no_evidence", _NONE_IN_WINDOW)
    return _synthesize(client, sample_id, claim, integrated, table)


def _select_anchor_candidates(
    client: Any,
    claim: str,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ranked = sorted(candidates, key=_rank_key)
    with_id: list[dict[str, Any]] = []
    seen: set[int] = set()
    for candidate in ranked:
        sentence_id = candidate["sentence_id"]
        if sentence_id is None or sentence_id in seen:
            continue
        seen.add(sentence_id)
        with_id.append(candidate)
    if not with_id:
        return [candidate for candidate in ranked if candidate["text"].strip()][:MAX_ANCHORS]
    if len(with_id) <= MAX_ANCHORS:
        return with_id

    payload = {
        "claim": claim,
        "candidates": [
            {
                "rank": candidate["rank"],
                "sentence_id": candidate["sentence_id"],
                "text": candidate["text"],
                "text_zh": candidate["text_zh"],
            }
            for candidate in with_id
        ],
    }
    raw = client.ask_json(json.dumps(payload, ensure_ascii=False), system=_ANCHOR_SYSTEM)
    model_ids = _anchor_ids_from_response(raw)
    allowed = [int(candidate["sentence_id"]) for candidate in with_id]
    picked = fill_anchor_ids(allowed, model_ids)
    by_id: dict[int, dict[str, Any]] = {}
    for candidate in with_id:
        by_id.setdefault(int(candidate["sentence_id"]), candidate)
    return [by_id[sentence_id] for sentence_id in picked]


def _extract_windows(
    client: Any,
    claim: str,
    windows: list[ContextWindow],
    table: SentenceTable,
) -> list[EvidenceSpan]:
    def run(window: ContextWindow) -> list[EvidenceSpan] | Exception:
        try:
            return _extract_one_window(client, claim, window, table)
        except Exception as exc:
            return exc

    workers = min(MAX_ANCHORS, len(windows))
    groups = client.map_parallel(run, windows, max_workers=workers)
    spans: list[EvidenceSpan] = []
    errors: list[Exception] = []
    successes = 0
    for group in groups:
        if isinstance(group, Exception):
            errors.append(group)
            continue
        successes += 1
        spans.extend(group)
    if successes == 0 and errors:
        raise errors[0]
    return spans


def _extract_one_window(
    client: Any,
    claim: str,
    window: ContextWindow,
    table: SentenceTable,
) -> list[EvidenceSpan]:
    payload = {
        "claim": claim,
        "window": [
            {"sentence_id": sentence_id, "text": table.by_id[sentence_id].text}
            for sentence_id in window.sentence_ids
            if sentence_id in table.by_id
        ],
    }
    raw = client.ask_json(json.dumps(payload, ensure_ascii=False), system=_EXTRACT_SYSTEM)
    return parse_extracted_spans(raw, window, table)


def _synthesize(
    client: Any,
    sample_id: str,
    claim: str,
    spans: list[EvidenceSpan],
    table: SentenceTable,
) -> dict[str, Any]:
    payload = {
        "claim": claim,
        "candidates": [
            {
                "index": index,
                "sentence_id_start": span.sentence_ids[0],
                "sentence_id_end": span.sentence_ids[-1],
                "evidence_text": evidence_text_for(table, span.sentence_ids),
            }
            for index, span in enumerate(spans)
        ],
    }
    raw = client.ask_json(json.dumps(payload, ensure_ascii=False), system=_FINAL_SYSTEM)
    result = _apply_final(raw, sample_id, claim, spans, table)
    if result["evidences"] or not _explanation_claims_support(result["explanation"]):
        return result
    retry_payload = {
        "claim": claim,
        "candidates": payload["candidates"],
        "previous_explanation": result["explanation"],
    }
    retried = client.ask_json(
        json.dumps(retry_payload, ensure_ascii=False),
        system=_FINAL_RETRY_SYSTEM,
    )
    result = _apply_final(retried, sample_id, claim, spans, table)
    if not result["evidences"] and _explanation_claims_support(result["explanation"]):
        result["explanation"] = _FINAL_REJECTED
    return result


def _apply_final(
    raw: Any,
    sample_id: str,
    claim: str,
    spans: list[EvidenceSpan],
    table: SentenceTable,
) -> dict[str, Any]:
    selected = raw.get("selected") if isinstance(raw, dict) else None
    if not isinstance(selected, list):
        selected = []
    evidences: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in selected:
        if not isinstance(item, dict):
            continue
        index = _as_int(item.get("index"))
        if index is None or index < 0 or index >= len(spans) or index in seen:
            continue
        seen.add(index)
        span = spans[index]
        sentence_ids = _ids_within_span(span, item, table)
        if not sentence_ids:
            continue
        if item.get("confidence") is None:
            confidence = span.confidence
        else:
            confidence = clamp_confidence(item.get("confidence"))
        evidences.append(
            {
                "evidence_text": evidence_text_for(table, sentence_ids),
                "sentence_id_start": sentence_ids[0],
                "sentence_id_end": sentence_ids[-1],
                "confidence": confidence,
                "anchor_source": list(span.anchor_source),
            }
        )
    explanation = str(raw.get("explanation") or "").strip() if isinstance(raw, dict) else ""
    if not evidences:
        return _result(
            sample_id,
            claim,
            [],
            "no_evidence",
            explanation or _FINAL_REJECTED,
        )
    verdict = str(raw.get("verdict") or "").strip().lower() if isinstance(raw, dict) else ""
    if verdict not in ("supported", "partial"):
        verdict = "partial"
        explanation = explanation or _INVALID_VERDICT
    elif verdict == "partial" and not explanation:
        explanation = _PARTIAL_FALLBACK
    elif verdict == "supported" and not explanation:
        explanation = _SUPPORTED_FALLBACK
    if verdict == "supported" and _explanation_admits_gap(explanation):
        verdict = "partial"
    return _result(sample_id, claim, evidences, verdict, explanation)


def _ids_within_span(span: EvidenceSpan, item: dict[str, Any], table: SentenceTable) -> list[int]:
    """把终版区间收进候选内部。未给区间则保留整段；给了但落在候选外则丢弃。"""
    start = _as_nonneg_int(item.get("sentence_id_start"))
    end = _as_nonneg_int(item.get("sentence_id_end"))
    if start is None or end is None:
        return list(span.sentence_ids)
    if end < start:
        start, end = end, start
    chosen = [sentence_id for sentence_id in span.sentence_ids if start <= sentence_id <= end]
    runs = contiguous_runs(chosen, table.order_index)
    if len(runs) == 1:
        return runs[0]
    return []


_GAP_PHRASES = (
    "缺",
    "未写出",
    "没有写出",
    "未提及",
    "未出现",
    "未说明",
    "不能支撑",
    "不足以",
)


def _explanation_admits_gap(text: str) -> bool:
    """说明承认原文没写全观点句。此时不能维持 supported。"""
    return any(phrase in text for phrase in _GAP_PHRASES)


def _explanation_claims_support(text: str) -> bool:
    """说明在论证候选能够支撑，但选中列表却是空的。"""
    if len(text) < 40:
        return False
    negative = ("不能支撑", "无法支撑", "没有支撑", "不足以支撑", "并不支撑", "未写出")
    positive = ("直接支撑", "完整支撑", "共同覆盖", "精准对应", "能够支撑", "可以支撑")
    if any(phrase in text for phrase in negative) and not any(
        phrase in text for phrase in positive
    ):
        return False
    return any(phrase in text for phrase in positive) or ("支撑" in text and "候选" in text)


def _result(
    sample_id: str,
    claim: str,
    evidences: list[dict[str, Any]],
    verdict: str,
    explanation: str,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "sample_id": sample_id,
        "claim": claim,
        "evidences": evidences,
        "verdict": verdict,
        "explanation": explanation,
        "need_human_review": verdict != "supported",
    }
    if error:
        record["error"] = error
    return record


def _ids_from_span_item(
    item: dict[str, Any],
    window_ids: list[int],
    window_set: set[int],
) -> list[int]:
    explicit = item.get("sentence_ids")
    if isinstance(explicit, list):
        selected: list[int] = []
        for value in explicit:
            sentence_id = _as_nonneg_int(value)
            if sentence_id is not None and sentence_id in window_set:
                selected.append(sentence_id)
        if selected:
            return selected
    start = _as_nonneg_int(item.get("sentence_id_start"))
    end = _as_nonneg_int(item.get("sentence_id_end"))
    if start is None or end is None:
        return []
    if end < start:
        start, end = end, start
    return [sentence_id for sentence_id in window_ids if start <= sentence_id <= end]


def _anchor_ids_from_response(raw: Any) -> list[int]:
    if not isinstance(raw, dict):
        return []
    values = raw.get("anchor_sentence_ids")
    if not isinstance(values, list):
        return []
    parsed: list[int] = []
    for value in values:
        sentence_id = _as_nonneg_int(value)
        if sentence_id is not None:
            parsed.append(sentence_id)
    return parsed


def _successful_by_id(existing: dict[str, Any] | None, *, force: bool) -> dict[str, dict[str, Any]]:
    if force or not isinstance(existing, dict):
        return {}
    found: dict[str, dict[str, Any]] = {}
    samples = existing.get("samples") or []
    if not isinstance(samples, list):
        return {}
    for sample in samples:
        if not isinstance(sample, dict) or sample.get("error"):
            continue
        sample_id = str(sample.get("sample_id") or "")
        verdict = sample.get("verdict")
        if not sample_id or verdict not in ("supported", "partial", "no_evidence"):
            continue
        found[sample_id] = sample
    return found


def _load_existing(path: Path, *, force: bool) -> dict[str, Any] | None:
    if force or not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("已有召回审核文件不是 JSON 对象: %s" % path)
    return data


def _reject_protected_output(output_file: Path, draft_file: Path, sentence_file: Path) -> None:
    output = output_file.resolve()
    if output == draft_file.resolve():
        raise ValueError("不能覆盖召回初稿: %s" % output_file)
    if output == sentence_file.resolve():
        raise ValueError("不能覆盖句表: %s" % output_file)


def _display_path(path: Path, root: Path | None) -> str:
    if not path.is_absolute():
        return path.as_posix()
    if root is not None:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return path.resolve().as_posix()


def _rank_key(candidate: dict[str, Any]) -> tuple[int, int]:
    rank = candidate.get("rank")
    if isinstance(rank, int):
        return (rank, 0)
    return (10**9, 0)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value).strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return None


def _as_nonneg_int(value: Any) -> int | None:
    number = _as_int(value)
    if number is None or number < 0:
        return None
    return number


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
