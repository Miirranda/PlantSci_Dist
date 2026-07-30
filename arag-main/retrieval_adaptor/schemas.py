"""跨语言检索的结构化证据 Schema。

原创代码（非 A-RAG 开源部分）。这里定义的 JSON 结构是检索底座与下游幻觉判定模块之间
的唯一契约：字段名、层级、取值域都固定，下游可直接消费而无需再解析自然语言。

顶层结构::

    {
      "schema_version": "1.0",
      "claim_zh":      "<公众号原文语句>",
      "verdict":       "SUPPORTED | INCONCLUSIVE | NO_EVIDENCE",
      "stop_reason":   "<终止原因>",
      "bilingual_terms": [{"zh": ..., "en": ..., "aliases": [...]}],
      "evidence_count": 2,
      "evidences":     [<EvidenceRecord>, ...],
      "stats":         {...}
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"

# 证据强度判定，与双阈值规则一一对应
VERDICT_SUPPORTED = "SUPPORTED"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICT_NO_EVIDENCE = "NO_EVIDENCE"
ALL_VERDICTS = (VERDICT_SUPPORTED, VERDICT_INCONCLUSIVE, VERDICT_NO_EVIDENCE)


@dataclass
class BilingualTerm:
    """一条中文专业名词到英文学术术语的映射。"""

    zh: str
    en: str
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"zh": self.zh, "en": self.en, "aliases": list(self.aliases)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BilingualTerm:
        aliases = data.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        return cls(
            zh=str(data.get("zh") or "").strip(),
            en=str(data.get("en") or "").strip(),
            aliases=[str(item).strip() for item in aliases if str(item).strip()],
        )

    def search_terms(self) -> list[str]:
        """送入 keyword_search 的全部英文写法，去重且保序。"""
        terms: list[str] = []
        for item in [self.en, *self.aliases]:
            if item and item not in terms:
                terms.append(item)
        return terms


@dataclass
class PaperMetadata:
    """英文论文的元数据。

    只保留定位来源必需的字段：标题、前两位作者、发表年份、来源文件。缺失字段留空字符串
    而不是 None，保证 JSON 结构稳定。
    """

    paper_id: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: str = ""
    source_file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "source_file": self.source_file,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PaperMetadata:
        data = data or {}
        authors = data.get("authors") or []
        if isinstance(authors, str):
            authors = [part.strip() for part in authors.split(";") if part.strip()]
        return cls(
            paper_id=str(data.get("paper_id") or ""),
            title=str(data.get("title") or ""),
            authors=[str(item) for item in authors][:2],
            year=str(data.get("year") or ""),
            source_file=str(data.get("source_file") or ""),
        )

    def citation(self) -> str:
        """给人看的一行引用，便于在 Agent 的自然语言输出里标注来源。"""
        parts = [self.authors[0] + " et al." if self.authors else "Unknown"]
        if self.year:
            parts.append("(%s)" % self.year)
        if self.title:
            parts.append(self.title)
        return " ".join(parts)


@dataclass
class ParagraphContext:
    """匹配片段所在的段落上下文。"""

    section: str = ""
    page: str = ""
    prev_text: str = ""
    target_text: str = ""
    next_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "page": self.page,
            "prev_text": self.prev_text,
            "target_text": self.target_text,
            "next_text": self.next_text,
        }

    def full_text(self) -> str:
        return " ".join(
            part for part in (self.prev_text, self.target_text, self.next_text) if part
        ).strip()


@dataclass
class EvidenceRecord:
    """单条跨语言证据：中文断言 + 英文论据 + 论文出处 + 段落上下文。"""

    evidence_id: str
    chunk_id: str
    evidence_en: str
    rerank_score: float = 0.0
    embed_score: float = 0.0
    verdict: str = VERDICT_INCONCLUSIVE
    matched_terms: list[str] = field(default_factory=list)
    paper: PaperMetadata = field(default_factory=PaperMetadata)
    context: ParagraphContext = field(default_factory=ParagraphContext)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "chunk_id": self.chunk_id,
            "evidence_en": self.evidence_en,
            "rerank_score": round(float(self.rerank_score), 6),
            "embed_score": round(float(self.embed_score), 6),
            "verdict": self.verdict,
            "matched_terms": list(self.matched_terms),
            "paper": self.paper.to_dict(),
            "context": self.context.to_dict(),
        }


@dataclass
class RetrievalOutput:
    """一条中文语句的完整检索结果，可直接序列化后交给幻觉判定模块。"""

    claim_zh: str
    verdict: str = VERDICT_NO_EVIDENCE
    stop_reason: str = ""
    bilingual_terms: list[BilingualTerm] = field(default_factory=list)
    evidences: list[EvidenceRecord] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "claim_zh": self.claim_zh,
            "verdict": self.verdict,
            "stop_reason": self.stop_reason,
            "bilingual_terms": [term.to_dict() for term in self.bilingual_terms],
            "evidence_count": len(self.evidences),
            "evidences": [item.to_dict() for item in self.evidences],
            "stats": dict(self.stats),
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @property
    def top_score(self) -> float:
        return max((item.rerank_score for item in self.evidences), default=0.0)
