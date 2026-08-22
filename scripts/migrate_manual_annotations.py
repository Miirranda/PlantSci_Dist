#!/usr/bin/env python3
"""从 readable JSON 提取人工审核标注，迁移到 annotation_draft.json。

修复 readable 中 9 处结构破坏（human_verified 后裸备注、尾随逗号、裸粘贴文本），
并将人工字段写入 draft 的正式 schema：
  - human_verified
  - human_note
  - retrieval_quality
  - human_recalled_sentence_ids

用法::
    python scripts/migrate_manual_annotations.py \\
        data/annotations/P001/P001_A001_annotation_draft_readable.json \\
        data/annotations/P001/P001_A001_annotation_draft.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent

# human_verified 后非法追加的裸备注 → (sample_id 由解析顺序推断)
_HV_NOTE_RE = re.compile(
    r'"human_verified"\s*:\s*(true|false)\s*:\s*([^\n\r,}]+)',
    re.IGNORECASE,
)

# 裸粘贴在 sample 之间的英文段落（C05 附近）
_ORPHAN_EN_RE = re.compile(
    r'\}\s*,\s*\n\s*The Cucurbitaceae family, as exemplified by cucumber[^\n]*\n'
    r'[^\n]*\n[^\n]*\n[^\n]*\n\s*\{',
    re.MULTILINE,
)

# 中文逗号 → ASCII 逗号（C07 sentence_ids）
_CN_COMMA_RE = re.compile(r"，")


def _sanitize_readable(raw: str) -> str:
    """修复已知结构破坏，保留 intentional 裸换行。"""
    text = raw

    # A. human_verified: true:备注 → 提取备注后还原合法 JSON
    notes_by_order: list[tuple[bool, str]] = []

    def _hv_repl(m: re.Match) -> str:
        verified = m.group(1).lower() == "true"
        note = m.group(2).strip()
        notes_by_order.append((verified, note))
        return '"human_verified": %s' % ("true" if verified else "false")

    text = _HV_NOTE_RE.sub(_hv_repl, text)

    # B. 尾随逗号
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r",(\s*[\]}])", r"\1", text)

    # C. 删除 sample 间裸粘贴英文
    text = _ORPHAN_EN_RE.sub("},\n    {", text)

    # D. 中文逗号（数组内）
    text = _CN_COMMA_RE.sub(",", text)

    return text, notes_by_order


def _infer_retrieval_quality(note: str) -> str | None:
    if not note:
        return None
    if any(k in note for k in ("rag检索效果很差", "检索效果差", "检索效果很差")):
        return "poor"
    if "检索效果比较好" in note:
        return "good"
    if "翻译有问题" in note:
        return "fair"
    return None


def _infer_recalled_ids(note: str, sentence_ids: list[int]) -> list[int]:
    if "人工召回" in note and sentence_ids:
        return list(sentence_ids)
    return []


def _extract_hv_notes(raw: str) -> dict[str, dict]:
    """按出现顺序将 human_verified 备注与 sample_id 对齐。"""
    # 先收集 sample_id 顺序
    sample_ids = re.findall(r'"sample_id"\s*:\s*"([^"]+)"', raw)
    notes_in_file: list[tuple[bool, str]] = []
    for m in _HV_NOTE_RE.finditer(raw):
        notes_in_file.append((m.group(1).lower() == "true", m.group(2).strip()))

    # 也收集合法的 human_verified（无备注）
    hv_entries: list[tuple[bool, str | None]] = []
    for m in re.finditer(
        r'"human_verified"\s*:\s*(true|false)(?:\s*:\s*([^\n\r,}]+))?',
        raw,
        re.IGNORECASE,
    ):
        verified = m.group(1).lower() == "true"
        note = (m.group(2) or "").strip() or None
        if note and note.lower() in ("true", "false"):
            note = None
        hv_entries.append((verified, note))

    result: dict[str, dict] = {}
    for i, sid in enumerate(sample_ids):
        if i >= len(hv_entries):
            break
        verified, note = hv_entries[i]
        if not verified and not note:
            continue
        entry: dict = {"human_verified": verified}
        if note:
            entry["human_note"] = note
            rq = _infer_retrieval_quality(note)
            if rq:
                entry["retrieval_quality"] = rq
        result[sid] = entry
    return result


def merge_readable_into_draft(readable_path: Path, draft_path: Path) -> None:
    raw = readable_path.read_text(encoding="utf-8")
    sanitized, _ = _sanitize_readable(raw)
    hv_meta = _extract_hv_notes(raw)

    try:
        readable_data = json.loads(sanitized, strict=False)
    except json.JSONDecodeError as exc:
        raise SystemExit("sanitize 后仍无法解析 readable: %s" % exc) from exc

    draft_data = json.loads(draft_path.read_text(encoding="utf-8"), strict=False)
    readable_by_id = {
        s["sample_id"]: s for s in readable_data.get("samples") or [] if s.get("sample_id")
    }

    merged = 0
    for sample in draft_data.get("samples") or []:
        sid = sample.get("sample_id")
        if not sid:
            continue
        rs = readable_by_id.get(sid)
        meta = hv_meta.get(sid)
        if not rs and not meta:
            continue

        if meta:
            sample["human_verified"] = meta["human_verified"]
            if meta.get("human_note"):
                sample["human_note"] = meta["human_note"]
            if meta.get("retrieval_quality"):
                sample["retrieval_quality"] = meta["retrieval_quality"]
            merged += 1
        elif rs and rs.get("human_verified"):
            sample["human_verified"] = True
            merged += 1

        # 合并 readable 中人工改过的 gold_retrieval / gold_classification
        if rs:
            for key in ("gold_retrieval", "gold_classification"):
                if key in rs and rs[key] != sample.get(key):
                    sample[key] = rs[key]

            gr = sample.get("gold_retrieval") or {}
            sids = gr.get("sentence_ids") or []
            if isinstance(sids, list):
                fixed_sids = []
                for x in sids:
                    if isinstance(x, int):
                        fixed_sids.append(x)
                    elif isinstance(x, str) and x.strip().isdigit():
                        fixed_sids.append(int(x.strip()))
                gr["sentence_ids"] = fixed_sids

            note = sample.get("human_note") or ""
            recalled = _infer_recalled_ids(note, gr.get("sentence_ids") or [])
            if recalled:
                sample["human_recalled_sentence_ids"] = recalled

    # 确保所有 sample 都有新字段默认值
    for sample in draft_data.get("samples") or []:
        sample.setdefault("human_verified", False)
        sample.setdefault("human_note", "")
        sample.setdefault("retrieval_quality", None)
        sample.setdefault("human_recalled_sentence_ids", [])

    draft_path.write_text(
        json.dumps(draft_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("[OK] merged %d samples with human annotations → %s" % (merged, draft_path))


def main() -> None:
    p = argparse.ArgumentParser(description="迁移 readable 人工标注到 draft")
    p.add_argument("readable", type=Path)
    p.add_argument("draft", type=Path)
    args = p.parse_args()
    merge_readable_into_draft(args.readable, args.draft)


if __name__ == "__main__":
    main()
