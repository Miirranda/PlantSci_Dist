#!/usr/bin/env python3
"""召回审核完成后，基于人工证据二次生成失真分析，产出独立文件2（失真审核底稿）。

纯离线批处理：读取文件1（``*_annotation_draft.json``）里 ``human_reviews`` 中
``recall_reviewed=true`` 的记录，用「勾选 gold 句 + 人工找回段落」二次生成失真分析
（千问），并给证据句补中文翻译（有道），产出文件2（``*_distortion_review.json``）。

文件2 自包含：每条 record 冻结 ``gold_evidences``（勾选句 text+text_zh）与
``manual_retrieved_paragraphs``（人工段落，含翻译）以及 ``generated_analysis``，
失真审核界面2 直接渲染文件2，不再查文件1 的召回池。

增量：若文件2 已存在且该记录 ``generated_analysis`` 未过期（``recall_updated_at <=
generated_at``），沿用其分析与已填失真字段；加 ``--force`` 强制重生成全部分析。
召回改动（``recall_updated_at`` 变新）会使旧失真字段失效并被清空。

用法::

    python scripts/regenerate_analysis.py --draft data/annotations/P001/P001_A001_annotation_draft.json
    python scripts/regenerate_analysis.py --draft ... --reviewer 牟德兰 --dry-run
    python scripts/regenerate_analysis.py --draft ... --out data/annotations/P001/P001_A001_distortion_review.json
"""

from __future__ import annotations

import argparse
import csv
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

from hallu.config import (  # noqa: E402
    DISTORTION_LABELS,
    LEVEL1_LABELS,
    NO_DISTORTION,
    QWEN_MODEL,
    SEVERITY_VALUES,
    TAXONOMY_VERSION,
    UNCOVERED_PHENOMENA,
    ensure_env,
)

ensure_env()

from api_client import QwenClient, build_messages, extract_json  # noqa: E402
from api_client.exceptions import APIClientError  # noqa: E402
from translate_text_fields import translate_youdao  # noqa: E402

DEFAULT_PROMPT = (
    _PROJECT_ROOT / "data" / "annotations" / "prompts" / "regen_analysis_api.md"
)

VALID_EVIDENCE_LEVELS = frozenset(("With_Evidence", "Weak_Evidence", "No_Evidence"))
VALID_SEVERITY = frozenset(SEVERITY_VALUES)
VALID_CONFIDENCE = frozenset(("high", "medium", "low"))
VALID_LEVEL2 = frozenset(DISTORTION_LABELS) | {NO_DISTORTION}
_EL_MAP = {
    "with_evidence": "With_Evidence",
    "weak_evidence": "Weak_Evidence",
    "no_evidence": "No_Evidence",
    "with": "With_Evidence",
    "weak": "Weak_Evidence",
    "no": "No_Evidence",
}
_LEVEL1_MAP = {"omission": "omission", "addition": "addition", "substitution": "substitution"}


# ---------------------------------------------------------------------------
# IO / 发现
# ---------------------------------------------------------------------------


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def discover_reviewables() -> list[Path]:
    base = _PROJECT_ROOT / "data" / "annotations"
    if not base.exists():
        return []
    out: list[Path] = []
    for p in sorted(base.rglob("*_annotation_draft.json")):
        if ".bak" in p.name:
            continue
        out.append(p)
    return out


def atomic_write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def derive_output_path(draft_path: Path) -> Path:
    """文件1 -> 文件2 命名：<prefix>_annotation_draft.json -> <prefix>_distortion_review.json。"""
    name = draft_path.name
    if name.endswith("_annotation_draft.json"):
        name = name[: -len("_annotation_draft.json")] + "_distortion_review.json"
    else:
        name = draft_path.stem + "_distortion_review.json"
    return draft_path.with_name(name)


def load_sentence_table(paper_id: str) -> dict[int, str]:
    if not paper_id:
        return {}
    csv_path = (
        _PROJECT_ROOT / "data" / "annotations" / paper_id / (paper_id + "_sentences.csv")
    )
    if not csv_path.exists():
        return {}
    index: dict[int, str] = {}
    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                sid = (row.get("sentence_id") or "").strip()
                if not sid.isdigit():
                    continue
                index[int(sid)] = (row.get("text") or "").strip()
    except Exception:
        return {}
    return index


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------


def _taxonomy_block() -> str:
    rows = ["| level1 | level2 | 中文 |", "|---|---|---|"]
    for slug, info in DISTORTION_LABELS.items():
        l1 = info["level1"]
        zh = info["zh"]
        l1_zh = (LEVEL1_LABELS.get(l1) or {}).get("zh") or l1
        rows.append("| %s (%s) | %s | %s |" % (l1, l1_zh, slug, zh))
    rows.append("| — | %s | 无失真 |" % NO_DISTORTION)
    uncovered = " | ".join(sorted(UNCOVERED_PHENOMENA))
    return (
        "taxonomy_version = %s\n\n" % TAXONOMY_VERSION
        + "\n".join(rows)
        + "\n\n8 类盖不住时 uncovered_phenomenon 仅允许: %s\n" % uncovered
    )


