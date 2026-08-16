"""按 paper_id 为单篇论文建句级向量索引。

一篇论文一座库：``data/index/<paper_id>/``。
已有完整索引则跳过；``rebuild=True`` 时强制重建。
"""

from __future__ import annotations

import csv
import json
import pickle
import time
from pathlib import Path
from typing import Any

import numpy as np
from tqdm import tqdm

from api_client import SiliconFlowClient

from .index_store import (
    INDEX_META_FILENAME,
    make_index_version,
    normalize,
    sentence_fingerprint,
    write_index_meta,
)
from .paper_registry import canonical_paper_id, is_index_ready, layout_for, resolve_pdf
from .pdf_ingest import ingest_papers, save_chunks, split_english_sentences


def load_chunks(chunks_file: str | Path) -> list[dict[str, Any]]:
    """兼容原生 ``["0:text", ...]`` 与带论文元数据的 dict 格式。"""
    with open(chunks_file, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    if data and isinstance(data[0], dict):
        return list(data)

    chunks: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, str):
            parts = item.split(":", 1)
            if len(parts) == 2:
                chunks.append({"id": parts[0], "text": parts[1]})
    return chunks


def embed_sentences(
    client: SiliconFlowClient,
    sentences: list[str],
    super_batch: int = 256,
) -> np.ndarray:
    vectors: list[list[float]] = []
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


