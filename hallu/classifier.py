"""模块3: 幻觉细分类。

输入: 观点句 + 证据句 + 幻觉分类体系（9类标签定义）
输出: {"evidence_level": "...", "primary_type": "...", "reasoning": "..."}

方法: 调用 Qwen API，使用分类体系 prompt 进行两级判断：
  Step 1 — 证据级别判定 (No_Evidence / Weak_Evidence / With_Evidence)
  Step 2 — 细粒度失真类型判定（仅 With_Evidence 时）
"""

from __future__ import annotations

import json
from typing import Any

from api_client import QwenClient, build_messages, extract_json

from .config import (
    HALLUCINATION_LABELS,
    QWEN_MODEL,
    QWEN_TEMPERATURE,
)

# ---------------------------------------------------------------------------
# 分类 Prompt
# ---------------------------------------------------------------------------

CLASSIFICATION_SYSTEM = """你是植物科学领域的学术审稿专家。你的任务是比较**中文科普文章中的观点句**与**英文学术论文中的证据句**，判断观点句是否存在信息失真（幻觉）。

## 分类体系

### Level 1 — 证据级别
- **No_Evidence**: 观点句中的核心科学断言在论文证据句中完全找不到对应
- **Weak_Evidence**: 论文证据句与观点句主题相关，但具体断言无法被证据直接验证
- **With_Evidence**: 论文证据句中有明确对应的内容，可以进行细粒度比较

### Level 2 — 细粒度失真类型（仅 With_Evidence 时判定）

| 标签 | 中文名 | 定义 |
|------|--------|------|
| certainty_amplification | 确定性放大 | 论文hedging/审慎表述（如may, suggest, indicate, provide insights into）→ 观点句中变为确定性断言（揭示、阐明、证明） |
| mechanism_simplification | 机制简化 | 论文的多层次复杂调控机制→ 观点句简化为单一关键因子，丢失了调控层级的复杂性 |
| scope_generalization | 范围泛化 | 论文特定条件（某物种、某组织、某发育阶段、某实验条件）下的结论 → 观点句扩展至更广范围 |
| numerical_distortion | 数值失真 | 论文中的数值（百分比、样本量、p值、统计量）被改变、模糊化或选择性引用 |
| causality_distortion | 因果扭曲 | 论文中的相关性→ 观点句变为因果；或因果方向颠倒；或夸大因果确定性 |
| context_stripping | 语境剥离 | 关键实验条件、方法局限、样本信息被省略，使结论看似比论文声称的更普适 |
| fact_addition | 事实添加 | 观点句加入了论文中不存在的断言、数据、结论或评价（如"首次""重大突破"） |
| semantic_contradiction | 反义矛盾 | 观点句与论文原文意思直接相反或明显矛盾 |
| accurate | 准确传达 | 观点句无信息失真，准确传达论文原意（允许合理的科普化转述） |

## 判定原则
1. **审慎措辞很重要**: 论文使用 suggest/may/indicate/provide insights 而观点句使用"揭示/阐明/证明/确定"→ certainty_amplification
2. **范围变化要敏感**: 论文说"in cucumber"而观点句说"在葫芦科植物中"→ scope_generalization
3. **"首次"是红旗**: 除非论文明确写了 first/novel/for the first time，否则观点句中的"首次"→ fact_addition
4. **省略即风险**: 论文有条件限定但观点句未提 → context_stripping
5. **科普化转述不等同于失真**: 用通俗语言解释专业术语是合理的，但如果改变了科学含义则是失真

## 输出格式
严格输出 JSON：
{
  "evidence_level": "With_Evidence",
  "primary_type": "accurate",
  "secondary_types": [],
  "reasoning": "详细的判定推理过程：1) 证据级别判定的依据 2) 逐维度检查失真类型 3) 综合结论",
  "discrepancy_summary": "一句话总结核心差异（如无差异则说明'无信息失真'）",
  "key_differences": ["差异1的描述", "差异2的描述"],
  "retained_accurately": ["准确传达的内容1", "准确传达的内容2"]
}
"""


def _build_user_prompt(
    claim_text: str,
    evidence_sentences: list[dict[str, Any]],
) -> str:
    """构造分类 prompt。"""
    # 格式化证据句
    evidence_text = ""
    for ev in evidence_sentences:
        rank = ev.get("rank", "?")
        sent = ev.get("sentence", "")
        evidence_text += f"\n[证据句 {rank}] {sent}"

    if not evidence_text:
        evidence_text = "(无证据句)"

    # 构建标签定义简要表
    labels_short = "\n".join(
        f"- {k}: {v['zh']} — {v['definition']}"
        for k, v in HALLUCINATION_LABELS.items()
    )

    return f"""## 标签定义
{labels_short}

## 待判定
**中文观点句**: {claim_text}

**论文证据句**:
{evidence_text}

请按照分类体系进行判定，输出 JSON。"""


def classify_single(
    claim_text: str,
    evidence_sentences: list[dict[str, Any]],
    client: QwenClient,
    model: str | None = None,
) -> dict[str, Any]:
    """对单条观点句 + 证据句对进行幻觉分类。

    Args:
        claim_text: 中文观点句
        evidence_sentences: 证据句列表 [{"sentence": "...", ...}]
        client: QwenClient
        model: 模型名

    Returns:
        分类结果 dict
    """
    used_model = model or QWEN_MODEL
    prompt = _build_user_prompt(claim_text, evidence_sentences)

    try:
        result = client.chat_json(
            build_messages(prompt, system=CLASSIFICATION_SYSTEM),
            temperature=QWEN_TEMPERATURE,
        )
    except Exception as e:
        print(f"    [classifier] LLM 分类失败: {e}")
        # 降级
        return {
            "evidence_level": "Weak_Evidence",
            "primary_type": "accurate",
            "secondary_types": [],
            "reasoning": f"分类API调用失败: {e}",
            "discrepancy_summary": "分类失败",
            "key_differences": [],
            "retained_accurately": [],
            "_error": str(e),
        }

    # 标准化输出字段
    return {
        "evidence_level": result.get("evidence_level", "Weak_Evidence"),
        "primary_type": result.get("primary_type", ""),
        "secondary_types": result.get("secondary_types", []),
        "reasoning": result.get("reasoning", ""),
        "discrepancy_summary": result.get("discrepancy_summary", ""),
        "key_differences": result.get("key_differences", []),
        "retained_accurately": result.get("retained_accurately", []),
    }


def classify_all(
    retrieval_results: list[dict[str, Any]],
    client: QwenClient | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """对全部检索结果进行幻觉分类（主入口）。

    Args:
        retrieval_results: retriever 模块的输出
        client: QwenClient
        model: 模型名

    Returns:
        [{"claim_id": "C01", "claim_text": "...", ...,
          "classification": {...}, "evidence_sentences": [...]}]
    """
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

    # 统计
    level_counts = {}
    type_counts = {}
    for r in results:
        clf = r.get("classification", {})
        lvl = clf.get("evidence_level", "Unknown")
        level_counts[lvl] = level_counts.get(lvl, 0) + 1
        ptype = clf.get("primary_type", "Unknown")
        type_counts[ptype] = type_counts.get(ptype, 0) + 1

    print(f"\n  [classifier] 分类完成:")
    print(f"    证据级别分布: {level_counts}")
    print(f"    失真类型分布: {type_counts}")

    return results
