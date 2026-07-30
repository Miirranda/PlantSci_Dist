"""统一异常体系。

上层业务只需捕获 ``APIClientError``，即可覆盖全部客户端的失败场景；
需要区分处理时（例如降级、限流退避）再捕获具体子类。
"""

from __future__ import annotations

from typing import Any


class APIClientError(Exception):
    """所有客户端异常的基类。"""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "unknown",
        status_code: int | None = None,
        payload: Any = None,
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        self.payload = payload
        super().__init__(self._format(message))

    def _format(self, message: str) -> str:
        parts = ["[%s]" % self.provider]
        if self.status_code is not None:
            parts.append("HTTP %s" % self.status_code)
        parts.append(message)
        return " ".join(parts)


class APIConfigError(APIClientError):
    """密钥缺失、参数非法等本地配置问题，不会触发重试。"""


class APIAuthError(APIClientError):
    """401/403，密钥无效或无权限，不会触发重试。"""


class APIRateLimitError(APIClientError):
    """429 限流，重试耗尽后抛出。"""


class APITimeoutError(APIClientError):
    """连接或读取超时，重试耗尽后抛出。"""


class APIConnectionError(APIClientError):
    """网络不可达 / DNS / TLS 失败，重试耗尽后抛出。"""


class APIResponseError(APIClientError):
    """服务端返回了非预期的状态码或无法解析的响应体。"""
