"""模块4: 证据链生成（本期简化实现）。

输入: 观点句 + 证据句 + 信息失真分类结果
输出: 格式化的证据链文本 + 读者注释建议
"""

from __future__ import annotations

import json
from typing import Any

from .config import label_zh, normalize_classification


def _distortion_status_text(view: dict[str, Any]) -> tuple[str, str]:
    """返回 (emoji, 文案)。"""
    evidence_level = view.get("evidence_level")
    has_d = view.get("has_distortion")
    if evidence_level in ("No_Evidence", "Weak_Evidence") or has_d is None:
        return "⚠️", "不适用（证据不足以判定失真类型）"
    if has_d:
        return "⚠️", "是"
    return "✅", "否"


def format_evidence_chain(
    classification_results: list[dict[str, Any]],
    paper_title: str = "",
    article_title: str = "",
) -> str:
    """将分类结果格式化为人类可读的证据链文本。"""
    lines = []
    lines.append("# 信息失真检测证据链报告\n")
    if article_title:
        lines.append(f"**文章**: {article_title}")
    if paper_title:
        lines.append(f"**论文**: {paper_title}")
    lines.append("")

    total = len(classification_results)
    if total == 0:
        lines.append("_无观点句_\n")
        return "\n".join(lines)

    level_counts = {"No_Evidence": 0, "Weak_Evidence": 0, "With_Evidence": 0}
    type_counts: dict[str, int] = {}
    distortion_count = 0
    unverifiable = 0
    for r in classification_results:
        view = normalize_classification(r.get("classification") or {})
        lvl = view.get("evidence_level") or "Unknown"
        if lvl in level_counts:
            level_counts[lvl] += 1
        ptype = view.get("primary_type") or ""
        if ptype:
            type_counts[ptype] = type_counts.get(ptype, 0) + 1
        if view.get("has_distortion") is True:
            distortion_count += 1
        if view.get("has_distortion") is None:
            unverifiable += 1

    lines.append("## 统计概览\n")
    lines.append(f"- 总观点句数: {total}")
    lines.append(
        f"- 存在信息失真: {distortion_count} ({distortion_count / total * 100:.0f}%)"
        if total
        else "- 存在信息失真: 0"
    )
    lines.append(f"- 证据不足、未判定失真类型: {unverifiable}")
    lines.append(
        f"- 有证据支持: {level_counts.get('With_Evidence', 0)} "
        f"({level_counts.get('With_Evidence', 0) / total * 100:.0f}%)"
        if total
        else "- 有证据支持: 0"
    )
    lines.append(f"- 弱证据: {level_counts.get('Weak_Evidence', 0)}")
    lines.append(f"- 无证据: {level_counts.get('No_Evidence', 0)}")
    lines.append("")

    if type_counts:
        lines.append("### 失真类型分布\n")
        for ptype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- {label_zh(ptype)} (`{ptype}`): {count} 条")
        lines.append("")

    lines.append("## 逐条证据链\n")

    for item in classification_results:
        claim_id = item.get("claim_id", "?")
        claim_text = item.get("claim_text", "")
        clf = item.get("classification", {})
        view = normalize_classification(clf)
        evidence_sents = item.get("evidence_sentences", [])

        lines.append(f"### {claim_id}\n")
        lines.append(f"**观点句**: {claim_text}\n")

        evidence_level = view.get("evidence_level") or ""
        level_emoji = {
            "With_Evidence": "✅",
            "Weak_Evidence": "⚠️",
            "No_Evidence": "❌",
        }
        emoji = level_emoji.get(evidence_level, "❓")
        lines.append(f"**证据级别**: {emoji} {evidence_level}\n")

        d_emoji, d_text = _distortion_status_text(view)
        lines.append(f"**是否存在信息失真**: {d_emoji} {d_text}\n")

        primary_type = view.get("primary_type") or ""
        if primary_type:
            primary = view.get("primary_label") or {}
            level1 = primary.get("level1") or ""
            extra = f" / {level1}" if level1 else ""
            lines.append(
                f"**主要失真类型**: {label_zh(primary_type)} (`{primary_type}`{extra})"
            )
            secondary = view.get("secondary_types") or []
            if secondary:
                names = [f"{label_zh(s)} (`{s}`)" for s in secondary]
                lines.append(f"**次要失真类型**: {', '.join(names)}")
            lines.append("")

        uncovered = view.get("uncovered_phenomenon") or ""
        if uncovered:
            lines.append(f"**未覆盖现象（需人工）**: `{uncovered}`\n")

        discrepancy = clf.get("discrepancy_summary") or ""
        if discrepancy:
            lines.append(f"**差异摘要**: {discrepancy}\n")

        reasoning = view.get("reason") or clf.get("reasoning") or ""
        if reasoning and len(reasoning) > 20:
            lines.append(
                f"<details>\n<summary>详细推理</summary>\n\n{reasoning}\n</details>\n"
            )

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
    """生成面向读者的注释建议。"""
    notes = []
    for item in classification_results:
        claim_id = item["claim_id"]
        view = normalize_classification(item.get("classification") or {})
        evidence_level = view.get("evidence_level") or ""
        primary_type = view.get("primary_type") or ""

        if evidence_level == "No_Evidence":
            notes.append({
                "claim_id": claim_id,
                "action": "add_caution",
                "suggestion": "⚠️ 这一断言在论文中未找到明确证据，无法按论文核实，建议读者谨慎对待。",
            })
        elif evidence_level == "Weak_Evidence":
            notes.append({
                "claim_id": claim_id,
                "action": "add_caution",
                "suggestion": "⚠️ 这一断言与论文主题相关，但现有证据既不能充分证明也不能充分否定，建议参考原文。",
            })
        elif not view.get("has_distortion"):
            notes.append({
                "claim_id": claim_id,
                "action": "ok",
                "suggestion": "",
            })
        else:
            label = label_zh(primary_type)
            notes.append({
                "claim_id": claim_id,
                "action": "correct",
                "suggestion": f"⚠️ 存在「{label}」信息失真，建议修改表述以更准确地反映论文原意。",
            })

    return notes


def build_final_output(
    classification_results: list[dict[str, Any]],
    paper_title: str = "",
    article_title: str = "",
    output_path: str | None = None,
) -> dict[str, Any]:
    """构建最终的完整输出 JSON。"""
    reader_notes = generate_reader_notes(classification_results)
    evidence_chain_text = format_evidence_chain(
        classification_results, paper_title, article_title
    )

    output = {
        "meta": {
            "paper_title": paper_title,
            "article_title": article_title,
            "generated_at": "",
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
