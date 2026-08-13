"""模块3: 信息失真细分类。

输入: 观点句 + 证据句 + distortion-v0.1 分类体系
输出: evidence_level + primary_label/secondary_label + has_distortion

方法: 调用 Qwen API，先判证据级别，仅 With_Evidence 时进入
Omission / Addition / Substitution 细类（决策树：Substitution > Addition > Omission）。
"""

from __future__ import annotations

from typing import Any

from api_client import QwenClient, build_messages

from .config import (
    DISTORTION_LABELS,
    LEVEL1_LABELS,
    NO_DISTORTION,
    QWEN_MODEL,
    QWEN_TEMPERATURE,
    TAXONOMY_VERSION,
    UNCOVERED_PHENOMENA,
    has_distortion,
    level1_of,
    normalize_classification,
)

# ---------------------------------------------------------------------------
# 分类 Prompt
# ---------------------------------------------------------------------------

_LABEL_ROWS = "\n".join(
    f"| {slug} | {LEVEL1_LABELS[info['level1']]['zh']} | {info['zh']} | {info['definition']} |"
    for slug, info in DISTORTION_LABELS.items()
)

_UNCOVERED_ROWS = "\n".join(
    f"| {k} | {v} |" for k, v in UNCOVERED_PHENOMENA.items()
)

CLASSIFICATION_SYSTEM = f"""你是植物科学领域的学术审稿专家。任务是比较**中文科普文章中的观点句**与**英文学术论文中的证据句**，判断从论文到公众号的转述是否发生**信息失真**。

这不是生成式「幻觉」检测。评的是：相对这篇论文，转述有没有改变科学含义。

taxonomy_version = {TAXONOMY_VERSION}

## 两个独立维度

1. `evidence_level`（证据维度）：以本篇论文为唯一依据，能否做细粒度比对？
2. 失真类型（内容维度）：**仅当 evidence_level = With_Evidence 时**判定。

**不可核实 ≠ 已判定失真类型。** Weak_Evidence / No_Evidence 时不要分配 8 类标签，`has_distortion` 必须为 null。

### 证据级别

- **No_Evidence**: 核心科学断言在证据句中完全找不到对应 → 结束
- **Weak_Evidence**: 主题相关，但核心断言无法被证据充分验证，也无法充分证伪 → 结束
- **With_Evidence**: 至少一句直接对应核心断言（对象+关系/结果，不只是共享关键词）→ 进入失真判定

### 失真细类（仅 With_Evidence；共 8 类，禁止自造标签）

| slug | 一级 | 中文 | 定义 |
|------|------|------|------|
{_LABEL_ROWS}
| {NO_DISTORTION} | — | 无失真 | 合理科普压缩/同义转述，科学含义未变 |

一级冲突优先级：**substitution > addition > omission**。

Primary = 导致科学含义变化最大的那一个操作。
Secondary = 另一独立信息变化（最多一条）。同一变化禁止重复标注。

### 不可标注区（必须先排除，标 no_distortion）

- 合理科学压缩：删机制细节但核心命题保留
- 术语通俗化、同义表达
- 去除非关键实验细节（如 24 hours、3.5-fold 的概括）
- 正常程度弱化（strongly increases → increases）
- 一般背景知识补充（不是针对该论文的新 claim）

### 明确不扩类

不要发明第 9 类。若无法归入 8 类，`primary_label.level2` 仍尽量选最接近者或留空，并设 `needs_manual_review=true`，`uncovered_phenomenon` 取下表之一：

| 值 | 何时 |
|----|------|
{_UNCOVERED_ROWS}

### 判定流程

Step 0 定 evidence_level；不足则结束。
Step 1 是否完全支持论文？是 → no_distortion。
Step 2 是否改变已有科学关系（相关→因果、间接→直接、机制被换成另一种）？→ substitution。
Step 3 是否增加论文没有的功能/应用或「首次/突破」类评价？→ addition。
Step 4 是否删除重要限定（物种/条件/不确定性/关键机制）？→ omission。
Step 5 是否存在第二个独立错误？最多一条 secondary_label。

内部冲突提要：
- 范围扩大 vs 机制压缩：范围扩大通常作 primary（context_omission）
- 删条件 vs 删 may：context_omission 优先
- 新应用 vs 「突破」：function_application_addition 优先
- 新增信息若同时改变了原始科学关系：substitution 优先
- contributes→determines：magnitude_substitution（关系类型未变）
- 机制被换成另一种 + 关系增强：mechanism_substitution 作 primary

关键区分：
- indicate/show/reveal/find/confirm 是学术断言词，对等翻译 ≠ evidence_uncertainty_omission
- contribute to/lead to/result in/drive 是因果动词，对等翻译 ≠ relation_substitution
- 只删 may/suggest → evidence_uncertainty_omission；新增强化词（definitely）→ addition，不是 omission
- 只删机制 → mechanism_omission；改成错误机制 → mechanism_substitution

## 输出格式

严格输出 JSON：

{{
  "evidence_level": "With_Evidence",
  "has_distortion": true,
  "primary_label": {{"level1": "substitution", "level2": "relation_substitution"}},
  "secondary_label": {{"level1": "addition", "level2": "function_application_addition"}},
  "severity": "moderate",
  "needs_manual_review": false,
  "uncovered_phenomenon": "",
  "reasoning": "（Step 0）证据级别依据；（Step 1–5）为何是该 primary/secondary，以及为何不是相邻类；措辞对照",
  "discrepancy_summary": "一句话核心差异；无失真则写「无信息失真」",
  "key_differences": ["差异1"],
  "retained_accurately": ["准确传达的内容"]
}}

规则：
- level1 只能是 omission / addition / substitution（小写）；level2 必须是上表 slug
- No_Evidence / Weak_Evidence：has_distortion=null，primary_label=null，secondary_label=null，severity=""
- With_Evidence 且无失真：has_distortion=false，primary_label={{"level1":"","level2":"{NO_DISTORTION}"}}，secondary_label=null，severity="none"
- With_Evidence 且有失真：has_distortion=true，severity 为 mild/moderate/severe
- 无独立次要失真时 secondary_label=null，不要空对象
- 不要输出 is_hallucination 字段
"""


