#!/usr/bin/env python3
"""
Build sentence-level embedding index for semantic search.

基于开源 A-RAG 改良，适配中英跨语言幻觉检测场景。

与原生实现的差异：
1. 原生用本地 sentence-transformers 推理，这里改为调用统一封装的
   ``api_client.SiliconFlowClient``（bge-m3），不再依赖 torch / transformers；
2. 新增 ``--papers`` 入口，可一条命令完成「英文论文 PDF -> 带元数据的 chunks.json -> 向量索引」；
3. 索引中额外落盘 ``sentence_offset`` / ``chunk_sentences``，供检索时还原段落上下文；
4. 句子切分改用带缩写保护的规则，避免把 "et al." "e.g." 这类学术缩写切断。

Usage:
    # 从 PDF 开始建库
    python scripts/build_index.py --papers data/papers --output data/index

    # 已有 chunks.json
    python scripts/build_index.py --chunks data/corpus/chunks.json --output data/index
"""

import argparse
import json
import pickle
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_client import SiliconFlowClient
from retrieval_adaptor.index_store import normalize

# 学术文本里以点号结尾但并未断句的常见缩写
ABBREVIATIONS = (
    "e.g", "i.e", "et al", "cf", "vs", "resp", "approx", "ca", "etc",
    "fig", "eq", "sec", "tab", "ref", "no", "vol", "pp", "al",
    "dr", "mr", "mrs", "ms", "prof",
)

SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str, min_chars: int = 30) -> List[str]:
    """英文分句。

    基于开源 A-RAG 改良：原生实现是 ``re.split(r'[.!?\\n]+', text)``，会把
    "Vaswani et al. 2017" 切成两段，也会把小数切碎。这里先按边界切，再把结尾是
    已知缩写或残缺的片段合并回去。
    """
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []

    pieces = SENTENCE_BOUNDARY.split(normalized)
    merged: List[str] = []
    for piece in pieces:
        candidate = piece.strip()
        if not candidate:
            continue
        if merged:
            tail = merged[-1].rstrip(".").rsplit(" ", 1)[-1].lower()
            # 上一段以缩写收尾，或短到不可能是完整句子，就并回去
            if tail in ABBREVIATIONS or len(merged[-1]) < 15:
                merged[-1] = "%s %s" % (merged[-1], candidate)
                continue
        merged.append(candidate)

    return [
        sentence
        for sentence in merged
        if len(sentence) >= min_chars and re.search(r"[A-Za-z]{3}", sentence)
    ]


