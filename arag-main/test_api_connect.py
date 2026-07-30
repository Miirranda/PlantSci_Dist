#!/usr/bin/env python3
"""api_client 连通性测试脚本（阶段 1 交付物）。

逐项探活并打印汇总表格，不涉及任何 A-RAG / 幻觉判定业务逻辑。

用法
----
    python test_api_connect.py                      # 测全部（Kimi、文心无密钥时自动跳过）
    python test_api_connect.py --only siliconflow   # 只测向量与重排
    python test_api_connect.py --only qwen --full   # 测 Qwen 全部能力（含批量与长文本）
    python test_api_connect.py --quiet              # 只看汇总表，屏蔽逐条请求日志

退出码：0 = 必测项全部通过；1 = 有必测项失败。
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api_client import (  # noqa: E402
    APIClientError,
    APIConfigError,
    ENV_FILE,
    QwenClient,
    SiliconFlowClient,
    WenxinBackupClient,
    build_messages,
    get_env,
    load_env,
)

ALL_TARGETS = ("siliconflow", "qwen", "kimi", "wenxin")

STATUS_OK = "PASS"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"


@dataclass
class CheckResult:
    target: str
    name: str
    status: str
    latency_ms: float
    detail: str
    required: bool


class SkipCheck(Exception):
    """主动跳过某项检查（例如密钥未配置）。"""


def run_check(
    target: str,
    name: str,
    func: Callable[[], str],
    *,
    required: bool = True,
) -> CheckResult:
    """执行单项检查，把异常统一转成结果行。"""
    print("\n>>> [%s] %s" % (target, name))
    started = time.time()
    try:
        detail = func()
    except SkipCheck as exc:
        return CheckResult(target, name, STATUS_SKIP, 0.0, str(exc), required=False)
    except APIClientError as exc:
        elapsed = (time.time() - started) * 1000
        print("    %s: %s" % (STATUS_FAIL, exc))
        return CheckResult(target, name, STATUS_FAIL, elapsed, str(exc), required)
    except Exception as exc:  # 保证单项失败不影响后续检查
        elapsed = (time.time() - started) * 1000
        message = "%s: %s" % (type(exc).__name__, exc)
        print("    %s: %s" % (STATUS_FAIL, message))
        return CheckResult(target, name, STATUS_FAIL, elapsed, message, required)
    elapsed = (time.time() - started) * 1000
    print("    %s (%.0f ms): %s" % (STATUS_OK, elapsed, detail))
    return CheckResult(target, name, STATUS_OK, elapsed, detail, required)


# ---------------------------------------------------------------------- 各家检查


def check_siliconflow(verbose: bool, full: bool) -> list[CheckResult]:
    results: list[CheckResult] = []
    if not get_env("SILICONFLOW_API_KEY"):
        return [
            CheckResult(
                "siliconflow", "配置检查", STATUS_SKIP, 0.0,
                "SILICONFLOW_API_KEY 未配置", required=False,
            )
        ]

    client = SiliconFlowClient(verbose=verbose)

    def embed_single() -> str:
        result = client.embed("A-RAG 是一个智能体检索增强生成框架")
        return "模型=%s / 维度=%d / tokens=%d" % (result.model, result.dim, result.total_tokens)

    def embed_batch() -> str:
        texts = ["幻觉检测", "向量召回", "重排序", "智能体规划", "上下文压缩"]
        result = client.embed(texts, batch_size=2)
        assert len(result) == len(texts), "返回条数与输入不一致"
        return "%d 条文本 / 切成 %d 批 / 维度=%d" % (len(result), 3, result.dim)

    def rerank_basic() -> str:
        query = "如何缓解大模型的幻觉问题？"
        documents = [
            "检索增强生成通过引入外部知识来减少模型凭空编造的内容。",
            "长城是中国古代的军事防御工程。",
            "对生成结果做事实性校验，可以进一步降低幻觉率。",
            "Python 的列表推导式比 for 循环更简洁。",
        ]
        result = client.rerank(query, documents, top_n=2)
        top = result.items[0]
        return "top1 索引=%d 分数=%.4f / 命中：%s" % (
            top.index,
            top.score,
            top.document[:24],
        )

    results.append(run_check("siliconflow", "bge-m3 单条向量化", embed_single))
    results.append(run_check("siliconflow", "bge-m3 批量向量化（自动切分）", embed_batch))
    results.append(run_check("siliconflow", "bge-reranker-v2-m3 重排", rerank_basic))

    if full:

        def rerank_shard() -> str:
            documents = ["第 %d 段无关文本，用于测试分片归并。" % i for i in range(10)]
            documents[7] = "幻觉检测的关键是把生成内容与检索到的证据逐句比对。"
            result = client.rerank("幻觉检测怎么做", documents, top_n=3, batch_size=4)
            return "10 篇文档分 3 片 / top1 原始索引=%d（期望 7）" % result.items[0].index

        results.append(run_check("siliconflow", "重排分片归并（跨批次排序）", rerank_shard))

    client.close()
    return results


def check_qwen(verbose: bool, full: bool) -> list[CheckResult]:
    results: list[CheckResult] = []
    if not (get_env("QWEN_API_KEY") or get_env("DASHSCOPE_API_KEY")):
        return [
            CheckResult(
                "qwen", "配置检查", STATUS_SKIP, 0.0,
                "QWEN_API_KEY / DASHSCOPE_API_KEY 未配置", required=False,
            )
        ]

    client = QwenClient(verbose=verbose)

    def chat_basic() -> str:
        result = client.chat(
            build_messages("用一句话说明什么是检索增强生成。", system="你是简洁的技术助手。"),
            max_tokens=120,
        )
        return "模型=%s / tokens=%d+%d / 回答：%s" % (
            result.model,
            result.prompt_tokens,
            result.completion_tokens,
            result.content[:40].replace("\n", " "),
        )

    def chat_json() -> str:
        data = client.ask_json(
            "把下面这句话拆成结构化字段，键为 subject、action、object："
            "A-RAG 框架调用重排模型筛选候选片段。",
        )
        return "解析出 %d 个字段: %s" % (len(data), list(data)[:5])

    def function_calling() -> str:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "semantic_search",
                    "description": "在知识库中做语义检索，返回相关文档片段",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "检索查询"},
                            "top_k": {"type": "integer", "description": "返回条数"},
                        },
                        "required": ["query"],
                    },
                },
            }
        ]
        result = client.chat_with_tools(
            build_messages("帮我在知识库里查一下 2023 年诺贝尔物理学奖得主，取前 3 条。"),
            tools,
        )
        if not result.has_tool_calls:
            raise AssertionError("模型未触发工具调用，返回：%s" % result.content[:80])
        call = result.tool_calls[0]
        return "调用工具=%s / 参数=%s" % (call.name, call.arguments)

    results.append(run_check("qwen", "通用对话", chat_basic))
    results.append(run_check("qwen", "强制 JSON 输出", chat_json))
    results.append(run_check("qwen", "Function Calling 工具调用", function_calling))

    if full:

        def long_text() -> str:
            filler = "检索增强生成把外部知识注入提示词以抑制幻觉。" * 200
            result = client.chat_long(
                build_messages("下面这段文字反复强调的核心观点是什么？只答一句。\n\n" + filler),
                max_tokens=80,
            )
            return "输入约 %d 字符 / 模型=%s / 回答：%s" % (
                len(filler),
                result.model,
                result.content[:40].replace("\n", " "),
            )

        def batch_chat() -> str:
            questions = ["1+1 等于几？只答数字。", "中国的首都是哪里？只答城市名。", "水的化学式？只答式子。"]
            batch = [build_messages(question) for question in questions]
            outputs = client.batch_chat(batch, max_tokens=16, return_exceptions=True)
            answers = [item.content.strip()[:10] if item else "失败" for item in outputs]
            return "并发 %d 组 / 回答=%s" % (len(outputs), answers)

        results.append(run_check("qwen", "长文本输入", long_text))
        results.append(run_check("qwen", "批量并发对话", batch_chat))

    client.close()
    return results


def check_kimi(verbose: bool, full: bool) -> list[CheckResult]:
    """Kimi 为预留通道，仅在显式要求时才测，且失败不影响整体退出码。"""
    if not get_env("MOONSHOT_API_KEY"):
        return [
            CheckResult(
                "kimi", "配置检查", STATUS_SKIP, 0.0,
                "MOONSHOT_API_KEY 未配置", required=False,
            )
        ]

    def chat_basic() -> str:
        # 主流水线不引入 Kimi，故在此处局部导入
        from api_client.kimi_backup_client import KimiBackupClient

        with KimiBackupClient(verbose=verbose) as client:
            result = client.chat(build_messages("回复两个字：收到"), max_tokens=32)
            return "模型=%s / 回答：%s" % (result.model, result.content[:20].replace("\n", " "))

    return [run_check("kimi", "预留通道对话（备用，非必测）", chat_basic, required=False)]


def check_wenxin(verbose: bool, full: bool) -> list[CheckResult]:
    """文心为降级备用，未配置密钥时跳过。"""
    if not get_env("WENXIN_API_KEY"):
        return [
            CheckResult(
                "wenxin", "配置检查", STATUS_SKIP, 0.0,
                "WENXIN_API_KEY 未配置（降级通道暂不可用）", required=False,
            )
        ]

    def chat_basic() -> str:
        with WenxinBackupClient(verbose=verbose) as client:
            result = client.chat(build_messages("回复两个字：收到"), max_tokens=32)
            return "模型=%s / 回答：%s" % (result.model, result.content[:20].replace("\n", " "))

    return [run_check("wenxin", "降级通道对话（备用，非必测）", chat_basic, required=False)]


CHECKERS: dict[str, Callable[[bool, bool], list[CheckResult]]] = {
    "siliconflow": check_siliconflow,
    "qwen": check_qwen,
    "kimi": check_kimi,
    "wenxin": check_wenxin,
}


# ---------------------------------------------------------------------- 汇总输出


def print_summary(results: list[CheckResult]) -> None:
    print("\n" + "=" * 92)
    print("连通性测试汇总")
    print("=" * 92)
    print("%-14s %-34s %-6s %10s  %s" % ("服务", "检查项", "结果", "耗时(ms)", "备注"))
    print("-" * 92)
    for item in results:
        detail = item.detail.replace("\n", " ")
        if len(detail) > 26:
            detail = detail[:26] + "..."
        latency = "%.0f" % item.latency_ms if item.latency_ms else "-"
        print("%-14s %-34s %-6s %10s  %s" % (item.target, item.name, item.status, latency, detail))
    print("-" * 92)

    passed = sum(1 for item in results if item.status == STATUS_OK)
    failed = [item for item in results if item.status == STATUS_FAIL]
    skipped = sum(1 for item in results if item.status == STATUS_SKIP)
    print("通过 %d / 失败 %d / 跳过 %d" % (passed, len(failed), skipped))

    if failed:
        print("\n失败详情：")
        for item in failed:
            flag = "必测" if item.required else "非必测"
            print("  [%s][%s] %s\n      %s" % (flag, item.target, item.name, item.detail))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="api_client 连通性测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--only",
        default="all",
        help="逗号分隔的待测服务，可选 %s 或 all（默认 all）" % "/".join(ALL_TARGETS),
    )
    parser.add_argument("--full", action="store_true", help="附加测试长文本、批量并发、分片归并")
    parser.add_argument("--quiet", action="store_true", help="屏蔽逐条 HTTP 请求日志")
    args = parser.parse_args()

    if args.only.strip().lower() == "all":
        targets = list(ALL_TARGETS)
    else:
        targets = [item.strip().lower() for item in args.only.split(",") if item.strip()]
        unknown = [item for item in targets if item not in CHECKERS]
        if unknown:
            parser.error("未知服务 %s，可选：%s" % (unknown, list(ALL_TARGETS)))

    load_env()
    verbose = not args.quiet

    print("=" * 92)
    print("api_client 连通性测试")
    print("配置文件 : %s（%s）" % (ENV_FILE, "已找到" if ENV_FILE.exists() else "不存在"))
    print("待测服务 : %s" % ", ".join(targets))
    print("附加用例 : %s" % ("开启" if args.full else "关闭（加 --full 开启）"))
    print("=" * 92)

    results: list[CheckResult] = []
    for target in targets:
        try:
            results.extend(CHECKERS[target](verbose, args.full))
        except APIConfigError as exc:
            results.append(
                CheckResult(target, "客户端初始化", STATUS_SKIP, 0.0, str(exc), required=False)
            )
        except Exception as exc:
            results.append(
                CheckResult(
                    target,
                    "客户端初始化",
                    STATUS_FAIL,
                    0.0,
                    "%s: %s" % (type(exc).__name__, exc),
                    required=True,
                )
            )

    print_summary(results)
    blocking = [item for item in results if item.status == STATUS_FAIL and item.required]
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
