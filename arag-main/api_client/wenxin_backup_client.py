"""文心一言（百度千帆 V2）备用客户端——用于 Qwen 不可用时的服务降级。

与 ``QwenClient`` 共同继承 ``OpenAICompatChatClient``，因此**调用入参、返回解析
完全一致**：业务侧只需替换实例，其余代码不用改。

    llm = QwenClient()
    try:
        result = llm.chat(messages)
    except APIClientError:
        result = WenxinBackupClient().chat(messages)   # 入参与返回同构
"""

from __future__ import annotations

from typing import Any

from .config import get_bool, get_env, get_float, get_int, require_env
from .openai_compat import OpenAICompatChatClient

# 千帆 V2 的 OpenAI 兼容端点，用 Bearer <千帆 API Key> 鉴权
DEFAULT_BASE_URL = "https://qianfan.baidubce.com/v2"
DEFAULT_MODEL = "ernie-4.5-turbo-128k"


class WenxinBackupClient(OpenAICompatChatClient):
    """文心一言对话客户端（降级备用）。

    参数
    ----
    api_key : 千帆 V2 API Key，缺省读取 ``WENXIN_API_KEY``
    app_id  : 可选，千帆应用 ID，会作为 ``appid`` 请求头发送
    """

    provider = "wenxin"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        app_id: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        max_workers: int | None = None,
        verbose: bool | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key or require_env("WENXIN_API_KEY", self.provider),
            base_url=base_url or get_env("WENXIN_BASE_URL", DEFAULT_BASE_URL),
            model=model or get_env("WENXIN_MODEL", DEFAULT_MODEL),
            temperature=(
                temperature if temperature is not None else get_float("WENXIN_TEMPERATURE", 0.0)
            ),
            max_tokens=(
                max_tokens if max_tokens is not None else get_int("WENXIN_MAX_TOKENS", 0) or None
            ),
            timeout=timeout if timeout is not None else get_float("WENXIN_TIMEOUT", 120.0),
            max_retries=(
                max_retries if max_retries is not None else get_int("WENXIN_MAX_RETRIES", 3)
            ),
            max_workers=(
                max_workers if max_workers is not None else get_int("WENXIN_MAX_WORKERS", 4)
            ),
            verbose=verbose if verbose is not None else get_bool("API_CLIENT_VERBOSE", True),
        )
        self.app_id = app_id or get_env("WENXIN_APP_ID", "")

    def default_headers(self) -> dict[str, str]:
        headers = super().default_headers()
        if self.app_id:
            headers["appid"] = self.app_id
        return headers

    def extra_payload(self, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        """千帆不接受 temperature=0，最小值需大于 0。"""
        if payload.get("temperature") is not None and float(payload["temperature"]) <= 0:
            return {"temperature": 0.01}
        return {}
