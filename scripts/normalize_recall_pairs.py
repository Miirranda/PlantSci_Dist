#!/usr/bin/env python3
"""清洗已完成召回审核的观点句—论文原句对。

规则部分在本地完成：引用编号清洗、人工段落切句。
角色建议由千问 API 生成。同一次调用里，由模型判断该句是否需要专有名词对照表，
以及需要哪些名词；不需要则不写对照。脚本不自行决定对照范围。

角色只作为建议写入 ``recall_normalization``，不从证据里删句。
背景句要等人工确认后才退出失真比对。

用法::

    python scripts/normalize_recall_pairs.py --papers P011 P012
    python scripts/normalize_recall_pairs.py --papers P011 --skip-api
    python scripts/normalize_recall_pairs.py --papers P011 P012 --force
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
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

from hallu.config import QWEN_MODEL, ensure_env  # noqa: E402

ensure_env()

from api_client import QwenClient, build_messages, extract_json  # noqa: E402
from api_client.exceptions import APIClientError  # noqa: E402

_DASH = r"[\u2013\u2014\-]"
# 至少 4 个字母的单词，或右括号，后面粘着逗号分隔的引用编号，可再带一个破折号区间。
_COMMA_CITE = re.compile(
    rf"(?P<prefix>[A-Za-z]{{4,}}|\))"
    rf"(?P<cite>\d{{1,3}}(?:\s*[,，]\s*\d{{1,3}})+(?:\s*{_DASH}\s*\d{{1,3}})?)"
    rf"(?![A-Za-z0-9%])"
)
_PAREN_SINGLE = re.compile(
    r"(?<=\))(?P<cite>\d{1,3})(?=\s|[,.;:，。；）)]|$)"
)
# 短符号 + 逗号编号（P1,2、PAD430,31）和纯破折号编号（S8-4、33–35）只进队列。
_SHORT_COMMA = re.compile(
    rf"(?<![A-Za-z])(?P<prefix>[A-Za-z]{{1,3}})"
    rf"(?P<cite>\d{{1,3}}(?:\s*[,，]\s*\d{{1,3}})+)"
    rf"(?![A-Za-z0-9%])"
)
_HYPHEN_WORD = re.compile(
    rf"(?<![A-Za-z])(?P<prefix>[A-Za-z]{{1,40}})"
    rf"(?P<cite>\d{{1,3}}\s*{_DASH}\s*\d{{1,3}})"
    rf"(?![A-Za-z0-9%])"
)
_HYPHEN_PAREN = re.compile(
    rf"(?P<prefix>\))"
    rf"(?P<cite>\d{{1,3}}\s*{_DASH}\s*\d{{1,3}})"
    rf"(?![A-Za-z0-9%])"
)
_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+$")
_CLAIM_PAIR = re.compile(
    r"([\u4e00-\u9fff·αβ/]{2,20})[（(]([^）)]{1,80})[）)]"
)
_EVIDENCE_DEF = re.compile(
    r"([A-Za-z][A-Za-z][A-Za-z \-]{1,70}?)\s*\(([A-Z][A-Z0-9]{1,12})\)"
)
_ABBR = re.compile(r"\b([A-Z][A-Z0-9]{1,12})\b")
_ROLES = frozenset(("support", "qualifier", "background", "unsure"))
_CONF = frozenset(("high", "medium", "low"))

_SYSTEM_PROMPT = """你是植物科学论文的证据句角色标注助手。你只能依据给定的观点句和已编号的论文原句作答，不能改写这些句子。

先做角色标注，再判断要不要专有名词对照表。对照表不是每句都有。

1. 证据句角色。每个 unit_id 恰好标注一次：
   - support：该句陈述了观点句所依赖的命题。
   - qualifier：条件、范围、不确定性或负结果。这类句子即使不直接支持观点，也必须标为 qualifier，不能标为 background。
   - background：与这条观点无关的实验细节。
   - unsure：无法判断。
   若干句合在一起才支撑观点时，把这些 unit_id 放进同一个 support_groups 元素。单句即可支撑的，单独成组。qualifier、background、unsure 不要放进 support_groups。

