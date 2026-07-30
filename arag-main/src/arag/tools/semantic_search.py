"""Semantic search tool - cross-lingual embedding retrieval with reranking.

基于开源 A-RAG 改良，适配中英跨语言幻觉检测场景。

与原生实现的差异：
1. 原生用本地 sentence-transformers 编码查询，这里改为调用统一封装的
   ``api_client.SiliconFlowClient``（bge-m3），不再依赖 torch / transformers。
   bge-m3 本身是多语言模型，中文查询可以直接命中英文句子；
2. 新增重排阶段：向量层先粗召回 ``top_k * recall_multiplier`` 条，再交给
   bge-reranker-v2-m3 做中英交叉打分精排，过滤掉"向量相近但语义无关"的噪声——
   这是跨语言检索的关键，纯向量相似度在中英之间的区分度明显不足；
3. 新增双阈值终止判定：把重排分数交给 ``DualThresholdGate``，在工具返回里直接告诉
   Agent 该停还是该换词重检，避免它在证据不足时提前收手；
4. 索引改为共享的只读 ``IndexStore``，工具实例可按查询轻量新建（不再驻留本地模型），
   批量并发下天然线程安全；
5. 证据广度增强：双语术语多查询并集召回、命中句邻句扩展、多样性截断，让单轮也能留下
   更多可对照的相关原句，而不只依赖 Agent 多轮。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from arag.tools.base import BaseTool

if TYPE_CHECKING:
    from arag.core.context import AgentContext

try:
    import tiktoken

    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False

_TOKEN_SPLIT = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")


def _token_set(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_SPLIT.findall(text or "") if len(token) > 1}


def jaccard_similarity(left: str, right: str) -> float:
    """词袋 Jaccard，用作多样性截断的廉价相似度。"""
    a = _token_set(left)
    b = _token_set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def diversify_select(
    ranked: list[tuple[int, float, float, str]],
    top_k: int,
    *,
    diversity_lambda: float = 0.30,
) -> list[tuple[int, float, float, str]]:
    """在相关性与彼此差异之间折中选取 top_k。

    ranked 元素为 (sentence_index, rerank_score, embed_score, sentence_text)。
    diversity_lambda=0 退化为纯按分数截断。
    """
    if top_k <= 0 or not ranked:
        return []
    if diversity_lambda <= 0 or len(ranked) <= top_k:
        return ranked[:top_k]

    selected: list[tuple[int, float, float, str]] = []
    remaining = list(ranked)
    while remaining and len(selected) < top_k:
        best_idx = 0
        best_value = float("-inf")
        for idx, item in enumerate(remaining):
            relevance = item[1]
            if not selected:
                value = relevance
            else:
                max_sim = max(jaccard_similarity(item[3], chosen[3]) for chosen in selected)
                value = (1.0 - diversity_lambda) * relevance - diversity_lambda * max_sim
            if value > best_value:
                best_value = value
                best_idx = idx
        selected.append(remaining.pop(best_idx))
    return selected


def merge_hits(
    *hit_lists: list[tuple[int, float]],
) -> list[tuple[int, float]]:
    """多路向量召回按句子下标并集，同一句保留最高向量分，再按分数降序。"""
    best: dict[int, float] = {}
    for hits in hit_lists:
        for index, score in hits:
            previous = best.get(index)
            if previous is None or score > previous:
                best[index] = float(score)
    return sorted(best.items(), key=lambda item: item[1], reverse=True)


class SemanticSearchTool(BaseTool):
    """Cross-lingual semantic search: online embedding recall + cross-encoder rerank."""

    def __init__(
        self,
        index_store: Any = None,
        *,
        sf_client: Any = None,
        gate: Any = None,
        board: Any = None,
        default_top_k: int = 10,
        recall_multiplier: int = 5,
        rerank_candidates: int = 80,
        context_window: int = 2,
        neighbor_window: int = 1,
        neighbor_score_decay: float = 0.85,
        diversity_lambda: float = 0.30,
        multi_query_from_terms: bool = True,
        chunks_file: str = None,
        index_dir: str = "data/index",
        verbose: bool = False,
    ):
        """
        Args:
            index_store: 共享的 ``retrieval_adaptor.IndexStore``；为空时按 index_dir 自行加载
            sf_client: 共享的 ``api_client.SiliconFlowClient``；为空时自行创建
            gate: ``retrieval_adaptor.DualThresholdGate``，为空时用默认阈值
            board: 本次查询的 ``EvidenceBoard``，用于把候选传递给 read_chunk
            default_top_k: Agent 未传 top_k 时的默认返回条数
            recall_multiplier: 向量粗召回倍数
            rerank_candidates: 送入重排的候选上限
            neighbor_window: 命中句同 chunk 邻句扩展窗口
            neighbor_score_decay: 邻句分数 = 父句分数 × 衰减
            diversity_lambda: 多样性截断权重
            multi_query_from_terms: 是否用看板里的英文学术术语并集一路召回
            chunks_file: 保留原生参数以兼容既有调用，索引内已含 chunk，实际不再使用
        """
        if not HAS_TIKTOKEN:
            raise ImportError("tiktoken required. Install: pip install tiktoken")

        from api_client import SiliconFlowClient
        from retrieval_adaptor.index_store import IndexStore
        from retrieval_adaptor.thresholds import DualThresholdGate

        self.store = index_store if index_store is not None else IndexStore(index_dir)
        self.client = sf_client if sf_client is not None else SiliconFlowClient(verbose=verbose)
        self.gate = gate if gate is not None else DualThresholdGate()
        self.board = board
        self.default_top_k = max(1, int(default_top_k))
        self.recall_multiplier = max(1, int(recall_multiplier))
        self.rerank_candidates = max(1, int(rerank_candidates))
        self.context_window = max(0, int(context_window))
        self.neighbor_window = max(0, int(neighbor_window))
        self.neighbor_score_decay = float(neighbor_score_decay)
        self.diversity_lambda = max(0.0, min(float(diversity_lambda), 1.0))
        self.multi_query_from_terms = bool(multi_query_from_terms)
        self.chunks_file = chunks_file
        self.verbose = verbose
        self.tokenizer = tiktoken.encoding_for_model("gpt-4o")

    @property
    def name(self) -> str:
        return "semantic_search"

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "semantic_search",
                "description": """Cross-lingual semantic search over the English paper corpus, with cross-encoder reranking.

