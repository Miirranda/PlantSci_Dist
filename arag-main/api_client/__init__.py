"""统一 API 调用客户端（阶段 1）。

只做"把线上接口封装好"这一件事：超时重试、异常归一化、状态打印、批量优化。
不含 A-RAG 检索编排与幻觉判定等业务逻辑。

主流程可用：
    from api_client import SiliconFlowClient, QwenClient, WenxinBackupClient

Kimi 为预留通道，**不在此处导出**，需显式导入：
    from api_client.kimi_backup_client import KimiBackupClient
"""

from __future__ import annotations

from .base_client import BaseHTTPClient, get_logger
from .config import ENV_FILE, PROJECT_ROOT, get_env, load_env
from .exceptions import (
    APIAuthError,
    APIClientError,
    APIConfigError,
    APIConnectionError,
    APIRateLimitError,
    APIResponseError,
    APITimeoutError,
)
from .openai_compat import OpenAICompatChatClient, build_messages, tool_result_message
from .qwen_client import QwenClient
from .schemas import ChatResult, EmbeddingResult, RerankItem, RerankResult, ToolCall, extract_json
from .silicon_flow_client import SiliconFlowClient
from .wenxin_backup_client import WenxinBackupClient

__version__ = "1.0.0"

__all__ = [
    # 客户端
    "SiliconFlowClient",
    "QwenClient",
    "WenxinBackupClient",
    "BaseHTTPClient",
    "OpenAICompatChatClient",
    # 返回结构
    "ChatResult",
    "EmbeddingResult",
    "RerankItem",
    "RerankResult",
    "ToolCall",
    # 异常
    "APIClientError",
    "APIConfigError",
    "APIAuthError",
    "APIRateLimitError",
    "APITimeoutError",
    "APIConnectionError",
    "APIResponseError",
    # 工具
    "build_messages",
    "tool_result_message",
    "extract_json",
    "get_logger",
    "load_env",
    "get_env",
    "ENV_FILE",
    "PROJECT_ROOT",
]
