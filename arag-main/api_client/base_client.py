"""HTTP 客户端基类：连接复用、超时、指数退避重试、状态打印、异常归一化。

具体客户端只负责"拼请求体 + 解析响应"，网络层行为在此统一。
本模块只依赖 requests，不引入任何本地模型推理依赖。
"""

from __future__ import annotations

import json
import logging
import random
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

import requests

from .exceptions import (
    APIAuthError,
    APIClientError,
    APIConnectionError,
    APIRateLimitError,
    APIResponseError,
    APITimeoutError,
)

T = TypeVar("T")
R = TypeVar("R")

# 这些状态码代表"稍后重试可能成功"
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})

_logger_lock = threading.Lock()


def get_logger(name: str, verbose: bool = True) -> logging.Logger:
    """取得带统一格式的 logger，重复调用不会叠加 handler。"""
    logger = logging.getLogger("api_client.%s" % name)
    with _logger_lock:
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(
                logging.Formatter(
                    fmt="%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s",
                    datefmt="%H:%M:%S",
                )
            )
            logger.addHandler(handler)
            logger.propagate = False
    logger.setLevel(logging.INFO if verbose else logging.WARNING)
    return logger


def chunked(items: Sequence[T], size: int) -> Iterator[list[T]]:
    """把序列切成若干不超过 size 的批次。"""
    size = max(1, int(size))
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