The query may be written in Chinese OR English - the embedding model is multilingual, so a Chinese claim can directly match English sentences. Results are reranked by a cross-encoder, so the returned relevance scores are calibrated and comparable across queries.

WHEN TO USE:
- To find English paper passages that support or contradict a Chinese claim
- When keyword_search returns nothing (wording in the paper differs from your terms)
- For conceptual matching rather than exact terminology
- Prefer top_k around 8-12 when you need broader evidence coverage

IMPORTANT: The tool reports a retrieval decision at the end of its output:
- "STOP" means enough strong hits (see min_hits) were found; proceed to read_chunk and answer
- "CONTINUE" means evidence is still thin (e.g. only one strong hit); rephrase focusing on an uncovered sub-claim and search once more

RETURNS: Ranked snippets with calibrated relevance scores and chunk IDs. Use read_chunk on the top chunks to obtain the full structured evidence.""",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Query describing what to look for. Chinese or English both work; English academic phrasing usually scores higher.",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": (
                                "Number of results to return after reranking "
                                "(default: %d, max: 20)" % self.default_top_k
                            ),
                            "default": self.default_top_k,
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    # ------------------------------------------------------------------ 检索实现

    def _term_query(self) -> str:
        """从证据看板的双语术语拼一路英文补充查询。"""
        if not self.multi_query_from_terms or self.board is None:
            return ""
        terms = getattr(self.board, "terms", None) or []
        pieces: list[str] = []
        for term in terms:
            en = str(getattr(term, "en", "") or "").strip()
            if en and en.lower() not in {p.lower() for p in pieces}:
                pieces.append(en)
            for alias in getattr(term, "aliases", None) or []:
                alias_text = str(alias).strip()
                if alias_text and alias_text.lower() not in {p.lower() for p in pieces}:
                    pieces.append(alias_text)
            if len(pieces) >= 6:
                break
        return " ".join(pieces[:6]).strip()

    def _recall(self, query: str, limit: int) -> list[tuple[int, float]]:
        """向量粗召回（改良：在线 bge-m3 替代本地模型）。"""
        query_vector = self.client.embed_query(query)
        return self.store.search(query_vector, limit)

    def _recall_union(self, query: str, limit: int) -> list[tuple[int, float]]:
        """主查询 + 术语查询并集召回，拓宽单轮覆盖面。"""
        primary = self._recall(query, limit)
        term_query = self._term_query()
        if not term_query or term_query.lower() == query.strip().lower():
            return primary
        # 补充路取一半额度，避免挤掉主查询高分句
        secondary_limit = max(limit // 2, min(20, limit))
        secondary = self._recall(term_query, secondary_limit)
        return merge_hits(primary, secondary)[:limit]

    def _rerank(
        self, query: str, hits: list[tuple[int, float]]
    ) -> list[tuple[int, float, float]]:
        """交叉编码器精排（改良新增），返回 [(句子下标, 重排分, 向量分)]。"""
        if not hits:
            return []
        documents = [self.store.sentence(index) for index, _ in hits]
        result = self.client.rerank(query, documents, top_n=len(documents))

        embed_scores = {index: score for index, score in hits}
        ordered: list[tuple[int, float, float]] = []
        for item in result.items:
            if not 0 <= item.index < len(hits):
                continue
            sentence_index = hits[item.index][0]
            ordered.append((sentence_index, item.score, embed_scores.get(sentence_index, 0.0)))
        return ordered

    def _expand_neighbors(
        self, ranked: list[tuple[int, float, float]]
    ) -> list[tuple[int, float, float, str]]:
        """把命中句同 chunk 内的邻句带上（分数衰减），丰富可对照原句。"""
        best: dict[int, tuple[float, float]] = {}
        for sentence_index, rerank_score, embed_score in ranked:
            previous = best.get(sentence_index)
            if previous is None or rerank_score > previous[0]:
                best[sentence_index] = (rerank_score, embed_score)

            if self.neighbor_window <= 0:
                continue
            chunk_id = self.store.chunk_id_of(sentence_index)
            position = (
                self.store.sentence_offset[sentence_index]
                if sentence_index < len(self.store.sentence_offset)
                else -1
            )
            if position < 0:
                continue
            for delta in range(1, self.neighbor_window + 1):
                for neighbor_pos in (position - delta, position + delta):
                    neighbor_index = self.store.global_index(chunk_id, neighbor_pos)
                    if neighbor_index < 0:
                        continue
                    neighbor_score = rerank_score * (self.neighbor_score_decay**delta)
                    if not self.gate.keep(neighbor_score):
                        continue
                    existing = best.get(neighbor_index)
                    if existing is None or neighbor_score > existing[0]:
                        best[neighbor_index] = (neighbor_score, embed_score * 0.5)

        enriched = [
            (index, scores[0], scores[1], self.store.sentence(index))
            for index, scores in best.items()
        ]
        enriched.sort(key=lambda item: item[1], reverse=True)
        return enriched

    def execute(
        self, context: "AgentContext", query: str, top_k: int | None = None
    ) -> tuple[str, dict[str, Any]]:
        if top_k is None:
            top_k = self.default_top_k
        top_k = max(1, min(int(top_k), 20))
        recall_limit = min(top_k * self.recall_multiplier, self.rerank_candidates)

        hits = self._recall_union(query, recall_limit)
        if not hits:
            decision = self.gate.evaluate([])
            if self.board is not None:
                self.board.record_decision(decision)
            message = "No results for: %s\n\n[RETRIEVAL DECISION] %s" % (query, decision.describe())
            return message, {
                "retrieved_tokens": 0,
                "chunks_found": 0,
                **decision.to_dict(),
            }

        ranked = self._rerank(query, hits)
        kept_ranked = [item for item in ranked if self.gate.keep(item[1])]
        if not kept_ranked:
            kept_ranked = ranked[:1]

        enriched = self._expand_neighbors(kept_ranked)
        kept = diversify_select(
            enriched, top_k, diversity_lambda=self.diversity_lambda
        )
        if not kept:
            kept = enriched[:1]

        candidates = []
        result_parts = []
        matched_sentences = []
        for sentence_index, rerank_score, embed_score, sentence in kept:
            chunk_id = self.store.chunk_id_of(sentence_index)
            chunk = self.store.chunk(chunk_id)
            matched_sentences.append(sentence)

            label = self.gate.label(rerank_score)
            source = chunk.get("title") or chunk.get("source_file") or ""
            section = chunk.get("section") or ""
            result_parts.append(
                "Chunk ID: %s (relevance: %.4f | vector: %.4f | %s)\n"
                "Paper: %s%s\nMatched: ... %s ..."
                % (
                    chunk_id,
                    rerank_score,
                    embed_score,
                    label,
                    str(source)[:80] or "unknown",
                    " / %s" % section if section else "",
                    sentence,
                )
            )

            if self.board is not None:
                from retrieval_adaptor.evidence_board import Candidate

                candidates.append(
                    Candidate(
                        chunk_id=chunk_id,
                        sentence=sentence,
                        sentence_index=sentence_index,
                        embed_score=embed_score,
                        rerank_score=rerank_score,
                        round_index=self.board.search_rounds + 1,
                    )
                )

        if self.board is not None and candidates:
            self.board.add_candidates(candidates)

        # 双阈值判定（改良新增）：基于本次查询累积到的全部候选
        all_scores = (
            self.board.scores() if self.board is not None else [item[1] for item in kept]
        )
        decision = self.gate.evaluate(all_scores)
        if self.board is not None:
            self.board.record_decision(decision)

        tool_result = "%s\n\n[RETRIEVAL DECISION] %s" % (
            "\n\n".join(result_parts),
            decision.describe(),
        )

        retrieved_tokens = (
            len(self.tokenizer.encode("\n".join(matched_sentences))) if matched_sentences else 0
        )
        context.add_retrieval_log(
            tool_name="semantic_search",
            tokens=retrieved_tokens,
            metadata={
                "query": query,
                "chunks_found": len({item.chunk_id for item in candidates}) or len(kept),
                "recalled": len(hits),
                "reranked": len(ranked),
                "expanded": len(enriched),
                **decision.to_dict(),
            },
        )

        return tool_result, {
            "retrieved_tokens": retrieved_tokens,
            "chunks_found": len(kept),
            "recalled": len(hits),
            "expanded": len(enriched),
            **decision.to_dict(),
        }
