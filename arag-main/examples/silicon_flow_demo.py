#!/usr/bin/env python3
"""SiliconFlow 客户端使用示例：bge-m3 向量化 + bge-reranker-v2-m3 重排。

运行：
    python examples/silicon_flow_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_client import APIClientError, SiliconFlowClient


def demo_connectivity(client: SiliconFlowClient) -> None:
    """连通性探活：embedding 与 rerank 是两个独立端点，分别探一次。"""
    print("\n--- 1. 连通性检查 ---")
    print("embedding 端点:", client.health_check())
    try:
        client.ping_rerank()
        print("rerank 端点   : 可用")
    except APIClientError as exc:
        print("rerank 端点   : 不可用 ->", exc)


def demo_embed_single(client: SiliconFlowClient) -> None:
    """单条文本向量化。"""
    print("\n--- 2. 单条向量化 ---")
    result = client.embed("A-RAG 用智能体编排检索流程")
    print("模型:", result.model)
    print("维度:", result.dim)
    print("消耗 token:", result.total_tokens)
    print("前 5 维:", [round(value, 5) for value in result.vectors[0][:5]])

    # 只要一维向量时用 embed_query
    vector = client.embed_query("同上，但直接拿一维向量")
    print("embed_query 返回长度:", len(vector))


def demo_embed_batch(client: SiliconFlowClient) -> None:
    """批量向量化：超过 batch_size 自动切分并发，返回顺序与输入一致。"""
    print("\n--- 3. 批量向量化（自动切分 + 并发） ---")
    documents = [
        "检索增强生成通过外部知识抑制幻觉。",
        "重排模型用交叉编码器精排候选片段。",
        "智能体可以多轮决定检索什么。",
        "上下文压缩能省下宝贵的 token 预算。",
        "幻觉判定需要把生成内容与证据比对。",
    ]
    # 故意把 batch_size 设成 2，触发 3 个批次并发
    result = client.embed(documents, batch_size=2)
    print("输入 %d 条 -> 返回 %d 条，维度 %d" % (len(documents), len(result), result.dim))

    vectors = client.embed_documents(documents)
    print("embed_documents 返回形状: (%d, %d)" % (len(vectors), len(vectors[0])))


def demo_rerank(client: SiliconFlowClient) -> None:
    """重排：按与 query 的相关性给候选文档打分排序。"""
    print("\n--- 4. 重排 ---")
    query = "如何缓解大模型的幻觉问题？"
    documents = [
        "长城是中国古代的军事防御工程，全长两万多公里。",
        "检索增强生成引入外部知识，减少模型凭空编造内容。",
        "Python 的列表推导式比普通 for 循环更简洁。",
        "对生成结果逐句做事实性校验，可进一步降低幻觉率。",
    ]
    result = client.rerank(query, documents, top_n=3)
    print("查询:", query)
    for rank, item in enumerate(result, start=1):
        print("  #%d 原始索引=%d 分数=%.4f | %s" % (rank, item.index, item.score, item.document))
    print("命中的原始下标顺序:", result.indices)


def demo_rerank_sharding(client: SiliconFlowClient) -> None:
    """文档数超过 batch_size 时自动分片，再按分数全局归并。"""
    print("\n--- 5. 重排分片归并（大候选集） ---")
    documents = ["第 %d 段与问题无关的填充文本。" % index for index in range(12)]
    documents[9] = "幻觉检测的关键，是把生成的每句话与检索到的证据做比对。"
    result = client.rerank("幻觉检测怎么做", documents, top_n=3, batch_size=5)
    print("12 篇文档分 3 片请求，归并后 top1 原始索引 =", result.items[0].index, "（期望 9）")
    print("top3 分数:", [round(score, 4) for score in result.scores])


def main() -> None:
    # 不传 api_key 时自动从 .env 的 SILICONFLOW_API_KEY 读取
    with SiliconFlowClient() as client:
        demo_connectivity(client)
        demo_embed_single(client)
        demo_embed_batch(client)
        demo_rerank(client)
        demo_rerank_sharding(client)


if __name__ == "__main__":
    try:
        main()
    except APIClientError as exc:
        print("\n调用失败:", exc)
        raise SystemExit(1) from exc
