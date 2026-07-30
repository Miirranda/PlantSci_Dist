"""Moonshot Kimi 备用客户端——仅预留，主流水线不引入。

本模块**不会**被 ``api_client/__init__.py`` 主动导入，避免误接入主流程。
确需使用时显式导入：

    from api_client.kimi_backup_client import KimiBackupClient

调用入参与返回结构同 ``QwenClient``（共同继承 ``OpenAICompatChatClient``）。
"""

from __future__ import annotations

from .config import get_bool, get_env, get_float, get_int, require_env
from .openai_compat import OpenAICompatChatClient

DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_MODEL = "moonshot-v1-32k"


class KimiBackupClient(OpenAICompatChatClient):
    """Kimi 对话客户端（预留）。

    Moonshot 的 HTTP 鉴权只用 ``sk-`` 开头的 API Key；``ak-`` 开头的 api_id
    仅用于控制台侧标识，这里保留为 ``app_id`` 字段但不参与请求签名。
    """

    provider = "kimi"

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
            api_key=api_key or require_env("MOONSHOT_API_KEY", self.provider),
            base_url=base_url or get_env("MOONSHOT_BASE_URL", DEFAULT_BASE_URL),
            model=model or get_env("MOONSHOT_MODEL", DEFAULT_MODEL),
            temperature=(
                temperature if temperature is not None else get_float("MOONSHOT_TEMPERATURE", 0.0)
            ),
            max_tokens=(
                max_tokens if max_tokens is not None else get_int("MOONSHOT_MAX_TOKENS", 0) or None
            ),
            timeout=timeout if timeout is not None else get_float("MOONSHOT_TIMEOUT", 120.0),
            max_retries=(
                max_retries if max_retries is not None else get_int("MOONSHOT_MAX_RETRIES", 3)
            ),
            max_workers=(
                max_workers if max_workers is not None else get_int("MOONSHOT_MAX_WORKERS", 4)
            ),
            verbose=verbose if verbose is not None else get_bool("API_CLIENT_VERBOSE", True),
        )
        self.app_id = app_id or get_env("MOONSHOT_API_ID", "")
