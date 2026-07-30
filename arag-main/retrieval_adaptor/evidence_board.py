"""单次查询的证据看板。

原创代码（非 A-RAG 开源部分）。

存在的理由：``semantic_search`` 拿到的是"带 rerank 分数的候选句"，而 ``read_chunk`` 只收到
一串 chunk_id——两者之间需要一个载体传递分数、匹配句、命中术语，才能拼出完整的结构化证据。
每次查询独立创建一个看板并注入到该次查询使用的工具实例上，因此**不需要改动原生的
``BaseAgent`` 与 ``AgentContext``**，批量并发时也天然隔离。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .schemas import (
    VERDICT_INCONCLUSIVE,
    BilingualTerm,
    EvidenceRecord,
    PaperMetadata,
    ParagraphContext,
    RetrievalOutput,
)
from .thresholds import DualThresholdGate, GateDecision


@dataclass
class Candidate:
    """一条重排后的候选证据。

    ``sentence_index`` 统一为 ``IndexStore`` 里的**全局句子下标**（不是 chunk 内句序），
    可直接喂给 ``IndexStore.paragraph_context``；无法定位时为 -1。
    """

    chunk_id: str
    sentence: str
    sentence_index: int
    embed_score: float = 0.0
    rerank_score: float = 0.0
    matched_terms: list[str] = field(default_factory=list)
    round_index: int = 1


class EvidenceBoard:
    """累积一次查询过程中的全部候选证据与判定轨迹。"""

    def __init__(self, claim_zh: str, gate: DualThresholdGate) -> None:
        self.claim_zh = claim_zh
        self.gate = gate
        self._lock = threading.Lock()
        self.candidates: dict[str, Candidate] = {}
        self.terms: list[BilingualTerm] = []
        self.decisions: list[GateDecision] = []
        self.search_rounds = 0
        self.keyword_hits: list[str] = []

    # ------------------------------------------------------------------ 写入

    def set_terms(self, terms: list[dict[str, Any]] | list[BilingualTerm]) -> None:
        parsed: list[BilingualTerm] = []
        for item in terms:
            parsed.append(item if isinstance(item, BilingualTerm) else BilingualTerm.from_dict(item))
        with self._lock:
            self.terms = parsed

    def add_keyword_hits(self, chunk_ids: list[str]) -> None:
        with self._lock:
            for chunk_id in chunk_ids:
                if chunk_id not in self.keyword_hits:
                    self.keyword_hits.append(chunk_id)

    def add_candidates(self, candidates: list[Candidate], *, count_as_round: bool = True) -> None:
        """按 (chunk_id, 句子下标) 去重，同一句多轮命中只保留分数最高的一次。

        ``count_as_round`` 为 False 时不计入检索轮次——read_chunk 的就地补分虽然也产出
        候选，但它不是一次"检索"，计进去会让轮次统计与双阈值的轮次上限失真。
        """
        with self._lock:
            if count_as_round:
                self.search_rounds += 1
            for candidate in candidates:
                key = "%s:%d" % (candidate.chunk_id, candidate.sentence_index)
                existing = self.candidates.get(key)
                if existing is None or candidate.rerank_score > existing.rerank_score:
                    self.candidates[key] = candidate

    def record_decision(self, decision: GateDecision) -> None:
        with self._lock:
            self.decisions.append(decision)

    # ------------------------------------------------------------------ 读取

    def all_candidates(self) -> list[Candidate]:
        with self._lock:
            items = list(self.candidates.values())
        items.sort(key=lambda item: item.rerank_score, reverse=True)
        return items

    def scores(self) -> list[float]:
        return [item.rerank_score for item in self.all_candidates()]

    def best_for_chunk(self, chunk_id: str) -> Candidate | None:
        """某个 chunk 上分数最高的候选句。"""
        best: Candidate | None = None
        for candidate in self.all_candidates():
            if candidate.chunk_id == str(chunk_id):
                if best is None or candidate.rerank_score > best.rerank_score:
                    best = candidate
        return best

    def candidates_of_chunk(self, chunk_id: str) -> list[Candidate]:
        return [item for item in self.all_candidates() if item.chunk_id == str(chunk_id)]

    @property
    def last_decision(self) -> GateDecision | None:
        with self._lock:
            return self.decisions[-1] if self.decisions else None

    # ------------------------------------------------------------------ 产出

    def build_evidence(
        self,
        candidate: Candidate,
        *,
        chunk: dict[str, Any],
        context: tuple[str, str, str],
        evidence_id: str,
    ) -> EvidenceRecord:
        prev_text, target_text, next_text = context
        return EvidenceRecord(
            evidence_id=evidence_id,
            chunk_id=candidate.chunk_id,
            evidence_en=candidate.sentence,
            rerank_score=candidate.rerank_score,
            embed_score=candidate.embed_score,
            verdict=self.gate.label(candidate.rerank_score),
            matched_terms=list(candidate.matched_terms),
            paper=PaperMetadata.from_dict(chunk),
            context=ParagraphContext(
                section=str(chunk.get("section") or ""),
                page=str(chunk.get("page") or ""),
                prev_text=prev_text,
                target_text=target_text,
                next_text=next_text,
            ),
        )

    def build_output(
        self,
        evidences: list[EvidenceRecord],
        *,
        max_rounds: int | None = None,
        extra_stats: dict[str, Any] | None = None,
    ) -> RetrievalOutput:
        """汇总成最终交付给幻觉判定模块的结构。"""
        decision = self.gate.evaluate(
            self.scores(), round_index=max(1, self.search_rounds), max_rounds=max_rounds
        )
        stats = {
            "search_rounds": self.search_rounds,
            "candidate_count": len(self.candidates),
            "keyword_hit_chunks": list(self.keyword_hits),
            "top_score": round(decision.top_score, 6),
            "strong_hits": decision.strong_hits,
            "thresholds": {
                "high": self.gate.config.high,
                "low": self.gate.config.low,
                "min_hits": self.gate.config.min_hits,
            },
        }
        if extra_stats:
            stats.update(extra_stats)

        return RetrievalOutput(
            claim_zh=self.claim_zh,
            verdict=decision.verdict,
            # 终止原因必须与 verdict 出自同一次判定，否则会出现
            # 「SUPPORTED 却写着 continue_search」这类自相矛盾的输出
            stop_reason=decision.reason,
            bilingual_terms=list(self.terms),
            evidences=evidences,
            stats=stats,
        )

    def empty_output(self, reason: str) -> RetrievalOutput:
        return RetrievalOutput(
            claim_zh=self.claim_zh,
            verdict=VERDICT_INCONCLUSIVE,
            stop_reason=reason,
            bilingual_terms=list(self.terms),
            evidences=[],
            stats={"search_rounds": self.search_rounds, "candidate_count": 0},
        )
