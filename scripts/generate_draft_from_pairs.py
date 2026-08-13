#!/usr/bin/env python3
"""从 claim_evidence_pairs.jsonl 调 API 生成标注草稿。

模型只打标签和写 analysis；system_retrieval / 金标原文 / schema 由脚本填写。
失败批次自动降为逐条；校验通过才落盘；按 sample_id 续跑。

用法:
  python scripts/generate_draft_from_pairs.py `
    --paper P001 --article A001 --source-type high_quality `
    --limit 10 --batch-size 3

  python scripts/generate_draft_from_pairs.py --limit 3 --batch-size 3 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import date
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

from hallu.config import (  # noqa: E402
    DISTORTION_LABELS,
    LEGACY_LABELS,
    LEVEL1_LABELS,
    NO_DISTORTION,
    QWEN_MODEL,
    SEVERITY_VALUES,
    TAXONOMY_VERSION,
    UNCOVERED_PHENOMENA,
    ensure_env,
    level1_of,
)

ensure_env()

from api_client import QwenClient, build_messages, extract_json  # noqa: E402
from api_client.exceptions import APIClientError  # noqa: E402

SCHEMA_VERSION = "1.2"
DEFAULT_PROMPT = (
    _PROJECT_ROOT / "data" / "annotations" / "prompts" / "draft_from_pairs_api.md"
)
VALID_LEVEL2 = frozenset(DISTORTION_LABELS) | {NO_DISTORTION}
LEGACY_SLUGS = frozenset(LEGACY_LABELS)
VALID_UNCOVERED = frozenset(UNCOVERED_PHENOMENA)
VALID_EVIDENCE_LEVELS = frozenset(("With_Evidence", "Weak_Evidence", "No_Evidence"))
VALID_SEVERITY = frozenset(SEVERITY_VALUES)
VALID_CONFIDENCE = frozenset(("high", "medium", "low"))
VALID_REVIEW_FOCUS = frozenset(
    (
        "evidence_level",
        "gold_sentence_ids",
        "rag_top5",
        "primary_label",
        "secondary_label",
        "noisy_retrieval",
        "composite_claim",
        "uncovered_phenomenon",
        "none",
    )
)
VALID_UNSUPPORTED = frozenset(
    ("not_applicable", "likely_retrieval_miss", "likely_claim_error", "uncertain")
)
_EL_MAP = {
    "with_evidence": "With_Evidence",
    "weak_evidence": "Weak_Evidence",
    "no_evidence": "No_Evidence",
    "with": "With_Evidence",
    "weak": "Weak_Evidence",
    "no": "No_Evidence",
}
_FENCE = re.compile(r"```(?:\w*)\n(.*?)```", re.DOTALL)
_DIRTY_AUTHOR = re.compile(
    r"\d{1,2},\d{1,2},\s+[A-Z][a-z]+|[A-Z][a-z]+ [A-Z]\.\s+\S+1,\d+"
)
_DIRTY_REF = re.compile(r"https?://doi\.org|^\s*(nature |science |pnas )", re.I)
_CITED_ID = re.compile(r"(?:sentence_id|id)\s*=\s*([\d/]+)", re.I)
GOLD_ID_MAX = 5


# ---------------------------------------------------------------------------
# 路径 / IO
# ---------------------------------------------------------------------------


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_pairs(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not obj.get("claim_id"):
                raise SystemExit("pairs 第 %d 行缺少 claim_id" % line_no)
            rows.append(obj)
    return rows


def load_sentence_table(path: Path | None) -> dict[int, str]:
    if path is None or not path.exists():
        return {}
    out: dict[int, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = row.get("sentence_id")
            if raw is None or raw == "":
                continue
            try:
                sid = int(raw)
            except ValueError:
                continue
            out[sid] = row.get("text") or ""
    return out


def load_existing_doc(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit("已有 output 不是 JSON 对象: %s" % path)
    return data


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------


def build_taxonomy_block() -> str:
    rows = [
        "| level1 | level2 | 中文 |",
        "|---|---|---|",
    ]
    for slug, info in DISTORTION_LABELS.items():
        l1 = info["level1"]
        zh = info["zh"]
        l1_zh = LEVEL1_LABELS.get(l1, {}).get("zh") or l1
        rows.append("| %s (%s) | %s | %s |" % (l1, l1_zh, slug, zh))
    rows.append("| — | %s | 无失真 |" % NO_DISTORTION)
    uncovered = " | ".join(sorted(UNCOVERED_PHENOMENA))
    legacy = ", ".join(sorted(LEGACY_LABELS))
    return (
        "taxonomy_version = %s\n\n" % TAXONOMY_VERSION
        + "\n".join(rows)
        + "\n\n8 类盖不住时 uncovered_phenomenon 仅允许: %s\n" % uncovered
        + "禁止旧标签（作为 primary_level2 / primary_type / level2 即非法）: %s\n" % legacy
    )


def load_system_prompt(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    fences = _FENCE.findall(text)
    body = fences[-1].strip() if fences else text.strip()
    block = build_taxonomy_block()
    if "{{TAXONOMY_BLOCK}}" in body:
        return body.replace("{{TAXONOMY_BLOCK}}", block)
    return body.rstrip() + "\n\n" + block


# ---------------------------------------------------------------------------
# pairs → 检索字段 / user 报文
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _norm_ev_list(items: Any, limit: int | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return out
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        sid = _as_int(item.get("sentence_id"))
        if sid is None:
            continue
        rank = _as_int(item.get("rank"))
        if rank is None:
            rank = i + 1
        out.append(
            {
                "rank": rank,
                "sentence_id": sid,
                "text": str(item.get("text") or ""),
            }
        )
        if limit is not None and len(out) >= limit:
            break
    return out


def split_evidences(pair: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    classify = _norm_ev_list(pair.get("classify_evidences"), 5)
    review = _norm_ev_list(pair.get("review_evidences"), 10)
    if not classify and not review:
        raw = _norm_ev_list(pair.get("evidences"), 10)
        classify = raw[:5]
        review = raw[:10]
    if not review and classify:
        review = list(classify)
    if not classify and review:
        classify = review[:5]
    return classify, review


def build_system_retrieval(pair: dict[str, Any]) -> dict[str, Any]:
    classify, review = split_evidences(pair)
    return {"classify_evidences": classify, "review_evidences": review}


def pool_index(pair: dict[str, Any]) -> dict[int, str]:
    idx: dict[int, str] = {}
    classify, review = split_evidences(pair)
    for ev in review + classify:
        sid = ev["sentence_id"]
        if sid not in idx:
            idx[sid] = ev.get("text") or ""
    for ev in _norm_ev_list(pair.get("evidences")):
        sid = ev["sentence_id"]
        if sid not in idx:
            idx[sid] = ev.get("text") or ""
    return idx


def build_user_item(pair: dict[str, Any]) -> dict[str, Any]:
    classify, review = split_evidences(pair)
    return {
        "claim_id": pair["claim_id"],
        "claim_zh": pair.get("claim_zh") or "",
        "classify_top5_ids": [e["sentence_id"] for e in classify],
        "review_evidences": review,
    }


def build_user_payload(
    items: list[dict[str, Any]],
    *,
    paper_id: str,
    article_id: str,
    source_type: str,
    retry_errors: list[dict[str, Any]] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "paper_id": paper_id,
        "article_id": article_id,
        "article_source_type": source_type,
        "note": (
            "review_evidences rank 1-5 = classify top-5; "
            "只输出 {\"samples\":[...]}；不要抄 text / system_retrieval。"
        ),
        "items": [build_user_item(p) for p in items],
    }
    if retry_errors:
        payload["retry_instructions"] = (
            "上轮输出不合法，请只修正下列 claim，仍只输出 {\"samples\":[...]}："
        )
        payload["retry_errors"] = retry_errors
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 模型输出规整
# ---------------------------------------------------------------------------


def _nullish(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in ("", "null", "none", "nil"):
        return True
    return False


def _as_bool(value: Any) -> bool | None:
    if _nullish(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "1"):
            return True
        if low in ("false", "no", "0"):
            return False
    return None


def _as_str(value: Any) -> str:
    if _nullish(value):
        return ""
    return str(value).strip()


def _normalize_evidence_level(value: Any) -> str:
    raw = _as_str(value)
    if not raw:
        return ""
    return _EL_MAP.get(raw.lower(), raw)


def _int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, (int, float)):
        return [int(value)]
    if not isinstance(value, list):
        return []
    out: list[int] = []
    seen: set[int] = set()
    for item in value:
        if isinstance(item, dict):
            sid = _as_int(item.get("sentence_id"))
        else:
            sid = _as_int(item)
        if sid is None or sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
    return out


def _extract_samples_blob(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []
    samples = raw.get("samples")
    if isinstance(samples, list):
        return samples
    if "claim_id" in raw or "primary_level2" in raw or "gold_classification" in raw:
        return [raw]
    return []


def coerce_model_sample(raw: Any) -> dict[str, Any]:
    """把模型各种写法收成紧凑中间结构。"""
    if not isinstance(raw, dict):
        return {}
    gold = raw.get("gold_retrieval") if isinstance(raw.get("gold_retrieval"), dict) else {}
    clf = (
        raw.get("gold_classification")
        if isinstance(raw.get("gold_classification"), dict)
        else {}
    )
    analysis = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {}

    ids = _int_list(
        raw.get("gold_sentence_ids")
        or raw.get("sentence_ids")
        or gold.get("sentence_ids")
        or gold.get("evidences")
    )

    primary = raw.get("primary_level2")
    if _nullish(primary):
        label = clf.get("primary_label") or raw.get("primary_label")
        if isinstance(label, dict):
            primary = label.get("level2")
        elif isinstance(label, str):
            primary = label
        else:
            primary = clf.get("primary_type") or raw.get("primary_type")
    primary = None if _nullish(primary) else _as_str(primary)

    secondary = raw.get("secondary_level2")
    if secondary is None:
        label = clf.get("secondary_label") or raw.get("secondary_label")
        if isinstance(label, dict):
            secondary = label.get("level2")
        elif isinstance(label, str):
            secondary = label
        else:
            secs = clf.get("secondary_types") or raw.get("secondary_types") or []
            secondary = secs[0] if secs else None
    secondary = None if _nullish(secondary) else _as_str(secondary)

    evidence_level = _normalize_evidence_level(
        raw.get("evidence_level") or clf.get("evidence_level")
    )
    has_distortion = raw.get("has_distortion")
    if has_distortion is None:
        has_distortion = clf.get("has_distortion")
    has_distortion = _as_bool(has_distortion)

    is_answerable = raw.get("is_answerable")
    if is_answerable is None:
        is_answerable = gold.get("is_answerable")
    is_answerable = _as_bool(is_answerable)

    severity = raw.get("severity")
    if _nullish(severity):
        severity = clf.get("severity")
    severity = None if _nullish(severity) else _as_str(severity)

    uncovered = _as_str(raw.get("uncovered_phenomenon") or clf.get("uncovered_phenomenon"))
    reason = _as_str(raw.get("reason") or clf.get("reason") or clf.get("reasoning"))

    rag = analysis.get("rag_review") if isinstance(analysis.get("rag_review"), dict) else {}
    unsup = (
        analysis.get("unsupported_diagnosis")
        if isinstance(analysis.get("unsupported_diagnosis"), dict)
        else {}
    )
    diffs = analysis.get("key_differences")
    if not isinstance(diffs, list):
        diffs = []

    return {
        "claim_id": _as_str(raw.get("claim_id")),
        "gold_sentence_ids": ids,
        "is_answerable": is_answerable,
        "evidence_level": evidence_level,
        "has_distortion": has_distortion,
        "primary_level2": primary,
        "secondary_level2": secondary,
        "severity": severity,
        "uncovered_phenomenon": uncovered,
        "reason": reason,
        "analysis": {
            "evidence_judgement": _as_str(analysis.get("evidence_judgement")),
            "classification_reason": _as_str(analysis.get("classification_reason")),
            "key_differences": diffs,
            "rag_review": {
                "top5_is_best": _as_bool(rag.get("top5_is_best")),
                "better_in_review_pool": _int_list(rag.get("better_in_review_pool")),
                "notes": _as_str(rag.get("notes")),
            },
            "unsupported_diagnosis": {
                "verdict": _as_str(unsup.get("verdict")) or "not_applicable",
                "reasoning": _as_str(unsup.get("reasoning")),
                "suggested_keywords": [
                    str(x) for x in (unsup.get("suggested_keywords") or []) if x
                ]
                if isinstance(unsup.get("suggested_keywords"), list)
                else [],
                "suggested_sentence_ranges": _as_str(unsup.get("suggested_sentence_ranges")),
            },
            "manual_check_hints": _as_str(analysis.get("manual_check_hints")),
            "needs_manual_review": bool(_as_bool(analysis.get("needs_manual_review")) or False),
            "review_focus": [
                str(x) for x in (analysis.get("review_focus") or []) if x
            ]
            if isinstance(analysis.get("review_focus"), list)
            else [],
            "ai_confidence": _as_str(analysis.get("ai_confidence")) or "medium",
        },
        "gold_needs_manual_review": bool(
            _as_bool(clf.get("needs_manual_review") or raw.get("needs_manual_review")) or False
        ),
    }


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def cited_sentence_ids(*texts: str) -> set[int]:
    found: set[int] = set()
    for text in texts:
        if not text:
            continue
        for match in _CITED_ID.finditer(str(text)):
            for part in match.group(1).split("/"):
                if part.isdigit():
                    found.add(int(part))
    return found


def is_dirty_sentence(sid: int, text: str) -> bool:
    if sid == 0:
        return True
    t = text or ""
    if _DIRTY_REF.search(t):
        return True
    if _DIRTY_AUTHOR.search(t) and len(t) < 280:
        return True
    if re.search(r"\b(Xun Liu|Jocelyn K\.)", t) and "inferior" not in t.lower():
        return True
    return False


def morphology_errors(item: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    level = item["evidence_level"]
    ids = item["gold_sentence_ids"]
    primary = item["primary_level2"]
    secondary = item["secondary_level2"]
    has_d = item["has_distortion"]
    answerable = item["is_answerable"]
    severity = item["severity"]

    if level not in VALID_EVIDENCE_LEVELS:
        errors.append("evidence_level 非法: %s" % (level or "(空)"))
        return errors

    if primary and primary in LEGACY_SLUGS:
        errors.append("使用了旧标签 %s，请改为 8 类之一或 no_distortion" % primary)
    if secondary and secondary in LEGACY_SLUGS:
        errors.append("secondary 使用了旧标签 %s" % secondary)
    if primary and primary not in VALID_LEVEL2 and primary not in LEGACY_SLUGS:
        errors.append("primary_level2 非法: %s" % primary)
    if secondary and secondary not in VALID_LEVEL2 and secondary not in LEGACY_SLUGS:
        errors.append("secondary_level2 非法: %s" % secondary)

    uncovered = item["uncovered_phenomenon"]
    if uncovered and uncovered not in VALID_UNCOVERED:
        errors.append("uncovered_phenomenon 非法: %s" % uncovered)

    if level == "With_Evidence":
        if answerable is not True:
            errors.append("With_Evidence 时 is_answerable 必须为 true")
        if not ids:
            errors.append("With_Evidence 时 gold_sentence_ids 不能为空")
        if has_d is True:
            if not primary or primary == NO_DISTORTION:
                errors.append("有失真时 primary_level2 必须是 8 类之一")
            elif primary in VALID_LEVEL2 and primary != NO_DISTORTION:
                pass
            if severity not in ("mild", "moderate", "severe"):
                errors.append("有失真时 severity 必须为 mild|moderate|severe")
            if secondary == primary:
                errors.append("secondary 不得与 primary 相同")
        elif has_d is False:
            if primary != NO_DISTORTION:
                errors.append("无失真时 primary_level2 必须为 no_distortion")
            if secondary:
                errors.append("无失真时 secondary_level2 必须为 null")
            if severity not in (None, "none"):
                errors.append("无失真时 severity 必须为 none")
        else:
            errors.append("With_Evidence 时 has_distortion 必须为 true 或 false")
    else:
        if answerable is not False:
            errors.append("%s 时 is_answerable 必须为 false" % level)
        if has_d is not None:
            errors.append("%s 时 has_distortion 必须为 null" % level)
        if primary is not None:
            errors.append("%s 时 primary_level2 必须为 null" % level)
        if secondary is not None:
            errors.append("%s 时 secondary_level2 必须为 null" % level)
        if level == "No_Evidence" and ids:
            errors.append("No_Evidence 时 gold_sentence_ids 必须为空")

    return errors


def validate_batch(
    raw: Any, expected: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """返回 {claim_id: coerced} 与 retry_errors（含整批错误）。"""
    expected_ids = [p["claim_id"] for p in expected]
    errors: list[dict[str, Any]] = []
    blob = _extract_samples_blob(raw)
    if not blob:
        errors.append(
            {
                "claim_id": "*",
                "error": "无法从模型输出解析 samples 数组",
            }
        )
        return {}, errors

    by_id: dict[str, dict[str, Any]] = {}
    for sample in blob:
        coerced = coerce_model_sample(sample)
        cid = coerced.get("claim_id") or ""
        if not cid:
            errors.append({"claim_id": "*", "error": "某条 sample 缺少 claim_id"})
            continue
        by_id[cid] = coerced

    got = set(by_id)
    want = set(expected_ids)
    missing = [cid for cid in expected_ids if cid not in got]
    extra = sorted(got - want)
    if missing:
        errors.append(
            {
                "claim_id": "*",
                "error": "缺少 claim: %s" % ", ".join(missing),
            }
        )
    if extra:
        errors.append(
            {
                "claim_id": "*",
                "error": "多出 claim: %s" % ", ".join(extra),
            }
        )

    valid: dict[str, dict[str, Any]] = {}
    for cid in expected_ids:
        item = by_id.get(cid)
        if not item:
            continue
        morph = morphology_errors(item)
        if morph:
            errors.append({"claim_id": cid, "error": "; ".join(morph)})
            continue
        analysis = item["analysis"]
        if not analysis["evidence_judgement"] and not analysis["classification_reason"]:
            errors.append(
                {
                    "claim_id": cid,
                    "error": "analysis.evidence_judgement 与 classification_reason 都为空",
                }
            )
            continue
        valid[cid] = item
    return valid, errors


# ---------------------------------------------------------------------------
# 组装完整 sample
# ---------------------------------------------------------------------------


def _label_obj(level2: str | None) -> dict[str, str] | None:
    if not level2:
        return None
    if level2 == NO_DISTORTION:
        return {"level1": "", "level2": NO_DISTORTION}
    return {"level1": level1_of(level2), "level2": level2}


def assemble_sample(
    pair: dict[str, Any],
    item: dict[str, Any],
    *,
    paper_id: str,
    article_id: str,
    source_type: str,
    sentence_table: dict[int, str],
) -> dict[str, Any]:
    claim_id = pair["claim_id"]
    sample_id = "%s-%s-%s" % (paper_id, article_id, claim_id)
    pool = pool_index(pair)
    pool_ids = set(pool)
    classify, _review = split_evidences(pair)

    flags: list[str] = []
    evidences: list[dict[str, Any]] = []
    for sid in item["gold_sentence_ids"]:
        text = pool.get(sid)
        in_pool = text is not None
        if text is None:
            text = sentence_table.get(sid, "")
            if sid in sentence_table:
                flags.append("gold_id_%d_在句表但不在审核池" % sid)
            else:
                flags.append("gold_id_%d_无法回填原文" % sid)
        if is_dirty_sentence(sid, text or ""):
            flags.append("gold_id_%d_疑似脏句" % sid)
        evidences.append({"sentence_id": sid, "text": text or ""})
    if (
        item["evidence_level"] == "With_Evidence"
        and len(item["gold_sentence_ids"]) > GOLD_ID_MAX
    ):
        flags.append(
            "gold 超过 %d 条（%d），应按断言覆盖精简"
            % (GOLD_ID_MAX, len(item["gold_sentence_ids"]))
        )

    analysis = dict(item["analysis"])
    focus = [f for f in analysis.get("review_focus") or [] if f in VALID_REVIEW_FOCUS]
    if not focus:
        focus = ["none"] if not flags else ["gold_sentence_ids"]
    analysis["review_focus"] = focus[:3]

    conf = analysis.get("ai_confidence") or "medium"
    if conf not in VALID_CONFIDENCE:
        conf = "medium"
        flags.append("ai_confidence 非法，已改为 medium")
    analysis["ai_confidence"] = conf

    verdict = analysis["unsupported_diagnosis"].get("verdict") or "not_applicable"
    if verdict not in VALID_UNSUPPORTED:
        analysis["unsupported_diagnosis"]["verdict"] = "uncertain"
        flags.append("unsupported_diagnosis.verdict 非法")

    diffs: list[dict[str, Any]] = []
    for diff in analysis.get("key_differences") or []:
        if not isinstance(diff, dict):
            continue
        dtype = _as_str(diff.get("type"))
        if dtype in LEGACY_SLUGS:
            flags.append("key_differences 含旧标签 %s" % dtype)
        diffs.append(
            {
                "type": dtype,
                "paper_expression": _as_str(diff.get("paper_expression")),
                "article_expression": _as_str(diff.get("article_expression")),
                "description": _as_str(diff.get("description")),
            }
        )
    analysis["key_differences"] = diffs
    if item["has_distortion"] is True and not diffs:
        flags.append("有失真但 key_differences 为空")
    diff_types = {d["type"] for d in diffs if d.get("type")}
    if (
        item["has_distortion"] is True
        and item["primary_level2"]
        and item["primary_level2"] not in diff_types
    ):
        flags.append("key_differences 未覆盖 primary %s" % item["primary_level2"])
    if item["secondary_level2"] and item["secondary_level2"] not in diff_types:
        flags.append("key_differences 未覆盖 secondary %s" % item["secondary_level2"])

    gold_set = set(item["gold_sentence_ids"])
    cited = cited_sentence_ids(
        analysis.get("evidence_judgement") or "",
        analysis.get("classification_reason") or "",
        item.get("reason") or "",
    )
    missing_cited = sorted(sid for sid in cited if sid not in gold_set)
    if missing_cited:
        flags.append(
            "分析引用了但未列入 gold: %s" % ",".join(str(x) for x in missing_cited)
        )

    needs = bool(analysis.get("needs_manual_review")) or bool(
        item.get("gold_needs_manual_review")
    )
    if conf != "high":
        needs = True
    if item["uncovered_phenomenon"]:
        needs = True
    if flags:
        needs = True

    top5 = {e["sentence_id"] for e in classify}
    if (
        item["evidence_level"] == "With_Evidence"
        and gold_set
        and not (gold_set & top5)
        and analysis.get("rag_review", {}).get("top5_is_best") is True
    ):
        flags.append("金标句均不在 top-5 但 rag_review.top5_is_best=true")
        needs = True

    out_of_pool = [sid for sid in item["gold_sentence_ids"] if sid not in pool_ids]
    if out_of_pool:
        needs = True

    if flags:
        extra = "脚本标记: " + "；".join(flags)
        hints = (analysis.get("manual_check_hints") or "").strip()
        if extra not in hints:
            analysis["manual_check_hints"] = (hints + " " + extra).strip() if hints else extra

    analysis["needs_manual_review"] = needs
    rag = analysis["rag_review"]
    if rag.get("top5_is_best") is None:
        rag["top5_is_best"] = bool(gold_set <= top5) if gold_set else False

    severity = item["severity"]
    if item["evidence_level"] != "With_Evidence":
        severity = None
    elif item["has_distortion"] is False:
        severity = "none"

    gold_clf: dict[str, Any] = {
        "evidence_level": item["evidence_level"],
        "has_distortion": item["has_distortion"],
        "primary_label": _label_obj(item["primary_level2"]),
        "secondary_label": _label_obj(item["secondary_level2"]),
        "severity": severity,
        "needs_manual_review": needs,
        "uncovered_phenomenon": item["uncovered_phenomenon"],
        "reason": item["reason"],
    }

    return {
        "sample_id": sample_id,
        "paper_id": paper_id,
        "article_id": article_id,
        "article_source_type": source_type,
        "claim_zh": pair.get("claim_zh") or "",
        "system_retrieval": build_system_retrieval(pair),
        "gold_retrieval": {
            "evidences": evidences,
            "sentence_ids": list(item["gold_sentence_ids"]),
            "is_answerable": bool(item["is_answerable"]),
        },
        "gold_classification": gold_clf,
        "analysis": analysis,
        "human_verified": False,
        "_script_flags": flags,
    }


def claim_sort_key(sample: dict[str, Any]) -> tuple[int, str]:
    sid = str(sample.get("sample_id") or "")
    m = re.search(r"C(\d+)$", sid, re.I)
    return (int(m.group(1)) if m else 10**9, sid)


def empty_doc(
    *,
    paper_id: str,
    article_id: str,
    source_type: str,
    generation_mode: str,
    limit: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
        "status": "draft",
        "paper_id": paper_id,
        "article_id": article_id,
        "article_source_type": source_type,
        "generated_date": date.today().isoformat(),
        "generation_mode": generation_mode,
        "limit": limit,
        "sample_count": 0,
        "_description": (
            "标注草稿。审核顺序：claim → classify top-5 → review 6-10 → "
            "gold_retrieval → gold_classification → analysis → human_verified=true。"
            "生产由 scripts/generate_draft_from_pairs.py 生成；human_verified 全为 false。"
        ),
        "samples": [],
        "review_queue": {"must_review_sample_ids": [], "notes": ""},
    }


def refresh_doc(doc: dict[str, Any]) -> None:
    samples = list(doc.get("samples") or [])
    samples.sort(key=claim_sort_key)
    doc["samples"] = samples
    doc["sample_count"] = len(samples)
    doc["schema_version"] = SCHEMA_VERSION
    doc["taxonomy_version"] = TAXONOMY_VERSION
    must = []
    for s in samples:
        analysis = s.get("analysis") or {}
        clf = s.get("gold_classification") or {}
        if analysis.get("needs_manual_review") or clf.get("needs_manual_review"):
            must.append(s["sample_id"])
    doc["review_queue"] = {
        "must_review_sample_ids": must,
        "notes": (doc.get("review_queue") or {}).get("notes") or "",
    }


# ---------------------------------------------------------------------------
# API 调用
# ---------------------------------------------------------------------------


def max_tokens_for(batch_size: int, override: int | None) -> int:
    if override:
        return override
    return max(4096, min(8192, 2800 * batch_size + 1500))


def call_model(
    client: QwenClient,
    *,
    system: str,
    user: str,
    model: str,
    max_tokens: int,
    timeout: float,
) -> tuple[Any, dict[str, int]]:
    messages = build_messages(user, system=system)
    kwargs: dict[str, Any] = {
        "temperature": 0.0,
        "model": model,
        "max_tokens": max_tokens,
        "timeout": timeout,
    }
    try:
        result = client.chat(
            messages,
            response_format={"type": "json_object"},
            **kwargs,
        )
    except APIClientError as exc:
        if getattr(exc, "status_code", None) != 400:
            raise
        result = client.chat(messages, **kwargs)
    data = extract_json(result.content)
    usage = {
        "prompt_tokens": int(result.prompt_tokens or 0),
        "completion_tokens": int(result.completion_tokens or 0),
        "total_tokens": int(result.total_tokens or 0),
    }
    return data, usage


def process_items(
    client: QwenClient,
    items: list[dict[str, Any]],
    *,
    system: str,
    paper_id: str,
    article_id: str,
    source_type: str,
    model: str,
    max_retries: int,
    timeout: float,
    max_tokens_override: int | None,
    sentence_table: dict[int, str],
    token_stats: dict[str, int],
    split: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not items:
        return [], []

    ids = [p["claim_id"] for p in items]
    retry_errors: list[dict[str, Any]] | None = None
    last_errors: list[dict[str, Any]] = []
    attempts = max_retries + 1

    for attempt in range(attempts):
        user = build_user_payload(
            items,
            paper_id=paper_id,
            article_id=article_id,
            source_type=source_type,
            retry_errors=retry_errors,
        )
        ntok = max_tokens_for(len(items), max_tokens_override)
        print(
            "    API %s (attempt %d/%d, max_tokens=%d, user≈%d tok)"
            % (",".join(ids), attempt + 1, attempts, ntok, estimate_tokens(user))
        )
        try:
            raw, usage = call_model(
                client,
                system=system,
                user=user,
                model=model,
                max_tokens=ntok,
                timeout=timeout,
            )
        except APIClientError as exc:
            last_errors = [{"claim_id": "*", "error": str(exc)}]
            print("    API 错误: %s" % exc)
            time.sleep(min(8, 2 ** attempt))
            continue

        for k, v in usage.items():
            token_stats[k] = token_stats.get(k, 0) + v
        if usage.get("total_tokens"):
            print(
                "    用量 prompt=%d completion=%d total=%d"
                % (
                    usage["prompt_tokens"],
                    usage["completion_tokens"],
                    usage["total_tokens"],
                )
            )

        valid, errors = validate_batch(raw, items)
        last_errors = errors
        assembled: list[dict[str, Any]] = []
        failed_pairs: list[dict[str, Any]] = []
        for pair in items:
            cid = pair["claim_id"]
            if cid in valid:
                assembled.append(
                    assemble_sample(
                        pair,
                        valid[cid],
                        paper_id=paper_id,
                        article_id=article_id,
                        source_type=source_type,
                        sentence_table=sentence_table,
                    )
                )
            else:
                failed_pairs.append(pair)

        if not failed_pairs:
            return assembled, []

        print("    校验未过: %s" % json.dumps(errors, ensure_ascii=False))
        if failed_pairs and len(failed_pairs) < len(items):
            # 保住合法条目，只重试失败的
            more, still = process_items(
                client,
                failed_pairs,
                system=system,
                paper_id=paper_id,
                article_id=article_id,
                source_type=source_type,
                model=model,
                max_retries=max_retries,
                timeout=timeout,
                max_tokens_override=max_tokens_override,
                sentence_table=sentence_table,
                token_stats=token_stats,
                split=True,
            )
            return assembled + more, still

        retry_errors = errors
        items = failed_pairs

    if split and len(items) > 1:
        ok_all: list[dict[str, Any]] = []
        fail_all: list[dict[str, Any]] = []
        print("    整批失败，降为逐条: %s" % ",".join(p["claim_id"] for p in items))
        for pair in items:
            more, still = process_items(
                client,
                [pair],
                system=system,
                paper_id=paper_id,
                article_id=article_id,
                source_type=source_type,
                model=model,
                max_retries=max_retries,
                timeout=timeout,
                max_tokens_override=max_tokens_override,
                sentence_table=sentence_table,
                token_stats=token_stats,
                split=False,
            )
            ok_all.extend(more)
            fail_all.extend(still)
        return ok_all, fail_all

    failed_rows = [
        {
            "claim_id": p["claim_id"],
            "errors": last_errors,
        }
        for p in items
    ]
    return [], failed_rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_limit(raw: str | None) -> int | None:
    if raw is None or str(raw).strip().lower() in ("", "all"):
        return None
    try:
        n = int(raw)
    except ValueError:
        raise SystemExit("--limit 必须是正整数或 all")
    if n <= 0:
        raise SystemExit("--limit 必须是正整数或 all")
    return n


def apply_after(pairs: list[dict[str, Any]], after: str | None) -> list[dict[str, Any]]:
    if not after:
        return pairs
    token = after.strip()
    if token.lower().startswith("after:"):
        token = token.split(":", 1)[1]
    token = token.strip().upper()
    if not token:
        return pairs
    for i, pair in enumerate(pairs):
        if str(pair.get("claim_id") or "").upper() == token:
            return pairs[i + 1 :]
    raise SystemExit("--after %s 不在 pairs 中" % after)


def default_pairs_path(paper: str, article: str) -> Path:
    return _PROJECT_ROOT / "outputs" / paper / article / "claim_evidence_pairs.jsonl"


def default_sentences_path(paper: str) -> Path:
    return _PROJECT_ROOT / "data" / "annotations" / paper / ("%s_sentences.csv" % paper)


def default_output_path(
    paper: str, article: str, selected: list[dict[str, Any]], limit: int | None
) -> Path:
    folder = _PROJECT_ROOT / "data" / "annotations" / paper
    if selected and limit is not None:
        first = selected[0]["claim_id"]
        last = selected[-1]["claim_id"]
        name = "%s_%s_annotation_draft_%s_%s.json" % (paper, article, first, last)
    else:
        name = "%s_%s_annotation_draft.json" % (paper, article)
    return folder / name


def chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def existing_sample_ids(doc: dict[str, Any] | None) -> set[str]:
    if not doc:
        return set()
    out: set[str] = set()
    for s in doc.get("samples") or []:
        sid = s.get("sample_id")
        if sid:
            out.add(str(sid))
    return out


def print_summary(doc: dict[str, Any], token_stats: dict[str, int], failed: int) -> None:
    samples = doc.get("samples") or []
    print("\n==== 摘要 ====")
    print("samples: %d" % len(samples))
    must = (doc.get("review_queue") or {}).get("must_review_sample_ids") or []
    print("must_review: %s" % (", ".join(must) if must else "(无)"))
    dist: dict[str, int] = {}
    for s in samples:
        clf = s.get("gold_classification") or {}
        level = clf.get("evidence_level") or "?"
        label = clf.get("primary_label") or {}
        key = "%s/%s" % (level, (label or {}).get("level2") or "null")
        dist[key] = dist.get(key, 0) + 1
    print("标签分布:")
    for k, n in sorted(dist.items()):
        print("  %s: %d" % (k, n))
    if token_stats.get("total_tokens"):
        print(
            "token: prompt=%d completion=%d total=%d"
            % (
                token_stats.get("prompt_tokens", 0),
                token_stats.get("completion_tokens", 0),
                token_stats.get("total_tokens", 0),
            )
        )
    if failed:
        print("失败条数: %d（见 *_errors.jsonl）" % failed)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="API 生成标注草稿（模型只判，脚本拷检索）")
    p.add_argument("--pairs", default="", help="claim_evidence_pairs.jsonl")
    p.add_argument("--output", default="", help="草稿 JSON 路径")
    p.add_argument("--paper", required=True, help="如 P001")
    p.add_argument("--article", required=True, help="如 A001")
    p.add_argument("--source-type", default="high_quality", help="写入每条 sample")
    p.add_argument("--limit", default="all", help="正整数 N 或 all")
    p.add_argument("--after", default="", help="从该 claim_id 之后续跑，如 C10")
    p.add_argument("--batch-size", type=int, default=3)
    p.add_argument("--model", default="", help="默认 QWEN_MODEL（qwen-plus）")
    p.add_argument("--max-retries", type=int, default=2)
    p.add_argument("--max-tokens", type=int, default=0, help="覆盖每批 max_tokens")
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--sentences", default="", help="句表 CSV，用于池外 id 回填")
    p.add_argument("--prompt", default="", help="API system 提示词路径")
    p.add_argument(
        "--overwrite-unverified",
        action="store_true",
        help="覆盖本次范围内 human_verified=false 的已有样本（不覆盖已人工确认的）",
    )
    p.add_argument("--dry-run", action="store_true", help="只打印将发送的批，不调 API")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paper = args.paper.strip().upper()
    article = args.article.strip().upper()
    if args.batch_size < 1:
        raise SystemExit("--batch-size 必须 >= 1")
    if args.batch_size >= 10:
        raise SystemExit("--batch-size 禁止 >= 10（易截断、质量差）")

    pairs_path = _resolve(args.pairs) if args.pairs else default_pairs_path(paper, article)
    if not pairs_path.exists():
        raise SystemExit("找不到 pairs: %s" % pairs_path)

    all_pairs = load_pairs(pairs_path)
    selected = apply_after(all_pairs, args.after or None)
    limit_n = parse_limit(args.limit)
    if limit_n is not None:
        selected = selected[:limit_n]
    if not selected:
        print("切片后没有待处理 claim")
        return 0

    out_path = (
        _resolve(args.output)
        if args.output
        else default_output_path(paper, article, selected, limit_n)
    )
    prompt_path = _resolve(args.prompt) if args.prompt else DEFAULT_PROMPT
    if not prompt_path.exists():
        raise SystemExit("找不到提示词: %s" % prompt_path)
    system = load_system_prompt(prompt_path)

    sent_path = (
        _resolve(args.sentences) if args.sentences else default_sentences_path(paper)
    )
    if sent_path.exists():
        sentence_table = load_sentence_table(sent_path)
        print("句表: %d 句 (%s)" % (len(sentence_table), sent_path))
        if not sentence_table:
            print("  警告: 句表没有解析出任何 sentence_id")
    else:
        sentence_table = {}
        print("未找到句表 %s（池外 gold id 将无法回填原文）" % sent_path)

    if args.after:
        mode = "resume"
        limit_label = "after:%s+%s" % (args.after, args.limit)
    elif limit_n is not None:
        mode = "smoke"
        limit_label = str(args.limit)
    else:
        mode = "full"
        limit_label = "all"

    doc = load_existing_doc(out_path)
    if doc is None:
        doc = empty_doc(
            paper_id=paper,
            article_id=article,
            source_type=args.source_type,
            generation_mode=mode,
            limit=limit_label,
        )
    else:
        doc["generation_mode"] = "resume"
        skip_n = len(doc.get("samples") or [])
        if skip_n:
            print("已有草稿 %d 条" % skip_n)

    selected_ids = {
        "%s-%s-%s" % (paper, article, p["claim_id"]) for p in selected
    }
    if args.overwrite_unverified and doc.get("samples"):
        kept = []
        removed = 0
        for sample in doc.get("samples") or []:
            sid = str(sample.get("sample_id") or "")
            if sid in selected_ids and not sample.get("human_verified"):
                removed += 1
                continue
            kept.append(sample)
        doc["samples"] = kept
        if removed:
            print("overwrite-unverified: 移除 %d 条未确认样本，将重生成" % removed)

    have = existing_sample_ids(doc)
    todo: list[dict[str, Any]] = []
    skipped = 0
    for pair in selected:
        sid = "%s-%s-%s" % (paper, article, pair["claim_id"])
        if sid in have:
            skipped += 1
            continue
        todo.append(pair)

    print("pairs=%s" % pairs_path)
    print("output=%s" % out_path)
    print(
        "范围 %d 条，跳过 %d，待生成 %d，batch-size=%d"
        % (len(selected), skipped, len(todo), args.batch_size)
    )
    print("system 提示词 ≈ %d tok (%s)" % (estimate_tokens(system), prompt_path.name))

    if args.dry_run:
        for i, batch in enumerate(chunks(todo, args.batch_size), 1):
            user = build_user_payload(
                batch,
                paper_id=paper,
                article_id=article,
                source_type=args.source_type,
            )
            parsed_user = json.loads(user)
            leaked = any(
                set(it) & {"paper_sentences", "evidences"}
                for it in parsed_user.get("items") or []
            )
            print(
                "  批 %d: %s | user≈%d tok | 含 evidences/paper_sentences=%s"
                % (
                    i,
                    ",".join(p["claim_id"] for p in batch),
                    estimate_tokens(user),
                    leaked,
                )
            )
        print("dry-run 结束，未调用 API")
        return 0

    if not todo:
        refresh_doc(doc)
        atomic_write_json(out_path, doc)
        print("没有新条目需要生成")
        return 0

    model = args.model.strip() or QWEN_MODEL
    client = QwenClient(verbose=False, model=model)
    token_stats: dict[str, int] = {}
    errors_path = out_path.with_name(out_path.stem + "_errors.jsonl")
    failed_n = 0

    try:
        for bi, batch in enumerate(chunks(todo, args.batch_size), 1):
            print(
                "\n[%d/%d] 批 %s"
                % (
                    bi,
                    (len(todo) + args.batch_size - 1) // args.batch_size,
                    ",".join(p["claim_id"] for p in batch),
                )
            )
            ok, failed = process_items(
                client,
                batch,
                system=system,
                paper_id=paper,
                article_id=article,
                source_type=args.source_type,
                model=model,
                max_retries=args.max_retries,
                timeout=args.timeout,
                max_tokens_override=args.max_tokens or None,
                sentence_table=sentence_table,
                token_stats=token_stats,
                split=True,
            )
            for sample in ok:
                flags = sample.pop("_script_flags", [])
                doc.setdefault("samples", []).append(sample)
                mark = " review" if sample["analysis"]["needs_manual_review"] else ""
                extra = (" | " + "；".join(flags)) if flags else ""
                print(
                    "  ok %s %s/%s%s%s"
                    % (
                        sample["sample_id"],
                        sample["gold_classification"]["evidence_level"],
                        (sample["gold_classification"]["primary_label"] or {}).get(
                            "level2"
                        )
                        or "null",
                        mark,
                        extra,
                    )
                )
            if ok:
                refresh_doc(doc)
                atomic_write_json(out_path, doc)
            for row in failed:
                failed_n += 1
                append_jsonl(
                    errors_path,
                    {
                        "paper_id": paper,
                        "article_id": article,
                        **row,
                    },
                )
                print("  fail %s" % row.get("claim_id"))
    finally:
        refresh_doc(doc)
        atomic_write_json(out_path, doc)

    print_summary(doc, token_stats, failed_n)
    print("已写入 %s" % out_path)
    if failed_n:
        print("错误日志 %s" % errors_path)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
