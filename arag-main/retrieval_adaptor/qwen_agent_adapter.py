"""把阶段 1 的 QwenClient 适配成 A-RAG 的 LLMClient 契约。

原创代码（非 A-RAG 开源部分）。

设计要点：原生 ``BaseAgent`` 的 ReAct 循环依赖 ``LLMClient.chat()`` 返回
``{"message", "input_tokens", "output_tokens", "cost", "raw_response"}`` 这个字典，
并把 ``message`` 原样塞回 messages 列表。本适配器完整兑现该契约，因此
**ReAct 循环核心逻辑一行都不用改**，只是把底层 HTTP 换成统一封装的 QwenClient：
重试、超时、异常归一化、状态打印全部复用阶段 1 的实现。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import tiktoken

from api_client import QwenClient
from api_client.exceptions import APIClientError


class QwenAgentAdapter:
    """Qwen 驱动的 Agent LLM 客户端，接口与 ``arag.core.llm.LLMClient`` 兼容。

    参数
    ----
    client      : 可复用的 QwenClient 实例，缺省自行创建（从 .env 读密钥）
    model       : 覆盖 QwenClient 的默认模型
    temperature : 默认 0.0，跨语言证据判定需要可复现的输出
    max_tokens  : None 表示不下发该参数，由服务端用模型默认上限
    """

    # DashScope 计费（元 / 百万 token）：(输入, 输出)。资费会调整，可按需覆盖。
    PRICING_CNY = {
        "qwen-max": (2.4, 9.6),
        "qwen-plus": (0.8, 2.0),
        "qwen-turbo": (0.3, 0.6),
        "qwen-long": (0.5, 2.0),
        "default": (0.8, 2.0),
    }
    currency = "CNY"

    def __init__(
        self,
        client: QwenClient | None = None,
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        verbose: bool = False,
    ) -> None:
        self.client = client or QwenClient(verbose=verbose)
        self.model = model or self.client.model
        self.temperature = temperature
        self.max_tokens = max_tokens
        try:
            self.tokenizer = tiktoken.encoding_for_model("gpt-4o")
        except Exception:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")

    # ------------------------------------------------------------------ 计费与计数

    def count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text))

    def calculate_cost(self, usage: dict[str, Any]) -> float:
        """按模型名匹配资费，返回人民币金额。"""
        model_lower = (self.model or "").lower()
        for key, prices in self.PRICING_CNY.items():
            if key != "default" and key in model_lower:
                input_price, output_price = prices
                break
        else:
            input_price, output_price = self.PRICING_CNY["default"]

        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        cost = (
            prompt_tokens / 1_000_000 * input_price
            + completion_tokens / 1_000_000 * output_price
        )
        return round(cost, 6)

    # ------------------------------------------------------------------ LLMClient 契约

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """与 ``LLMClient.chat`` 同签名同返回结构。

        ``tools`` 非空时自动开启 Function Calling（tool_choice=auto）。
        """
        result = self.client.chat(
            messages,
            model=self.model,
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            tools=list(tools) if tools else None,
            tool_choice="auto" if tools else None,
        )

        raw = result.raw
        usage = raw.get("usage") or {}
        # 原样回传服务端的 message，Agent 会把它直接追加进 messages 继续多轮
        message = (raw.get("choices") or [{}])[0].get("message") or {
            "role": "assistant",
            "content": result.content,
        }

        return {
            "message": message,
            "input_tokens": result.prompt_tokens,
            "output_tokens": result.completion_tokens,
            "cost": self.calculate_cost(usage),
            "raw_response": raw,
        }

    def generate(
        self,
        messages: Sequence[dict[str, Any]],
        system: str | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> tuple[str, float]:
        """与 ``LLMClient.generate`` 同签名，返回 (文本, 成本)。"""
        payload = list(messages)
        if system:
            payload = [{"role": "system", "content": system}, *payload]
        result = self.chat(payload, tools=tools, temperature=temperature)
        return result["message"].get("content", "") or "", result["cost"]

    # ------------------------------------------------------------------ 结构化输出

    def extract_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        strict: bool = False,
    ) -> dict[str, Any]:
        """强制 JSON 输出，供双语术语抽取等结构化任务使用。"""
        return self.client.ask_json(prompt, system=system, strict=strict)

    def ping(self) -> bool:
        try:
            return self.client.ping()
        except APIClientError:
            return False

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> QwenAgentAdapter:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
