"""各客户端的统一返回结构。

沿用主包 ``src/arag`` 的 dataclass 约定（不引入 pydantic），
保证 Qwen / Kimi / 文心 三个对话客户端返回完全一致的对象，便于服务降级时直接替换。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """从模型输出中稳健地抽取 JSON 对象。

    依次尝试：直接解析 -> 剥离 ```json 代码围栏 -> 截取首个 ``{...}`` 片段。
    """
    if not text or not text.strip():
        raise ValueError("模型返回为空，无法解析 JSON")

    candidates = [text.strip()]

    fence = _JSON_FENCE.search(text)
    if fence:
        candidates.append(fence.group(1).strip())

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("无法从模型输出中解析出 JSON 对象: %s" % text[:200])


@dataclass
class ToolCall:
    """Function Calling 的单次工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCall:
        function = data.get("function") or {}
        raw_args = function.get("arguments") or ""
        try:
            arguments = json.loads(raw_args) if raw_args else {}
            if not isinstance(arguments, dict):
                arguments = {"_value": arguments}
        except (ValueError, TypeError):
            arguments = {}
        return cls(
            id=str(data.get("id") or ""),
            name=str(function.get("name") or ""),
            arguments=arguments,
            raw_arguments=raw_args if isinstance(raw_args, str) else str(raw_args),
        )


@dataclass
class ChatResult:
    """对话类接口的统一返回。"""

    content: str
    model: str
    finish_reason: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    provider: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def as_json(self) -> dict[str, Any]:
        """把 content 当作 JSON 解析（配合强制 JSON 输出使用）。"""
        return extract_json(self.content)

    def __str__(self) -> str:
        return self.content


@dataclass
class EmbeddingResult:
    """向量化接口的统一返回。"""

    vectors: list[list[float]]
    model: str
    dim: int = 0
    total_tokens: int = 0
    provider: str = ""

    def __post_init__(self) -> None:
        if not self.dim and self.vectors:
            self.dim = len(self.vectors[0])

    def __len__(self) -> int:
        return len(self.vectors)

    def __getitem__(self, index: int) -> list[float]:
        return self.vectors[index]

    def __iter__(self):
        return iter(self.vectors)


@dataclass
class RerankItem:
    """重排后的单条结果。``index`` 指向原始 documents 列表的下标。"""

    index: int
    score: float
    document: str = ""


@dataclass
class RerankResult:
    """重排接口的统一返回，``items`` 按分数降序。"""

    items: list[RerankItem]
    model: str
    total_tokens: int = 0
    provider: str = ""

    @property
    def indices(self) -> list[int]:
        return [item.index for item in self.items]

    @property
    def scores(self) -> list[float]:
        return [item.score for item in self.items]

    @property
    def documents(self) -> list[str]:
        return [item.document for item in self.items]

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> RerankItem:
        return self.items[index]

    def __iter__(self):
        return iter(self.items)
