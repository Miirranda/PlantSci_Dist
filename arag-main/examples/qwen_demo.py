#!/usr/bin/env python3
"""通义千问客户端使用示例：通用对话、Function Calling、强制 JSON、长文本、批量并发。

运行：
    python examples/qwen_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_client import APIClientError, QwenClient, build_messages, tool_result_message

# Function Calling 用的工具声明，格式与 OpenAI Function Schema 一致
SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "semantic_search",
        "description": "在本地知识库中做语义检索，返回最相关的文档片段",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索查询语句"},
                "top_k": {"type": "integer", "description": "返回片段数量，默认 5"},
            },
            "required": ["query"],
        },
    },
}


def demo_connectivity(client: QwenClient) -> None:
    print("\n--- 1. 连通性检查 ---")
    print(client.health_check())


def demo_chat(client: QwenClient) -> None:
    """通用对话：既可以传完整 messages，也可以用 ask 走单轮。"""
    print("\n--- 2. 通用对话 ---")
    messages = build_messages(
        "用一句话解释检索增强生成。",
        system="你是严谨的技术助手，回答尽量简短。",
    )
    result = client.chat(messages, max_tokens=200)
    print("模型:", result.model)
    print("token: 输入 %d / 输出 %d" % (result.prompt_tokens, result.completion_tokens))
    print("结束原因:", result.finish_reason)
    print("回答:", result.content)

    print("\nask 单轮快捷调用:", client.ask("只回复两个字：收到", max_tokens=16))


def demo_multi_turn(client: QwenClient) -> None:
    """多轮对话：把历史消息拼进 messages。"""
    print("\n--- 3. 多轮对话 ---")
    messages = [
        {"role": "system", "content": "你是简洁的助手。"},
        {"role": "user", "content": "记住这个数字：42。"},
        {"role": "assistant", "content": "好的，我记住了 42。"},
        {"role": "user", "content": "我刚让你记的数字是多少？只答数字。"},
    ]
    print("回答:", client.chat(messages, max_tokens=16).content)


def demo_json_output(client: QwenClient) -> None:
    """强制 JSON 输出：优先走服务端原生 JSON 模式，不支持时自动降级为提示词约束。"""
    print("\n--- 4. 强制 JSON 输出 ---")
    data = client.ask_json(
        "把这句话拆成结构化字段，键固定为 subject / action / object：\n"
        "A-RAG 框架调用重排模型筛选候选片段。",
        system="你是信息抽取引擎。",
    )
    print("类型:", type(data).__name__)
    print(json.dumps(data, ensure_ascii=False, indent=2))

    # strict=False 时解析失败不抛异常，返回 {"_raw": ..., "_error": ...}
    loose = client.chat_json(
        build_messages("输出一个 JSON，键为 ok，值为布尔真。"), strict=False, max_tokens=64
    )
    print("宽松模式结果:", loose)


def demo_function_calling(client: QwenClient) -> None:
    """Function Calling 完整回路：模型请求调用工具 -> 本地执行 -> 结果喂回模型。"""
    print("\n--- 5. Function Calling ---")
    messages = build_messages("帮我在知识库里查一下 A-RAG 的核心创新点，取前 3 条。")
    result = client.chat_with_tools(messages, [SEARCH_TOOL])

    if not result.has_tool_calls:
        print("模型选择直接回答:", result.content)
        return

    call = result.tool_calls[0]
    print("模型请求调用工具:", call.name)
    print("解析后的参数:", call.arguments)

    # 这里用假数据模拟工具执行，阶段 1 不接入真实检索
    fake_result = json.dumps(
        {
            "chunks": [
                "A-RAG 用智能体自主决定检索时机与检索内容。",
                "引入重排模型对候选片段做精排。",
                "对生成结果做幻觉判定并触发重新检索。",
            ]
        },
        ensure_ascii=False,
    )

    messages.append(result.raw["choices"][0]["message"])
    messages.append(tool_result_message(call.id, fake_result, call.name))
    final = client.chat(messages, max_tokens=300)
    print("\n模型基于工具结果的最终回答:")
    print(final.content)


def demo_long_text(client: QwenClient) -> None:
    """长文本输入：chat_long 会切到 QWEN_LONG_MODEL 指定的长上下文模型。"""
    print("\n--- 6. 长文本输入 ---")
    document = "检索增强生成通过引入外部知识来抑制模型幻觉。" * 300
    result = client.chat_long(
        build_messages("下面这段文字反复强调的核心观点是什么？只答一句。\n\n" + document),
        max_tokens=100,
    )
    print("输入长度: %d 字符" % len(document))
    print("使用模型:", result.model)
    print("回答:", result.content)


def demo_batch(client: QwenClient) -> None:
    """批量并发对话：返回顺序与输入一致，单条失败返回 None 而不中断整批。"""
    print("\n--- 7. 批量并发对话 ---")
    questions = [
        "1+1 等于几？只答数字。",
        "中国的首都是哪里？只答城市名。",
        "水的化学式是什么？只答式子。",
    ]
    batch = [build_messages(question) for question in questions]
    outputs = client.batch_chat(batch, max_tokens=16, return_exceptions=True)
    for question, output in zip(questions, outputs):
        answer = output.content.strip() if output else "调用失败"
        print("  %s -> %s" % (question, answer))


def demo_error_handling(client: QwenClient) -> None:
    """异常捕获：所有失败都归一到 APIClientError 层次。"""
    print("\n--- 8. 异常处理 ---")
    try:
        client.chat(build_messages("测试"), model="不存在的模型名")
    except APIClientError as exc:
        print("已捕获:", type(exc).__name__)
        print("状态码:", exc.status_code)
        print("信息:", str(exc)[:120])


def main() -> None:
    # 不传 api_key 时自动从 .env 的 QWEN_API_KEY / DASHSCOPE_API_KEY 读取
    with QwenClient() as client:
        demo_connectivity(client)
        demo_chat(client)
        demo_multi_turn(client)
        demo_json_output(client)
        demo_function_calling(client)
        demo_long_text(client)
        demo_batch(client)
        demo_error_handling(client)


if __name__ == "__main__":
    try:
        main()
    except APIClientError as exc:
        print("\n调用失败:", exc)
        raise SystemExit(1) from exc