def _build_user_prompt(
    claim_text: str,
    evidence_sentences: list[dict[str, Any]],
) -> str:
    evidence_text = ""
    for ev in evidence_sentences:
        rank = ev.get("rank", "?")
        sent = ev.get("sentence", "")
        evidence_text += f"\n[证据句 {rank}] {sent}"
    if not evidence_text:
        evidence_text = "(无证据句)"

    labels_short = "\n".join(
        f"- {k} ({LEVEL1_LABELS[v['level1']]['en']}): {v['zh']} — {v['definition']}"
        for k, v in DISTORTION_LABELS.items()
    )

    return f"""## 标签定义
{labels_short}
- {NO_DISTORTION}: 无失真 — 科学含义未变的合理科普转述

## 待判定
**中文观点句**: {claim_text}

**论文证据句**:
{evidence_text}

请按照分类体系进行判定，输出 JSON。"""


def _coerce_label(raw: Any) -> dict[str, str] | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        level2 = str(raw.get("level2") or "").strip()
        level1 = str(raw.get("level1") or "").strip().lower()
        if not level2:
            return None
        if not level1:
            level1 = level1_of(level2)
        return {"level1": level1, "level2": level2}
    if isinstance(raw, str):
        slug = raw.strip()
        if not slug:
            return None
        return {"level1": level1_of(slug), "level2": slug}
    return None