def load_system_prompt(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if "{{TAXONOMY_BLOCK}}" in text:
        return text.replace("{{TAXONOMY_BLOCK}}", _taxonomy_block())
    return text.rstrip() + "\n\n" + _taxonomy_block()


# ---------------------------------------------------------------------------
# 证据收集 + 翻译
# ---------------------------------------------------------------------------


def gather_evidence(
    sample: dict[str, Any],
    record: dict[str, Any],
    sentence_table: dict[int, str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """返回 (gold 证据, 人工找回段落)，各自带 text + text_zh（缺翻译就地补）。"""
    sr = sample.get("system_retrieval") or {}
    ev_map: dict[int, dict[str, str]] = {}
    for e in sr.get("review_evidences") or []:
        sid = e.get("sentence_id")
        if sid is not None:
            ev_map[sid] = {"text": str(e.get("text") or ""), "text_zh": str(e.get("text_zh") or "")}
    for e in sr.get("classify_evidences") or []:
        sid = e.get("sentence_id")
        if sid is not None and sid not in ev_map:
            ev_map[sid] = {"text": str(e.get("text") or ""), "text_zh": ""}

    gold: list[dict[str, str]] = []
    for sid in record.get("gold_sentence_ids") or []:
        sid = int(sid)
        if sid in ev_map:
            item = {"sentence_id": str(sid), **ev_map[sid]}
        elif sentence_table.get(sid):
            item = {"sentence_id": str(sid), "text": sentence_table[sid], "text_zh": ""}
        else:
            continue
        if not item["text_zh"]:
            item["text_zh"] = translate_youdao(item["text"]) or ""
        gold.append(item)

    manual: list[dict[str, str]] = []
    for p in record.get("manual_retrieved_paragraphs") or []:
        text = str((p or {}).get("text") or "").strip()
        if not text:
            continue
        zh = str((p or {}).get("text_zh") or "").strip()
        if not zh:
            zh = translate_youdao(text) or ""
        manual.append({"text": text, "text_zh": zh})

    return gold, manual


# ---------------------------------------------------------------------------
# 模型调用
# ---------------------------------------------------------------------------


def _build_user(sample: dict[str, Any], gold: list[dict[str, str]], manual: list[dict[str, str]]) -> str:
    return json.dumps(
        {
            "claim_zh": sample.get("claim_zh") or "",
            "gold_evidences": gold,
            "manual_evidences": manual,
        },
        ensure_ascii=False,
    )


def _call(client: QwenClient, *, system: str, user: str, model: str, max_tokens: int, timeout: float) -> Any:
    messages = build_messages(user, system=system)
    kwargs: dict[str, Any] = {
        "temperature": 0.0,
        "model": model,
        "max_tokens": max_tokens,
        "timeout": timeout,
    }
    try:
        result = client.chat(messages, response_format={"type": "json_object"}, **kwargs)
    except APIClientError as exc:
        if getattr(exc, "status_code", None) != 400:
            raise
        result = client.chat(messages, **kwargs)
    return extract_json(result.content)


def _label_obj(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    level2 = str(raw.get("level2") or "").strip()
    if level2 not in VALID_LEVEL2:
        return None
    level1 = str(raw.get("level1") or "").strip()
    level1 = _LEVEL1_MAP.get(level1, DISTORTION_LABELS.get(level2, {}).get("level1", level1))
    return {"level1": level1, "level2": level2}


def _coerce(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("模型输出不是 JSON 对象: %r" % (raw,))
    el = str(raw.get("evidence_level") or "").strip()
    el = _EL_MAP.get(el.lower(), el)
    if el not in VALID_EVIDENCE_LEVELS:
        el = ""
    sev = str(raw.get("severity") or "").strip()
    if sev not in VALID_SEVERITY:
        sev = "none"
    conf = str(raw.get("ai_confidence") or "").strip().lower()
    if conf not in VALID_CONFIDENCE:
        conf = "low"
    kdf = raw.get("key_differences") if isinstance(raw.get("key_differences"), list) else []
    kdf = [d for d in kdf if isinstance(d, dict)]
    return {
        "evidence_level": el,
        "primary_label": _label_obj(raw.get("primary_label")),
        "secondary_label": _label_obj(raw.get("secondary_label")),
        "severity": sev,
        "classification_reason": str(raw.get("classification_reason") or "").strip(),
        "evidence_judgement": str(raw.get("evidence_judgement") or "").strip(),
        "key_differences": kdf,
        "ai_confidence": conf,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="召回审核后二次生成失真分析（文件1 -> 文件2）")
    parser.add_argument("--draft", default="", help="召回审核文件（文件1）JSON 路径，缺省自动发现")
    parser.add_argument("--out", default="", help="输出文件2 路径，缺省 <prefix>_distortion_review.json")
    parser.add_argument("--reviewer", default="", help="只处理指定审核人（缺省全部）")
    parser.add_argument("--model", default="", help="覆盖默认模型")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--force", action="store_true", help="强制重生成所有 recall_reviewed 记录的分析")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不调 API、不翻译、不写回")
    args = parser.parse_args()

    if args.draft:
        draft_path = _resolve(args.draft)
    else:
        files = discover_reviewables()
        if len(files) == 1:
            draft_path = files[0]
        elif not files:
            raise SystemExit("未找到召回审核文件（data/annotations 下无 *_annotation_draft.json）")
        else:
            raise SystemExit(
                "多个召回审核文件，请用 --draft 指定：\n  " + "\n  ".join(str(f) for f in files)
            )
    if not draft_path.exists():
        raise SystemExit("找不到召回审核文件: %s" % draft_path)

    out_path = _resolve(args.out) if args.out else derive_output_path(draft_path)

    doc = json.loads(draft_path.read_text(encoding="utf-8"))
    samples = doc.get("samples") or []
    human_reviews = doc.get("human_reviews") or {}
    sentence_table = load_sentence_table(doc.get("paper_id") or "")
    system = load_system_prompt(DEFAULT_PROMPT)
    model = args.model.strip() or QWEN_MODEL

    # 已有文件2：增量重跑时保留未改动记录的 analysis + 已填失真字段
    existing_reviews: dict[str, Any] = {}
    if out_path.exists():
        try:
            existing_reviews = json.loads(out_path.read_text(encoding="utf-8")).get("human_reviews") or {}
        except Exception:
            existing_reviews = {}

    # 收集 recall_reviewed 记录，并判定是否 stale（需要重新 gather + 生成）
    targets: list[tuple[dict[str, Any], str, dict[str, Any], dict[str, Any], bool]] = []
    skipped = 0
    for sample in samples:
        sid = sample.get("sample_id") or ""
        for reviewer, recs in human_reviews.items():
            if args.reviewer and reviewer != args.reviewer:
                continue
            rec = (recs or {}).get(sid)
            if not rec or not rec.get("recall_reviewed"):
                continue
            existing = (existing_reviews.get(reviewer) or {}).get(sid) or {}
            gen = existing.get("generated_analysis") or {}
            recall_ts = rec.get("recall_updated_at") or ""
            gen_ts = gen.get("generated_at") or ""
            stale = args.force or (not gen_ts) or (recall_ts and recall_ts > gen_ts)
            targets.append((sample, reviewer, rec, existing, stale))
            if not stale:
                skipped += 1

    print("文件1 = %s" % draft_path)
    print("文件2 = %s" % out_path)
    print("recall_reviewed 共 %d 条，需二次生成 %d 条，跳过（未改动）%d 条" % (len(targets), len(targets) - skipped, skipped))

    if not targets:
        print("没有 recall_reviewed 记录，无可生成内容")
        return 0

    stale_targets = [t for t in targets if t[4]]

    if args.dry_run:
        for sample, reviewer, rec, existing, stale in stale_targets:
            gold_n = len(rec.get("gold_sentence_ids") or [])
            manual_n = len(rec.get("manual_retrieved_paragraphs") or [])
            print("  [dry] %s @%s gold=%d manual=%d" % (sample.get("sample_id"), reviewer, gold_n, manual_n))
        return 0

    client = QwenClient(verbose=False, model=model) if stale_targets else None

    new_reviews: dict[str, Any] = {}
    done = 0
    for sample, reviewer, rec, existing, stale in targets:
        sid = sample.get("sample_id") or ""
        if not stale:
            new_reviews.setdefault(reviewer, {})[sid] = dict(existing)
            continue
        gold, manual = gather_evidence(sample, rec, sentence_table)
        record: dict[str, Any] = {
            "gold_sentence_ids": [int(x) for x in (rec.get("gold_sentence_ids") or [])],
            "gold_evidences": [
                {"sentence_id": g["sentence_id"], "text": g["text"], "text_zh": g["text_zh"]} for g in gold
            ],
            "recall_note": str(rec.get("recall_note") or ""),
            "manual_retrieved_paragraphs": manual,
            "recall_reviewed": True,
            "recall_updated_at": rec.get("recall_updated_at") or "",
        }
        new_reviews.setdefault(reviewer, {})[sid] = record
        user = _build_user(sample, gold, manual)
        print("  生成 %s @%s（gold=%d manual=%d）…" % (sid, reviewer, len(gold), len(manual)))
        try:
            raw = _call(client, system=system, user=user, model=model, max_tokens=args.max_tokens, timeout=args.timeout)
            record["generated_analysis"] = _coerce(raw)
            done += 1
        except Exception as exc:  # noqa: BLE001
            print("    [ERR] %s: %s" % (sid, exc))

    out_doc: dict[str, Any] = {
        "schema_version": "1.3",
        "kind": "distortion_review",
        "paper_id": doc.get("paper_id") or "",
        "article_id": doc.get("article_id") or "",
        "source_draft": draft_path.name,
        "samples": [
            {"sample_id": s.get("sample_id") or "", "claim_zh": s.get("claim_zh") or ""} for s in samples
        ],
        "human_reviews": new_reviews,
    }
    atomic_write(out_path, out_doc)
    print("完成：二次生成 %d/%d，已写文件2 %s" % (done, len(stale_targets), out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
