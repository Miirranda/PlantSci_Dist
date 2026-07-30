"""Bilingual entity mapper tool - Chinese term extraction and academic English normalization.

原创新增模块（非 A-RAG 开源部分），适配中英跨语言幻觉检测场景。

作用：中文公众号语句里的专业名词无法直接命中英文论文库的 keyword_search。本工具用 Qwen
抽取中文专业名词，翻译成规范的英文学术术语（含常用缩写别名），产出可直接喂给
``keyword_search`` 的英文关键词列表。

两层本地 JSON 缓存把重复的 API 调用压到最低：

* ``docs``  —— 整句指纹 -> 抽取结果，同一句话不会重复抽取；
* ``terms`` —— 中文术语 -> 英文术语，跨语句复用，并作为术语表回灌提示词，
  保证同一概念在不同语句中翻译一致。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from arag.tools.base import BaseTool

if TYPE_CHECKING:
    from arag.core.context import AgentContext

EXTRACT_SYSTEM_PROMPT = """你是中英学术术语标准化引擎，服务于跨语言论文证据检索。

任务：从给定的中文句子中抽取需要在英文论文库中检索的专业名词，并给出规范英文学术术语。

规则：
1. 只抽取专业名词：技术概念、模型名、方法名、数据集名、指标名、机构名、人名。
2. 不要抽取通用词汇（如"研究""提升""效果""方法"这类没有检索区分度的词）。
3. 英文译名必须是学术文献中的规范写法，不是字面直译。
4. aliases 里给出该术语在论文中的常见别名与缩写（如 large language model 的 LLM）。
5. 专有名词（模型名、数据集名）若本身就是英文，直接原样保留。
6. 最多抽取 8 个术语，按检索价值从高到低排列。

只输出 JSON，格式为：
{"terms": [{"zh": "大语言模型", "en": "large language model", "aliases": ["LLM", "large-scale language model"]}]}

若句子中没有任何值得检索的专业名词，返回 {"terms": []}。"""

TRANSLATE_SYSTEM_PROMPT = """你是中英学术术语标准化引擎。

任务：把给定的中文专业名词逐个翻译为规范的英文学术术语，并给出常见别名与缩写。

规则：
1. 译名必须是学术文献中的规范写法，不是字面直译。
2. aliases 给出论文中的常见别名与缩写。
3. 输入几个词就输出几条，顺序保持一致。

