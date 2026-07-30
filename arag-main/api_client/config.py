"""环境变量加载与读取。

密钥一律来自 ``.env`` 或系统环境变量，代码内不出现任何明文密钥。
"""

from __future__ import annotations

import os
from pathlib import Path

from .exceptions import APIConfigError

# 项目根目录（api_client 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

_loaded = False


def load_env(dotenv_path: str | Path | None = None, override: bool = False) -> None:
    """加载 .env。重复调用只生效一次，除非显式 override。"""
    global _loaded
    if _loaded and not override:
        return

    path = Path(dotenv_path) if dotenv_path else ENV_FILE
    try:
        from dotenv import load_dotenv
    except ImportError:
        _load_env_fallback(path, override)
    else:
        load_dotenv(dotenv_path=str(path), override=override, encoding="utf-8")
    _loaded = True


def _load_env_fallback(path: Path, override: bool) -> None:
    """未安装 python-dotenv 时的极简解析，避免硬依赖。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value


def get_env(key: str, default: str | None = None) -> str | None:
    """读取环境变量，空串视作未设置。"""
    load_env()
    value = os.getenv(key)
    if value is None:
        return default
    value = value.strip()
    return value or default


def require_env(key: str, provider: str) -> str:
    """读取必填项，缺失时抛出配置异常并给出可操作的提示。"""
    value = get_env(key)
    if not value:
        raise APIConfigError(
            "环境变量 %s 未设置，请在 %s 中补齐后重试" % (key, ENV_FILE),
            provider=provider,
        )
    return value


def get_int(key: str, default: int) -> int:
    value = get_env(key)
    try:
        return int(value) if value else default
    except ValueError:
        return default


def get_float(key: str, default: float) -> float:
    value = get_env(key)
    try:
        return float(value) if value else default
    except ValueError:
        return default


def get_bool(key: str, default: bool = False) -> bool:
    value = get_env(key)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "y", "on")