2. 专有名词对照。在标完角色之后判断：做失真判断时，会不会因为「论文用缩写或拉丁名，观点句用另一个解释性说法」而把换叫法误当成内容变化。
   - 会，则 term_table_needed 为 true，并且 term_links 里只放需要对照的那几个名词。
   - 不会，则 term_table_needed 为 false，term_links 和 term_gaps 都是空数组。
   - 普通中英对译不要写入，例如铁/iron、结瘤/nodulation、固氮酶/nitrogenase、大豆/soybean。
   - 观点句里已经写出同一个缩写或拉丁名时，不要再为它写对照。
   - claim_surface 必须是观点句里的连续原文，paper_surface 必须是论文原句里的连续原文。
   - 拿不准的名词不要写入 term_links，改写入 term_gaps，并令 unsure 为 true。

只返回一个 JSON 对象，不要 Markdown，不要解释。格式：
{
  "units": [
    {"unit_id": "m0s0", "role": "support", "confidence": "high", "reason": "一句话说明"}
  ],
  "support_groups": [["m0s0"]],
  "term_table_needed": false,
  "term_links": [
    {"claim_surface": "转座元件", "paper_surface": "transposable elements", "paper_abbr": "TEs"}
  ],
  "term_gaps": [
    {"claim_surface": "某个拿不准的说法", "unsure": true, "reason": "无法确定对应论文中的哪一个缩写"}
  ]
}
confidence 只允许 high、medium、low。role 只允许 support、qualifier、background、unsure。"""


def _load_segment_english():
    path = _PROJECT_ROOT / "arag-main" / "retrieval_adaptor" / "pdf_ingest.py"
    spec = importlib.util.spec_from_file_location("pdf_ingest_for_norm", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载分句模块: %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.segment_english


segment_english = _load_segment_english()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(not (end <= left or start >= right) for left, right in occupied)


def _context(text: str, start: int, end: int) -> str:
    left = max(0, start - 24)
    right = min(len(text), end + 16)
    return text[left:right].replace("\n", " ")


def analyze_citations(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回 (自动删除片段, 人工队列片段)。片段坐标相对原文。"""
    auto: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for match in _COMMA_CITE.finditer(text):
        cite = re.sub(r"\s+", "", match.group("cite"))
        if _THOUSANDS.fullmatch(cite):
            continue
        span = (match.start("cite"), match.end("cite"))
        occupied.append(span)
        auto.append(
            {
                "rule": "comma_cite",
                "removed": match.group("cite"),
                "start": span[0],
                "end": span[1],
                "context": _context(text, span[0], span[1]),
            }
        )
    for match in _PAREN_SINGLE.finditer(text):
        span = (match.start("cite"), match.end("cite"))
        if _overlaps(span, occupied):
            continue
        occupied.append(span)
        auto.append(
            {
                "rule": "paren_cite",
                "removed": match.group("cite"),
                "start": span[0],
                "end": span[1],
                "context": _context(text, span[0], span[1]),
            }
        )
    queue: list[dict[str, Any]] = []
    for rule, pattern in (
        ("hyphen_number", _HYPHEN_WORD),
        ("hyphen_number", _HYPHEN_PAREN),
        ("short_stem_comma", _SHORT_COMMA),
    ):
        for match in pattern.finditer(text):
            span = (match.start("cite"), match.end("cite"))
            if _overlaps(span, occupied):
                continue
            queue.append(
                {
                    "rule": rule,
                    "surface": match.group("prefix") + match.group("cite"),
                    "start": span[0],
                    "end": span[1],
                    "context": _context(text, match.start("prefix"), span[1]),
                }
            )
    return auto, queue


def apply_citation_edits(text: str, edits: list[dict[str, Any]]) -> str:
    updated = text
    for edit in sorted(edits, key=lambda item: item["start"], reverse=True):
        updated = updated[: edit["start"]] + updated[edit["end"] :]
    return updated


def _word_in(text: str, abbr: str) -> bool:
    if not abbr:
        return False
    return re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(abbr), text) is not None