只输出 JSON，格式为：
{"terms": [{"zh": "注意力机制", "en": "attention mechanism", "aliases": ["self-attention"]}]}"""

# 抽取失败时的兜底：直接捞出连续的 ASCII 词组（公众号常混排英文模型名）
LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-\.]{2,}(?:\s+[A-Z][A-Za-z0-9\-\.]{1,})*")


class TermCache:
    """双语术语的本地 JSON 缓存，进程内加锁 + 原子落盘。"""

    def __init__(self, cache_file: str | Path) -> None:
        self.cache_file = Path(cache_file)
        self._lock = threading.Lock()
        self.docs: dict[str, list[dict[str, Any]]] = {}
        self.terms: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0
        self._load()

    def _load(self) -> None:
        if not self.cache_file.exists():
            return
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return
        self.docs = data.get("docs") or {}
        self.terms = data.get("terms") or {}

    def save(self) -> None:
        """先写临时文件再替换，避免并发写坏缓存。"""
        with self._lock:
            payload = json.dumps(
                {"docs": self.docs, "terms": self.terms}, ensure_ascii=False, indent=2
            )
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
                dir=str(self.cache_file.parent),
                suffix=".tmp",
            )
            try:
                handle.write(payload)
                handle.close()
                os.replace(handle.name, self.cache_file)
            except OSError:
                Path(handle.name).unlink(missing_ok=True)
                raise

    @staticmethod
    def fingerprint(text: str) -> str:
        normalized = re.sub(r"\s+", "", text or "")
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]

    def get_doc(self, text: str) -> list[dict[str, Any]] | None:
        key = self.fingerprint(text)
        with self._lock:
            cached = self.docs.get(key)
        if cached is None:
            self.misses += 1
            return None
        self.hits += 1
        return cached

    def put_doc(self, text: str, terms: list[dict[str, Any]]) -> None:
        key = self.fingerprint(text)
        with self._lock:
            self.docs[key] = terms
            for term in terms:
                zh = str(term.get("zh") or "").strip()
                if zh:
                    self.terms[zh] = {
                        "en": term.get("en", ""),
                        "aliases": term.get("aliases", []),
                    }

    def get_terms(self, zh_terms: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """返回 (命中的映射, 未命中的中文词)。"""
        found: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        with self._lock:
            for zh in zh_terms:
                entry = self.terms.get(zh)
                if entry:
                    found[zh] = entry
                else:
                    missing.append(zh)
        self.hits += len(found)
        self.misses += len(missing)
        return found, missing

    def put_terms(self, mapping: dict[str, dict[str, Any]]) -> None:
        with self._lock:
            self.terms.update(mapping)

    def glossary_hint(self, limit: int = 30) -> str:
        """把已有术语表压成提示词片段，保证跨语句译名一致。"""
        with self._lock:
            items = list(self.terms.items())[:limit]
        if not items:
            return ""
        lines = ["%s => %s" % (zh, entry.get("en", "")) for zh, entry in items if entry.get("en")]
        if not lines:
            return ""
        return "已确定的术语表（同一概念请沿用同一译名）：\n" + "\n".join(lines)

    def stats(self) -> dict[str, int]:
        return {
            "cache_hits": self.hits,
            "cache_misses": self.misses,
            "cached_docs": len(self.docs),
            "cached_terms": len(self.terms),
        }


class BilingualEntityMapperTool(BaseTool):
    """把中文专业名词映射为英文学术术语，供 keyword_search 使用。"""

    def __init__(
        self,
        llm: Any,
        cache_file: str | Path = "data/cache/bilingual_terms.json",
        *,
        max_terms: int = 8,
        verbose: bool = False,
    ) -> None:
        """
        Args:
            llm: 具备 ``extract_json(prompt, system=..., strict=...)`` 的 Qwen 客户端，
                 通常是 ``retrieval_adaptor.QwenAgentAdapter``
            cache_file: 本地术语缓存路径
            max_terms: 单句最多抽取多少术语
        """
        self.llm = llm
        self.cache = TermCache(cache_file)
        self.max_terms = max_terms
        self.verbose = verbose

    @property
    def name(self) -> str:
        return "bilingual_entity_mapper"

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "bilingual_entity_mapper",
                "description": """Extract Chinese technical terms from a Chinese sentence and translate them into standardized English academic terminology.

WHEN TO USE:
- ALWAYS call this FIRST when the claim to verify is written in Chinese
- The paper corpus is in English, so Chinese terms must be normalized before keyword_search
- Also useful when your English keywords returned nothing and you suspect a translation issue

