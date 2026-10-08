#!/usr/bin/env python3
"""本地审核服务器（零依赖、自包含，方便迁移给多人使用）。

用法::

    python scripts/review_server.py                    # 扫描 data/annotations 下列表并交互选择
    python scripts/review_server.py --list             # 仅列出可审核文件
    python scripts/review_server.py --draft <file.json> [--port 8765] [--no-open]

- 数据源   : 任意 annotation draft JSON（含 samples + claim_zh + system_retrieval + analysis）
- 审核结果 : 写回评测文件顶层 ``human_reviews`` 键，按审核人分列、多审核人并存（不互相覆盖）
- 前端     : scripts/review_ui/recall.html（召回审核）+ distortion.html（失真审核），按文件名自动路由

纯标准库 http.server，**不依赖 hallu / api_client / .env / API key**。
失真分类体系为迁移用的内嵌副本，权威定义见 ``hallu/config.py``，改动需两边同步。

多人协作模型：
    每人负责不同的评测文件（按文章粒度分工）。审核人甲审完 → 结果写回评测文件
    （``human_reviews`` 里有 human_verified=true）→ 把该文件导入乙的电脑 → 乙打开看到
    甲的结果作为参考条，若有异议可填写自己的独立意见（存到自己的审核人名下）。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
UI_DIR = SCRIPT_DIR / "review_ui"

# ---------------------------------------------------------------------------
# 信息失真分类体系（distortion-v0.1）——内嵌副本，权威定义见 hallu/config.py
# ---------------------------------------------------------------------------

TAXONOMY: dict[str, Any] = {
    "version": "distortion-v0.1",
    "level1": {
        "omission": {"zh": "信息删减", "en": "Omission"},
        "addition": {"zh": "信息添加", "en": "Addition"},
        "substitution": {"zh": "信息替换", "en": "Substitution"},
    },
    "labels": {
        "context_omission": {
            "level1": "omission",
            "zh": "背景限定删减",
            "en": "Context omission",
            "definition": (
                "删除论文中限定研究对象、环境、实验条件的信息（物种/品种/组织/"
                "细胞类型/发育阶段/环境或处理条件），使结论看起来适用于更广情境。"
                "核心问题：若恢复被删信息，公众号结论的适用范围是否会明显缩小？"
            ),
        },
        "evidence_uncertainty_omission": {
            "level1": "omission",
            "zh": "证据与不确定性删减",
            "en": "Evidence/Uncertainty omission",
            "definition": (
                "删除论文中表达证据强度、不确定程度或研究限制的信息（may/might/"
                "suggest/indicate/potentially/preliminary），使结论显得更确定。"
                "核心问题：删除的信息是否影响「这个结论有多确定」？"
            ),
        },
        "mechanism_omission": {
            "level1": "omission",
            "zh": "机制删减",
            "en": "Mechanism omission",
            "definition": (
                "删除论文中的关键作用机制，使研究发现被简化为更直接、更强的功能关系。"
                "核心问题：删除机制后，科学关系是否被改变？合理机制压缩不算失真。"
            ),
        },
        "function_application_addition": {
            "level1": "addition",
            "zh": "功能/应用添加",
            "en": "Function/Application addition",
            "definition": (
                "增加论文没有证明的功能、用途或应用价值（如抗旱功能、育种应用）。"
                "核心问题：公众号是否提出论文实验没有支持的新功能？"
            ),
        },
        "significance_addition": {
            "level1": "addition",
            "zh": "意义/重要性添加",
            "en": "Significance addition",
            "definition": (
                "增加论文没有支持的重要性评价（first/breakthrough/revolutionary/"
                "key/critical 等）。已有「major regulator」转述为「重要作用」通常不算。"
            ),
        },
        "relation_substitution": {
            "level1": "substitution",
            "zh": "关系替换",
            "en": "Relation substitution",
            "definition": (
                "改变科学关系类型：相关→因果、关联→调控、影响→决定。"
                "注意：contribute to / lead to / result in / drive 本身是因果动词，"
                "对等翻译不算替换。"
            ),
        },
        "magnitude_substitution": {
            "level1": "substitution",
            "zh": "作用程度替换",
            "en": "Magnitude substitution",
            "definition": (
                "改变作用强弱、重要程度或贡献大小（如 contributes → determines）。"
                "正常程度弱化（strongly increases → increases）通常不算失真。"
            ),
        },
        "mechanism_substitution": {
            "level1": "substitution",
            "zh": "机制替换",
            "en": "Mechanism substitution",
            "definition": (
                "将论文中的真实机制替换成另一种机制解释。"
                "同义表达（regulates ABA pathway → participates in ABA signaling）不算。"
            ),
        },
    },
    "no_distortion": "no_distortion",
    "severity": ["none", "mild", "moderate", "severe"],
    "uncovered": {
        "numerical_change": (
            "精确数值被方向性改动（如 60%→超六成、10 亿→14 亿），"
            "且不能归入 magnitude_substitution（程度词 contributes→determines）。"
        ),
        "semantic_contradiction": (
            "与论文科学含义正负相反，且不能归入 relation_substitution / "
            "mechanism_substitution。"
        ),
        "other": "现有 8 类无法覆盖的独立信息变化。",
    },
    "evidence_levels": ["With_Evidence", "Weak_Evidence", "No_Evidence"],
}


# ---------------------------------------------------------------------------
# 完整判据（正例/反例/判断流程/不可标注区/易混对照）—— 来源见仓库根目录
# 《植物科学科普文本信息失真标注规范 v0.1.md》《信息失真标签优先级和冲突决策树.md》
# 前端据此把右侧「失真判断指南」从一句定义升级为可对号入座的判据。
# ---------------------------------------------------------------------------

GUIDE: dict[str, Any] = {
    # 判断流程：先证据级别，再失真类型
    "judge_flow": [
        {
            "step": "Step 0",
            "q": "证据能否支撑细粒度比对？",
            "how": "完全找不到对应句 → No_Evidence；主题相关但证不充分 → Weak_Evidence；至少一句直接对应核心断言 → With_Evidence（继续）。不可核实 ≠ 已判定失真。",
        },
        {"step": "Step 1", "q": "公众号是否完全支持论文？", "how": "是 → No distortion，结束。"},
        {
            "step": "Step 2",
            "q": "是否改变论文已有科学关系？",
            "how": "关联→因果、间接→直接、机制被替换 → Substitution。",
        },
        {"step": "Step 3", "q": "是否增加论文没有的信息？", "how": "是 → Addition。"},
        {"step": "Step 4", "q": "是否删除论文重要限定？", "how": "条件 / 不确定性 / 机制 → Omission。"},
        {"step": "Step 5", "q": "是否存在第二个独立错误？", "how": "最多补一个 Secondary，不重复标同一个变化。"},
    ],
    # 不可标注区：这些不算失真，防止过度标注
    "no_distortion_guide": [
        {"name": "合理科学压缩", "p": "ABA activates SnRK2 kinases, which regulate downstream transcription factors…", "a": "ABA signaling regulates drought response.", "why": "机制细节减少，但科学关系保留"},
        {"name": "术语通俗化", "p": "reactive oxygen species accumulation", "a": "植物产生氧化压力", "why": "专业术语转通俗"},
        {"name": "同义表达转换", "p": "regulates", "a": "controls", "why": "普通语境下的同义"},
        {"name": "去除非关键实验细节", "p": "after 24 hours treatment", "a": "after treatment", "why": "处理细节概括，不算"},
        {"name": "正常程度弱化", "p": "strongly increases", "a": "increases", "why": "科普可能主动降低表达强度"},
        {"name": "一般背景知识补充", "p": "Plants use photosynthesis.", "a": "Plants use sunlight to produce energy.", "why": "非针对该论文的新 claim"},
    ],
    # 易混对照：遇到不好选时，按 rule 判断
    "confusions": [
        {"a": "mechanism_omission", "b": "mechanism_substitution", "rule": "删掉机制 → omission；换成另一种机制 → substitution"},
        {"a": "relation_substitution", "b": "magnitude_substitution", "rule": "关系类型没变、只是程度变（贡献→决定）→ magnitude；相关变因果 → relation"},
        {"a": "context_omission", "b": "mechanism_omission", "rule": "删物种/条件导致范围扩大 → context；删机制链 → mechanism"},
        {"a": "context_omission", "b": "evidence_uncertainty_omission", "rule": "删条件（范围）→ context；删 may/suggest（确定性）→ evidence"},
        {"a": "function_application_addition", "b": "significance_addition", "rule": "新增应用预测 → function；新增价值评价（first/breakthrough）→ significance"},
        {"a": "relation_substitution", "b": "mechanism_substitution", "rule": "机制被换成另一种 → mechanism（作 Primary）"},
    ],
    # 每类的核心判断问题 + 正例（pos）+ 反例（neg）
    "examples": {
        "context_omission": {
            "judge": "恢复被删信息后，结论适用范围是否会明显缩小？",
            "pos": [
                {"p": "In Arabidopsis seedlings, Gene X increased under salt stress.", "a": "Gene X helps plants respond to salt stress.", "why": "删「拟南芥幼苗」→ 变成所有植物"},
                {"p": "Gene X promotes resistance under drought stress conditions.", "a": "Gene X improves plant resistance.", "why": "删「干旱胁迫」条件"},
            ],
            "neg": [
                {"p": "Gene X increased 3.5-fold after 24 hours of treatment.", "a": "Gene X increased after treatment.", "why": "处理细节概括，不算失真"},
                {"p": "ABA activates SnRK2…", "a": "ABA signaling regulates drought response.", "why": "合理机制压缩"},
            ],
        },
        "evidence_uncertainty_omission": {
            "judge": "删除的信息是否影响「这个结论有多确定」？",
            "pos": [
                {"p": "Gene X may contribute to drought tolerance.", "a": "Gene X contributes to drought tolerance.", "why": "删 may → 显得更确定"},
                {"p": "These results suggest that Gene X regulates stress response.", "a": "Gene X regulates stress response.", "why": "删 suggest"},
            ],
            "neg": [
                {"p": "Gene X regulates drought tolerance.", "a": "Gene X definitely regulates drought tolerance.", "why": "加 definitely 是 Addition，不是删减"},
                {"p": "Gene X has not been fully characterized.", "a": "Gene X controls drought resistance.", "why": "未知→已知，是 Substitution"},
            ],
        },
        "mechanism_omission": {
            "judge": "删除机制后，科学关系是否被改变？",
            "pos": [
                {"p": "Protein A activates pathway B, which regulates transcription factor C and affects drought response.", "a": "Protein A controls drought resistance.", "why": "删 A-B-C 链 → 间接调控变直接"},
            ],
            "neg": [
                {"p": "ABA signaling regulates drought response through multiple pathways.", "a": "ABA signaling helps plants respond to drought.", "why": "合理总结，不算失真"},
            ],
        },
        "function_application_addition": {
            "judge": "是否提出论文实验没有支持的新功能/用途？",
            "pos": [
                {"p": "Gene X expression changes during drought stress.", "a": "Gene X improves drought resistance.", "why": "新增「抗旱功能」"},
                {"p": "Gene X is involved in stress response.", "a": "Gene X can be used to breed drought-resistant crops.", "why": "新增「育种应用」"},
            ],
            "neg": [
                {"p": "Gene X improves drought resistance.", "a": "Gene X may help crop breeding.", "why": "已有应用暗示，不一定错"},
            ],
        },
        "significance_addition": {
            "judge": "是否增加论文没有支持的重要性评价（first/breakthrough/key…）？",
            "pos": [
                {"p": "We identified Gene X involved in drought response.", "a": "Scientists discovered the world's first drought resistance gene.", "why": "新增「first」"},
            ],
            "neg": [
                {"p": "Gene X is a major regulator.", "a": "Gene X plays an important role.", "why": "已有意义，不算"},
            ],
        },
        "relation_substitution": {
            "judge": "是否改变科学关系类型（相关→因果、关联→调控）？",
            "pos": [
                {"p": "Gene X is associated with drought response.", "a": "Gene X controls drought resistance.", "why": "相关 → 因果"},
            ],
            "neg": [
                {"p": "Gene X regulates drought response.", "a": "Gene X is involved in drought response.", "why": "只是弱化，不算替换"},
            ],
        },
        "magnitude_substitution": {
            "judge": "是否改变作用强弱 / 贡献大小？",
            "pos": [
                {"p": "Gene X contributes to drought tolerance.", "a": "Gene X determines drought resistance.", "why": "贡献 → 决定"},
            ],
            "neg": [
                {"p": "Gene X strongly affects drought tolerance.", "a": "Gene X affects drought tolerance.", "why": "弱化不算失真"},
            ],
        },
        "mechanism_substitution": {
            "judge": "是否把论文真实机制换成另一种机制？",
            "pos": [
                {"p": "Gene X affects drought response through ABA signaling.", "a": "Gene X directly protects plant cells from dehydration.", "why": "机制被替换"},
            ],
            "neg": [
                {"p": "Gene X regulates ABA pathway.", "a": "Gene X participates in ABA signaling.", "why": "同义表达，不算"},
            ],
        },
    },
}


# ---------------------------------------------------------------------------
# 文件发现（按文件粒度分工：每人负责不同评测文件）
# ---------------------------------------------------------------------------

def discover_reviewables() -> list[Path]:
    """扫描 data/annotations 下的可审核文件。

    两类可审核文件：
    - ``<PAPER>_<ARTICLE>_annotation_draft.json`` —— 召回审核（文件1）
    - ``<PAPER>_<ARTICLE>_distortion_review.json`` —— 失真审核（文件2，由 regenerate_analysis.py 产出）

    _translated / _draft_2 / _readable 等中间产物一律不扫。
    ``*_recall_review.json`` 只作为召回页的附加结果，不单独打开。
    """
    base = ROOT / "data" / "annotations"
    if not base.exists():
        return []
    out: set[Path] = set()
    for pattern in ("*_annotation_draft.json", "*_distortion_review.json"):
        for p in base.rglob(pattern):
            if ".bak" in p.name:
                continue
            out.add(p)
    return sorted(out)


def detect_mode(draft_path: Path) -> str:
    """按文件名判定审核阶段：文件2 失真审核，否则召回审核。"""
    return "distortion" if draft_path.name.endswith("_distortion_review.json") else "recall"


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def load_draft(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


_DRAFT_SUFFIX = "_annotation_draft.json"
_RECALL_REVIEW_SUFFIX = "_recall_review.json"
_OPT_VERDICTS = frozenset(("supported", "partial", "no_evidence"))


def recall_review_path_for_draft(draft_path: Path) -> Path | None:
    """同目录下把初稿文件名换成 ``*_recall_review.json``。非初稿返回 None。"""
    name = draft_path.name
    if not name.endswith(_DRAFT_SUFFIX):
        return None
    stem = name[: -len(_DRAFT_SUFFIX)]
    return draft_path.with_name(stem + _RECALL_REVIEW_SUFFIX)


def load_optimized_recall(path: Path) -> dict[str, dict[str, Any]]:
    """按 sample_id 读取优化召回。文件不存在或无法解析时返回空表。"""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("优化召回文件无法解析，已忽略: %s (%s)" % (path, exc), file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        return {}
    found: dict[str, dict[str, Any]] = {}
    for sample in data.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        sample_id = str(sample.get("sample_id") or "").strip()
        if not sample_id:
            continue
        found[sample_id] = _slim_optimized_recall(sample)
    return found


def attach_optimized_recall(samples: list[dict[str, Any]], draft_path: Path) -> Path | None:
    """把优化召回挂到内存样本上。不写回初稿。未生成的样本为 None。"""
    path = recall_review_path_for_draft(draft_path)
    index = load_optimized_recall(path) if path is not None else {}
    for sample in samples:
        sample_id = str(sample.get("sample_id") or "")
        sample["optimized_recall"] = index.get(sample_id)
    if path is not None and path.is_file():
        return path
    return None


def _slim_optimized_recall(sample: dict[str, Any]) -> dict[str, Any]:
    evidences: list[dict[str, Any]] = []
    for item in sample.get("evidences") or []:
        if not isinstance(item, dict):
            continue
        start = _as_int(item.get("sentence_id_start"))
        end = _as_int(item.get("sentence_id_end"))
        if start is None or end is None:
            continue
        if end < start:
            start, end = end, start
        anchors = item.get("anchor_source") or []
        if not isinstance(anchors, list):
            anchors = []
        evidences.append(
            {
                "sentence_id_start": start,
                "sentence_id_end": end,
                "confidence": item.get("confidence"),
                "anchor_source": anchors,
                "evidence_text": str(item.get("evidence_text") or ""),
            }
        )
    verdict = str(sample.get("verdict") or "").strip().lower()
    if verdict not in _OPT_VERDICTS:
        verdict = "no_evidence"
    return {
        "claim": str(sample.get("claim") or ""),
        "verdict": verdict,
        "explanation": str(sample.get("explanation") or ""),
        "need_human_review": bool(sample.get("need_human_review")),
        "evidences": evidences,
    }


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


def _sample_claim_original(samples: list[dict[str, Any]], sample_id: str) -> str:
    """冻结原文：样本上的 claim_zh_original 优先，否则退回 claim_zh。"""
    for sample in samples or []:
        if str(sample.get("sample_id") or "") == sample_id:
            original = str(sample.get("claim_zh_original") or "").strip()
            if original:
                return original
            return str(sample.get("claim_zh") or "").strip()
    return ""


def _apply_claim_revision(
    record: dict[str, Any],
    sample_claim: str,
    revised: Any,
    *,
    keep_blank: bool = False,
) -> None:
    """原始句只在第一次写入时从样本抄下，之后不再改。配对句单独存放。

    失真审核里清空观点句时保留空白，不把抽取原文写进判断句。
    """
    original = str(record.get("claim_zh_original") or "").strip()
    if not original:
        original = str(sample_claim or "").strip()
        record["claim_zh_original"] = original
    text = str(revised or "").strip()
    if not text and not keep_blank:
        text = original
    record["claim_zh_revised"] = text
    record["claim_changed"] = text != original
    record["claim_zh"] = text


def load_sentence_index(paper_id: str) -> dict[int, str]:
    """读取某篇论文的句表（**只读**），构建 sentence_id -> 文本 索引。

    句表位置：``data/annotations/{paper_id}/{paper_id}_sentences.csv``。
    仅用于前端「点击证据句展开上下文」，绝不改写 CSV。
    空 sentence_id 的行（如 front_matter / 作者 / 日期行）跳过。
    """
    if not paper_id:
        return {}
    csv_path = ROOT / "data" / "annotations" / paper_id / (paper_id + "_sentences.csv")
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


def _pick_sample_fields(sample: dict[str, Any]) -> dict[str, Any]:
    """抽出前端需要的字段，去掉无关的重字段。"""
    sr = sample.get("system_retrieval") or {}
    classify = sr.get("classify_evidences") or []
    review = sr.get("review_evidences") or []

    def slim_ev(ev: dict[str, Any]) -> dict[str, Any]:
        return {
            "rank": ev.get("rank"),
            "sentence_id": ev.get("sentence_id"),
            "text": ev.get("text") or "",
            "text_zh": ev.get("text_zh") or "",
        }

    gr = sample.get("gold_retrieval") or {}
    gc = sample.get("gold_classification") or {}
    analysis = sample.get("analysis") or {}

    return {
        "sample_id": sample.get("sample_id") or "",
        "claim_zh": sample.get("claim_zh") or "",
        "claim_zh_original": sample.get("claim_zh_original") or "",
        "human_verified": bool(sample.get("human_verified")),
        "human_note": sample.get("human_note") or "",
        "classify_evidences": [slim_ev(e) for e in classify],
        "review_evidences": [slim_ev(e) for e in review],
        "gold_retrieval": {
            "sentence_ids": gr.get("sentence_ids") or [],
            "is_answerable": gr.get("is_answerable"),
        },
        "gold_classification": {
            "evidence_level": gc.get("evidence_level") or "",
            "has_distortion": gc.get("has_distortion"),
            "primary_label": gc.get("primary_label") or {},
            "secondary_label": gc.get("secondary_label") or {},
            "severity": gc.get("severity"),
            "needs_manual_review": bool(gc.get("needs_manual_review")),
            "uncovered_phenomenon": gc.get("uncovered_phenomenon") or "",
            "reason": gc.get("reason") or "",
        },
        "analysis": {
            "evidence_judgement": analysis.get("evidence_judgement") or "",
            "classification_reason": analysis.get("classification_reason") or "",
            "key_differences": analysis.get("key_differences") or [],
            "rag_review": analysis.get("rag_review") or {},
            "unsupported_diagnosis": analysis.get("unsupported_diagnosis") or {},
            "manual_check_hints": analysis.get("manual_check_hints") or "",
            "needs_manual_review": bool(analysis.get("needs_manual_review")),
            "review_focus": analysis.get("review_focus") or [],
            "ai_confidence": analysis.get("ai_confidence") or "",
        },
    }


def _norm_manual_paragraphs(raw: Any) -> list[dict[str, str]]:
    """规范化人工找回原文段落：保留 text（人工填）+ text_zh（脚本补的翻译）。

    前端会回传完整对象（含 text_zh），这里只做白名单字段清洗，
    避免把脚本已补好的翻译清掉。
    """
    out: list[dict[str, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "text": text,
            "text_zh": str(item.get("text_zh") or "").strip(),
        })
    return out


def _apply_revision_fields(item: dict[str, Any], incoming: dict[str, Any]) -> None:
    """只写 text_revised / text_zh_revised。与原文相同或空串则删掉修订，不动 text / text_zh。"""
    if "text_revised" in incoming:
        revised = str(incoming.get("text_revised") or "").strip()
        original = str(item.get("text") or "").strip()
        if not revised or revised == original:
            item.pop("text_revised", None)
        else:
            item["text_revised"] = revised
    if "text_zh_revised" in incoming:
        revised = str(incoming.get("text_zh_revised") or "").strip()
        original = str(item.get("text_zh") or "").strip()
        if not revised or revised == original:
            item.pop("text_zh_revised", None)
        else:
            item["text_zh_revised"] = revised


def _apply_evidence_revisions(record: dict[str, Any], payload: dict[str, Any]) -> None:
    """按 sentence_id / 人工段序号把修订合并进已有数组，不新建证据、不改原文。"""
    gold_in = payload.get("gold_evidence_revisions")
    if isinstance(gold_in, list):
        by_sid: dict[str, dict[str, Any]] = {}
        for item in gold_in:
            if isinstance(item, dict) and item.get("sentence_id") is not None:
                by_sid[str(item.get("sentence_id")).strip()] = item
        gold = record.get("gold_evidences")
        if isinstance(gold, list):
            for ev in gold:
                if not isinstance(ev, dict):
                    continue
                incoming = by_sid.get(str(ev.get("sentence_id")).strip())
                if incoming:
                    _apply_revision_fields(ev, incoming)
    manual_in = payload.get("manual_paragraph_revisions")
    if isinstance(manual_in, list):
        by_idx: dict[int, dict[str, Any]] = {}
        for item in manual_in:
            if not isinstance(item, dict):
                continue
            try:
                by_idx[int(item.get("index"))] = item
            except (TypeError, ValueError):
                continue
        manual = record.get("manual_retrieved_paragraphs")
        if isinstance(manual, list):
            for index, para in enumerate(manual):
                if isinstance(para, dict) and index in by_idx:
                    _apply_revision_fields(para, by_idx[index])


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "ReviewServer/1.0"

    # 由 main() 注入的共享状态
    samples: list[dict[str, Any]] = []
    taxonomy: dict[str, Any] = TAXONOMY
    human_reviews: dict[str, Any] = {}
    sentences: dict[int, str] = {}
    draft_path: Path | None = None
    meta: dict[str, Any] = {}
    mode: str = "recall"  # 'recall' | 'distortion' | 'normalize'
    normalize_books: dict[str, dict[str, Any]] = {}

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.address_string(), fmt % args))

    # -- 响应辅助 --

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # -- 持久化：写回评测文件 human_reviews 键 --

    def _persist(self) -> None:
        if self.draft_path is None:
            return
        draft = load_draft(self.draft_path)
        draft["human_reviews"] = self.human_reviews
        tmp = self.draft_path.with_suffix(self.draft_path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False, indent=2)
            f.write("\n")
        tmp.replace(self.draft_path)

    def _save_normalize(self) -> None:
        payload = self._read_body()
        paper_id = str(payload.get("paper_id") or "").strip()
        sample_id = str(payload.get("sample_id") or "").strip()
        reviewer = str(payload.get("reviewer") or "").strip()
        book = (self.normalize_books or {}).get(paper_id)
        if book is None or not sample_id or not reviewer:
            self._send_json({"ok": False, "error": "缺少论文、样本或审核人"}, 400)
            return
        draft = book["draft"]
        record = ((draft.get("human_reviews") or {}).get(reviewer) or {}).get(sample_id)
        norm = record.get("recall_normalization") if isinstance(record, dict) else None
        if not isinstance(norm, dict):
            self._send_json({"ok": False, "error": "找不到这条整理记录"}, 404)
            return
        units = {
            unit.get("unit_id"): unit
            for unit in (norm.get("evidence_units") or [])
            if isinstance(unit, dict)
        }
        for item in payload.get("units") or []:
            if not isinstance(item, dict):
                continue
            unit = units.get(item.get("unit_id"))
            role = str(item.get("role") or "")
            if unit is None or role not in _NORM_ROLES:
                continue
            unit["role"] = role
            unit["needs_human"] = role == "unsure"
            unit["role_source"] = "human"
        if "term_table_needed" in payload:
            norm["term_table_needed"] = bool(payload.get("term_table_needed"))
        if "term_links" in payload:
            norm["term_links"] = _clean_term_links(payload.get("term_links"))
        if "term_gaps" in payload:
            norm["term_gaps"] = _clean_term_gaps(payload.get("term_gaps"))
        sample = next(
            (row for row in (draft.get("samples") or []) if row.get("sample_id") == sample_id),
            None,
        )
        changed_units = _apply_citation_decisions(
            norm.get("citation_queue") or [],
            payload.get("citation_decisions") or [],
            units,
            record,
            sample if isinstance(sample, dict) else None,
        )
        support_ids = {uid for uid, unit in units.items() if unit.get("role") == "support"}
        regrouped = []
        for group in norm.get("support_groups") or []:
            if not isinstance(group, list):
                continue
            kept = [uid for uid in group if uid in support_ids]
            if kept:
                regrouped.append(kept)
        norm["support_groups"] = regrouped
        if payload.get("confirmed"):
            norm["normalization_reviewed"] = True
            norm["normalization_reviewed_at"] = datetime.now().isoformat(timespec="seconds")
            norm["background_removal"] = "human_confirmed"
        elif "confirmed" in payload:
            norm["normalization_reviewed"] = False
        if changed_units:
            _retranslate_units(changed_units)
        norm["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _write_draft(book["path"], draft)
        saved = build_normalize_items({paper_id: book})
        item = next((row for row in saved if row["sample_id"] == sample_id and row["reviewer"] == reviewer), None)
        self._send_json({"ok": True, "item": item})

    # -- 路由 --

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/recall.html", "/distortion.html", "/normalize.html"):
            if self.mode == "normalize":
                html_name = "normalize.html"
            else:
                html_name = "distortion.html" if self.mode == "distortion" else "recall.html"
            html = UI_DIR / html_name
            if html.exists():
                self._send_file(html, "text/html; charset=utf-8")
            else:
                self._send_json({"error": "前端文件缺失: %s" % html}, 500)
        elif path == "/style.css":
            css = UI_DIR / "style.css"
            if css.exists():
                self._send_file(css, "text/css; charset=utf-8")
            else:
                self._send_json({"error": "前端文件缺失: %s" % css}, 500)
        elif path == "/api/data":
            if self.mode == "normalize":
                self._send_json(
                    {
                        "meta": self.meta,
                        "items": build_normalize_items(self.normalize_books),
                    }
                )
            else:
                self._send_json(
                    {
                        "meta": self.meta,
                        "samples": self.samples,
                        "taxonomy": self.taxonomy,
                        "human_reviews": self.human_reviews,
                        "sentences": self.sentences,
                    }
                )
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if self.mode == "normalize":
            if path != "/api/save":
                self._send_json({"error": "not found"}, 404)
                return
            self._save_normalize()
            return
        if path != "/api/save":
            self._send_json({"error": "not found"}, 404)
            return
        payload = self._read_body()
        reviewer = str(payload.get("reviewer") or "").strip()
        sample_id = str(payload.get("sample_id") or "").strip()
        if not reviewer:
            self._send_json({"ok": False, "error": "缺少审核人 reviewer"}, 400)
            return
        if not sample_id:
            self._send_json({"ok": False, "error": "缺少 sample_id"}, 400)
            return
        # 合并语义：从已有记录出发，只更新 payload 里「显式出现」的白名单字段。
        # 这样第一屏保存召回字段不会清掉第二屏的失真字段，也不会清掉
        # regenerate_analysis.py 写回的 generated_analysis / 翻译。
        existing = (self.human_reviews.get(reviewer) or {}).get(sample_id) or {}
        record = dict(existing)

        if "human_verified" in payload:
            record["human_verified"] = bool(payload.get("human_verified"))
        if "evidence_level" in payload:
            record["evidence_level"] = str(payload.get("evidence_level") or "").strip()
        if "primary_level2" in payload:
            record["primary_level2"] = str(payload.get("primary_level2") or "").strip()
        if "secondary_level2" in payload:
            record["secondary_level2"] = str(payload.get("secondary_level2") or "").strip()
        if "severity" in payload:
            record["severity"] = str(payload.get("severity") or "").strip()
        if "uncovered_phenomenon" in payload:
            record["uncovered_phenomenon"] = str(payload.get("uncovered_phenomenon") or "").strip()
        if "note" in payload:
            record["note"] = str(payload.get("note") or "").strip()
        if "gold_sentence_ids" in payload:
            record["gold_sentence_ids"] = [
                int(x) for x in (payload.get("gold_sentence_ids") or [])
                if isinstance(x, int) or (isinstance(x, str) and x.strip().isdigit())
            ]
        if "recall_reviewed" in payload:
            record["recall_reviewed"] = bool(payload.get("recall_reviewed"))
        if "recall_note" in payload:
            record["recall_note"] = str(payload.get("recall_note") or "").strip()
        if "manual_retrieved_paragraphs" in payload:
            record["manual_retrieved_paragraphs"] = _norm_manual_paragraphs(
                payload.get("manual_retrieved_paragraphs")
            )
        if "claim_zh" in payload or "claim_zh_revised" in payload:
            revised = payload.get("claim_zh_revised") if "claim_zh_revised" in payload else payload.get("claim_zh")
            _apply_claim_revision(
                record,
                _sample_claim_original(self.samples, sample_id),
                revised,
                keep_blank=(self.mode == "distortion"),
            )
        if "claim_zh_corrected" in payload:
            corrected = str(payload.get("claim_zh_corrected") or "").strip()
            revised_now = str(record.get("claim_zh_revised") or "").strip()
            record["claim_zh_corrected"] = "" if (not corrected or corrected == revised_now) else corrected
        if "gold_evidence_revisions" in payload or "manual_paragraph_revisions" in payload:
            _apply_evidence_revisions(record, payload)
        if "weak_evidence" in payload:
            record["weak_evidence"] = bool(payload.get("weak_evidence"))
        if "review_decision" in payload:
            decision = str(payload.get("review_decision") or "").strip().lower()
            record["review_decision"] = "drop" if decision == "drop" else "keep"
        if record.get("weak_evidence"):
            record["review_decision"] = "drop"
        elif "weak_evidence" in payload:
            record["review_decision"] = "keep"

        # 召回步骤相关字段有更新时，刷新 recall_updated_at（脚本据此判断是否需二次生成）。
        # 失真界面改的是对照文本修订稿，不应当成召回变更。
        if self.mode != "distortion" and any(
            k in payload
            for k in (
                "gold_sentence_ids",
                "recall_reviewed",
                "recall_note",
                "manual_retrieved_paragraphs",
                "claim_zh",
                "claim_zh_revised",
                "weak_evidence",
                "review_decision",
            )
        ):
            record["recall_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        record["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 更新内存：当前审核人名下这条样本的记录，不动其他人的
        self.human_reviews.setdefault(reviewer, {})[sample_id] = record
        self._persist()
        self._send_json({"ok": True, "reviewer": reviewer, "record": record})


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

_NORM_ROLES = {"support", "qualifier", "background", "unsure"}
_CITE_SPLIT = re.compile(r"^(?P<prefix>.*?)(?P<cite>\d.*)$", re.DOTALL)


def _write_draft(path: Path, draft: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def discover_normalize_drafts() -> list[Path]:
    """只打开已经写过 recall_normalization 的召回草稿。"""
    base = ROOT / "data" / "annotations"
    if not base.exists():
        return []
    found: list[Path] = []
    for path in sorted(base.glob("*/*_annotation_draft.json")):
        if ".bak" in path.name:
            continue
        try:
            with path.open(encoding="utf-8") as f:
                chunk = f.read(1024 * 1024)
                has_key = '"recall_normalization"' in chunk
                if not has_key:
                    has_key = '"recall_normalization"' in f.read()
        except OSError:
            continue
        if has_key:
            found.append(path)
    return found


def _split_surface(surface: str) -> tuple[str, str]:
    match = _CITE_SPLIT.match(surface or "")
    if not match:
        return surface or "", ""
    return match.group("prefix"), match.group("cite")


def _clean_term_links(raw: Any) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return links
    for item in raw:
        if not isinstance(item, dict):
            continue
        claim_surface = str(item.get("claim_surface") or "").strip()
        paper_surface = str(item.get("paper_surface") or "").strip()
        paper_abbr = str(item.get("paper_abbr") or "").strip()
        if not (claim_surface or paper_surface or paper_abbr):
            continue
        links.append(
            {
                "claim_surface": claim_surface,
                "paper_surface": paper_surface,
                "paper_abbr": paper_abbr,
                "source": "human",
            }
        )
    return links


def _clean_term_gaps(raw: Any) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return gaps
    for item in raw:
        if not isinstance(item, dict) or item.get("discarded"):
            continue
        claim_surface = str(item.get("claim_surface") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not claim_surface and not reason:
            continue
        gaps.append(
            {
                "claim_surface": claim_surface,
                "reason": reason,
                "written": False,
            }
        )
    return gaps


def _sentence_id(obj: dict[str, Any]) -> int | None:
    try:
        return int(obj.get("sentence_id"))
    except (TypeError, ValueError):
        return None


def _replace_surface(text: str, surface: str, prefix: str) -> str:
    if surface and surface in (text or ""):
        return text.replace(surface, prefix, 1)
    return text


def _restore_surface(text: str, surface: str, prefix: str) -> str:
    if not text or not surface or surface in text:
        return text
    if prefix == ")" and ")" in text:
        return text.replace(")", surface, 1)
    if len(prefix) >= 4 and prefix in text:
        return text.replace(prefix, surface, 1)
    return text


def _citation_slots(
    entry: dict[str, Any],
    units: dict[Any, dict[str, Any]],
    record: dict[str, Any],
    sample: dict[str, Any] | None,
) -> list[tuple[dict[str, Any], str]]:
    slots: list[tuple[dict[str, Any], str]] = [(unit, "text") for unit in units.values()]
    where = str(entry.get("where") or "")
    if where.startswith("manual:"):
        try:
            index = int(where.split(":", 1)[1])
        except ValueError:
            index = -1
        manuals = record.get("manual_retrieved_paragraphs") or []
        if 0 <= index < len(manuals) and isinstance(manuals[index], dict):
            slots.append((manuals[index], "text"))
    if where.startswith("gold:") and sample is not None:
        try:
            sid = int(where.split(":", 1)[1])
        except ValueError:
            sid = -1
        retrieval = sample.get("system_retrieval") or {}
        for key in ("review_evidences", "classify_evidences"):
            for evidence in retrieval.get(key) or []:
                if isinstance(evidence, dict) and _sentence_id(evidence) == sid:
                    slots.append((evidence, "text"))
        for snap in record.get("gold_sentence_snapshots") or []:
            if isinstance(snap, dict) and _sentence_id(snap) == sid:
                slots.append((snap, "text"))
    return slots


def _apply_citation_decisions(
    queue: list[Any],
    decisions: list[Any],
    units: dict[Any, dict[str, Any]],
    record: dict[str, Any],
    sample: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    chosen: dict[int, str] = {}
    for item in decisions:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        decision = str(item.get("decision") or "")
        if decision in ("citation", "keep"):
            chosen[index] = decision
    changed: list[dict[str, Any]] = []
    for index, entry in enumerate(queue):
        if not isinstance(entry, dict) or index not in chosen:
            continue
        decision = chosen[index]
        surface = str(entry.get("surface") or "")
        prefix, cite = _split_surface(surface)
        entry["decision"] = decision
        entry["prefix"] = prefix
        mutate = _replace_surface if decision == "citation" and not entry.get("applied") else None
        if decision == "keep" and entry.get("applied"):
            mutate = _restore_surface
        if mutate is None:
            continue
        touched: list[dict[str, Any]] = []
        for holder, key in _citation_slots(entry, units, record, sample):
            before = str(holder.get(key) or "")
            after = mutate(before, surface, prefix)
            if after == before:
                continue
            if decision == "citation" and not holder.get("text_before_citation_review"):
                holder["text_before_citation_review"] = before
            holder[key] = after
            if any(holder is unit for unit in units.values()):
                touched.append(holder)
        if decision == "citation":
            entry["applied"] = True
            entry["removed"] = cite
        else:
            entry["applied"] = False
        changed.extend(touched)
    return changed


def _translation_tools():
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    from translate_text_fields import cache_get, cache_put, load_cache, save_cache, translate_youdao

    return cache_get, cache_put, load_cache, save_cache, translate_youdao


def _retranslate_units(units: list[dict[str, Any]]) -> None:
    try:
        cache_get, cache_put, load_cache, save_cache, translate_youdao = _translation_tools()
    except Exception as exc:
        print("译文未能更新：%s" % exc, flush=True)
        return
    cache = load_cache()
    wrote = False
    for unit in units:
        text = str(unit.get("text") or "").strip()
        if not text:
            continue
        zh = cache_get(cache, text) or translate_youdao(text)
        if not zh:
            continue
        unit["text_zh"] = zh
        wrote = cache_put(cache, text, zh) or wrote
        time.sleep(0.2)
    if wrote:
        save_cache(cache)


def _seed_unit_zh(unit: dict[str, Any], record: dict[str, Any], sample: dict[str, Any]) -> str:
    """整句已有中文时直接借用。拆开的长段落不对齐，不把整段译文贴到每一句上。"""
    if unit.get("source") != "manual":
        sid = unit.get("sentence_id")
        retrieval = (sample or {}).get("system_retrieval") or {}
        for key in ("review_evidences", "classify_evidences"):
            for evidence in retrieval.get(key) or []:
                if not isinstance(evidence, dict):
                    continue
                if evidence.get("sentence_id") == sid and str(evidence.get("text_zh") or "").strip():
                    return str(evidence.get("text_zh")).strip()
        return ""
    try:
        index = int(unit.get("manual_index"))
        paragraph = (record.get("manual_retrieved_paragraphs") or [])[index]
    except (TypeError, ValueError, IndexError):
        return ""
    if not isinstance(paragraph, dict):
        return ""
    paragraph_zh = str(paragraph.get("text_zh") or "").strip()
    if not paragraph_zh:
        return ""
    same = [
        row
        for row in (record.get("recall_normalization") or {}).get("evidence_units") or []
        if isinstance(row, dict) and row.get("manual_index") == index
    ]
    if len(same) == 1:
        return paragraph_zh
    return ""


def fill_missing_translations(books: dict[str, dict[str, Any]]) -> int:
    """给还没有中文的召回句补译文，并写回草稿。已有译文不重翻。"""
    try:
        cache_get, cache_put, load_cache, save_cache, translate_youdao = _translation_tools()
    except Exception as exc:
        print("翻译不可用，页面仍会打开，缺译文的句子会标明：%s" % exc, flush=True)
        return 0
    cache = load_cache()
    pending: list[tuple[str, dict[str, Any]]] = []
    changed: set[str] = set()
    for paper_id, book in books.items():
        draft = book["draft"]
        samples = {
            row.get("sample_id"): row
            for row in (draft.get("samples") or [])
            if isinstance(row, dict)
        }
        for records in (draft.get("human_reviews") or {}).values():
            if not isinstance(records, dict):
                continue
            for sample_id, record in records.items():
                if not isinstance(record, dict):
                    continue
                norm = record.get("recall_normalization")
                if not isinstance(norm, dict):
                    continue
                sample = samples.get(sample_id) or {}
                for unit in norm.get("evidence_units") or []:
                    if not isinstance(unit, dict):
                        continue
                    if str(unit.get("text_zh") or "").strip():
                        cache_put(cache, unit.get("text") or "", unit["text_zh"])
                        continue
                    hit = cache_get(cache, unit.get("text") or "")
                    if hit:
                        unit["text_zh"] = hit
                        changed.add(paper_id)
                        continue
                    seeded = _seed_unit_zh(unit, record, sample)
                    if seeded:
                        unit["text_zh"] = seeded
                        cache_put(cache, unit.get("text") or "", seeded)
                        changed.add(paper_id)
                        continue
                    pending.append((paper_id, unit))
    done = 0
    failures = 0
    failed: list[tuple[str, dict[str, Any]]] = []
    print("  需要新翻译 %d 句" % len(pending), flush=True)

    def _translate_one(paper_id: str, unit: dict[str, Any]) -> bool:
        nonlocal done, wrote_cache
        text = str(unit.get("text") or "").strip()
        zh = translate_youdao(text) if text else None
        if not zh:
            return False
        unit["text_zh"] = zh
        if cache_put(cache, text, zh):
            wrote_cache = True
        changed.add(paper_id)
        done += 1
        return True

    wrote_cache = False
    for index, (paper_id, unit) in enumerate(pending, 1):
        print("  翻译 %d/%d" % (index, len(pending)), flush=True)
        if _translate_one(paper_id, unit):
            failures = 0
        else:
            failures += 1
            failed.append((paper_id, unit))
            if failures >= 12:
                print("  连续翻译失败，先跳过剩余，稍后再试。", flush=True)
                failed.extend(pending[index:])
                break
        if index % 10 == 0 and (wrote_cache or changed):
            save_cache(cache)
            wrote_cache = False
            for paper in changed:
                book = books[paper]
                _write_draft(book["path"], book["draft"])
        time.sleep(0.2)
    if failed:
        print("  重试刚才失败的 %d 句" % len(failed), flush=True)
        time.sleep(2)
        for paper_id, unit in failed:
            if str(unit.get("text_zh") or "").strip():
                continue
            _translate_one(paper_id, unit)
            time.sleep(0.2)
    save_cache(cache)
    for paper_id in changed:
        book = books[paper_id]
        _write_draft(book["path"], book["draft"])
    return done


def build_normalize_items(books: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for paper_id, book in books.items():
        draft = book["draft"]
        for reviewer, records in (draft.get("human_reviews") or {}).items():
            if not isinstance(records, dict):
                continue
            for sample_id, record in records.items():
                if not isinstance(record, dict):
                    continue
                norm = record.get("recall_normalization")
                if not isinstance(norm, dict):
                    continue
                units = []
                for unit in norm.get("evidence_units") or []:
                    if not isinstance(unit, dict):
                        continue
                    units.append(
                        {
                            "unit_id": unit.get("unit_id"),
                            "source": "manual" if unit.get("source") == "manual" else "gold",
                            "text": unit.get("text") or "",
                            "text_zh": unit.get("text_zh") or "",
                            "role": unit.get("role") if unit.get("role") in _NORM_ROLES else "unsure",
                            "reason": unit.get("reason") or "",
                        }
                    )
                queue = []
                for entry in norm.get("citation_queue") or []:
                    if not isinstance(entry, dict):
                        continue
                    surface = str(entry.get("surface") or "")
                    prefix, _cite = _split_surface(surface)
                    owner = ""
                    for unit in units:
                        text = unit["text"]
                        if surface and surface in text:
                            owner = unit["unit_id"]
                            break
                    if not owner and entry.get("applied") and prefix:
                        for unit in units:
                            if prefix in unit["text"]:
                                owner = unit["unit_id"]
                                break
                    queue.append(
                        {
                            "rule": entry.get("rule") or "",
                            "surface": surface,
                            "prefix": prefix,
                            "context": entry.get("context") or surface,
                            "decision": entry.get("decision") or "",
                            "applied": bool(entry.get("applied")),
                            "unit_id": owner,
                        }
                    )
                links = []
                for link in norm.get("term_links") or []:
                    if not isinstance(link, dict):
                        continue
                    links.append(
                        {
                            "claim_surface": link.get("claim_surface") or "",
                            "paper_surface": link.get("paper_surface") or "",
                            "paper_abbr": link.get("paper_abbr") or "",
                        }
                    )
                gaps = []
                for gap in norm.get("term_gaps") or []:
                    if not isinstance(gap, dict):
                        continue
                    gaps.append(
                        {
                            "claim_surface": gap.get("claim_surface") or "",
                            "paper_surface": gap.get("paper_surface") or "",
                            "paper_abbr": gap.get("paper_abbr") or "",
                            "reason": gap.get("reason") or "",
                            "discarded": False,
                        }
                    )
                items.append(
                    {
                        "paper_id": paper_id,
                        "sample_id": sample_id,
                        "reviewer": reviewer,
                        "claim_zh": record.get("claim_zh") or "",
                        "confirmed": bool(norm.get("normalization_reviewed")),
                        "units": units,
                        "support_groups": norm.get("support_groups") or [],
                        "term_table_needed": bool(norm.get("term_table_needed")),
                        "term_links": links,
                        "term_gaps": gaps,
                        "citation_queue": queue,
                    }
                )
    items.sort(key=lambda row: (row["paper_id"], row["sample_id"]))
    return items


def run_normalize(args: argparse.Namespace) -> int:
    paths = discover_normalize_drafts()
    if not paths:
        raise SystemExit("没有找到带召回整理结果的草稿。")
    books: dict[str, dict[str, Any]] = {}
    for path in paths:
        draft = load_draft(path)
        paper_id = str(draft.get("paper_id") or path.parent.name)
        books[paper_id] = {"path": path, "draft": draft}
    print("正在补全缺中文的召回句…", flush=True)
    translated = fill_missing_translations(books)
    print("新补中文 %d 句" % translated, flush=True)
    handler = ReviewHandler
    handler.mode = "normalize"
    handler.normalize_books = books
    handler.meta = {
        "mode": "normalize",
        "papers": sorted(books),
        "hint": "核对哪些召回句留下，以及专有名词对照是否正确。中文在上，英文在下。",
    }
    port = args.port or 8766
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = "http://127.0.0.1:%d/" % port
    print("=" * 56)
    print("  召回整理审核")
    print("  papers : %s" % ", ".join(sorted(books)))
    print("  url    : %s" % url)
    print("  quit   : Ctrl+C")
    print("=" * 56, flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def _pick_interactive() -> Path | None:
    files = discover_reviewables()
    if not files:
        return None
    if len(files) == 1:
        return files[0]
    print("可审核的评测文件：")
    for i, p in enumerate(files, 1):
        rel = p.relative_to(ROOT) if str(p).startswith(str(ROOT)) else p
        print("  [%d] %s" % (i, rel))
    while True:
        try:
            raw = input("输入序号（默认 1）: ").strip()
            idx = int(raw) if raw else 1
            if 1 <= idx <= len(files):
                return files[idx - 1]
        except (ValueError, EOFError):
            pass
        print("序号无效，请重试。")


def main() -> int:
    try:  # Windows 控制台中文正常显示（配合 start_review.bat 的 chcp 65001）
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="标注草稿本地审核服务器（零依赖）")
    parser.add_argument("--draft", default="", help="评测文件 JSON 路径")
    parser.add_argument("--list", action="store_true", help="列出可审核文件后退出")
    parser.add_argument("--port", type=int, default=None, help="端口，召回/失真默认 8765，整理审核默认 8766")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--normalize", action="store_true", help="打开召回整理审核（已有 recall_normalization 的草稿）")
    args = parser.parse_args()

    if args.normalize:
        return run_normalize(args)

    if args.list:
        files = discover_reviewables()
        if not files:
            print("未找到可审核文件（data/annotations 下无 *_annotation_draft.json / *_distortion_review.json）。")
        else:
            print("可审核的评测文件（共 %d 份）：" % len(files))
            for i, p in enumerate(files, 1):
                rel = p.relative_to(ROOT) if str(p).startswith(str(ROOT)) else p
                print("  [%d] %s" % (i, rel))
        return 0

    if args.draft:
        draft_path = Path(args.draft)
        if not draft_path.is_absolute():
            draft_path = ROOT / draft_path
        draft_path = draft_path.resolve()
    else:
        draft_path = _pick_interactive()

    if draft_path is None:
        raise SystemExit(
            "未指定评测文件。用法：python scripts/review_server.py --draft <评测文件.json>\n"
            "或先把评测文件放到 data/annotations/ 下。"
        )
    if not draft_path.exists():
        raise SystemExit("找不到评测文件: %s" % draft_path)

    try:
        draft = load_draft(draft_path)
    except json.JSONDecodeError as e:
        raise SystemExit(
            "评测文件不是合法严格 JSON：%s\n%s\n"
            "提示：这可能是给人看的排版稿（*_readable.json，含真实换行/尾逗号/注释）。"
            "请把 *_translated.json 或严格 JSON 版的内容存为该文件后重试。" % (draft_path, e)
        )
    samples = [_pick_sample_fields(s) for s in (draft.get("samples") or [])]
    if not samples:
        raise SystemExit("评测文件里没有 samples: %s" % draft_path)

    human_reviews = draft.get("human_reviews") or {}
    mode = detect_mode(draft_path)
    optimized_path = attach_optimized_recall(samples, draft_path) if mode == "recall" else None
    meta = {
        "file": str(draft_path.relative_to(ROOT)) if str(draft_path).startswith(str(ROOT)) else str(draft_path),
        "paper_id": draft.get("paper_id") or "",
        "article_id": draft.get("article_id") or "",
        "sample_count": len(samples),
        "reviewers": sorted(human_reviews.keys()),
        "mode": mode,
        "kind": "distortion_review" if mode == "distortion" else "recall_draft",
    }

    handler = ReviewHandler
    handler.samples = samples
    handler.taxonomy = {**TAXONOMY, "guide": GUIDE}
    handler.human_reviews = human_reviews
    handler.sentences = load_sentence_index(draft.get("paper_id") or "")
    handler.draft_path = draft_path
    handler.meta = meta
    handler.mode = mode

    port = args.port or 8765
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = "http://127.0.0.1:%d/" % port
    print("=" * 56)
    print("  Review server started")
    print("  draft  : %s (%d samples)" % (draft_path, len(samples)))
    if optimized_path is not None:
        print("  recall : %s" % optimized_path)
    print("  url    : %s" % url)
    print("  quit   : Ctrl+C")
    print("=" * 56, flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
