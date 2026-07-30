"""向量索引的加载与检索。

原创代码（非 A-RAG 开源部分）。

索引本体由 ``scripts/build_index.py`` 调用 SiliconFlow 的 bge-m3 生成。本类只做只读访问，
因此可以在批量并发场景下被多个查询共享——检索工具实例可以按查询轻量新建（不再像原生
A-RAG 那样需要驻留一个本地 embedding 模型），从而天然线程安全。
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import numpy as np

INDEX_FILENAME = "sentence_index.pkl"


def normalize(matrix: np.ndarray) -> np.ndarray:
    """按行做 L2 归一化，之后点积即余弦相似度。"""
    array = np.asarray(matrix, dtype=np.float32)
    if array.ndim == 1:
        norm = np.linalg.norm(array)
        return array / norm if norm else array
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return array / norms


class IndexStore:
    """句子级向量索引 + 带元数据的 chunk 查找表。"""

    def __init__(self, index_dir: str | Path) -> None:
        self.index_dir = Path(index_dir)
        self.index_file = self.index_dir / INDEX_FILENAME
        if not self.index_file.exists():
            raise FileNotFoundError(
                "索引不存在: %s\n请先运行: python scripts/build_index.py" % self.index_file
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
        self.model_name: str = str(data.get("model_name") or "")
        self.provider: str = str(data.get("provider") or "")
        self.built_at: str = str(data.get("built_at") or "")

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
        return "索引: %d 句 / %d 块 / dim=%d / model=%s" % (
            len(self.sentences),
            len(self.chunks),
            self.dim,
            self.model_name or "unknown",
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
        """写出 CSV 句表（sentence_id, chunk_id, text）。"""
        import csv

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["sentence_id", "chunk_id", "text"]
                + (["paper_id"] if paper_id else []),
            )
            writer.writeheader()
            for row in self.iter_sentence_rows():
                if paper_id:
                    row = {**row, "paper_id": paper_id}
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
