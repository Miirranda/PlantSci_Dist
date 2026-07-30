#!/usr/bin/env python3
"""Kimi 预留通道使用示例。

Kimi **不参与主流水线**，因此不从 ``api_client`` 顶层导出，必须显式导入子模块。
调用入参与返回结构和 QwenClient 完全一致。

运行：
    python examples/kimi_backup_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_client import APIClientError, build_messages

# 显式从子模块导入，避免主流程误引入
from api_client.kimi_backup_client import KimiBackupClient


def demo_connectivity(client: KimiBackupClient) -> None:
    print("\n--- 1. 连通性检查 ---")
    print(client.health_check())


def demo_chat(client: KimiBackupClient) -> None:
    print("\n--- 2. 通用对话（与 QwenClient 同签名） ---")
    result = client.chat(
        build_messages("用一句话解释什么是幻觉检测。", system="你是简洁的技术助手。"),
        max_tokens=200,
    )
    print("模型:", result.model)
    print("token: 输入 %d / 输出 %d" % (result.prompt_tokens, result.completion_tokens))
    print("回答:", result.content)


def demo_json(client: KimiBackupClient) -> None:
    print("\n--- 3. 强制 JSON 输出 ---")
    data = client.ask_json("输出一个 JSON，键为 status，值为字符串 ok。", strict=False)
    print("结果:", data)


def main() -> None:
    print("提示：Kimi 仅为预留通道，主流水线不会调用。")
    with KimiBackupClient() as client:
        print("控制台 api_id（不参与鉴权）:", client.app_id or "未配置")
        demo_connectivity(client)
        demo_chat(client)
        demo_json(client)


if __name__ == "__main__":
    try:
        main()
    except APIClientError as exc:
        print("\n调用失败:", exc)
        raise SystemExit(1) from exc