def rule_term_links(claim: str, evidence_text: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """括号里已经写明、且论文原句里能对上的术语对。返回 (links, glossary)。"""
    glossary: list[dict[str, str]] = []
    seen_def: set[tuple[str, str]] = set()
    for match in _EVIDENCE_DEF.finditer(evidence_text):
        full = re.sub(r"\s+", " ", match.group(1)).strip(" -")
        abbr = match.group(2)
        key = (full.casefold(), abbr)
        if key in seen_def or len(full) < 3:
            continue
        seen_def.add(key)
        glossary.append({"en_full": full, "abbr": abbr})

    links: list[dict[str, Any]] = []
    seen_link: set[tuple[str, str, str]] = set()
    evidence_cf = evidence_text.casefold()
    for match in _CLAIM_PAIR.finditer(claim):
        surface = match.group(1).strip()
        inside = match.group(2).strip()
        if "的" in surface:
            surface = surface.rsplit("的", 1)[-1].strip()
        if len(surface) < 2 or not re.search(r"[A-Za-z]", inside):
            continue
        abbrs = _ABBR.findall(inside)
        abbr = ""
        for candidate in abbrs:
            if _word_in(evidence_text, candidate):
                abbr = candidate
                break
        latin = re.sub(r"\([^)]*\)", " ", inside)
        latin = re.sub(r"\b[A-Z][A-Z0-9]{1,12}\b", " ", latin)
        latin = re.sub(r"[,，;；/]+", " ", latin)
        latin = re.sub(r"\s+", " ", latin).strip(" .")
        paper_surface = ""
        if len(latin) >= 3 and latin.casefold() in evidence_cf:
            paper_surface = latin
        elif abbr:
            for item in glossary:
                if item["abbr"] == abbr:
                    paper_surface = item["en_full"]
                    break
            if not paper_surface:
                paper_surface = abbr
        if not paper_surface and not abbr:
            continue
        key = (surface, paper_surface.casefold(), abbr)
        if key in seen_link:
            continue
        seen_link.add(key)
        links.append(
            {
                "claim_surface": surface,
                "paper_surface": paper_surface,
                "paper_abbr": abbr,
                "relation": "same_referent",
                "source": "rule",
            }
        )
    return links, glossary


def _is_dropped(rec: dict[str, Any]) -> bool:
    if rec.get("weak_evidence"):
        return True
    return str(rec.get("review_decision") or "").strip().lower() == "drop"


def _claim_zh(sample: dict[str, Any], rec: dict[str, Any]) -> str:
    edited = str(rec.get("claim_zh") or "").strip()
    if edited:
        return edited
    return str(sample.get("claim_zh") or "").strip()


def load_sentence_table(paper_id: str) -> dict[int, str]:
    csv_path = (
        _PROJECT_ROOT / "data" / "annotations" / paper_id / ("%s_sentences.csv" % paper_id)
    )
    index: dict[int, str] = {}
    if not csv_path.exists():
        return index
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            sid = (row.get("sentence_id") or "").strip()
            if sid.isdigit():
                index[int(sid)] = (row.get("text") or "").strip()
    return index


def _evidence_map(sample: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    found: dict[int, list[dict[str, Any]]] = {}
    retrieval = sample.get("system_retrieval") or {}
    for key in ("review_evidences", "classify_evidences"):
        for item in retrieval.get(key) or []:
            if not isinstance(item, dict) or item.get("sentence_id") is None:
                continue
            found.setdefault(int(item["sentence_id"]), []).append(item)
    return found


def _clean_inplace(container: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """清洗 container['text']。原文留在 text_before_citation_clean。"""
    current = str(container.get("text") or "")
    original = str(container.get("text_before_citation_clean") or current)
    edits, queue = analyze_citations(original)
    cleaned = apply_citation_edits(original, edits)
    if cleaned != original:
        container["text_before_citation_clean"] = original
        container["text"] = cleaned
    elif "text_before_citation_clean" not in container:
        container["text"] = original
    zh = str(container.get("text_zh") or "")
    if zh and edits and "text_zh_before_citation_clean" not in container:
        updated_zh = zh
        for edit in edits:
            updated_zh = re.sub(
                r"(?<=[\u4e00-\u9fff])\s*%s" % re.escape(edit["removed"]),
                "",
                updated_zh,
            )
        if updated_zh != zh:
            container["text_zh_before_citation_clean"] = zh
            container["text_zh"] = updated_zh
    return cleaned, edits, queue


def _units_for_record(
    rec: dict[str, Any],
    sample: dict[str, Any],
    sentence_table: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """返回 (units, auto_edits, queue)。units 的 text 已是清洗后的句子。"""
    units: list[dict[str, Any]] = []
    auto_edits: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = []
    evidence_map = _evidence_map(sample)
    snapshots = []
    seen_ids: set[int] = set()
    for raw_id in rec.get("gold_sentence_ids") or []:
        try:
            sid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        holders = evidence_map.get(sid) or []
        if holders:
            cleaned, edits, queued = _clean_inplace(holders[0])
            for other in holders[1:]:
                if edits:
                    other["text_before_citation_clean"] = str(
                        other.get("text_before_citation_clean") or other.get("text") or ""
                    )
                    other["text"] = cleaned
            source = "draft"
        else:
            original = sentence_table.get(sid) or ""
            if not original:
                queue.append(
                    {
                        "rule": "missing_text",
                        "surface": "sentence_id=%s" % sid,
                        "context": "草稿和句表都没有这句原文",
                    }
                )
                continue
            snapshot = {"sentence_id": sid, "text": original, "source": "sentences_csv"}
            cleaned, edits, queued = _clean_inplace(snapshot)
            snapshots.append(snapshot)
            source = "sentences_csv"
        for edit in edits:
            auto_edits.append({"where": "gold:%s" % sid, **edit})
        for item in queued:
            queue.append({"where": "gold:%s" % sid, **item})
        units.append(
            {
                "unit_id": "g%s" % sid,
                "source": source,
                "sentence_id": sid,
                "text": cleaned,
            }
        )
    if snapshots:
        rec["gold_sentence_snapshots"] = snapshots

    manuals = []
    for index, paragraph in enumerate(rec.get("manual_retrieved_paragraphs") or []):
        if not isinstance(paragraph, dict):
            continue
        text = str(paragraph.get("text") or "").strip()
        if not text and not paragraph.get("text_before_citation_clean"):
            continue
        cleaned, edits, queued = _clean_inplace(paragraph)
        for edit in edits:
            auto_edits.append({"where": "manual:%s" % index, **edit})
        for item in queued:
            queue.append({"where": "manual:%s" % index, **item})
        sentences = segment_english(cleaned) or ([cleaned] if cleaned else [])
        for sent_index, sentence in enumerate(sentences):
            units.append(
                {
                    "unit_id": "m%ss%s" % (index, sent_index),
                    "source": "manual",
                    "manual_index": index,
                    "sent_index": sent_index,
                    "text": sentence,
                }
            )
        manuals.append(index)
    return units, auto_edits, queue


def _build_user(claim: str, units: list[dict[str, Any]]) -> str:
    payload = {
        "claim_zh": claim,
        "units": [{"unit_id": item["unit_id"], "text": item["text"]} for item in units],
    }
    return json.dumps(payload, ensure_ascii=False)


def _call_api(client: QwenClient, user: str, model: str) -> dict[str, Any]:
    messages = build_messages(user, system=_SYSTEM_PROMPT)
    kwargs: dict[str, Any] = {
        "temperature": 0.0,
        "model": model,
        "max_tokens": 4096,
        "timeout": 180.0,
    }
    try:
        result = client.chat(messages, response_format={"type": "json_object"}, **kwargs)
    except APIClientError as exc:
        if getattr(exc, "status_code", None) != 400:
            raise
        result = client.chat(messages, **kwargs)
    parsed = extract_json(result.content)
    parsed["_model"] = result.model
    return parsed


def _validate_api(
    raw: dict[str, Any],
    claim: str,
    units: list[dict[str, Any]],
    evidence_text: str,
) -> dict[str, Any]:
    by_id = {item["unit_id"]: item for item in units}
    evidence_cf = evidence_text.casefold()
    needed_raw = raw.get("term_table_needed")
    if isinstance(needed_raw, str):
        term_table_needed = needed_raw.strip().lower() in {"true", "yes", "1"}
    else:
        term_table_needed = bool(needed_raw)
    links: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    if term_table_needed:
        for item in raw.get("term_gaps") or []:
            if not isinstance(item, dict):
                continue
            gaps.append(
                {
                    "claim_surface": str(item.get("claim_surface") or "").strip(),
                    "unsure": True,
                    "reason": str(item.get("reason") or "").strip(),
                    "written": False,
                }
            )
    link_items = (raw.get("term_links") or []) if term_table_needed else []
    for item in link_items:
        if not isinstance(item, dict):
            continue
        surface = str(item.get("claim_surface") or "").strip()
        paper_surface = re.sub(r"\s+", " ", str(item.get("paper_surface") or "")).strip()
        abbr = str(item.get("paper_abbr") or "").strip()
        unsure = bool(item.get("unsure"))
        if unsure or not surface or surface not in claim:
            gaps.append(
                {
                    "claim_surface": surface,
                    "unsure": True,
                    "reason": "模型标为拿不准，或说法不在观点句中",
                    "written": False,
                }
            )
            continue
        surface_ok = len(paper_surface) >= 3 and paper_surface.casefold() in evidence_cf
        abbr_ok = _word_in(evidence_text, abbr) if abbr else False
        if not surface_ok and not abbr_ok:
            gaps.append(
                {
                    "claim_surface": surface,
                    "unsure": True,
                    "reason": "论文原句中找不到对应英文",
                    "written": False,
                }
            )
            continue
        if abbr and not abbr_ok:
            abbr = ""
        if not surface_ok:
            paper_surface = abbr
        links.append(
            {
                "claim_surface": surface,
                "paper_surface": paper_surface,
                "paper_abbr": abbr,
                "relation": "same_referent",
                "source": "api",
            }
        )

    roles: dict[str, dict[str, Any]] = {}
    for item in raw.get("units") or []:
        if not isinstance(item, dict):
            continue
        unit_id = str(item.get("unit_id") or "").strip()
        if unit_id not in by_id:
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in _ROLES:
            role = "unsure"
        confidence = str(item.get("confidence") or "").strip().lower()
        if confidence not in _CONF:
            confidence = "low"
        roles[unit_id] = {
            "role": role,
            "confidence": confidence,
            "reason": str(item.get("reason") or "").strip(),
            "needs_human": role == "unsure" or confidence == "low",
        }
    for unit_id in by_id:
        roles.setdefault(
            unit_id,
            {
                "role": "unsure",
                "confidence": "low",
                "reason": "模型没有返回这个句子的角色",
                "needs_human": True,
            },
        )

    groups: list[list[str]] = []
    for group in raw.get("support_groups") or []:
        if not isinstance(group, list):
            continue
        kept = [
            str(unit_id)
            for unit_id in group
            if str(unit_id) in roles and roles[str(unit_id)]["role"] == "support"
        ]
        if kept:
            groups.append(kept)
    if term_table_needed and not links and not gaps:
        term_table_needed = False
    return {
        "term_table_needed": term_table_needed,
        "term_links": links,
        "term_gaps": gaps,
        "roles": roles,
        "support_groups": groups,
    }


_SPECIES = re.compile(r"(?<![A-Za-z])([A-Z]\. [a-z]{3,})")
_GENE_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Za-z]*\d+[A-Za-z0-9]*)(?![A-Za-z0-9])")


def _token_in(text: str, token: str) -> bool:
    if not token:
        return False
    return re.search(
        r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(token),
        text,
    ) is not None


def missing_name_tokens(evidence_text: str, claim: str) -> list[str]:
    """论文原句里有、观点句里没有的缩写、基因符号、物种拉丁名。"""
    found: list[str] = []
    seen: set[str] = set()
    patterns = (_ABBR, _SPECIES, _GENE_TOKEN)
    for pattern in patterns:
        for match in pattern.finditer(evidence_text):
            token = match.group(1)
            if token in seen or _token_in(claim, token):
                continue
            seen.add(token)
            found.append(token)
    return found


def _link_tokens(link: dict[str, Any]) -> list[str]:
    surface = link.get("paper_surface") or ""
    tokens = []
    abbr = (link.get("paper_abbr") or "").strip()
    if abbr:
        tokens.append(abbr)
    tokens.extend(_ABBR.findall(surface))
    tokens.extend(match.group(1) for match in _SPECIES.finditer(surface))
    tokens.extend(match.group(1) for match in _GENE_TOKEN.finditer(surface))
    return tokens


def keep_distortion_reference(link: dict[str, Any], claim: str, missing: set[str]) -> bool:
    """只保留失真判断需要的叫法对照，不保留普通中英对译。"""
    claim_surface = (link.get("claim_surface") or "").strip()
    paper_surface = (link.get("paper_surface") or "").strip()
    abbr = (link.get("paper_abbr") or "").strip()
    if not claim_surface or claim_surface not in claim:
        return False
    if abbr and _token_in(claim, abbr):
        return False
    if paper_surface and _token_in(claim, paper_surface):
        return False
    if claim_surface == abbr or claim_surface == paper_surface:
        return False
    return any(token in missing for token in _link_tokens(link))


def _merge_links(rule_links: list[dict[str, Any]], api_links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in rule_links + api_links:
        key = (
            item.get("claim_surface") or "",
            (item.get("paper_surface") or "").casefold(),
            item.get("paper_abbr") or "",
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _signature(units: list[dict[str, Any]]) -> list[list[str]]:
    return [[item["unit_id"], item["text"]] for item in units]


def atomic_write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp.replace(path)


def process_paper(
    paper_id: str,
    *,
    client: QwenClient | None,
    model: str,
    force: bool,
) -> dict[str, Any]:
    draft_path = (
        _PROJECT_ROOT
        / "data"
        / "annotations"
        / paper_id
        / ("%s_A001_annotation_draft.json" % paper_id)
    )
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    samples = {item["sample_id"]: item for item in draft.get("samples") or []}
    sentence_table = load_sentence_table(paper_id)
    report: dict[str, Any] = {
        "paper_id": paper_id,
        "generated_at": _now(),
        "model": model if client else "",
        "claims": [],
        "auto_edit_count": 0,
        "queue_count": 0,
        "api_ok": 0,
        "api_failed": 0,
        "api_reused": 0,
    }
    reviews = draft.get("human_reviews") or {}
    for reviewer, records in reviews.items():
        if not isinstance(records, dict):
            continue
        for sample_id, rec in records.items():
            if not isinstance(rec, dict) or not rec.get("recall_reviewed") or _is_dropped(rec):
                continue
            sample = samples.get(sample_id) or {}
            claim = _claim_zh(sample, rec)
            units, edits, queue = _units_for_record(rec, sample, sentence_table)
            evidence_text = "\n".join(item["text"] for item in units)
            signature = _signature(units)
            previous = rec.get("recall_normalization") or {}
            reuse = (
                not force
                and previous.get("api_status") == "ok"
                and previous.get("signature") == signature
            )
            api_status = "skipped"
            api_error = ""
            api_links: list[dict[str, Any]] = []
            gaps: list[dict[str, Any]] = []
            term_table_needed = False
            roles: dict[str, dict[str, Any]] = {}
            groups: list[list[str]] = []
            used_model = ""
            if reuse and "term_table_needed" in previous:
                api_status = "ok"
                term_table_needed = bool(previous.get("term_table_needed"))
                api_links = list(previous.get("term_links") or [])
                gaps = list(previous.get("term_gaps") or [])
                groups = list(previous.get("support_groups") or [])
                for unit in previous.get("evidence_units") or []:
                    roles[unit["unit_id"]] = {
                        "role": unit.get("role") or "unsure",
                        "confidence": unit.get("confidence") or "low",
                        "reason": unit.get("reason") or "",
                        "needs_human": bool(unit.get("needs_human")),
                    }
                used_model = str(previous.get("model") or "")
                report["api_reused"] += 1
            elif client is None:
                api_status = "skipped"
            elif not units:
                api_status = "skipped_empty"
            else:
                try:
                    raw = _call_api(client, _build_user(claim, units), model)
                    used_model = str(raw.pop("_model", model))
                    checked = _validate_api(raw, claim, units, evidence_text)
                    term_table_needed = bool(checked["term_table_needed"])
                    api_links = checked["term_links"]
                    gaps = checked["term_gaps"]
                    roles = checked["roles"]
                    groups = checked["support_groups"]
                    api_status = "ok"
                    report["api_ok"] += 1
                except (APIClientError, ValueError, TypeError) as exc:
                    api_status = "failed"
                    api_error = str(exc)[:500]
                    report["api_failed"] += 1
                    print("API 失败 %s %s: %s" % (paper_id, sample_id, api_error))
            evidence_units = []
            for unit in units:
                role_info = roles.get(unit["unit_id"]) or {
                    "role": "",
                    "confidence": "",
                    "reason": "",
                    "needs_human": api_status != "ok",
                }
                evidence_units.append({**unit, **role_info})
            rec["recall_normalization"] = {
                "schema_version": "recall-norm-v1",
                "updated_at": _now(),
                "model": used_model,
                "api_status": api_status,
                "api_error": api_error,
                "signature": signature,
                "evidence_units": evidence_units,
                "support_groups": groups,
                "term_table_needed": term_table_needed,
                "term_links": api_links if term_table_needed else [],
                "term_gaps": gaps if term_table_needed else [],
                "citation_queue": queue,
                "background_removal": "pending_human",
            }
            report["auto_edit_count"] += len(edits)
            report["queue_count"] += len(queue)
            report["claims"].append(
                {
                    "sample_id": sample_id,
                    "reviewer": reviewer,
                    "api_status": api_status,
                    "unit_count": len(units),
                    "auto_edits": edits,
                    "citation_queue": queue,
                    "term_table_needed": term_table_needed,
                    "term_link_count": len(rec["recall_normalization"]["term_links"]),
                    "term_gap_count": len(gaps),
                }
            )
            print(
                "%s %s units=%d edits=%d queue=%d api=%s table=%s links=%d"
                % (
                    paper_id,
                    sample_id,
                    len(units),
                    len(edits),
                    len(queue),
                    api_status,
                    "yes" if term_table_needed else "no",
                    len(rec["recall_normalization"]["term_links"]),
                )
            )
            atomic_write(draft_path, draft)
    report_path = draft_path.with_name("%s_A001_recall_normalization_report.json" % paper_id)
    atomic_write(report_path, report)
    return report


def self_test() -> None:
    samples = {
        "all lines7,8,50.": "all lines.",
        "fixation1,30\u201333.": "fixation.",
        "fixation40,41.": "fixation.",
        "the sdg8 (efs-3)19 mutant": "the sdg8 (efs-3) mutant",
        "morphology (Fig. 3b)13.": "morphology (Fig. 3b).",
        "stage S8-4 and TN9-1 remain": "stage S8-4 and TN9-1 remain",
        "pathotypes P1,2 stay": "pathotypes P1,2 stay",
        "proteins PAD430,31 stay": "proteins PAD430,31 stay",
        "factor HSP70 and gene Ne1 and mark H3K36me3": "factor HSP70 and gene Ne1 and mark H3K36me3",
        "used 7,659 individuals": "used 7,659 individuals",
        "hexaploid33\u201335 stays": "hexaploid33\u201335 stays",
    }
    for original, expected in samples.items():
        edits, _queue = analyze_citations(original)
        cleaned = apply_citation_edits(original, edits)
        if cleaned != expected:
            raise AssertionError("%r -> %r, expected %r" % (original, cleaned, expected))
    _edits, queued = analyze_citations("stage S8-4 and pathotypes P1,2 and PAD430,31 and hexaploid33\u201335")
    rules = {item["rule"] for item in queued}
    if "hyphen_number" not in rules or "short_stem_comma" not in rules:
        raise AssertionError("队列未收录破折号编号或短符号: %r" % queued)
    print("引用清洗自测通过")


def main() -> None:
    parser = argparse.ArgumentParser(description="清洗 P011/P012 召回审核后的观点句与论文原句")
    parser.add_argument("--papers", nargs="+", default=["P011", "P012"])
    parser.add_argument("--skip-api", action="store_true", help="只做规则清洗和切句，不调用千问")
    parser.add_argument("--force", action="store_true", help="已有成功的 API 结果也重新请求")
    parser.add_argument("--model", default=QWEN_MODEL)
    parser.add_argument("--self-test-only", action="store_true")
    args = parser.parse_args()
    self_test()
    if args.self_test_only:
        return
    client = None if args.skip_api else QwenClient(verbose=False, model=args.model)
    for paper_id in args.papers:
        report = process_paper(paper_id, client=client, model=args.model, force=args.force)
        print(
            "%s 完成：观点句 %d，自动删引用 %d 处，人工队列 %d 处，API 成功 %d，复用 %d，失败 %d"
            % (
                paper_id,
                len(report["claims"]),
                report["auto_edit_count"],
                report["queue_count"],
                report["api_ok"],
                report["api_reused"],
                report["api_failed"],
            )
        )


if __name__ == "__main__":
    main()
