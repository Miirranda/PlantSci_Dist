"""观点句提取（arag 前端）：规则分句 + LLM 核验。

流程：
  1. 清洗 Markdown
  2. 按句号 / 问号 / 叹号 / 分号 / 换行切句（复用 pdf_ingest 规则）
  3. 规则粗滤标题、图注等
  4. LLM 按批核验 keep/drop（只判断，不改写、不发明新句）
  5. 输出带上下文的 claims，供下游跨语言检索
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from api_client import QwenClient, build_messages, extract_json
from api_client.config import get_env, get_float, get_int

from .pdf_ingest import CJK_SENTENCE_END, is_claim_like

QWEN_MODEL = get_env("QWEN_MODEL", "qwen-plus") or "qwen-plus"
QWEN_TEMPERATURE = get_float("QWEN_TEMPERATURE", 0.0)
VERIFY_BATCH_SIZE = max(5, get_int("CLAIM_VERIFY_BATCH_SIZE", 25))

# 公众号常见小节名（整行匹配时更新 section）
_SECTION_LINE = re.compile(
    r"^\s*(研究背景|研究结果|研究结论|总结与讨论|总结|讨论|方法|引言|前言|"
    r"背景介绍|主要内容|未来展望|展望)\s*$"
)
_SECTION_NUMBER = re.compile(r"^\s*\d{1,2}\s*$")

CLAIM_VERIFY_SYSTEM = """你是植物科学领域的学术审稿人。系统已经把科普文章切成候选句子。
你的任务是逐条判断：该句是否包含**值得对照英文学术论文核查**的事实性科学断言。

## 应 keep=true
- 科学发现、机制、因果、实验结果、物种/基因/数值等可核查陈述
- 对论文研究内容、方法、结论的具体描述

## 应 keep=false
- 纯发表元信息（团队、期刊、标题报道，无科学断言）
- 标题、分节标题、过渡句、图注
- 无信息量的常识或空泛套话
- 过碎、无法独立核查的残句

