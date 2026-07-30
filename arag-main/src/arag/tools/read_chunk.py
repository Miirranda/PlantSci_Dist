"""Read chunk tool - structured cross-lingual evidence output.

基于开源 A-RAG 改良，适配中英跨语言幻觉检测场景。

与原生实现的差异：
1. 原生返回用 ``===`` 分隔的自然语言长文本，只能给人/模型读；这里改为输出**固定 JSON
   结构**（schema 见 ``retrieval_adaptor.schemas``），字段包含公众号原文、匹配英文论据、
   论文元数据、段落上下文，可直接送入下游幻觉判定模块而无需再解析自然语言；
2. 每条证据都带 bge-reranker 的校准相关性分数与三值判定标签；若某个 chunk 是被
   keyword_search 命中、还没经过重排，则在此处就地补一次重排打分，保证输出里不存在
   "无分数证据"；
3. 保留原生的 Context Tracker 语义（已读 chunk 不重复计费、不重复返回全文）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from arag.tools.base import BaseTool

if TYPE_CHECKING:
    from arag.core.context import AgentContext

try:
    import tiktoken

    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False


class ReadChunkTool(BaseTool):
    """Read chunks and emit structured evidence records."""

    def __init__(
        self,
        chunks_file: str = None,
        *,
        index_store: Any = None,
        board: Any = None,
        gate: Any = None,
        sf_client: Any = None,
        context_window: int = 2,
        max_chunk_chars: int = 4000,
    ):
        """
        Args:
            chunks_file: 原生参数；未提供 index_store 时从该文件加载 chunk
            index_store: 共享的 ``retrieval_adaptor.IndexStore``
            board: 本次查询的 ``EvidenceBoard``，提供中文断言与已有重排分数
            gate: ``DualThresholdGate``，用于给每条证据打三值标签
            sf_client: ``api_client.SiliconFlowClient``，用于给未打分的 chunk 补重排
            max_chunk_chars: 单个 chunk 回传给模型的正文上限，防止吃满上下文预算
        """
        if not HAS_TIKTOKEN:
            raise ImportError("tiktoken required. Install: pip install tiktoken")

        from retrieval_adaptor.thresholds import DualThresholdGate

        self.chunks_file = chunks_file
        self.store = index_store
        self.board = board
        self.gate = gate if gate is not None else DualThresholdGate()
        self.client = sf_client
        self.context_window = max(0, int(context_window))
        self.max_chunk_chars = max(200, int(max_chunk_chars))

        if self.store is None:
            if not chunks_file:
                raise ValueError("必须提供 index_store 或 chunks_file 之一")
            self.chunks_dict = self._load_chunks(chunks_file)
        else:
            self.chunks_dict = self.store.chunks

        self.tokenizer = tiktoken.encoding_for_model("gpt-4o")

    @staticmethod
    def _load_chunks(chunks_file: str) -> dict[str, dict[str, Any]]:
        """兼容原生 ``["0:text"]`` 与扩展的带元数据 dict 格式。"""
        with open(chunks_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        chunks: dict[str, dict[str, Any]] = {}
        for item in data:
            if isinstance(item, dict):
                chunks[str(item["id"])] = item
            elif isinstance(item, str):
                parts = item.split(":", 1)
                if len(parts) == 2:
                    chunks[parts[0]] = {"id": parts[0], "text": parts[1]}
        return chunks

    @property
    def name(self) -> str:
        return "read_chunk"

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "read_chunk",
                "description": """Read the full content of paper chunks by ID and return STRUCTURED EVIDENCE as JSON.

Unlike the search tools (which return abbreviated snippets), this tool returns a fixed JSON structure containing, for every chunk: the Chinese claim under verification, the matched English evidence sentence, its calibrated relevance score, the paper metadata (title / authors / year / doi / section / page), and the surrounding paragraph context.

STRATEGY:
- Call this on the top-ranked chunks from semantic_search or keyword_search
- Read the chunks that actually matter; every chunk you read consumes token budget
- If the evidence looks truncated, read the adjacent chunk IDs (+/- 1)

