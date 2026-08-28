"""跨语言检索流水线：把中文断言变成结构化英文论文证据。

原创代码（非 A-RAG 开源部分）。

职责是"装配"而不是"重写"：共享重资源（索引、HTTP 客户端、术语缓存），为每条断言创建
独立的证据看板与轻量工具实例，然后交给原生的 ``BaseAgent`` ReAct 循环去调度——
循环逻辑一行未改。
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from api_client import QwenClient, SiliconFlowClient
from api_client.exceptions import APIClientError

from .config import AgentRuntimeConfig, RetrievalConfig, ensure_dirs
from .evidence_board import EvidenceBoard
from .index_store import IndexStore
from .qwen_agent_adapter import QwenAgentAdapter
from .schemas import (
    VERDICT_INCONCLUSIVE,
    VERDICT_NO_EVIDENCE,
    EvidenceRecord,
    PaperMetadata,
    ParagraphContext,
    RetrievalOutput,
    RetrievalSufficiency,
)
from .thresholds import STOP_INSUFFICIENT_HINT, DualThresholdGate


class BoardAwareKeywordSearch:
    """给原生 ``KeywordSearchTool`` 套一层，把命中的 chunk 记进证据看板。

    用组合而非修改原生文件：``keyword_search.py`` 保持开源原样，不在改造范围内。
    """

    def __init__(self, inner: Any, board: EvidenceBoard) -> None:
        self._inner = inner
        self._board = board

    @property
    def name(self) -> str:
        return self._inner.name

    def get_schema(self) -> dict[str, Any]:
        return self._inner.get_schema()

    def execute(self, context: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        result, log = self._inner.execute(context, **kwargs)
        chunk_ids = [
            str(item)
            for item in (log.get("chunk_ids") or self._parse_chunk_ids(result))
        ]
        if chunk_ids:
            self._board.add_keyword_hits(chunk_ids)
        return result, log

    @staticmethod
    def _parse_chunk_ids(result: str) -> list[str]:
        ids: list[str] = []
        for line in (result or "").splitlines():
            if line.startswith("Chunk ID:"):
                candidate = line.split(":", 1)[1].split(",")[0].strip()
                if candidate:
                    ids.append(candidate)
        return ids


class CrossLingualRetrievalPipeline:
    """中文断言 -> 英文论文结构化证据。"""

    def __init__(
        self,
        *,
        config: RetrievalConfig | None = None,
        runtime: AgentRuntimeConfig | None = None,
        index_store: IndexStore | None = None,
        verbose: bool = False,
    ) -> None:
        ensure_dirs()
        self.config = config or RetrievalConfig.from_env()
        self.runtime = runtime or AgentRuntimeConfig.from_env()
        self.verbose = verbose or self.runtime.verbose

        self.gate = DualThresholdGate(self.config.thresholds)
        self.store = index_store or IndexStore(self.config.index_dir)

        # 重资源全局共享：HTTP 会话复用 + 索引只读
        self.sf_client = SiliconFlowClient(verbose=False)
        self.qwen_client = QwenClient(verbose=False)

        # 术语缓存跨断言共享，命中即零 API 调用
        from arag.tools.bilingual_entity_mapper import BilingualEntityMapperTool

        self.mapper = BilingualEntityMapperTool(
            llm=QwenAgentAdapter(self.qwen_client),
            cache_file=self.config.term_cache_file,
            verbose=self.verbose,
        )

        from arag.agent.prompt import build_system_prompt

        self.system_prompt = build_system_prompt(
            high=self.config.thresholds.high,
            low=self.config.thresholds.low,
            min_hits=self.config.thresholds.min_hits,
        )

        if self.verbose:
            print(self.store.describe())

    # ------------------------------------------------------------------ 单条检索

    def _build_registry(self, board: EvidenceBoard) -> Any:
        """为一条断言装配工具集。工具实例轻量，按查询新建即可保证并发隔离。"""
        from arag.tools.keyword_search import KeywordSearchTool
        from arag.tools.read_chunk import ReadChunkTool
        from arag.tools.registry import ToolRegistry
        from arag.tools.semantic_search import SemanticSearchTool

        registry = ToolRegistry()
        registry.register(self.mapper)
        registry.register(
            BoardAwareKeywordSearch(
                KeywordSearchTool(chunks_file=str(self.config.chunks_file)), board
            )
        )
        registry.register(
            SemanticSearchTool(
                index_store=self.store,
                sf_client=self.sf_client,
                gate=self.gate,
                board=board,
                default_top_k=self.config.default_top_k,
                recall_multiplier=self.config.recall_multiplier,
                rerank_candidates=self.config.rerank_candidates,
                context_window=self.config.context_window,
                neighbor_window=self.config.neighbor_window,
                neighbor_score_decay=self.config.neighbor_score_decay,
                diversity_lambda=self.config.diversity_lambda,
                multi_query_from_terms=self.config.multi_query_from_terms,
                verbose=False,
            )
        )
        registry.register(
            ReadChunkTool(
                index_store=self.store,
                board=board,
                gate=self.gate,
                sf_client=self.sf_client,
                context_window=self.config.context_window,
            )
        )
        return registry

    def retrieve(self, claim_zh: str, *, max_loops: int | None = None) -> RetrievalOutput:
        """检索单条中文断言，返回结构化证据。"""
        from arag.agent.base import BaseAgent
        from arag.agent.prompt import parse_final_answer

        board = EvidenceBoard(claim_zh=claim_zh, gate=self.gate)

        # 预热术语表：提前抽好双语术语写进看板，Agent 后续仍可自行再调用该工具
        try:
            board.set_terms(self.mapper.extract_from_text(claim_zh))
        except APIClientError as exc:
            if self.verbose:
                print("术语预抽取失败，交由 Agent 自行处理: %s" % exc)

        agent = BaseAgent(
            llm_client=QwenAgentAdapter(self.qwen_client, verbose=self.verbose),
            tools=self._build_registry(board),
            system_prompt=self.system_prompt,
            max_loops=max_loops or self.runtime.max_loops,
            max_token_budget=self.runtime.max_token_budget,
            verbose=self.verbose,
        )

        user_message = (
            "CLAIM (Chinese, from a WeChat article):\n%s\n\n"
            "Find English paper passages that support or fail to support this claim." % claim_zh
        )

        try:
            run_result = agent.run(user_message)
        except Exception as exc:
            output = board.empty_output("agent_error")
            output.stats["error"] = "%s: %s" % (type(exc).__name__, exc)
            return output

        # ---- 检索充分性检查 + 关键词补检（agent.run 之后、_finalize 之前）----
        # 补检产生的候选必须先入看板，_finalize 才会把它们写进最终 evidences。
        # ARAG_SUFFICIENCY_ENABLED=0 时整块跳过，行为与改造前完全一致。
        sufficiency: RetrievalSufficiency | None = None
        if self.config.sufficiency.enabled:
            from .sufficiency import run_sufficiency_loop

            try:
                sufficiency = run_sufficiency_loop(
                    board,
                    store=self.store,
                    sf_client=self.sf_client,
                    qwen_client=self.qwen_client,
                    config=self.config.sufficiency,
                    verbose=self.verbose,
                )
            except Exception as exc:
                # 补检自身异常绝不让单条检索整体失败，降级为提示
                sufficiency = RetrievalSufficiency.failed("%s: %s" % (type(exc).__name__, exc))
                if self.verbose:
                    print("充分性补检失败，回退为仅提示: %s" % exc)

        output = self._finalize(board, sufficiency=sufficiency)
        parsed = parse_final_answer(run_result.get("answer", ""))
        output.stats.update(
            {
                "agent_verdict": parsed.get("verdict", ""),
                "agent_evidence_chunks": parsed.get("evidence_chunks", []),
                "agent_reason": parsed.get("reason", ""),
                "agent_answer": run_result.get("answer", ""),
                "agent_loops": run_result.get("loops", 0),
                "agent_cost_cny": round(float(run_result.get("total_cost", 0.0)), 6),
                "retrieved_tokens": run_result.get("total_retrieved_tokens", 0),
                "chunks_read": run_result.get("chunks_read_ids", []),
                **self.mapper.cache.stats(),
            }
        )
        if sufficiency is not None:
            output.stats["sufficiency_checks"] = len(sufficiency.checks)
            output.stats["sufficiency_sufficient"] = bool(sufficiency.sufficient)
        return output

    def _finalize(
        self,
        board: EvidenceBoard,
        *,
        max_evidences: int | None = None,
        sufficiency: RetrievalSufficiency | None = None,
    ) -> RetrievalOutput:
        """从证据看板汇总最终输出。

        不依赖 Agent 是否记得调用 read_chunk——只要向量层召回过候选，这里就能把结构化证据
        补齐，保证下游永远拿到完整的 JSON。
        """
        limit = self.config.max_evidences if max_evidences is None else max_evidences
        evidences: list[EvidenceRecord] = []
        for index, candidate in enumerate(board.all_candidates(), start=1):
            if not self.gate.keep(candidate.rerank_score):
                continue
            if len(evidences) >= limit:
                break

            chunk = self.store.chunk(candidate.chunk_id)
            if candidate.sentence_index >= 0:
                context = self.store.paragraph_context(
                    candidate.sentence_index, self.config.context_window
                )
            else:
                context = ("", candidate.sentence, "")

            evidences.append(
                EvidenceRecord(
                    evidence_id="ev_%d" % index,
                    chunk_id=candidate.chunk_id,
                    evidence_en=candidate.sentence,
                    sentence_id=int(candidate.sentence_index)
                    if candidate.sentence_index >= 0
                    else -1,
                    rerank_score=candidate.rerank_score,
                    embed_score=candidate.embed_score,
                    verdict=self.gate.label(candidate.rerank_score),
                    matched_terms=list(candidate.matched_terms),
                    paper=PaperMetadata.from_dict(chunk),
                    context=ParagraphContext(
                        section=str(chunk.get("section") or ""),
                        page=str(chunk.get("page") or ""),
                        prev_text=context[0],
                        target_text=context[1],
                        next_text=context[2],
                    ),
                )
            )

        output = board.build_output(evidences, max_rounds=self.runtime.max_loops)
        if sufficiency is not None:
            # 存对象而非 dict：RetrievalOutput.to_dict() 统一负责序列化
            output.retrieval_sufficiency = sufficiency
            # 硬规则：语义低分/零召回只代表「当前检索没找着」，不代表论文无此内容；
            # 补检确证不足时禁止输出 NO_EVIDENCE，避免下游据此误判「论文未提及」。
            if not sufficiency.sufficient and output.verdict == VERDICT_NO_EVIDENCE:
                output.verdict = VERDICT_INCONCLUSIVE
                output.stop_reason = STOP_INSUFFICIENT_HINT
        return output

    # ------------------------------------------------------------------ 批量检索

    def retrieve_batch(
        self,
        claims: Sequence[str],
        *,
        workers: int = 4,
        on_result: Any = None,
    ) -> list[RetrievalOutput]:
        """并发检索多条断言，返回顺序与输入一致。

        单条失败不会中断整批：对应位置返回带 error 的空证据输出。
        """
        if not claims:
            return []

        def run_one(claim: str) -> RetrievalOutput:
            try:
                output = self.retrieve(claim)
            except Exception as exc:
                board = EvidenceBoard(claim_zh=claim, gate=self.gate)
                output = board.empty_output("pipeline_error")
                output.stats["error"] = "%s: %s" % (type(exc).__name__, exc)
            if on_result is not None:
                on_result(output)
            return output

        if workers <= 1 or len(claims) == 1:
            return [run_one(claim) for claim in claims]

        with ThreadPoolExecutor(max_workers=min(workers, len(claims))) as pool:
            return list(pool.map(run_one, list(claims)))

    # ------------------------------------------------------------------ 生命周期

    def close(self) -> None:
        self.sf_client.close()
        self.qwen_client.close()

    def __enter__(self) -> CrossLingualRetrievalPipeline:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


def load_claims_from_file(path: str | Path) -> list[dict[str, Any]]:
    """从 JSON / JSONL / 纯文本读取待检索的中文断言。"""
    import json

    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")

    if file_path.suffix == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    elif file_path.suffix == ".json":
        rows = json.loads(text)
    else:
        from .pdf_ingest import split_chinese_sentences

        return [
            {"claim_id": "%s#%d" % (file_path.stem, index), "claim_zh": sentence}
            for index, sentence in enumerate(split_chinese_sentences(text))
        ]

    claims: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if isinstance(row, str):
            claims.append({"claim_id": str(index), "claim_zh": row})
        elif isinstance(row, dict):
            claim = row.get("claim_zh") or row.get("claim") or row.get("text") or ""
            if claim:
                claims.append(
                    {
                        "claim_id": str(row.get("claim_id") or row.get("id") or index),
                        "claim_zh": claim,
                        **{k: v for k, v in row.items() if k not in ("claim_zh", "claim", "text")},
                    }
                )
    return claims