## 硬性规则
1. 只对给定候选句判决，禁止改写原文，禁止发明新句子。
2. 每个输入 id 都必须给出一条决策。
3. 严格输出 JSON：
{
  "decisions": [
    {"id": 1, "keep": true, "reason": "简短理由"},
    {"id": 2, "keep": false, "reason": "简短理由"}
  ]
}
"""


def _clean_markdown(text: str) -> str:
    """移除 Markdown 标记与文首元数据，保留正文。"""
    # 文首 YAML / 存档头：从文件开头到第一条单独的 --- 分隔线
    text = re.sub(r"\A[\s\S]*?^---\s*\n", "", text, count=1, flags=re.MULTILINE)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^---+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
    return text


def _strip_leading_meta(text: str) -> str:
    """去掉正文前残留的存档字段行（来源/链接等）。"""
    lines = text.split("\n")
    start = 0
    meta_prefix = re.compile(
        r"^\s*[-*]?\s*(来源|来源类型|发布日期|原文链接|对应论文|存档日期|作者)\s*[:：]"
    )
    while start < len(lines):
        line = lines[start].strip()
        if not line:
            start += 1
            continue
        if meta_prefix.match(line) or re.match(r"^\[A\d+\]", line):
            start += 1
            continue
        break
    return "\n".join(lines[start:])


def _is_section_header(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _SECTION_LINE.match(stripped):
        return True
    if _SECTION_NUMBER.match(stripped):
        return True
    return False


def split_article_candidates(text: str, *, min_chars: int = 8) -> list[dict[str, Any]]:
    """规则分句 + 粗滤，返回候选列表（高召回）。

    每项含：cand_id(从1起)、text、section。
    """
    normalized = re.sub(r"[ \t]+", " ", text.replace("\r", "\n"))
    candidates: list[dict[str, Any]] = []
    section = ""
    pending_section = ""

    for block in normalized.split("\n"):
        line = block.strip()
        if not line:
            continue
        if _is_section_header(line):
            # 纯数字小节先记着，下一行小节名覆盖；否则直接作为 section
            if _SECTION_NUMBER.match(line):
                pending_section = line
            else:
                section = line
                pending_section = ""
            continue
        if pending_section and _SECTION_LINE.match(line):
            section = line
            pending_section = ""
            continue

        for piece in CJK_SENTENCE_END.split(line):
            candidate = piece.strip()
            if not is_claim_like(candidate, min_chars=min_chars):
                continue
            candidates.append(
                {
                    "cand_id": len(candidates) + 1,
                    "text": candidate,
                    "section": section,
                }
            )
    return candidates


def _parse_decisions(result: Any, batch_ids: list[int]) -> dict[int, bool]:
    """把 LLM JSON 解析成 {cand_id: keep}。缺省的 id 视为 keep=True（偏召回）。"""
    decisions: dict[int, bool] = {}
    if not isinstance(result, dict):
        return {i: True for i in batch_ids}
    rows = result.get("decisions") or result.get("results") or []
    if not isinstance(rows, list):
        return {i: True for i in batch_ids}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("id", row.get("cand_id"))
        try:
            cid = int(raw_id)
        except (TypeError, ValueError):
            continue
        keep = row.get("keep", row.get("retain", True))
        if isinstance(keep, str):
            keep = keep.strip().lower() in ("1", "true", "yes", "y", "keep")
        decisions[cid] = bool(keep)
    for cid in batch_ids:
        decisions.setdefault(cid, True)
    return decisions


def verify_candidates_with_llm(
    candidates: list[dict[str, Any]],
    *,
    client: QwenClient,
    model: str,
    batch_size: int = VERIFY_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """按批调用 LLM 核验，返回 keep=true 的候选（保持原文顺序）。"""
    if not candidates:
        return []

    kept: list[dict[str, Any]] = []
    total = len(candidates)
    for start in range(0, total, batch_size):
        batch = candidates[start : start + batch_size]
        batch_ids = [int(item["cand_id"]) for item in batch]
        lines = []
        for item in batch:
            lines.append("%d. %s" % (item["cand_id"], item["text"]))
        prompt = (
            "请对下列候选句逐条判决 keep（true/false）。"
            "禁止改写，禁止新增句子。\n\n"
            + "\n".join(lines)
        )
        print(
            "  [arag.claim_extractor] 核验批次 %d–%d / %d"
            % (start + 1, start + len(batch), total)
        )
        try:
            result = client.chat_json(
                build_messages(prompt, system=CLAIM_VERIFY_SYSTEM),
                temperature=QWEN_TEMPERATURE,
                model=model,
            )
        except Exception as exc:
            print("  [arag.claim_extractor] 核验失败，本批默认保留: %s" % exc)
            try:
                raw = client.ask(prompt, system=CLAIM_VERIFY_SYSTEM, model=model)
                result = extract_json(raw)
            except Exception as exc2:
                print("  [arag.claim_extractor] 降级解析也失败，本批全部保留: %s" % exc2)
                result = {"decisions": [{"id": i, "keep": True} for i in batch_ids]}

        flags = _parse_decisions(result, batch_ids)
        for item in batch:
            if flags.get(int(item["cand_id"]), True):
                kept.append(item)

    return kept


def _build_claim_records(
    kept: list[dict[str, Any]],
    *,
    source_file: str,
) -> list[dict[str, Any]]:
    """为保留句补全上下文与稳定 claim_id。"""
    texts = [str(item["text"]) for item in kept]
    records: list[dict[str, Any]] = []
    for index, item in enumerate(kept):
        claim_id = "C%02d" % (index + 1)
        records.append(
            {
                "claim_id": claim_id,
                "claim_zh": item["text"],
                "context_before": texts[index - 1] if index > 0 else "",
                "context_after": texts[index + 1] if index + 1 < len(texts) else "",
                "section": str(item.get("section") or "").strip(),
                "source_file": source_file,
            }
        )
    return records


def extract_claims_from_article(
    article_path: str | Path,
    *,
    client: QwenClient | None = None,
    model: str | None = None,
    batch_size: int | None = None,
    skip_llm_verify: bool = False,
    min_chars: int = 8,
) -> list[dict[str, Any]]:
    """从 Markdown 文章提取事实性科学观点句（规则分句 + LLM 核验）。

    Args:
        skip_llm_verify: True 时只做规则切分粗滤（调试/离线）。
        batch_size: LLM 核验批大小，默认读环境变量 CLAIM_VERIFY_BATCH_SIZE。

    Returns:
        列表元素含 claim_id / claim_zh，以及 context_before / context_after / section。
    """
    article_path = Path(article_path)
    raw_text = article_path.read_text(encoding="utf-8")

    cleaned = _clean_markdown(raw_text)
    cleaned = _strip_leading_meta(cleaned)
    cleaned = re.sub(r"^---[\s\S]*?---\s*", "", cleaned)

    print("  [arag.claim_extractor] 文章长度: %d 字符" % len(cleaned))
    candidates = split_article_candidates(cleaned, min_chars=min_chars)
    print("  [arag.claim_extractor] 规则候选: %d 句" % len(candidates))

    if not candidates:
        return []

    if skip_llm_verify:
        kept = candidates
        print("  [arag.claim_extractor] 跳过 LLM 核验，保留全部候选")
    else:
        if client is None:
            client = QwenClient(verbose=False)
        used_model = model or QWEN_MODEL
        print("  [arag.claim_extractor] 核验模型: %s" % used_model)
        kept = verify_candidates_with_llm(
            candidates,
            client=client,
            model=used_model,
            batch_size=batch_size or VERIFY_BATCH_SIZE,
        )

    claims = _build_claim_records(kept, source_file=article_path.name)
    print(
        "  [arag.claim_extractor] 最终观点句: %d 条（候选 %d → 保留 %d）"
        % (len(claims), len(candidates), len(kept))
    )
    return claims


def save_claims_jsonl(claims: list[dict[str, Any]], path: str | Path) -> Path:
    """写出 arag 可读的 claims.jsonl。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for claim in claims:
            row = {
                "claim_id": claim.get("claim_id") or claim.get("id"),
                "claim_zh": claim.get("claim_zh") or claim.get("claim_text") or "",
            }
            for key in ("context_before", "context_after", "section", "source_file"):
                if claim.get(key):
                    row[key] = claim[key]
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out


def save_claims_json(claims: list[dict[str, Any]], path: str | Path) -> Path:
    """写出可读的 claims.json（hallu/人工检查用）。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for claim in claims:
        payload.append(
            {
                "id": claim.get("claim_id") or claim.get("id"),
                "claim_text": claim.get("claim_zh") or claim.get("claim_text") or "",
                "context_before": claim.get("context_before", ""),
                "context_after": claim.get("context_after", ""),
                "section": claim.get("section", ""),
            }
        )
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
