"""模块4: 证据链生成（本期简化实现）。

输入: 观点句 + 证据句 + 幻觉分类结果
输出: 格式化的证据链文本 + 读者注释建议

本期: 简单的格式化输出
"""

from __future__ import annotations

import json
from typing import Any

from .config import HALLUCINATION_LABELS


def format_evidence_chain(
    classification_results: list[dict[str, Any]],
    paper_title: str = "",
    article_title: str = "",
) -> str:
    """将分类结果格式化为人类可读的证据链文本。

    Args:
        classification_results: classifier 模块的输出
        paper_title: 论文标题
        article_title: 文章标题

    Returns:
        格式化的 Markdown 证据链文本
    """
    lines = []
    lines.append("# 幻觉检测证据链报告\n")
    if article_title:
        lines.append(f"**文章**: {article_title}")
    if paper_title:
        lines.append(f"**论文**: {paper_title}")
    lines.append("")

    # 统计概览
    total = len(classification_results)
    if total == 0:
        lines.append("_无观点句_\n")
        return "\n".join(lines)

    level_counts = {"No_Evidence": 0, "Weak_Evidence": 0, "With_Evidence": 0}
    type_counts = {}
    for r in classification_results:
        clf = r.get("classification", {})
        lvl = clf.get("evidence_level", "Unknown")
        if lvl in level_counts:
            level_counts[lvl] += 1
        ptype = clf.get("primary_type", "")
        if ptype:
            type_counts[ptype] = type_counts.get(ptype, 0) + 1

    lines.append("## 统计概览\n")
    lines.append(f"- 总观点句数: {total}")
    lines.append(f"- 有证据支持: {level_counts.get('With_Evidence', 0)} ({level_counts.get('With_Evidence', 0) / total * 100:.0f}%)")
    lines.append(f"- 弱证据: {level_counts.get('Weak_Evidence', 0)}")
    lines.append(f"- 无证据: {level_counts.get('No_Evidence', 0)}")
    lines.append("")

    if type_counts:
        lines.append("### 失真类型分布\n")
        for ptype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            label_info = HALLUCINATION_LABELS.get(ptype, {})
            label_zh = label_info.get("zh", ptype)
            lines.append(f"- {label_zh} (`{ptype}`): {count} 条")
        lines.append("")

    # 逐条详情
    lines.append("## 逐条证据链\n")

    for item in classification_results:
        claim_id = item.get("claim_id", "?")
        claim_text = item.get("claim_text", "")
        clf = item.get("classification", {})
        evidence_sents = item.get("evidence_sentences", [])

        lines.append(f"### {claim_id}\n")

        # 观点句
        lines.append(f"**观点句**: {claim_text}\n")

        # 证据级别
        evidence_level = clf.get("evidence_level", "")
        level_emoji = {"With_Evidence": "✅", "Weak_Evidence": "⚠️", "No_Evidence": "❌"}
        emoji = level_emoji.get(evidence_level, "❓")
        lines.append(f"**证据级别**: {emoji} {evidence_level}\n")

        # 失真类型
        primary_type = clf.get("primary_type", "")
        if primary_type:
            label_info = HALLUCINATION_LABELS.get(primary_type, {})
            label_zh = label_info.get("zh", primary_type)
            lines.append(f"**主要失真类型**: {label_zh} (`{primary_type}`)")

            secondary = clf.get("secondary_types", [])
            if secondary:
                secondary_names = [
                    HALLUCINATION_LABELS.get(s, {}).get("zh", s) for s in secondary
                ]
                lines.append(f"**次要失真类型**: {', '.join(secondary_names)}")
            lines.append("")

        # 差异摘要
        discrepancy = clf.get("discrepancy_summary", "")
        if discrepancy:
            lines.append(f"**差异摘要**: {discrepancy}\n")

        # 判定推理
        reasoning = clf.get("reasoning", "")
        if reasoning and len(reasoning) > 20:
            lines.append(f"<details>\n<summary>详细推理</summary>\n\n{reasoning}\n</details>\n")

        # 证据句
        if evidence_sents:
            lines.append("**证据句**:\n")
            for ev in evidence_sents:
                sent = ev.get("sentence", "")
                score = ev.get("relevance_score", "")
                reason = ev.get("relevance_reason", "")
                lines.append(f"> {sent}")
                if score:
                    lines.append(f"> _(相关度: {score:.2f})_")
                if reason:
                    lines.append(f"> _(理由: {reason})_")
                lines.append("")
        else:
            lines.append("_(无证据句)_\n")

        lines.append("---\n")

    return "\n".join(lines)


def generate_reader_notes(
    classification_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """生成面向读者的注释建议。

    标注哪些观点句需要加"审慎提示"或"更正"。

    Returns:
        [{"claim_id": "C01", "action": "add_caution" | "correct" | "ok",
          "suggestion": "建议标注文本"}]
    """
    notes = []
    for item in classification_results:
        claim_id = item["claim_id"]
        clf = item.get("classification", {})
        evidence_level = clf.get("evidence_level", "")
        primary_type = clf.get("primary_type", "")

        if evidence_level == "No_Evidence":
            notes.append({
                "claim_id": claim_id,
                "action": "add_caution",
                "suggestion": "⚠️ 本文中的这一断言在论文中未找到明确证据支持，建议读者谨慎对待。",
            })
        elif evidence_level == "Weak_Evidence":
            notes.append({
                "claim_id": claim_id,
                "action": "add_caution",
                "suggestion": "⚠️ 本文中的这一断言与论文主题相关但无法被直接验证，建议读者参考原文。",
            })
        elif primary_type == "accurate":
            notes.append({
                "claim_id": claim_id,
                "action": "ok",
                "suggestion": "",
            })
        else:
            label_info = HALLUCINATION_LABELS.get(primary_type, {})
            label_zh = label_info.get("zh", primary_type)
            notes.append({
                "claim_id": claim_id,
                "action": "correct",
                "suggestion": f"⚠️ 存在「{label_zh}」失真，建议修改表述以更准确地反映论文原意。",
            })

    return notes


def build_final_output(
    classification_results: list[dict[str, Any]],
    paper_title: str = "",
    article_title: str = "",
    output_path: str | None = None,
) -> dict[str, Any]:
    """构建最终的完整输出 JSON。

    Args:
        classification_results: classifier 模块的输出
        paper_title: 论文标题
        article_title: 文章标题
        output_path: 输出文件路径

    Returns:
        完整的输出 dict
    """
    reader_notes = generate_reader_notes(classification_results)
    evidence_chain_text = format_evidence_chain(
        classification_results, paper_title, article_title
    )

    output = {
        "meta": {
            "paper_title": paper_title,
            "article_title": article_title,
            "generated_at": "",  # 由 run_pipeline 填充
            "total_claims": len(classification_results),
        },
        "claims": classification_results,
        "reader_notes": reader_notes,
        "evidence_chain_markdown": evidence_chain_text,
    }

    if output_path:
        from datetime import datetime
        output["meta"]["generated_at"] = datetime.now().isoformat()
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"  [evidence_chain] 结果已保存: {output_path}")

    return output