Note: chunks already read earlier are reported as such and not returned in full again.""",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chunk_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of chunk IDs to retrieve (e.g., ['0', '24', '172'])",
                        }
                    },
                    "required": ["chunk_ids"],
                },
            },
        }

    # ------------------------------------------------------------------ 补分逻辑

    def _score_unranked(self, chunk_ids: list[str]) -> dict[str, tuple[str, int, float]]:
        """给没有重排分数的 chunk 就地补一次打分。

        返回 ``{chunk_id: (最佳句子, 句子下标, 分数)}``。所有 chunk 的候选句合并成一次
        rerank 请求，避免逐个 chunk 打分带来的多次往返。
        """
        if not (self.client and self.board and self.store and self.board.claim_zh):
            return {}

        pending: list[tuple[str, int, str]] = []
        for chunk_id in chunk_ids:
            sentences = self.store.chunk_sentences.get(str(chunk_id)) or []
            for position, sentence in enumerate(sentences[:20]):
                pending.append((str(chunk_id), position, sentence))

        if not pending:
            return {}

        try:
            result = self.client.rerank(
                self.board.claim_zh, [item[2] for item in pending], top_n=len(pending)
            )
        except Exception:
            # 补分失败不应让 read_chunk 整体失败，退回无分数状态
            return {}

        best: dict[str, tuple[str, int, float]] = {}
        for item in result.items:
            if not 0 <= item.index < len(pending):
                continue
            chunk_id, position, sentence = pending[item.index]
            current = best.get(chunk_id)
            if current is None or item.score > current[2]:
                best[chunk_id] = (sentence, position, item.score)
        return best

    # ------------------------------------------------------------------ 工具接口

    def execute(
        self,
        context: "AgentContext",
        chunk_ids: list[str] = None,
        chunk_id: str = None,
    ) -> tuple[str, dict[str, Any]]:
        """Read chunks and return the fixed JSON evidence structure."""
        if chunk_ids is None:
            if chunk_id is None:
                return (
                    json.dumps({"error": "No chunk IDs provided"}, ensure_ascii=False),
                    {"retrieved_tokens": 0, "error": "missing_argument"},
                )
            chunk_ids = [str(chunk_id)]
        chunk_ids = [str(item) for item in chunk_ids]

        from retrieval_adaptor.evidence_board import Candidate, EvidenceBoard
        from retrieval_adaptor.schemas import (
            VERDICT_INCONCLUSIVE,
            PaperMetadata,
            ParagraphContext,
            RetrievalOutput,
        )

        board = self.board
        # 独立使用（不走流水线）时临时建一个空看板，保证输出结构一致
        if board is None:
            board = EvidenceBoard(claim_zh="", gate=self.gate)

        unscored = [cid for cid in chunk_ids if board.best_for_chunk(cid) is None]
        rescored = self._score_unranked(unscored) if unscored else {}

        evidences = []
        new_chunks_read: list[str] = []
        already_read: list[str] = []
        missing: list[str] = []
        total_tokens = 0

        for index, cid in enumerate(chunk_ids, start=1):
            chunk = self.chunks_dict.get(cid)
            if chunk is None:
                missing.append(cid)
                continue

            candidate = board.best_for_chunk(cid)
            if candidate is None and cid in rescored:
                sentence, position, score = rescored[cid]
                candidate = Candidate(
                    chunk_id=cid,
                    sentence=sentence,
                    # 补分拿到的是 chunk 内句序，换算成全局下标后再入看板
                    sentence_index=self.store.global_index(cid, position)
                    if self.store is not None
                    else -1,
                    rerank_score=score,
                )
                # 补分结果并入看板，但不计入检索轮次——它不是一次检索
                board.add_candidates([candidate], count_as_round=False)

            if context.is_chunk_read(cid):
                already_read.append(cid)
            else:
                content = str(chunk.get("text") or "")
                total_tokens += len(self.tokenizer.encode(content))
                context.mark_chunk_as_read(cid)
                new_chunks_read.append(cid)

            evidences.append(
                self._build_record(
                    evidence_id="ev_%d" % index,
                    chunk_id=cid,
                    chunk=chunk,
                    candidate=candidate,
                    already_read=cid in already_read,
                    board=board,
                    fallback_verdict=VERDICT_INCONCLUSIVE,
                    metadata_cls=PaperMetadata,
                    context_cls=ParagraphContext,
                )
            )

        output: RetrievalOutput = board.build_output(
            evidences,
            extra_stats={
                "requested_chunks": chunk_ids,
                "new_chunks_read": new_chunks_read,
                "already_read": already_read,
                "missing_chunks": missing,
                "rescored_chunks": sorted(rescored),
            },
        )
        tool_result = output.to_json()

        context.add_retrieval_log(
            tool_name="read_chunk",
            tokens=total_tokens,
            metadata={
                "chunk_ids_requested": chunk_ids,
                "new_chunks_read": new_chunks_read,
                "already_read": already_read,
                "verdict": output.verdict,
            },
        )

        return tool_result, {
            "retrieved_tokens": total_tokens,
            "new_chunks_count": len(new_chunks_read),
            "already_read_count": len(already_read),
            "evidence_count": len(evidences),
            "verdict": output.verdict,
            "structured_output": output.to_dict(),
        }

    def _build_record(
        self,
        *,
        evidence_id: str,
        chunk_id: str,
        chunk: dict[str, Any],
        candidate: Any,
        already_read: bool,
        board: Any,
        fallback_verdict: str,
        metadata_cls: Any,
        context_cls: Any,
    ) -> Any:
        """拼装单条 EvidenceRecord。"""
        from retrieval_adaptor.schemas import EvidenceRecord

        chunk_text = str(chunk.get("text") or "")
        if already_read:
            # 已读过的 chunk 不再重复回传全文，只保留定位信息
            target_text = "(chunk already read earlier in this session)"
            prev_text = next_text = ""
        elif self.store is not None and candidate is not None and candidate.sentence_index >= 0:
            prev_text, target_text, next_text = self.store.paragraph_context(
                candidate.sentence_index, self.context_window
            )
        else:
            target_text = chunk_text[: self.max_chunk_chars]
            prev_text = next_text = ""

        score = float(candidate.rerank_score) if candidate is not None else 0.0
        verdict = self.gate.label(score) if candidate is not None else fallback_verdict

        return EvidenceRecord(
            evidence_id=evidence_id,
            chunk_id=chunk_id,
            evidence_en=candidate.sentence if candidate is not None else chunk_text[:500],
            rerank_score=score,
            embed_score=float(candidate.embed_score) if candidate is not None else 0.0,
            verdict=verdict,
            matched_terms=list(candidate.matched_terms) if candidate is not None else [],
            paper=metadata_cls.from_dict(chunk),
            context=context_cls(
                section=str(chunk.get("section") or ""),
                page=str(chunk.get("page") or ""),
                prev_text=prev_text,
                target_text=target_text,
                next_text=next_text,
            ),
        )