def _standardize_result(result: dict[str, Any]) -> dict[str, Any]:
    evidence_level = result.get("evidence_level") or "Weak_Evidence"
    primary = _coerce_label(result.get("primary_label") or result.get("primary_type"))
    secondary = _coerce_label(result.get("secondary_label"))
    if secondary is None:
        secs = result.get("secondary_types") or []
        if secs:
            secondary = _coerce_label(secs[0])

    if evidence_level in ("No_Evidence", "Weak_Evidence"):
        primary = None
        secondary = None
        distortion = None
        severity = ""
        is_accurate = None
    else:
        level2 = (primary or {}).get("level2") or NO_DISTORTION
        if not primary:
            primary = {"level1": "", "level2": NO_DISTORTION}
            level2 = NO_DISTORTION
        distortion = has_distortion(evidence_level, level2)
        if distortion is False:
            primary = {"level1": "", "level2": NO_DISTORTION}
            secondary = None
            severity = "none"
            is_accurate = True
        else:
            severity = str(result.get("severity") or "moderate")
            if severity not in ("mild", "moderate", "severe"):
                severity = "moderate"
            is_accurate = False

    primary_type = (primary or {}).get("level2") or ""
    secondary_types = []
    if secondary and secondary.get("level2"):
        secondary_types = [secondary["level2"]]

    uncovered = str(result.get("uncovered_phenomenon") or "")
    needs_review = bool(result.get("needs_manual_review"))
    if uncovered:
        needs_review = True

    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "evidence_level": evidence_level,
        "has_distortion": distortion,
        "primary_label": primary,
        "secondary_label": secondary,
        "severity": severity,
        "needs_manual_review": needs_review,
        "uncovered_phenomenon": uncovered,
        "reasoning": result.get("reasoning") or result.get("reason") or "",
        "discrepancy_summary": result.get("discrepancy_summary", ""),
        "key_differences": result.get("key_differences", []),
        "retained_accurately": result.get("retained_accurately", []),
        # 扁平别名，便于旧评测脚本读取
        "primary_type": primary_type,
        "secondary_types": secondary_types,
        "is_accurate": is_accurate,
    }


def classify_single(
    claim_text: str,
    evidence_sentences: list[dict[str, Any]],
    client: QwenClient,
    model: str | None = None,
) -> dict[str, Any]:
    """对单条观点句 + 证据句对进行信息失真分类。"""
    used_model = model or QWEN_MODEL
    prompt = _build_user_prompt(claim_text, evidence_sentences)

    try:
        result = client.chat_json(
            build_messages(prompt, system=CLASSIFICATION_SYSTEM),
            temperature=QWEN_TEMPERATURE,
            model=used_model,
        )
    except Exception as e:
        print(f"    [classifier] LLM 分类失败: {e}")
        out = _standardize_result(
            {
                "evidence_level": "Weak_Evidence",
                "reasoning": f"分类API调用失败: {e}",
                "discrepancy_summary": "分类失败",
                "key_differences": [],
                "retained_accurately": [],
                "needs_manual_review": True,
            }
        )
        out["_error"] = str(e)
        return out

    if not isinstance(result, dict):
        result = {}
    return _standardize_result(result)


def classify_all(
    retrieval_results: list[dict[str, Any]],
    client: QwenClient | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """对全部检索结果进行信息失真分类（主入口）。"""
    if client is None:
        client = QwenClient(verbose=False)

    results = []
    total = len(retrieval_results)

    for i, item in enumerate(retrieval_results):
        claim_id = item["claim_id"]
        claim_text = item.get("claim_text", "")
        evidence_sents = item.get("evidence_sentences", [])

        print(f"  [{i + 1}/{total}] 分类: {claim_id}: {claim_text[:60]}...")

        classification = classify_single(
            claim_text=claim_text,
            evidence_sentences=evidence_sents,
            client=client,
            model=model,
        )

        results.append({
            "claim_id": claim_id,
            "claim_text": claim_text,
            "evidence_sentences": evidence_sents,
            "overall_assessment": item.get("overall_assessment", ""),
            "retrieval_stats": item.get("retrieval_stats", {}),
            "classification": classification,
        })

    level_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for r in results:
        clf = r.get("classification", {})
        view = normalize_classification(clf)
        lvl = view.get("evidence_level") or "Unknown"
        level_counts[lvl] = level_counts.get(lvl, 0) + 1
        ptype = view.get("primary_type") or "(none)"
        type_counts[ptype] = type_counts.get(ptype, 0) + 1

    print(f"\n  [classifier] 分类完成:")
    print(f"    证据级别分布: {level_counts}")
    print(f"    失真类型分布: {type_counts}")

    return results
