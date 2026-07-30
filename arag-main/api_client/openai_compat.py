"""OpenAI 兼容对话接口的共享实现。

Qwen / Kimi / 文心 三个客户端都继承本类，因此它们的**调用入参与返回解析完全一致**，
服务降级时可以直接替换实例而无需改动业务代码。
"""

from __future__ import annotations

from typing import Any, Sequence

from .base_client import BaseHTTPClient
from .exceptions import APIClientError, APIConfigError, APIResponseError
from .schemas import ChatResult, ToolCall

# 强制 JSON 输出时注入的约束。多数服务端要求提示词中出现 "JSON" 字样才允许
# response_format=json_object，这条提示同时也是降级模式下的唯一约束。
JSON_INSTRUCTION = (
    "你必须只输出一个合法的 JSON 对象，不要输出任何解释性文字、前后缀或 Markdown 代码围栏。"
)


def build_messages(
    prompt: str,
    system: str | None = None,
    history: Sequence[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """把单轮提问拼成标准 messages 列表。"""
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    if history:
        messages.extend(dict(item) for item in history)
    messages.append({"role": "user", "content": prompt})
    return messages


def tool_result_message(tool_call_id: str, content: str, name: str = "") -> dict[str, Any]:
    """构造工具执行结果消息，用于把 Function Calling 的返回喂回模型。"""
    message: dict[str, Any] = {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
    }
    if name:
        message["name"] = name
    return message


class OpenAICompatChatClient(BaseHTTPClient):
    """``/chat/completions`` 风格接口的通用封装。"""

    provider = "openai-compat"
    chat_path = "/chat/completions"
    models_path = "/models"
    # 服务端不支持 response_format 时，是否退化为"提示词约束 + 宽松解析"
    json_mode_fallback = True

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        model: str,
        long_model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        self.model = model
        self.long_model = long_model or model
        self.temperature = temperature
        self.max_tokens = max_tokens

    # ------------------------------------------------------------------ 钩子

    def extra_payload(self, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        """子类可覆写，注入厂商特有参数。"""
        return {}

    # ------------------------------------------------------------------ 核心对话

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        stop: Sequence[str] | None = None,
        extra_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        """通用对话。支持 Function Calling、JSON 模式与长文本输入。"""
        if not messages:
            raise APIConfigError("messages 不能为空", provider=self.provider)

        used_model = model or self.model
        payload: dict[str, Any] = {
            "model": used_model,
            "messages": [dict(item) for item in messages],
            "temperature": self.temperature if temperature is None else temperature,
        }
        if top_p is not None:
            payload["top_p"] = top_p
        limit = self.max_tokens if max_tokens is None else max_tokens
        if limit:
            payload["max_tokens"] = limit
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = tool_choice or "auto"
        elif tool_choice:
            payload["tool_choice"] = tool_choice
        if response_format:
            payload["response_format"] = response_format
        if stop:
            payload["stop"] = list(stop)

        payload.update(self.extra_payload(used_model, payload))
        if extra_body:
            payload.update(extra_body)

        total_chars = sum(len(str(item.get("content") or "")) for item in payload["messages"])
        self.log.info(
            "chat: model=%s / %d 条消息 / 约 %d 字符%s",
            used_model,
            len(payload["messages"]),
            total_chars,
            " / 携带 %d 个工具" % len(tools) if tools else "",
        )

        response = self.request(
            "POST",
            self.chat_path,
            json_body=payload,
            timeout=timeout,
            tag="%s[%s]" % (self.chat_path, used_model),
        )
        return self.parse_chat_response(response, used_model)

    def parse_chat_response(self, response: dict[str, Any], model: str) -> ChatResult:
        """把 OpenAI 兼容响应体解析成 ChatResult。"""
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise APIResponseError(
                "响应缺少 choices 字段: %s" % response, provider=self.provider
            )
        choice = choices[0] or {}
        message = choice.get("message") or {}
        content = message.get("content")
        # reasoning 类模型可能把正文放在 reasoning_content
        if not content:
            content = message.get("reasoning_content") or ""

        tool_calls = [
            ToolCall.from_dict(item)
            for item in (message.get("tool_calls") or [])
            if isinstance(item, dict)
        ]
        usage = response.get("usage") or {}
        result = ChatResult(
            content=str(content or ""),
            model=str(response.get("model") or model),
            finish_reason=str(choice.get("finish_reason") or ""),
            tool_calls=tool_calls,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
            provider=self.provider,
            raw=response,
        )
        self.log.info(
            "chat 完成: finish=%s / tokens=%d+%d / 工具调用=%d",
            result.finish_reason or "-",
            result.prompt_tokens,
            result.completion_tokens,
            len(result.tool_calls),
        )
        return result

    # ------------------------------------------------------------------ 便捷封装

    def ask(
        self,
        prompt: str,
        *,
        system: str | None = None,
        history: Sequence[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> str:
        """单轮提问，直接返回文本。"""
        return self.chat(build_messages(prompt, system, history), **kwargs).content

    def chat_json(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        strict: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """强制 JSON 输出并解析成 dict。

        先走服务端原生 ``response_format={"type": "json_object"}``；若该模型不支持，
        自动退化为"提示词约束 + 宽松解析"，保证上层拿到的始终是 dict。
        """
        payload_messages = self._inject_json_instruction(messages)
        kwargs.pop("response_format", None)

        try:
            result = self.chat(
                payload_messages,
                response_format={"type": "json_object"},
                **kwargs,
            )
        except APIClientError as exc:
            if not self.json_mode_fallback or getattr(exc, "status_code", None) != 400:
                raise
            self.log.warning("原生 JSON 模式不可用（%s），退化为提示词约束模式", exc)
            result = self.chat(payload_messages, **kwargs)

        try:
            return result.as_json()
        except ValueError as exc:
            if strict:
                raise APIResponseError(str(exc), provider=self.provider, payload=result.content)
            return {"_raw": result.content, "_error": str(exc)}

    def ask_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        strict: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """单轮提问 + 强制 JSON 输出。"""
        return self.chat_json(build_messages(prompt, system), strict=strict, **kwargs)

    @staticmethod
    def _inject_json_instruction(
        messages: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        payload = [dict(item) for item in messages]
        joined = " ".join(str(item.get("content") or "") for item in payload).lower()
        if "json" in joined:
            return payload
        for item in payload:
            if item.get("role") == "system":
                item["content"] = "%s\n%s" % (item.get("content") or "", JSON_INSTRUCTION)
                return payload
        payload.insert(0, {"role": "system", "content": JSON_INSTRUCTION})
        return payload

    def chat_with_tools(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        *,
        tool_choice: str | dict[str, Any] = "auto",
        **kwargs: Any,
    ) -> ChatResult:
        """Function Calling。``tools`` 使用 OpenAI Function Schema。"""
        if not tools:
            raise APIConfigError("tools 不能为空", provider=self.provider)
        return self.chat(messages, tools=tools, tool_choice=tool_choice, **kwargs)

    def chat_long(
        self,
        messages: Sequence[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResult:
        """长文本对话，切换到长上下文模型（未单独配置时与主模型相同）。"""
        kwargs.setdefault("model", self.long_model)
        return self.chat(messages, **kwargs)

    def batch_chat(
        self,
        batch_messages: Sequence[Sequence[dict[str, Any]]],
        *,
        max_workers: int | None = None,
        return_exceptions: bool = False,
        **kwargs: Any,
    ) -> list[ChatResult | None]:
        """并发跑多组对话，返回顺序与输入一致。

        ``return_exceptions=True`` 时单条失败不会中断整批，对应位置返回 None。
        """

        def run_one(messages: Sequence[dict[str, Any]]) -> ChatResult | None:
            if not return_exceptions:
                return self.chat(messages, **kwargs)
            try:
                return self.chat(messages, **kwargs)
            except APIClientError as exc:
                self.log.error("批量对话中有一条失败: %s", exc)
                return None

        self.log.info("批量对话: %d 组请求", len(batch_messages))
        return self.map_parallel(run_one, list(batch_messages), max_workers=max_workers)

    # ------------------------------------------------------------------ 连通性

    def ping(self) -> bool:
        """发一条最短对话做探活。"""
        result = self.chat(
            [{"role": "user", "content": "ping"}],
            max_tokens=16,
            temperature=0.0,
        )
        if not result.raw:
            raise APIResponseError("探活失败：响应为空", provider=self.provider)
        return True