def _unique_paper_ids(chunks: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for chunk in chunks:
        paper_id = canonical_paper_id(str(chunk.get("paper_id") or ""))
        if paper_id and paper_id not in found:
            found.append(paper_id)
    return found


def _write_sentence_table(
    path: Path,
    sentences: list[str],
    sentence_to_chunk: list[str],
    chunks: list[dict[str, Any]],
    paper_id: str,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lookup = {str(chunk["id"]): chunk for chunk in chunks}
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["sentence_id", "chunk_id", "text", "paper_id"]
        )
        writer.writeheader()
        for index, sentence in enumerate(sentences):
            chunk_id = sentence_to_chunk[index] if index < len(sentence_to_chunk) else ""
            row_paper = canonical_paper_id(
                str((lookup.get(str(chunk_id)) or {}).get("paper_id") or "")
            ) or paper_id
            writer.writerow(
                {
                    "sentence_id": index,
                    "chunk_id": chunk_id,
                    "text": sentence,
                    "paper_id": row_paper,
                }
            )
    return path


def build_index(
    chunks_file: str | Path,
    output_dir: str | Path,
    papers_dir: str | Path | None = None,
    target_chars: int = 1200,
    min_sentence_chars: int = 30,
    verbose: bool = True,
    skip_embed: bool = False,
    paper_id: str = "",
    sentences_out: str | Path | None = None,
) -> dict[str, Any]:
    """从单篇 PDF 或已有 chunks 建索引。多篇混库会直接报错。"""
    paper_id = canonical_paper_id(paper_id)
    if papers_dir:
        source = Path(papers_dir)
        if source.is_dir():
            pdfs = sorted(source.glob("*.pdf"))
            if len(pdfs) > 1:
                raise ValueError(
                    "禁止把目录里的多篇 PDF 打进同一索引（发现 %d 个）。"
                    "请改用 --paper-id Pxxx，一次只建一篇。" % len(pdfs)
                )
        print("Ingesting paper from: %s" % source)
        chunks = ingest_papers(
            source,
            target_chars=target_chars,
            verbose=verbose,
            paper_id=paper_id,
            allow_multiple=False,
        )
        save_chunks(chunks, Path(chunks_file))
        print("Saved %d chunks to: %s" % (len(chunks), chunks_file))
    else:
        print("Loading chunks from: %s" % chunks_file)
        chunks = load_chunks(chunks_file)

    print("Loaded %d chunks" % len(chunks))
    if not chunks:
        raise ValueError("没有可用的 chunk，请检查输入")

    mixed = _unique_paper_ids(chunks)
    if len(mixed) > 1:
        raise ValueError("chunks 里混有多篇论文 %s，拒绝建库" % mixed)
    if not paper_id and mixed:
        paper_id = mixed[0]
    if paper_id:
        for chunk in chunks:
            chunk["paper_id"] = paper_id

    chunk_lookup = {str(chunk["id"]): chunk for chunk in chunks}

    print("Extracting sentences...")
    sentences: list[str] = []
    sentence_to_chunk: list[str] = []
    sentence_offset: list[int] = []
    chunk_sentences: dict[str, list[str]] = {}

    for chunk in tqdm(chunks, desc="Processing chunks"):
        chunk_id = str(chunk["id"])
        own_sentences = split_english_sentences(
            chunk.get("text", ""), min_chars=min_sentence_chars
        )
        chunk_sentences[chunk_id] = own_sentences
        for position, sentence in enumerate(own_sentences):
            sentences.append(sentence)
            sentence_to_chunk.append(chunk_id)
            sentence_offset.append(position)

    print("Total sentences: %d" % len(sentences))
    if not sentences:
        raise ValueError("没有抽出任何句子，请检查 PDF 解析结果或降低 --min-sentence-chars")

    built_at = time.strftime("%Y-%m-%d %H:%M:%S")
    fingerprint = sentence_fingerprint(sentences)
    index_version = make_index_version(built_at, fingerprint)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    meta: dict[str, Any] = {
        "paper_id": paper_id,
        "index_version": index_version,
        "built_at": built_at,
        "fingerprint": fingerprint,
        "n_sentences": len(sentences),
        "n_chunks": len(chunks),
        "min_sentence_chars": min_sentence_chars,
        "embedded": (not skip_embed),
    }

    if sentences_out:
        table = _write_sentence_table(
            Path(sentences_out), sentences, sentence_to_chunk, chunks, paper_id
        )
        print("句表: %s" % table)

    if skip_embed:
        write_index_meta(output_path, meta)
        print("Skip embed: wrote %s (version=%s)" % (INDEX_META_FILENAME, index_version))
        print("  - paper_id: %s" % (paper_id or "(未指定)"))
        print("  - Chunks: %d" % len(chunks))
        print("  - Sentences: %d" % len(sentences))
        return {
            "sentences": sentences,
            "sentence_to_chunk": sentence_to_chunk,
            "index_version": index_version,
            "chunks": chunks,
            "paper_id": paper_id,
            "reused": False,
        }

    with SiliconFlowClient(verbose=False) as client:
        print("Embedding via SiliconFlow: %s" % client.embed_model)
        embeddings = embed_sentences(client, sentences)
        model_name = client.embed_model

    index_file = output_path / "sentence_index.pkl"
    meta["model_name"] = model_name
    meta["dim"] = int(embeddings.shape[1])
    meta["provider"] = "siliconflow"

    index_data = {
        "sentences": sentences,
        "embeddings": embeddings,
        "sentence_to_chunk": sentence_to_chunk,
        "chunks": chunk_lookup,
        "model_name": model_name,
        "sentence_offset": sentence_offset,
        "chunk_sentences": chunk_sentences,
        "provider": "siliconflow",
        "dim": int(embeddings.shape[1]),
        "built_at": built_at,
        "fingerprint": fingerprint,
        "index_version": index_version,
        "paper_id": paper_id,
    }

    print("Saving index to: %s" % index_file)
    with open(index_file, "wb") as handle:
        pickle.dump(index_data, handle)
    write_index_meta(output_path, meta)

    print("Index built successfully!")
    print("  - paper_id: %s" % (paper_id or "(未指定)"))
    print("  - Chunks: %d" % len(chunks))
    print("  - Sentences: %d" % len(sentences))
    print("  - Embedding dim: %d" % embeddings.shape[1])
    print("  - Model: %s (online, SiliconFlow)" % model_name)
    print("  - index_version: %s" % index_version)
    return index_data


def ensure_index(
    paper_id: str,
    *,
    rebuild: bool = False,
    skip_embed: bool = False,
    pdf: str | Path | None = None,
    target_chars: int = 1200,
    min_sentence_chars: int = 30,
    verbose: bool = True,
) -> dict[str, Any]:
    """陌生论文才建库；已有完整索引则直接复用。"""
    paper_id = canonical_paper_id(paper_id)
    if not paper_id:
        raise ValueError("ensure_index 需要 paper_id")
    layout = layout_for(paper_id, pdf=pdf)
    if not rebuild and not skip_embed and is_index_ready(paper_id, index_dir=layout.index_dir):
        print("复用已有索引: %s" % layout.index_dir)
        meta = {}
        meta_file = layout.index_meta_file
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
        return {
            "reused": True,
            "paper_id": paper_id,
            "index_dir": str(layout.index_dir),
            "chunks_file": str(layout.chunks_file),
            "index_version": str(meta.get("index_version") or ""),
        }

    if skip_embed and is_index_ready(paper_id, index_dir=layout.index_dir) and not rebuild:
        print("已有完整索引，--skip-embed 不覆盖 pkl，仅确认句表路径: %s" % layout.sentences_csv)
        return {
            "reused": True,
            "paper_id": paper_id,
            "index_dir": str(layout.index_dir),
            "skipped_embed_preview": True,
        }

    pdf_path = resolve_pdf(paper_id, pdf=pdf)
    print("为 %s 建索引 <- %s" % (paper_id, pdf_path))
    result = build_index(
        chunks_file=layout.chunks_file,
        output_dir=layout.index_dir,
        papers_dir=pdf_path,
        target_chars=target_chars,
        min_sentence_chars=min_sentence_chars,
        verbose=verbose,
        skip_embed=skip_embed,
        paper_id=paper_id,
        sentences_out=layout.sentences_csv,
    )
    result["reused"] = False
    result["index_dir"] = str(layout.index_dir)
    result["chunks_file"] = str(layout.chunks_file)
    return result