def load_chunks(chunks_file: str) -> List[Dict[str, Any]]:
    """Load chunks from file.

    兼容原生的 ``["0:text", ...]`` 与本项目扩展的带论文元数据的 dict 格式。
    """
    with open(chunks_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data and isinstance(data[0], dict):
        return data

    chunks = []
    for item in data:
        if isinstance(item, str):
            parts = item.split(":", 1)
            if len(parts) == 2:
                chunks.append({"id": parts[0], "text": parts[1]})
    return chunks


def embed_sentences(
    client: SiliconFlowClient,
    sentences: List[str],
    super_batch: int = 256,
) -> np.ndarray:
    """调用 SiliconFlow 批量向量化。

    外层按 super_batch 分组只为了显示进度；每组内部由 api_client 再按
    ``SILICONFLOW_EMBED_BATCH_SIZE`` 切分并并发下发。
    """
    vectors: List[List[float]] = []
    total_tokens = 0

    with tqdm(total=len(sentences), desc="Embedding", unit="sent") as bar:
        for start in range(0, len(sentences), super_batch):
            group = sentences[start : start + super_batch]
            result = client.embed(group)
            vectors.extend(result.vectors)
            total_tokens += result.total_tokens
            bar.update(len(group))

    print("  向量化消耗 token: %d" % total_tokens)
    return normalize(np.asarray(vectors, dtype=np.float32))


def build_index(
    chunks_file: str,
    output_dir: str,
    papers_dir: str = None,
    target_chars: int = 1200,
    min_sentence_chars: int = 30,
    verbose: bool = True,
):
    """Build sentence-level embedding index."""
    # 步骤 0（新增）：PDF -> chunks.json
    if papers_dir:
        from retrieval_adaptor.pdf_ingest import ingest_papers, save_chunks

        print("Ingesting papers from: %s" % papers_dir)
        chunks = ingest_papers(Path(papers_dir), target_chars=target_chars, verbose=verbose)
        save_chunks(chunks, Path(chunks_file))
        print("Saved %d chunks to: %s" % (len(chunks), chunks_file))
    else:
        print("Loading chunks from: %s" % chunks_file)
        chunks = load_chunks(chunks_file)

    print("Loaded %d chunks" % len(chunks))
    if not chunks:
        raise ValueError("没有可用的 chunk，请检查输入")

    chunk_lookup = {str(c["id"]): c for c in chunks}

    # 抽句子，同时记录句子在所属 chunk 内的序号（新增，用于还原段落上下文）
    print("Extracting sentences...")
    sentences: List[str] = []
    sentence_to_chunk: List[str] = []
    sentence_offset: List[int] = []
    chunk_sentences: Dict[str, List[str]] = {}

    for chunk in tqdm(chunks, desc="Processing chunks"):
        chunk_id = str(chunk["id"])
        own_sentences = split_sentences(chunk.get("text", ""), min_chars=min_sentence_chars)
        chunk_sentences[chunk_id] = own_sentences
        for position, sentence in enumerate(own_sentences):
            sentences.append(sentence)
            sentence_to_chunk.append(chunk_id)
            sentence_offset.append(position)

    print("Total sentences: %d" % len(sentences))
    if not sentences:
        raise ValueError("没有抽出任何句子，请检查 PDF 解析结果或降低 --min-sentence-chars")

    # 在线向量化（替换原生的本地模型推理）
    with SiliconFlowClient(verbose=False) as client:
        print("Embedding via SiliconFlow: %s" % client.embed_model)
        embeddings = embed_sentences(client, sentences)
        model_name = client.embed_model

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    index_file = output_path / "sentence_index.pkl"

    index_data = {
        "sentences": sentences,
        "embeddings": embeddings,
        "sentence_to_chunk": sentence_to_chunk,
        "chunks": chunk_lookup,
        "model_name": model_name,
        # 以下为改良新增字段
        "sentence_offset": sentence_offset,
        "chunk_sentences": chunk_sentences,
        "provider": "siliconflow",
        "dim": int(embeddings.shape[1]),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    print("Saving index to: %s" % index_file)
    with open(index_file, "wb") as f:
        pickle.dump(index_data, f)

    print("Index built successfully!")
    print("  - Chunks: %d" % len(chunks))
    print("  - Sentences: %d" % len(sentences))
    print("  - Embedding dim: %d" % embeddings.shape[1])
    print("  - Model: %s (online, SiliconFlow)" % model_name)


def main():
    parser = argparse.ArgumentParser(description="Build semantic search index")
    parser.add_argument(
        "--chunks",
        "-c",
        default="data/corpus/chunks.json",
        help="chunks.json 路径；配合 --papers 时作为输出路径",
    )
    parser.add_argument("--output", "-o", default="data/index", help="Output directory for index")
    parser.add_argument(
        "--papers",
        "-p",
        default=None,
        help="英文论文 PDF 目录；给定时先解析 PDF 生成 chunks.json（改良新增）",
    )
    parser.add_argument(
        "--target-chars", type=int, default=1200, help="单个 chunk 的目标字符数"
    )
    parser.add_argument(
        "--min-sentence-chars", type=int, default=30, help="入索引的句子最小字符数"
    )
    # 保留原生参数名以兼容既有命令行习惯，在线调用下不再有实际作用
    parser.add_argument("--model", "-m", default=None, help="（已弃用）改由 .env 的 SILICONFLOW_EMBED_MODEL 指定")
    parser.add_argument("--device", "-d", default=None, help="（已弃用）在线调用无需指定设备")
    parser.add_argument("--batch-size", "-b", type=int, default=None, help="（已弃用）见 SILICONFLOW_EMBED_BATCH_SIZE")

    args = parser.parse_args()

    if args.model or args.device or args.batch_size:
        print("提示：--model/--device/--batch-size 在在线向量化模式下已弃用，"
              "请在 .env 中配置 SILICONFLOW_* 相关项。")

    build_index(
        chunks_file=args.chunks,
        output_dir=args.output,
        papers_dir=args.papers,
        target_chars=args.target_chars,
        min_sentence_chars=args.min_sentence_chars,
    )


if __name__ == "__main__":
    main()
