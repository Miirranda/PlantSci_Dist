"""arag 检索结果 ↔ hallu 分类器输入 的格式转换。

契约：
  arag claims.jsonl  : {"claim_id", "claim_zh", ...}
  arag evidences.jsonl: RetrievalOutput（完整）
  arag pairs.jsonl   : {"claim_zh", "paper_sentences": [...]}  或带 claim_id
  hallu retrieval    : {"claim_id", "claim_text", "evidence_sentences": [{rank, sentence, ...}]}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def save_claims_jsonl(claims: list[dict[str, Any]], path: str | Path) -> Path:
    """把 hallu 提取的观点句写成 arag 可读的 JSONL。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for claim in claims:
            row = {
                "claim_id": claim.get("id") or claim.get("claim_id"),
                "claim_zh": claim.get("claim_text") or claim.get("claim_zh") or "",
            }
            # 保留上下文等附加字段，arag 会忽略未知键
            for key in ("context_before", "context_after", "section"):
                if claim.get(key):
                    row[key] = claim[key]
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out


def load_evidences_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """读取 arag 完整 evidences.jsonl。"""
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def load_claim_paper_pairs(path: str | Path) -> list[dict[str, Any]]:
    """读取精简对照表 claim_paper_pairs.jsonl。"""
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _sentence_from_evidence(item: dict[str, Any]) -> str:
    text = str(item.get("evidence_en") or "").strip()
    if text:
        return text
    context = item.get("context") or {}
    return str(context.get("target_text") or "").strip()


def evidences_to_pairs(evidences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """完整 RetrievalOutput → claim_paper_pairs 精简格式。"""
    pairs: list[dict[str, Any]] = []
    for record in evidences:
        claim = str(record.get("claim_zh") or "").strip()
        claim_id = str(record.get("claim_id") or "").strip()
        sentences: list[str] = []
        seen: set[str] = set()
        for item in record.get("evidences") or []:
            if not isinstance(item, dict):
                continue
            sentence = _sentence_from_evidence(item)
            if not sentence or sentence in seen:
                continue
            seen.add(sentence)
            sentences.append(sentence)
        if not claim:
            continue
        pairs.append({
            "claim_id": claim_id,
            "claim_zh": claim,
            "paper_sentences": sentences,
            "verdict": record.get("verdict", ""),
        })
    return pairs


def arag_pairs_to_retrieval_results(
    pairs: list[dict[str, Any]],
    claims: list[dict[str, Any]] | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """把 arag pairs 转成 hallu.classifier 期望的 retrieval_results。

    优先用 claim_id 对齐；若 pairs 无 id，则按 claim_zh / claim_text 对齐。
    """
    claim_by_id: dict[str, dict[str, Any]] = {}
    claim_by_text: dict[str, dict[str, Any]] = {}
    if claims:
        for c in claims:
            cid = str(c.get("id") or c.get("claim_id") or "")
            text = str(c.get("claim_text") or c.get("claim_zh") or "").strip()
            if cid:
                claim_by_id[cid] = c
            if text:
                claim_by_text[text] = c

    results: list[dict[str, Any]] = []
    for i, pair in enumerate(pairs):
        claim_zh = str(pair.get("claim_zh") or "").strip()
        claim_id = str(pair.get("claim_id") or "").strip()
        if not claim_id and claim_zh in claim_by_text:
            claim_id = str(
                claim_by_text[claim_zh].get("id")
                or claim_by_text[claim_zh].get("claim_id")
                or f"C{i + 1:02d}"
            )
        if not claim_id:
            claim_id = f"C{i + 1:02d}"

        sentences = pair.get("paper_sentences") or []
        evidence_sentences = [
            {
                "rank": rank,
                "sentence": sent,
                "relevance_score": None,
                "relevance_reason": "arag retrieval",
            }
            for rank, sent in enumerate(sentences[:top_k], start=1)
            if isinstance(sent, str) and sent.strip()
        ]

        meta = claim_by_id.get(claim_id) or claim_by_text.get(claim_zh) or {}
        results.append({
            "claim_id": claim_id,
            "claim_text": claim_zh or meta.get("claim_text", ""),
            "evidence_sentences": evidence_sentences,
            "overall_assessment": pair.get("verdict", ""),
            "retrieval_stats": {
                "evidence_count": len(evidence_sentences),
                "verdict": pair.get("verdict", ""),
                "source": "arag",
            },
        })
    return results


def save_pairs_jsonl(pairs: list[dict[str, Any]], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in pairs:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out
