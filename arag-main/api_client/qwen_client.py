"""通义千问（DashScope）客户端——项目唯一主 LLM。

走 DashScope 的 OpenAI 兼容端点，支持通用对话、Function Calling、强制 JSON 输出
与长文本输入。仅在线 HTTP 调用。
"""

from __future__ import annotations

from typing import Any

from .config import get_bool, get_env, get_float, get_int, require_env
from .openai_compat import OpenAICompatChatClient

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"
# qwen-long 支持超长上下文，适合塞入大量检索片段
DEFAULT_LONG_MODEL = "qwen-long"


class QwenClient(OpenAICompatChatClient):
    """通义千问对话客户端。

    参数
    ----
    api_key     : 缺省依次读取 ``QWEN_API_KEY`` / ``DASHSCOPE_API_KEY``
    model       : 主模型，默认 ``qwen-plus``
    long_model  : 长文本模型，默认 ``qwen-long``
    temperature : 默认 0.0，判定类任务需要可复现的输出
    """

    provider = "qwen"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        model: str | None = None,
        long_model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        max_workers: int | None = None,
        verbose: bool | None = None,
    ) -> None:
        if not api_key:
            api_key = get_env("QWEN_API_KEY") or require_env("DASHSCOPE_API_KEY", self.provider)
        super().__init__(
            api_key=api_key,
            base_url=base_url or get_env("QWEN_BASE_URL", DEFAULT_BASE_URL),
            model=model or get_env("QWEN_MODEL", DEFAULT_MODEL),
            long_model=long_model or get_env("QWEN_LONG_MODEL", DEFAULT_LONG_MODEL),
            temperature=(
                temperature if temperature is not None else get_float("QWEN_TEMPERATURE", 0.0)
            ),
            max_tokens=(
                max_tokens if max_tokens is not None else get_int("QWEN_MAX_TOKENS", 0) or None
            ),
            timeout=timeout if timeout is not None else get_float("QWEN_TIMEOUT", 120.0),
            max_retries=max_retries if max_retries is not None else get_int("QWEN_MAX_RETRIES", 3),
            max_workers=max_workers if max_workers is not None else get_int("QWEN_MAX_WORKERS", 4),
            verbose=verbose if verbose is not None else get_bool("API_CLIENT_VERBOSE", True),
        )

    def extra_payload(self, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Qwen3 系列的混合思考模型在非流式调用下必须显式关闭思考模式。"""
        if model.lower().startswith("qwen3"):
            return {"enable_thinking": False}
        return {}
