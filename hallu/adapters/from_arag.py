"""arag 检索结果 ↔ hallu 分类器输入 的格式转换。

契约：
  arag claims.jsonl  : {"claim_id", "claim_zh", ...}
  arag evidences.jsonl: RetrievalOutput（完整，含 sentence_id）
  arag pairs.jsonl   :
      {
        "claim_id", "claim_zh",
        "classify_evidences": [{rank, sentence_id, text}, ...],  # top-5，供分类
        "review_evidences":   [{rank, sentence_id, text}, ...],  # 固定 10 条，供审核
        "evidences": [...]   # 兼容旧字段，等同 review 池
      }
  hallu retrieval    : {"claim_id", "claim_text", "evidence_sentences": [{rank, sentence, sentence_id, ...}]}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CLASSIFY_TOP_K = 5
REVIEW_POOL_SIZE = 10


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
            for key in ("claim_role", "context_before", "context_after", "section"):
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
    """读取精简对照表（claim_evidence_pairs / 旧 claim_paper_pairs）。"""
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _sentence_from_evidence(item: dict[str, Any]) -> str:
    text = str(
        item.get("evidence_en") or item.get("text") or item.get("sentence") or ""
    ).strip()
    if text:
        return text
    context = item.get("context") or {}
    return str(context.get("target_text") or "").strip()


def _sentence_id(item: dict[str, Any]) -> int:
    try:
        return int(item.get("sentence_id", -1))
    except (TypeError, ValueError):
        return -1


def _ranked_from_raw(
    items: list[Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if len(rows) >= limit:
            break
        if isinstance(item, str):
            sentence = item.strip()
            sid = -1
        elif isinstance(item, dict):
            sentence = _sentence_from_evidence(item)
            sid = _sentence_id(item)
        else:
            continue
        if not sentence or sentence in seen:
            continue
        seen.add(sentence)
        rows.append(
            {
                "rank": len(rows) + 1,
                "sentence_id": sid,
                "text": sentence,
            }
        )
    return rows


def split_retrieval_pools(
    record: dict[str, Any],
    *,
    classify_top_k: int = CLASSIFY_TOP_K,
    review_pool_size: int = REVIEW_POOL_SIZE,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """从一条 pairs / evidences 记录拆出分类池与审核池。"""
    review_raw = record.get("review_evidences")
    classify_raw = record.get("classify_evidences")

    if isinstance(review_raw, list) and review_raw:
        review = _ranked_from_raw(review_raw, limit=review_pool_size)
    else:
        fallback = list(
            record.get("evidences") or record.get("paper_sentences") or []
        )
        review = _ranked_from_raw(fallback, limit=review_pool_size)

    if isinstance(classify_raw, list) and classify_raw:
        classify = _ranked_from_raw(classify_raw, limit=classify_top_k)
    else:
        classify = review[:classify_top_k]
    return classify, review


def evidences_to_pairs(evidences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """完整 RetrievalOutput → claim_evidence_pairs（分类 top-5 + 审核池 10）。"""
    pairs: list[dict[str, Any]] = []
    for record in evidences:
        claim = str(record.get("claim_zh") or "").strip()
        claim_id = str(record.get("claim_id") or "").strip()
        if not claim:
            continue
        classify, review = split_retrieval_pools(record)
        row: dict[str, Any] = {
            "claim_id": claim_id,
            "claim_zh": claim,
            "classify_evidences": classify,
            "review_evidences": review,
            "evidences": [
                {"sentence_id": ev["sentence_id"], "text": ev["text"]} for ev in review
            ],
            "paper_sentences": [ev["text"] for ev in review],
            "verdict": record.get("verdict", ""),
        }
        pairs.append(row)
    return pairs


def arag_pairs_to_retrieval_results(
    pairs: list[dict[str, Any]],
    claims: list[dict[str, Any]] | None = None,
    top_k: int = CLASSIFY_TOP_K,
) -> list[dict[str, Any]]:
    """把 arag pairs 转成 hallu.classifier 期望的 retrieval_results。

    分类只使用 classify_evidences（或 review/evidences 的前 top_k 条）。
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

        classify, review = split_retrieval_pools(
            pair, classify_top_k=top_k, review_pool_size=REVIEW_POOL_SIZE
        )
        evidence_sentences = [
            {
                "rank": int(item.get("rank") or rank),
                "sentence": str(item.get("text") or "").strip(),
                "sentence_id": _sentence_id(item),
                "relevance_score": None,
                "relevance_reason": "arag retrieval",
            }
            for rank, item in enumerate(classify[:top_k], start=1)
            if str(item.get("text") or "").strip()
        ]

        meta = claim_by_id.get(claim_id) or claim_by_text.get(claim_zh) or {}
        results.append(
            {
                "claim_id": claim_id,
                "claim_text": claim_zh or meta.get("claim_text", ""),
                "evidence_sentences": evidence_sentences,
                "review_evidences": review,
                "overall_assessment": pair.get("verdict", ""),
                "retrieval_stats": {
                    "evidence_count": len(evidence_sentences),
                    "review_pool_size": len(review),
                    "classify_top_k": top_k,
                    "verdict": pair.get("verdict", ""),
                    "source": "arag",
                },
            }
        )
    return results


def save_pairs_jsonl(pairs: list[dict[str, Any]], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in pairs:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out