RETURNS: JSON with the term mapping plus a ready-to-use `keyword_search_terms` list. Feed those English terms directly into keyword_search.""",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "chinese_text": {
                            "type": "string",
                            "description": "The Chinese sentence to extract technical terms from",
                        },
                        "chinese_terms": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional: translate these specific Chinese terms only (skips extraction)",
                        },
                    },
                    "required": [],
                },
            },
        }

    # ------------------------------------------------------------------ 核心逻辑

    def _call_llm(self, system: str, prompt: str) -> list[dict[str, Any]]:
        try:
            data = self.llm.extract_json(prompt, system=system, strict=False)
        except Exception as exc:
            if self.verbose:
                print("术语抽取失败: %s" % exc)
            return []

        terms = data.get("terms")
        if not isinstance(terms, list):
            return []

        cleaned: list[dict[str, Any]] = []
        for item in terms:
            if not isinstance(item, dict):
                continue
            english = str(item.get("en") or "").strip()
            if not english:
                continue
            aliases = item.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            cleaned.append(
                {
                    "zh": str(item.get("zh") or "").strip(),
                    "en": english,
                    "aliases": [str(a).strip() for a in aliases if str(a).strip()],
                }
            )
        return cleaned[: self.max_terms]

    def extract_from_text(self, chinese_text: str) -> list[dict[str, Any]]:
        """抽取 + 翻译。整句命中缓存时不发起任何 API 调用。"""
        cached = self.cache.get_doc(chinese_text)
        if cached is not None:
            return cached

        glossary = self.cache.glossary_hint()
        prompt = "中文句子：\n%s" % chinese_text
        if glossary:
            prompt = "%s\n\n%s" % (glossary, prompt)

        terms = self._call_llm(EXTRACT_SYSTEM_PROMPT, prompt)
        if not terms:
            terms = self._fallback_terms(chinese_text)

        self.cache.put_doc(chinese_text, terms)
        self.cache.save()
        return terms

    def translate_terms(self, chinese_terms: list[str]) -> list[dict[str, Any]]:
        """只做翻译。全部命中术语缓存时不发起 API 调用。"""
        wanted = [term.strip() for term in chinese_terms if term and term.strip()]
        if not wanted:
            return []

        found, missing = self.cache.get_terms(wanted)

        if missing:
            prompt = "中文专业名词列表：\n%s" % "\n".join("- %s" % term for term in missing)
            glossary = self.cache.glossary_hint()
            if glossary:
                prompt = "%s\n\n%s" % (glossary, prompt)
            for item in self._call_llm(TRANSLATE_SYSTEM_PROMPT, prompt):
                if item["zh"]:
                    found[item["zh"]] = {"en": item["en"], "aliases": item["aliases"]}
            self.cache.put_terms({zh: found[zh] for zh in found if zh in missing})
            self.cache.save()

        return [
            {"zh": zh, "en": found[zh].get("en", ""), "aliases": found[zh].get("aliases", [])}
            for zh in wanted
            if zh in found
        ]

    def _fallback_terms(self, chinese_text: str) -> list[dict[str, Any]]:
        """LLM 不可用时的降级：把句子里混排的英文词直接当作检索术语。"""
        terms: list[dict[str, Any]] = []
        seen: set[str] = set()
        for match in LATIN_TOKEN.findall(chinese_text or ""):
            token = match.strip()
            if len(token) < 3 or token.lower() in seen:
                continue
            seen.add(token.lower())
            terms.append({"zh": token, "en": token, "aliases": []})
        return terms[: self.max_terms]

    @staticmethod
    def search_terms(terms: list[dict[str, Any]]) -> list[str]:
        """摊平成 keyword_search 可直接使用的英文关键词列表，去重保序。"""
        flat: list[str] = []
        for term in terms:
            for candidate in [term.get("en", ""), *term.get("aliases", [])]:
                candidate = (candidate or "").strip()
                if candidate and candidate.lower() not in {item.lower() for item in flat}:
                    flat.append(candidate)
        return flat

    # ------------------------------------------------------------------ 工具接口

    def execute(
        self,
        context: "AgentContext",
        chinese_text: str = None,
        chinese_terms: list[str] = None,
    ) -> tuple[str, dict[str, Any]]:
        if chinese_terms:
            terms = self.translate_terms(list(chinese_terms))
            mode = "translate"
        elif chinese_text:
            terms = self.extract_from_text(chinese_text)
            mode = "extract"
        else:
            return (
                "Error: provide either chinese_text or chinese_terms",
                {"retrieved_tokens": 0, "error": "missing_argument"},
            )

        keyword_terms = self.search_terms(terms)
        payload = {
            "mode": mode,
            "terms": terms,
            "keyword_search_terms": keyword_terms,
            "hint": (
                "Pass keyword_search_terms into keyword_search. "
                "If nothing matches, try the aliases or a broader term."
            ),
        }
        tool_result = json.dumps(payload, ensure_ascii=False, indent=2)

        stats = self.cache.stats()
        context.add_retrieval_log(
            tool_name="bilingual_entity_mapper",
            tokens=0,
            metadata={
                "mode": mode,
                "term_count": len(terms),
                "keyword_terms": keyword_terms,
                **stats,
            },
        )

        return tool_result, {
            "retrieved_tokens": 0,
            "term_count": len(terms),
            "keyword_terms": keyword_terms,
            **stats,
        }
