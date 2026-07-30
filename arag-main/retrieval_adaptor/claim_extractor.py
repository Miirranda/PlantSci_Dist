"""LLM 观点句提取（arag 前端）。

从中文公众号 Markdown 文章中筛选事实性科学断言，供下游跨语言检索使用。
替代旧的规则切句（load_wechat_claims / split_chinese_sentences）。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from api_client import QwenClient, build_messages, extract_json
from api_client.config import get_env, get_float

QWEN_MODEL = get_env("QWEN_MODEL", "qwen-plus") or "qwen-plus"
QWEN_TEMPERATURE = get_float("QWEN_TEMPERATURE", 0.0)

CLAIM_EXTRACTION_SYSTEM = """你是植物科学领域的学术审稿人。你的任务是从中文科普文章中提取所有包含**事实性科学断言**的句子。

## 什么是"事实性科学断言"？
- 包含具体的科学发现、数据、机制、因果关系、实验结果的陈述
- 对论文研究内容、方法、结论的描述
- 包含数值、基因名、物种名、实验方法等技术信息的句子

## 什么不是"事实性科学断言"？
- 纯叙述性句子（如"XX团队在Nature上发表了论文"——这只是报道，不含科学断言）
- 标题、分节标题（如"01""研究背景""图1 ..."）
- 过渡句（如"下面我们来看第二个实验"）
- 图片说明（如"图1 黄瓜单性花与子房下位的进化和发育"）
- 纯背景介绍中的常识性陈述（如"黄瓜是一种常见蔬菜"）
- 仅列出论文作者、发表时间的元信息（但如果该句同时包含科学断言则要保留）

## 输出格式
严格输出 JSON，格式为：
{
  "claims": [
    {
      "id": "C01",
      "claim_text": "观点句原文（完整一句）",
      "context_before": "紧邻的前一句（如无则为空字符串）",
      "context_after": "紧邻的后一句（如无则为空字符串）",
      "section": "该句所在的小节名称（如'研究背景''研究结果''总结与讨论'）"
    }
  ]
}

## 规则
1. 每个元素是一个完整的句子（以句号、分号或换行分隔的完整语义单元）。
2. 只提取有科学信息含量的句子。跳过纯叙述、过渡、标题。
3. id 从 C01 开始递增。
4. 按文章顺序输出。
5. 保留原文表述，不要改写。"""


def _clean_markdown(text: str) -> str:
    """移除 Markdown 标记，保留纯文本。"""
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^---+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
    return text


def extract_claims_from_article(
    article_path: str | Path,
    *,
    client: QwenClient | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """从 Markdown 文章中用 LLM 提取事实性科学观点句。

    Returns:
        列表元素含 claim_id / claim_zh（arag 检索契约），并保留
        context_before / context_after / section。
    """
    if client is None:
        client = QwenClient(verbose=False)

    used_model = model or QWEN_MODEL
    article_path = Path(article_path)
    raw_text = article_path.read_text(encoding="utf-8")

    cleaned = _clean_markdown(raw_text)
    cleaned = re.sub(r"^---[\s\S]*?---\s*", "", cleaned)

    print("  [arag.claim_extractor] 文章长度: %d 字符" % len(cleaned))
    print("  [arag.claim_extractor] 模型: %s" % used_model)

    prompt = "请从以下中文科普文章中提取所有事实性科学观点句：\n\n%s" % cleaned

    try:
        result = client.chat_json(
            build_messages(prompt, system=CLAIM_EXTRACTION_SYSTEM),
            temperature=QWEN_TEMPERATURE,
            model=used_model,
        )
    except Exception as exc:
        print("  [arag.claim_extractor] LLM 调用失败: %s" % exc)
        try:
            raw = client.ask(prompt, system=CLAIM_EXTRACTION_SYSTEM, model=used_model)
            result = extract_json(raw)
        except Exception as exc2:
            print("  [arag.claim_extractor] 降级解析也失败: %s" % exc2)
            return []

    claims_raw = result.get("claims", [])
    if not isinstance(claims_raw, list):
        print("  [arag.claim_extractor] 警告: claims 不是 list: %s" % type(claims_raw))
        return []

    cleaned_claims: list[dict[str, Any]] = []
    for i, claim in enumerate(claims_raw):
        if not isinstance(claim, dict):
            continue
        claim_text = (claim.get("claim_text") or claim.get("claim_zh") or "").strip()
        if not claim_text:
            continue
        claim_id = str(claim.get("id") or claim.get("claim_id") or "C%02d" % (i + 1))
        cleaned_claims.append(
            {
                "claim_id": claim_id,
                "claim_zh": claim_text,
                "context_before": (claim.get("context_before") or "").strip(),
                "context_after": (claim.get("context_after") or "").strip(),
                "section": (claim.get("section") or "").strip(),
                "source_file": article_path.name,
            }
        )

    print("  [arag.claim_extractor] 提取到 %d 条观点句" % len(cleaned_claims))
    return cleaned_claims


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
