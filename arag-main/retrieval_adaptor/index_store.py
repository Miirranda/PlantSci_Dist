"""向量索引的加载与检索。

原创代码（非 A-RAG 开源部分）。

索引本体由 ``scripts/build_index.py`` 调用 SiliconFlow 的 bge-m3 生成。本类只做只读访问，
因此可以在批量并发场景下被多个查询共享——检索工具实例可以按查询轻量新建（不再像原生
A-RAG 那样需要驻留一个本地 embedding 模型），从而天然线程安全。
"""

from __future__ import annotations

import hashlib
import json
import pickle
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

INDEX_FILENAME = "sentence_index.pkl"
INDEX_META_FILENAME = "index_meta.json"


def normalize(matrix: np.ndarray) -> np.ndarray:
    """按行做 L2 归一化，之后点积即余弦相似度。"""
    array = np.asarray(matrix, dtype=np.float32)
    if array.ndim == 1:
        norm = np.linalg.norm(array)
        return array / norm if norm else array
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return array / norms


def sentence_fingerprint(sentences: list[str]) -> str:
    """句子序列指纹：切句或顺序一变就会变。"""
    digest = hashlib.sha256()
    for sentence in sentences:
        digest.update(sentence.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()[:16]


def make_index_version(built_at: str, fingerprint: str) -> str:
    return "%s#%s" % (built_at, fingerprint)


def write_index_meta(index_dir: str | Path, payload: dict[str, Any]) -> Path:
    path = Path(index_dir) / INDEX_META_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_index_meta(index_dir: str | Path) -> dict[str, Any]:
    path = Path(index_dir) / INDEX_META_FILENAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class IndexStore:
    """句子级向量索引 + 带元数据的 chunk 查找表。"""

    def __init__(self, index_dir: str | Path) -> None:
        self.index_dir = Path(index_dir)
        self.index_file = self.index_dir / INDEX_FILENAME
        if not self.index_file.exists():
            raise FileNotFoundError(
                "索引不存在: %s\n请先运行: python scripts/build_index.py --paper-id Pxxx"
                % self.index_file
            )

        with self.index_file.open("rb") as handle:
            data = pickle.load(handle)

        self.sentences: list[str] = list(data["sentences"])
        self.embeddings: np.ndarray = normalize(np.asarray(data["embeddings"], dtype=np.float32))
        self.sentence_to_chunk: list[str] = [str(item) for item in data["sentence_to_chunk"]]
        self.chunks: dict[str, dict[str, Any]] = {
            str(key): value for key, value in (data.get("chunks") or {}).items()
        }
        # 下面两项由改造后的 build_index 写入；缺失时按 chunk 现场重建，兼容旧索引
        self.sentence_offset: list[int] = [
            int(item) for item in (data.get("sentence_offset") or [])
        ]
        self.chunk_sentences: dict[str, list[str]] = {
            str(key): list(value) for key, value in (data.get("chunk_sentences") or {}).items()
        }
        # 建库时被判为版式噪声的句子；不参与检索，只随句表导出供人工复核误删
        self.dropped_sentences: list[dict[str, Any]] = [
            dict(item) for item in (data.get("dropped_sentences") or [])
        ]
        self.model_name: str = str(data.get("model_name") or "")
        self.provider: str = str(data.get("provider") or "")
        self.built_at: str = str(data.get("built_at") or "")
        self.fingerprint: str = str(data.get("fingerprint") or "") or sentence_fingerprint(
            self.sentences
        )
        self.index_version: str = str(data.get("index_version") or "") or make_index_version(
            self.built_at, self.fingerprint
        )
        meta = read_index_meta(self.index_dir)
        self.paper_id: str = str(data.get("paper_id") or meta.get("paper_id") or "")

        if not self.sentence_offset:
            self.sentence_offset = self._rebuild_offsets()
        if not self.chunk_sentences:
            self.chunk_sentences = self._rebuild_chunk_sentences()

        # (chunk_id, chunk 内句序) -> 全局句子下标，避免检索时线性扫描
        self._position_lookup: dict[tuple[str, int], int] = {
            (chunk_id, offset): index
            for index, (chunk_id, offset) in enumerate(
                zip(self.sentence_to_chunk, self.sentence_offset)
            )
        }

    # ------------------------------------------------------------------ 兼容旧索引

    def _rebuild_offsets(self) -> list[int]:
        counters: dict[str, int] = {}
        offsets: list[int] = []
        for chunk_id in self.sentence_to_chunk:
            position = counters.get(chunk_id, 0)
            offsets.append(position)
            counters[chunk_id] = position + 1
        return offsets

    def _rebuild_chunk_sentences(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for sentence, chunk_id in zip(self.sentences, self.sentence_to_chunk):
            grouped.setdefault(chunk_id, []).append(sentence)
        return grouped

    # ------------------------------------------------------------------ 基本信息

    @property
    def dim(self) -> int:
        return int(self.embeddings.shape[1]) if self.embeddings.size else 0

    def __len__(self) -> int:
        return len(self.sentences)

    def describe(self) -> str:
        return "索引: %s / %d 句 / %d 块 / dim=%d / model=%s / version=%s" % (
            self.paper_id or "unknown",
            len(self.sentences),
            len(self.chunks),
            self.dim,
            self.model_name or "unknown",
            self.index_version or "unknown",
        )

    # ------------------------------------------------------------------ 检索

    def search(self, query_vector: list[float] | np.ndarray, top_n: int) -> list[tuple[int, float]]:
        """向量粗召回，返回 [(句子下标, 余弦相似度)]，按相似度降序。"""
        if not len(self.sentences) or top_n <= 0:
            return []
        vector = normalize(np.asarray(query_vector, dtype=np.float32))
        similarities = self.embeddings @ vector
        limit = min(int(top_n), similarities.shape[0])
        # argpartition 只保证前 limit 个是最大的，再对这一小段精排
        candidate = np.argpartition(-similarities, limit - 1)[:limit]
        ordered = candidate[np.argsort(-similarities[candidate])]
        return [(int(index), float(similarities[index])) for index in ordered]

    def search_sentences_by_keywords(
        self,
        keywords: Sequence[str],
        *,
        anchors: Sequence[str] | None = None,
        limit: int = 50,
    ) -> list[tuple[int, str, str, list[str]]]:
        """句级 case-insensitive 关键词补检，返回 [(sentence_id, chunk_id, text, matched_terms)]。

        与 chunk 级 ``KeywordSearchTool`` 的分工：这里直接产出 sentence_id，供充分性补检
        构建 ``Candidate`` 入看板，不再依赖 Agent 记得调用 read_chunk。

        - 关键词：normalize 空白后 substring 匹配（"paraffin-embedded" 可命中
          "paraffin-embedded sections"）；整体不中时回退词级匹配——每个词（长度>1）
          或其剥后缀词干（-ation/-ing/-ed/-es/-s）在句中出现即算命中
          （"paraffin sectioning" 可命中 "paraffin-embedded sections"）。
        - 锚点（图号/基因名/物种名）：长度 <=4 的纯字母数字串用整词正则
          ``(?<![a-z0-9])X(?![a-z0-9])`` 匹配，避免 "1b" 误中 "1-based"；更长的按 substring。
        - 排序：命中关键词数 desc -> 命中锚点数 desc -> sentence_id asc，截断到 limit。
        """
        needle_kws = [
            (str(kw), " ".join(str(kw).lower().split()))
            for kw in keywords
            if str(kw).strip()
        ]
        if not needle_kws:
            return []

        anchor_pats: list[tuple[str, re.Pattern[str] | None]] = []
        for anchor in anchors or []:
            anchor = str(anchor).strip()
            if not anchor:
                continue
            lowered = anchor.lower()
            if len(lowered) <= 4 and lowered.isalnum():
                pattern = re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(lowered))
            else:
                pattern = None
            anchor_pats.append((lowered, pattern))

        def _stem(word: str) -> str:
            """剥常见英语后缀的轻量词干：embedding/embedded -> embed，sectioning -> section。"""
            for suffix in ("ation", "ing", "ed", "es", "s"):
                if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                    return word[: -len(suffix)]
            return word

        def keyword_hit(needle: str, hay: str) -> bool:
            if needle in hay:
                return True
            words = [w for w in needle.split() if len(w) > 1]
            if not words:
                return False
            # 词级回退：容忍连字符/词形差异（checker 给 embedding、语料写 embedded）
            return all(w in hay or _stem(w) in hay for w in words)

        rows: list[tuple[int, str, str, list[str], int, int]] = []
        for index, sentence in enumerate(self.sentences):
            hay = sentence.lower()
            terms = [kw for kw, needle in needle_kws if keyword_hit(needle, hay)]
            if not terms:
                continue
            n_anchor = 0
            for anchor, pattern in anchor_pats:
                if pattern is not None:
                    if pattern.search(hay):
                        n_anchor += 1
                elif anchor in hay:
                    n_anchor += 1
            rows.append(
                (index, self.sentence_to_chunk[index], sentence, terms, len(terms), n_anchor)
            )
        rows.sort(key=lambda row: (-row[4], -row[5], row[0]))
        return [
            (sentence_id, chunk_id, text, terms)
            for sentence_id, chunk_id, text, terms, _n_terms, _n_anchor in rows[:limit]
        ]

    # ------------------------------------------------------------------ 上下文与元数据

    def sentence(self, index: int) -> str:
        return self.sentences[index]

    @staticmethod
    def sentence_id(index: int) -> int:
        """全局句子编号（整数），与 ``sentences`` 下标一致；文档级另存 paper_id。"""
        return int(index)

    def chunk_id_of(self, index: int) -> str:
        return self.sentence_to_chunk[index]

    def iter_sentence_rows(self) -> list[dict[str, Any]]:
        """导出句表行：sentence_id / chunk_id / text，供人工标注勾选。"""
        rows: list[dict[str, Any]] = []
        for index, text in enumerate(self.sentences):
            rows.append(
                {
                    "sentence_id": self.sentence_id(index),
                    "chunk_id": self.chunk_id_of(index),
                    "text": text,
                }
            )
        return rows

    def export_sentence_table(self, path: str | Path, *, paper_id: str = "") -> Path:
        """写出 CSV 句表：入库句在前，被筛掉的句子附在末尾（``sentence_id`` 留空）。"""
        import csv

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fields = ["sentence_id", "chunk_id", "text"]
        if paper_id:
            fields.append("paper_id")
        fields += ["status", "drop_reason"]

        with out.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in self.iter_sentence_rows():
                row = {**row, "status": "kept", "drop_reason": ""}
                if paper_id:
                    row["paper_id"] = paper_id
                writer.writerow(row)
            for item in self.dropped_sentences:
                row = {
                    "sentence_id": "",
                    "chunk_id": str(item.get("chunk_id") or ""),
                    "text": str(item.get("text") or ""),
                    "status": "dropped",
                    "drop_reason": str(item.get("reason") or ""),
                }
                if paper_id:
                    row["paper_id"] = paper_id
                writer.writerow(row)
        return out

    def resolve_sentence_id(self, text: str) -> int:
        """用规范化文本在索引中查找 sentence_id；找不到返回 -1。"""
        needle = " ".join(str(text or "").split()).strip().lower()
        if not needle:
            return -1
        for index, sentence in enumerate(self.sentences):
            hay = " ".join(sentence.split()).strip().lower()
            if hay == needle or needle in hay or hay in needle:
                return self.sentence_id(index)
        return -1

    def chunk(self, chunk_id: str) -> dict[str, Any]:
        return self.chunks.get(str(chunk_id), {})

    def chunk_text(self, chunk_id: str) -> str:
        return str(self.chunk(chunk_id).get("text") or "")

    def global_index(self, chunk_id: str, position: int) -> int:
        """把「chunk 内第 n 句」换算成全局句子下标，找不到返回 -1。"""
        return self._position_lookup.get((str(chunk_id), int(position)), -1)

    def paragraph_context(self, index: int, window: int = 2) -> tuple[str, str, str]:
        """取某个句子在所属 chunk 内的前后文，返回 (前文, 本句, 后文)。"""
        chunk_id = self.sentence_to_chunk[index]
        sentences = self.chunk_sentences.get(chunk_id) or []
        position = self.sentence_offset[index] if index < len(self.sentence_offset) else -1

        if not sentences or not 0 <= position < len(sentences):
            return "", self.sentences[index], ""

        start = max(0, position - window)
        end = min(len(sentences), position + window + 1)
        prev_text = " ".join(sentences[start:position])
        next_text = " ".join(sentences[position + 1 : end])
        return prev_text, sentences[position], next_text
