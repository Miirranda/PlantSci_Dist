"""api_client 离线单元测试：不发真实网络请求，全部用 mock 驱动。

覆盖那些只靠连通性测试无法验证的分支：批量切分后的顺序还原、重排分片的全局归并、
重试与不重试的判定、JSON 模式降级。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_client import (
    APIAuthError,
    APIResponseError,
    QwenClient,
    SiliconFlowClient,
    extract_json,
)
from api_client.base_client import BaseHTTPClient

FAKE_KEY = "sk-unit-test-not-a-real-key"


class FakeResponse:
    """最小可用的 requests.Response 替身。"""

    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)
        self.headers: dict[str, str] = {}

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def make_base_client(max_retries: int) -> BaseHTTPClient:
    """backoff=0 让重试测试不真的睡觉。"""
    return BaseHTTPClient(
        FAKE_KEY,
        "https://example.test",
        max_retries=max_retries,
        backoff=0,
        verbose=False,
    )


def make_embedding_payload(batch: list[str], reverse: bool = False) -> dict:
    """按批次生成可辨识的向量：每条向量首位等于文本长度。"""
    data = [
        {"index": position, "embedding": [float(len(text)), 0.5, 0.25]}
        for position, text in enumerate(batch)
    ]
    if reverse:
        # 模拟服务端乱序返回，客户端必须按 index 重排
        data.reverse()
    return {"model": "BAAI/bge-m3", "data": data, "usage": {"total_tokens": len(batch)}}


# ---------------------------------------------------------------- SiliconFlow 向量


def test_embed_splits_batches_and_preserves_order():
    client = SiliconFlowClient(api_key=FAKE_KEY, verbose=False)
    texts = ["a", "bb", "ccc", "dddd", "eeeee"]
    seen_batches: list[list[str]] = []

    def fake_request(method, path, *, json_body=None, **kwargs):
        batch = json_body["input"]
        seen_batches.append(batch)
        return make_embedding_payload(batch)

    with patch.object(SiliconFlowClient, "request", side_effect=fake_request):
        result = client.embed(texts, batch_size=2)

    assert [len(batch) for batch in seen_batches] == [2, 2, 1]
    assert len(result) == 5
    # 首位维度等于原文长度，可据此确认顺序未被打乱
    assert [vector[0] for vector in result.vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert result.dim == 3
    assert result.total_tokens == 5


def test_embed_reorders_out_of_order_response():
    client = SiliconFlowClient(api_key=FAKE_KEY, verbose=False)
    texts = ["a", "bb", "ccc"]

    def fake_request(method, path, *, json_body=None, **kwargs):
        return make_embedding_payload(json_body["input"], reverse=True)

    with patch.object(SiliconFlowClient, "request", side_effect=fake_request):
        result = client.embed(texts)

    assert [vector[0] for vector in result.vectors] == [1.0, 2.0, 3.0]


def test_embed_count_mismatch_raises():
    client = SiliconFlowClient(api_key=FAKE_KEY, verbose=False)

    def fake_request(method, path, *, json_body=None, **kwargs):
        return {"model": "m", "data": [{"index": 0, "embedding": [1.0]}], "usage": {}}

    with patch.object(SiliconFlowClient, "request", side_effect=fake_request):
        with pytest.raises(APIResponseError, match="数量不匹配"):
            client.embed(["a", "b", "c"])


def test_embed_empty_input_short_circuits():
    client = SiliconFlowClient(api_key=FAKE_KEY, verbose=False)
    with patch.object(SiliconFlowClient, "request") as mocked:
        result = client.embed([])
    assert len(result) == 0
    mocked.assert_not_called()


def test_embed_truncates_overlong_text():
    client = SiliconFlowClient(api_key=FAKE_KEY, verbose=False, max_chars=10)
    captured: list[str] = []

    def fake_request(method, path, *, json_body=None, **kwargs):
        captured.extend(json_body["input"])
        return make_embedding_payload(json_body["input"])

    with patch.object(SiliconFlowClient, "request", side_effect=fake_request):
        client.embed("x" * 50)

    assert len(captured[0]) == 10


# ---------------------------------------------------------------- SiliconFlow 重排


def test_rerank_merges_shards_by_global_score():
    """12 篇文档分 3 片；最相关的一篇在最后一片，归并后应排到首位。"""
    client = SiliconFlowClient(api_key=FAKE_KEY, verbose=False)
    documents = ["填充文本 %d" % index for index in range(12)]
    documents[9] = "命中文本"

    def fake_request(method, path, *, json_body=None, **kwargs):
        results = []
        for position, text in enumerate(json_body["documents"]):
            score = 0.99 if text == "命中文本" else 0.1 + position * 0.01
            results.append({"index": position, "relevance_score": score})
        results.sort(key=lambda item: item["relevance_score"], reverse=True)
        return {
            "results": results[: json_body["top_n"]],
            "meta": {"tokens": {"input_tokens": 7}},
        }

    with patch.object(SiliconFlowClient, "request", side_effect=fake_request):
        result = client.rerank("查询", documents, top_n=3, batch_size=5)

    assert len(result) == 3
    assert result.items[0].index == 9
    assert result.items[0].document == "命中文本"
    # 分数必须整体降序
    assert result.scores == sorted(result.scores, reverse=True)
    # 3 个分片的 token 累加
    assert result.total_tokens == 21


def test_rerank_top_n_defaults_to_all_documents():
    client = SiliconFlowClient(api_key=FAKE_KEY, verbose=False)

    def fake_request(method, path, *, json_body=None, **kwargs):
        return {
            "results": [
                {"index": position, "relevance_score": 1.0 - position * 0.1}
                for position in range(len(json_body["documents"]))
            ],
            "meta": {},
        }

    with patch.object(SiliconFlowClient, "request", side_effect=fake_request):
        result = client.rerank("查询", ["a", "b", "c"])

    assert result.indices == [0, 1, 2]


def test_rerank_empty_documents_short_circuits():
    client = SiliconFlowClient(api_key=FAKE_KEY, verbose=False)
    with patch.object(SiliconFlowClient, "request") as mocked:
        result = client.rerank("查询", [])
    assert len(result) == 0
    mocked.assert_not_called()


# ---------------------------------------------------------------- 对话解析


def test_chat_parses_content_and_usage():
    client = QwenClient(api_key=FAKE_KEY, verbose=False)
    payload = {
        "model": "qwen-plus",
        "choices": [{"finish_reason": "stop", "message": {"content": "你好"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
    }

    with patch.object(QwenClient, "request", return_value=payload):
        result = client.chat([{"role": "user", "content": "hi"}])

    assert result.content == "你好"
    assert result.finish_reason == "stop"
    assert (result.prompt_tokens, result.completion_tokens) == (11, 2)
    assert result.provider == "qwen"
    assert not result.has_tool_calls


def test_chat_parses_tool_calls():
    client = QwenClient(api_key=FAKE_KEY, verbose=False)
    payload = {
        "model": "qwen-plus",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "semantic_search",
                                "arguments": '{"query": "幻觉检测", "top_k": 3}',
                            },
                        }
                    ],
                },
            }
        ],
    }

    with patch.object(QwenClient, "request", return_value=payload):
        result = client.chat_with_tools(
            [{"role": "user", "content": "查一下"}],
            [{"type": "function", "function": {"name": "semantic_search"}}],
        )

    assert result.has_tool_calls
    call = result.tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "semantic_search"
    assert call.arguments == {"query": "幻觉检测", "top_k": 3}


def test_chat_missing_choices_raises():
    client = QwenClient(api_key=FAKE_KEY, verbose=False)
    with patch.object(QwenClient, "request", return_value={"model": "m"}):
        with pytest.raises(APIResponseError, match="choices"):
            client.chat([{"role": "user", "content": "hi"}])


def test_qwen3_disables_thinking_mode():
    """qwen3 混合思考模型在非流式下必须显式关闭 enable_thinking。"""
    client = QwenClient(api_key=FAKE_KEY, model="qwen3-max", verbose=False)
    payload = {"model": "qwen3-max", "choices": [{"message": {"content": "ok"}}]}

    with patch.object(QwenClient, "request", return_value=payload) as mocked:
        client.chat([{"role": "user", "content": "hi"}])

    assert mocked.call_args.kwargs["json_body"]["enable_thinking"] is False


def test_non_qwen3_model_omits_thinking_flag():
    client = QwenClient(api_key=FAKE_KEY, model="qwen-plus", verbose=False)
    payload = {"model": "qwen-plus", "choices": [{"message": {"content": "ok"}}]}

    with patch.object(QwenClient, "request", return_value=payload) as mocked:
        client.chat([{"role": "user", "content": "hi"}])

    assert "enable_thinking" not in mocked.call_args.kwargs["json_body"]


# ---------------------------------------------------------------- JSON 模式


def test_chat_json_uses_native_json_mode():
    client = QwenClient(api_key=FAKE_KEY, verbose=False)
    payload = {"model": "qwen-plus", "choices": [{"message": {"content": '{"a": 1}'}}]}

    with patch.object(QwenClient, "request", return_value=payload) as mocked:
        data = client.ask_json("给我一个 JSON")

    assert data == {"a": 1}
    body = mocked.call_args.kwargs["json_body"]
    assert body["response_format"] == {"type": "json_object"}


def test_chat_json_falls_back_when_json_mode_unsupported():
    """服务端以 400 拒绝 response_format 时，应退化为提示词约束再试一次。"""
    client = QwenClient(api_key=FAKE_KEY, verbose=False)
    calls: list[dict] = []

    def fake_request(method, path, *, json_body=None, **kwargs):
        calls.append(json_body)
        if "response_format" in json_body:
            raise APIResponseError("不支持该参数", provider="qwen", status_code=400)
        return {
            "model": "qwen-plus",
            "choices": [{"message": {"content": '```json\n{"b": 2}\n```'}}],
        }

    with patch.object(QwenClient, "request", side_effect=fake_request):
        data = client.ask_json("给我一个 JSON")

    assert data == {"b": 2}
    assert len(calls) == 2
    assert "response_format" not in calls[1]


def test_chat_json_injects_instruction_when_absent():
    client = QwenClient(api_key=FAKE_KEY, verbose=False)
    payload = {"model": "qwen-plus", "choices": [{"message": {"content": "{}"}}]}

    with patch.object(QwenClient, "request", return_value=payload) as mocked:
        client.chat_json([{"role": "user", "content": "抽取字段"}])

    messages = mocked.call_args.kwargs["json_body"]["messages"]
    assert messages[0]["role"] == "system"
    assert "JSON" in messages[0]["content"]


def test_chat_json_non_strict_returns_raw_on_parse_failure():
    client = QwenClient(api_key=FAKE_KEY, verbose=False)
    payload = {"model": "qwen-plus", "choices": [{"message": {"content": "抱歉，我不会"}}]}

    with patch.object(QwenClient, "request", return_value=payload):
        data = client.ask_json("给我 JSON", strict=False)

    assert data["_raw"] == "抱歉，我不会"
    assert "_error" in data


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('好的，结果是 {"a": 1} 请查收', {"a": 1}),
    ],
)
def test_extract_json_variants(text, expected):
    assert extract_json(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "完全没有 JSON"])
def test_extract_json_rejects_invalid(text):
    with pytest.raises(ValueError):
        extract_json(text)


# ---------------------------------------------------------------- 重试与异常


def test_retries_on_500_then_succeeds():
    client = make_base_client(max_retries=3)
    responses = [
        FakeResponse(500, {"error": {"message": "boom"}}),
        FakeResponse(503, {"error": {"message": "unavailable"}}),
        FakeResponse(200, {"ok": True}),
    ]

    with patch.object(client._session, "request", side_effect=responses) as mocked:
        result = client.request("POST", "/v1/thing")

    assert result == {"ok": True}
    assert mocked.call_count == 3


def test_retries_exhausted_raises_last_error():
    client = make_base_client(max_retries=2)
    responses = [FakeResponse(500, {"error": {"message": "boom"}}) for _ in range(3)]

    with patch.object(client._session, "request", side_effect=responses) as mocked:
        with pytest.raises(APIResponseError):
            client.request("POST", "/v1/thing")

    assert mocked.call_count == 3


def test_auth_error_is_not_retried():
    client = make_base_client(max_retries=3)
    unauthorized = FakeResponse(401, {"message": "Token is invalid."})

    with patch.object(client._session, "request", return_value=unauthorized) as mocked:
        with pytest.raises(APIAuthError, match="Token is invalid"):
            client.request("POST", "/v1/thing")

    assert mocked.call_count == 1


def test_client_error_is_not_retried():
    """404 之类的确定性错误立即抛出，不浪费重试次数。"""
    client = make_base_client(max_retries=3)
    not_found = FakeResponse(404, {"error": {"message": "no model"}})

    with patch.object(client._session, "request", return_value=not_found) as mocked:
        with pytest.raises(APIResponseError, match="no model"):
            client.request("POST", "/v1/thing")

    assert mocked.call_count == 1


def test_non_json_response_raises():
    client = make_base_client(max_retries=0)
    html = FakeResponse(200, None, text="<html>502</html>")

    with patch.object(client._session, "request", return_value=html):
        with pytest.raises(APIResponseError, match="不是合法 JSON"):
            client.request("GET", "/v1/thing")


def test_health_check_reports_failure_without_raising():
    client = SiliconFlowClient(api_key=FAKE_KEY, verbose=False)

    with patch.object(
        SiliconFlowClient,
        "request",
        side_effect=APIAuthError("Token is invalid.", provider="siliconflow", status_code=401),
    ):
        report = client.health_check()

    assert report["ok"] is False
    assert "Token is invalid" in report["error"]
    assert report["provider"] == "siliconflow"


def test_batch_chat_isolates_single_failure():
    client = QwenClient(api_key=FAKE_KEY, verbose=False)

    def fake_request(method, path, *, json_body=None, **kwargs):
        # 按消息内容判定失败，避免依赖线程池的执行顺序
        content = json_body["messages"][0]["content"]
        if content == "1":
            raise APIResponseError("boom", provider="qwen", status_code=500)
        return {"model": "qwen-plus", "choices": [{"message": {"content": "ok-" + content}}]}

    with patch.object(QwenClient, "request", side_effect=fake_request):
        outputs = client.batch_chat(
            [[{"role": "user", "content": str(index)}] for index in range(3)],
            return_exceptions=True,
        )

    assert len(outputs) == 3
    assert outputs[1] is None
    # 顺序必须与输入一致，不受并发完成先后影响
    assert outputs[0].content == "ok-0"
    assert outputs[2].content == "ok-2"
