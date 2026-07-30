"""SiliconFlow 在线客户端：bge-m3 向量化 + bge-reranker-v2-m3 重排。

两个接口共用同一个 API Key。纯 HTTP 调用，不加载任何本地模型。
"""

from __future__ import annotations

from typing import Any, Sequence

from .base_client import BaseHTTPClient, chunked
from .config import get_bool, get_env, get_float, get_int, require_env
from .exceptions import APIConfigError, APIResponseError
from .schemas import EmbeddingResult, RerankItem, RerankResult

DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_EMBED_MODEL = "BAAI/bge-m3"
DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"


class SiliconFlowClient(BaseHTTPClient):
    """SiliconFlow 向量与重排服务。

    参数
    ----
    api_key           : 缺省从环境变量 ``SILICONFLOW_API_KEY`` 读取
    embed_model       : 向量模型，默认 ``BAAI/bge-m3``
    rerank_model      : 重排模型，默认 ``BAAI/bge-reranker-v2-m3``
    embed_batch_size  : 单次 embedding 请求最多携带多少条文本，超出自动切分
    rerank_batch_size : 单次 rerank 请求最多携带多少篇文档，超出自动分片后合并重排
    max_chars         : 单条文本的字符上限，超出截断（bge-m3 上限 8192 token）
    """

    provider = "siliconflow"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        embed_model: str | None = None,
        rerank_model: str | None = None,
        embed_batch_size: int | None = None,
        rerank_batch_size: int | None = None,
        max_chars: int | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        max_workers: int | None = None,
        verbose: bool | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key or require_env("SILICONFLOW_API_KEY", self.provider),
            base_url=base_url or get_env("SILICONFLOW_BASE_URL", DEFAULT_BASE_URL),
            timeout=timeout if timeout is not None else get_float("SILICONFLOW_TIMEOUT", 60.0),
            max_retries=(
                max_retries if max_retries is not None else get_int("SILICONFLOW_MAX_RETRIES", 3)
            ),
            max_workers=(
                max_workers if max_workers is not None else get_int("SILICONFLOW_MAX_WORKERS", 4)
            ),
            verbose=verbose if verbose is not None else get_bool("API_CLIENT_VERBOSE", True),
        )
        self.embed_model = embed_model or get_env("SILICONFLOW_EMBED_MODEL", DEFAULT_EMBED_MODEL)
        self.rerank_model = rerank_model or get_env(
            "SILICONFLOW_RERANK_MODEL", DEFAULT_RERANK_MODEL
        )
        self.embed_batch_size = (
            embed_batch_size
            if embed_batch_size is not None
            else get_int("SILICONFLOW_EMBED_BATCH_SIZE", 32)
        )
        self.rerank_batch_size = (
            rerank_batch_size
            if rerank_batch_size is not None
            else get_int("SILICONFLOW_RERANK_BATCH_SIZE", 64)
        )
        self.max_chars = (
            max_chars if max_chars is not None else get_int("SILICONFLOW_MAX_CHARS", 8000)
        )

    # ------------------------------------------------------------------ 内部工具

    def _prepare(self, texts: Sequence[str]) -> list[str]:
        """空文本会被服务端拒绝，这里统一兜底并按字符上限截断。"""
        prepared: list[str] = []
        for text in texts:
            item = "" if text is None else str(text)
            item = item.strip() or " "
            if self.max_chars > 0 and len(item) > self.max_chars:
                item = item[: self.max_chars]
            prepared.append(item)
        return prepared

    # ------------------------------------------------------------------ Embedding

    def embed(
        self,
        texts: str | Sequence[str],
        *,
        model: str | None = None,
        batch_size: int | None = None,
        max_workers: int | None = None,
    ) -> EmbeddingResult:
        """把文本转成向量。

        传入单条字符串或列表均可；超过 ``batch_size`` 时自动切分成多个请求并发下发，
        返回顺序与输入严格一致。
        """
        raw_texts = [texts] if isinstance(texts, str) else list(texts)
        used_model = model or self.embed_model
        if not raw_texts:
            return EmbeddingResult(vectors=[], model=used_model, provider=self.provider)

        payload_texts = self._prepare(raw_texts)
        batches = list(chunked(payload_texts, batch_size or self.embed_batch_size))

        self.log.info(
            "embedding: %d 条文本 / %d 个批次 (model=%s)",
            len(payload_texts),
            len(batches),
            used_model,
        )

        def run_batch(batch: list[str]) -> dict[str, Any]:
            return self.request(
                "POST",
                "/embeddings",
                json_body={"model": used_model, "input": batch, "encoding_format": "float"},
                tag="/embeddings[n=%d]" % len(batch),
            )

        responses = self.map_parallel(run_batch, batches, max_workers=max_workers)

        vectors: list[list[float]] = []
        total_tokens = 0
        for response in responses:
            data = response.get("data")
            if not isinstance(data, list):
                raise APIResponseError(
                    "embedding 响应缺少 data 字段: %s" % response, provider=self.provider
                )
            # 服务端不保证顺序，按 index 排序后再拼接
            for item in sorted(data, key=lambda x: x.get("index", 0)):
                embedding = item.get("embedding")
                if not isinstance(embedding, list):
                    raise APIResponseError(
                        "embedding 响应缺少 embedding 字段: %s" % item, provider=self.provider
                    )
                vectors.append([float(value) for value in embedding])
            total_tokens += int((response.get("usage") or {}).get("total_tokens", 0) or 0)

        if len(vectors) != len(payload_texts):
            raise APIResponseError(
                "embedding 数量不匹配：输入 %d 条，返回 %d 条"
                % (len(payload_texts), len(vectors)),
                provider=self.provider,
            )

        result = EmbeddingResult(
            vectors=vectors,
            model=used_model,
            total_tokens=total_tokens,
            provider=self.provider,
        )
        self.log.info(
            "embedding 完成: %d 条 / dim=%d / tokens=%d", len(result), result.dim, total_tokens
        )
        return result

    def embed_query(self, text: str, **kwargs: Any) -> list[float]:
        """单条查询向量化，直接返回一维向量。"""
        return self.embed(text, **kwargs).vectors[0]

    def embed_documents(self, texts: Sequence[str], **kwargs: Any) -> list[list[float]]:
        """批量文档向量化，直接返回二维向量列表。"""
        return self.embed(texts, **kwargs).vectors

    # ------------------------------------------------------------------ Rerank

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int | None = None,
        model: str | None = None,
        return_documents: bool = True,
        batch_size: int | None = None,
        max_workers: int | None = None,
    ) -> RerankResult:
        """按与 query 的相关性对 documents 重排。

        文档数超过 ``batch_size`` 时分片请求，再按分数全局归并——交叉编码器的分数
        是"查询-文档"对的绝对相关度，分片之间可直接比较。
        """
        if not query or not query.strip():
            raise APIConfigError("rerank 的 query 不能为空", provider=self.provider)

        raw_documents = list(documents)
        used_model = model or self.rerank_model
        if not raw_documents:
            return RerankResult(items=[], model=used_model, provider=self.provider)

        payload_documents = self._prepare(raw_documents)
        limit = top_n if top_n is not None else len(payload_documents)
        limit = max(1, min(limit, len(payload_documents)))
        size = batch_size or self.rerank_batch_size

        # 记录原始下标，分片后仍能映射回输入位置
        indexed = list(enumerate(payload_documents))
        shards = list(chunked(indexed, size))

        self.log.info(
            "rerank: %d 篇文档 / %d 个分片 / top_n=%d (model=%s)",
            len(payload_documents),
            len(shards),
            limit,
            used_model,
        )

        def run_shard(shard: list[tuple[int, str]]) -> tuple[list[tuple[int, str]], dict[str, Any]]:
            body = {
                "model": used_model,
                "query": query,
                "documents": [text for _, text in shard],
                "top_n": min(limit, len(shard)),
                "return_documents": False,
            }
            response = self.request(
                "POST", "/rerank", json_body=body, tag="/rerank[n=%d]" % len(shard)
            )
            return shard, response

        outputs = self.map_parallel(run_shard, shards, max_workers=max_workers)

        items: list[RerankItem] = []
        total_tokens = 0
        for shard, response in outputs:
            results = response.get("results")
            if not isinstance(results, list):
                raise APIResponseError(
                    "rerank 响应缺少 results 字段: %s" % response, provider=self.provider
                )
            for entry in results:
                local_index = int(entry.get("index", 0))
                if local_index >= len(shard):
                    continue
                original_index = shard[local_index][0]
                items.append(
                    RerankItem(
                        index=original_index,
                        score=float(entry.get("relevance_score", 0.0)),
                        document=raw_documents[original_index] if return_documents else "",
                    )
                )
            tokens = ((response.get("meta") or {}).get("tokens") or {})
            total_tokens += int(tokens.get("input_tokens", 0) or 0)

        items.sort(key=lambda item: item.score, reverse=True)
        result = RerankResult(
            items=items[:limit],
            model=used_model,
            total_tokens=total_tokens,
            provider=self.provider,
        )
        top_score = result.items[0].score if result.items else 0.0
        self.log.info("rerank 完成: 返回 %d 条 / 最高分=%.4f", len(result), top_score)
        return result

    # ------------------------------------------------------------------ 连通性

    def ping(self) -> bool:
        """用一条极短文本做最小成本探活。"""
        result = self.embed("ping")
        if not result.vectors or not result.dim:
            raise APIResponseError("探活失败：未返回有效向量", provider=self.provider)
        return True

    def ping_rerank(self) -> bool:
        """单独探活重排接口（embedding 与 rerank 是两个不同的服务端点）。"""
        result = self.rerank("苹果的颜色", ["苹果是红色的水果", "长城位于中国北方"], top_n=1)
        if not result.items:
            raise APIResponseError("探活失败：重排未返回结果", provider=self.provider)
        return True
