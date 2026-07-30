"""arag 输出 → hallu 分类器输入 适配层。"""

from .from_arag import (
    arag_pairs_to_retrieval_results,
    evidences_to_pairs,
    load_claim_paper_pairs,
    load_evidences_jsonl,
    save_claims_jsonl,
)

__all__ = [
    "save_claims_jsonl",
    "load_evidences_jsonl",
    "load_claim_paper_pairs",
    "evidences_to_pairs",
    "arag_pairs_to_retrieval_results",
]
