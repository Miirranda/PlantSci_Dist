#!/usr/bin/env python3
"""文心一言降级通道使用示例。

重点演示"同构替换"：WenxinBackupClient 与 QwenClient 的入参、返回结构完全一致，
所以降级逻辑只是换一个实例，业务代码零改动。

运行：
    python examples/wenxin_backup_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_client import (
    APIClientError,
    ChatResult,
    QwenClient,
    WenxinBackupClient,
    build_messages,
    get_env,
)


def demo_connectivity(client: WenxinBackupClient) -> None:
    print("\n--- 1. 连通性检查 ---")
    print(client.health_check())


def demo_chat(client: WenxinBackupClient) -> None:
    print("\n--- 2. 通用对话（与 QwenClient 同签名） ---")
    result = client.chat(
        build_messages("用一句话解释检索增强生成。", system="你是简洁的技术助手。"),
        max_tokens=200,
    )
    print("模型:", result.model)
    print("回答:", result.content)


def demo_failover() -> None:
    """降级模式：主 LLM 报错时切到备用，两者返回同一种 ChatResult。"""
    print("\n--- 3. 主备切换（服务降级） ---")
    messages = build_messages("只回复两个字：收到")

    def call_with_failover() -> ChatResult:
        try:
            return QwenClient().chat(messages, max_tokens=16)
        except APIClientError as exc:
            print("主 LLM（Qwen）失败，切换备用:", str(exc)[:80])
            return WenxinBackupClient().chat(messages, max_tokens=16)

    result = call_with_failover()
    print("实际服务方:", result.provider)
    print("返回类型:", type(result).__name__)
    print("回答:", result.content)


def main() -> None:
    if not get_env("WENXIN_API_KEY"):
        print("未配置 WENXIN_API_KEY，跳过文心直连示例，仅演示降级代码结构。")
        print("降级写法见本文件 demo_failover()。")
        return

    with WenxinBackupClient() as client:
        demo_connectivity(client)
        demo_chat(client)
    demo_failover()


if __name__ == "__main__":
    try:
        main()
    except APIClientError as exc:
        print("\n调用失败:", exc)
        raise SystemExit(1) from exc