class BaseHTTPClient:
    """线上 HTTP API 的通用封装。

    参数
    ----
    api_key         : 鉴权密钥，由子类从环境变量读取后传入
    base_url        : 服务根地址，末尾斜杠会被去掉
    timeout         : 单次请求的读取超时（秒）
    connect_timeout : 建立连接的超时（秒）
    max_retries     : 失败重试次数（不含首次请求）
    backoff         : 退避基数，第 n 次重试等待 backoff * 2**(n-1) + 随机抖动
    max_workers     : 批量请求的默认并发度
    verbose         : 是否打印每次请求的状态行
    """

    provider = "base"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = 60.0,
        connect_timeout: float = 10.0,
        max_retries: int = 3,
        backoff: float = 0.8,
        max_backoff: float = 16.0,
        max_workers: int = 4,
        verbose: bool = True,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.connect_timeout = float(connect_timeout)
        self.max_retries = max(0, int(max_retries))
        self.backoff = float(backoff)
        self.max_backoff = float(max_backoff)
        self.max_workers = max(1, int(max_workers))
        self.verbose = verbose
        self.log = get_logger(self.provider, verbose)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "arag-api-client/1.0"})

    # ------------------------------------------------------------------ 基础设施

    def default_headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer %s" % self.api_key,
            "Content-Type": "application/json",
        }

    def _sleep_seconds(self, attempt: int, retry_after: str | None = None) -> float:
        """计算第 attempt 次重试前的等待秒数，优先尊重服务端的 Retry-After。"""
        if retry_after:
            try:
                return min(float(retry_after), self.max_backoff)
            except ValueError:
                pass
        wait = self.backoff * (2 ** max(0, attempt - 1))
        return min(wait, self.max_backoff) + random.uniform(0, 0.3)

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        tag: str = "",
    ) -> dict[str, Any]:
        """发一次带重试的请求，返回解析后的 JSON。

        鉴权失败与明确的客户端错误立即抛出，不做无意义的重试；
        超时、网络抖动、限流与 5xx 走指数退避重试。
        """
        url = path if path.startswith("http") else "%s/%s" % (self.base_url, path.lstrip("/"))
        merged_headers = self.default_headers()
        if headers:
            merged_headers.update(headers)
        read_timeout = float(timeout or self.timeout)
        label = tag or path
        last_error: APIClientError | None = None
        retry_after: str | None = None

        for attempt in range(self.max_retries + 1):
            started = time.time()
            try:
                response = self._session.request(
                    method.upper(),
                    url,
                    json=json_body,
                    params=params,
                    headers=merged_headers,
                    timeout=(self.connect_timeout, read_timeout),
                )
            except requests.exceptions.Timeout as exc:
                retry_after = None
                last_error = APITimeoutError(
                    "请求超时（connect=%.1fs read=%.1fs）: %s"
                    % (self.connect_timeout, read_timeout, exc),
                    provider=self.provider,
                )
            except requests.exceptions.RequestException as exc:
                retry_after = None
                last_error = APIConnectionError("网络异常: %s" % exc, provider=self.provider)
            else:
                elapsed_ms = (time.time() - started) * 1000
                status = response.status_code
                if status < 400:
                    self.log.info(
                        "%s %s -> %d (%.0f ms)", method.upper(), label, status, elapsed_ms
                    )
                    return self._parse_json(response)

                detail = self._error_detail(response)
                if status in (401, 403):
                    raise APIAuthError(
                        "鉴权失败，请检查密钥或权限: %s" % detail,
                        provider=self.provider,
                        status_code=status,
                        payload=detail,
                    )
                if status not in RETRYABLE_STATUS:
                    raise APIResponseError(
                        "服务端返回错误: %s" % detail,
                        provider=self.provider,
                        status_code=status,
                        payload=detail,
                    )

                error_cls = APIRateLimitError if status == 429 else APIResponseError
                retry_after = response.headers.get("Retry-After")
                last_error = error_cls(
                    "可重试错误: %s" % detail,
                    provider=self.provider,
                    status_code=status,
                    payload=detail,
                )

            if attempt >= self.max_retries:
                self.log.error(
                    "%s 已重试 %d 次仍失败: %s", label, self.max_retries, last_error
                )
                raise last_error

            wait = self._sleep_seconds(attempt + 1, retry_after)
            self.log.warning(
                "%s 第 %d/%d 次尝试失败: %s | %.1fs 后重试",
                label,
                attempt + 1,
                self.max_retries + 1,
                last_error,
                wait,
            )
            time.sleep(wait)

        # 循环内必然 return 或 raise，这里仅为类型完备
        raise last_error if last_error else APIResponseError("未知错误", provider=self.provider)

    def _parse_json(self, response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise APIResponseError(
                "响应不是合法 JSON: %s | body=%s" % (exc, response.text[:300]),
                provider=self.provider,
                status_code=response.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise APIResponseError(
                "响应结构非预期（期望 object，实得 %s）" % type(data).__name__,
                provider=self.provider,
                status_code=response.status_code,
                payload=data,
            )
        return data

    @staticmethod
    def _error_detail(response: requests.Response) -> str:
        """从各家不同的错误体里提取可读信息。"""
        try:
            data = response.json()
        except ValueError:
            return response.text[:300]
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error)
            for key in ("message", "error_msg", "msg", "detail", "code"):
                if data.get(key):
                    return str(data[key])
        return json.dumps(data, ensure_ascii=False)[:300]

    # ------------------------------------------------------------------ 批量工具

    def map_parallel(
        self,
        func: Callable[[T], R],
        items: Sequence[T],
        *,
        max_workers: int | None = None,
    ) -> list[R]:
        """并发执行并保持输入顺序；单个任务时不起线程池。"""
        if not items:
            return []
        if len(items) == 1:
            return [func(items[0])]
        workers = min(max_workers or self.max_workers, len(items))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(func, items))

    # ------------------------------------------------------------------ 连通性

    def ping(self) -> bool:
        """子类实现最小成本的连通性探测。"""
        raise NotImplementedError

    def health_check(self) -> dict[str, Any]:
        """统一的连通性检查结果，供 test_api_connect.py 汇总。"""
        started = time.time()
        try:
            self.ping()
        except APIClientError as exc:
            return self._health_result(started, False, str(exc))
        except Exception as exc:  # 兜底，避免测试脚本因未知异常中断
            return self._health_result(started, False, "%s: %s" % (type(exc).__name__, exc))
        return self._health_result(started, True, None)

    def _health_result(self, started: float, ok: bool, error: str | None) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "ok": ok,
            "latency_ms": round((time.time() - started) * 1000, 1),
            "error": error,
        }

    # ------------------------------------------------------------------ 生命周期

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> BaseHTTPClient:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
