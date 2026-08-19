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
from .pdf_ingest import ingest_papers, save_chunks, split_english_sentences_detailed

SENTENCE_TABLE_FIELDS = [
    "sentence_id",
    "chunk_id",
    "text",
    "paper_id",
    "status",
    "drop_reason",
]


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


def _count_reasons(dropped: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in dropped:
        reason = str(row.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _print_drop_summary(dropped: list[dict[str, Any]]) -> None:
    if not dropped:
        print("  - 未筛掉任何句子")
        return
    print("  - 筛掉 %d 句（已附在句表末尾，请复核是否误删）:" % len(dropped))
    for reason, count in _count_reasons(dropped).items():
        print("      %-20s %d" % (reason, count))


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
    dropped: list[dict[str, Any]] | None = None,
) -> Path:
    """写句表：先是入库句，再在文件末尾附上被筛掉的句子及原因。

    被筛掉的行 ``sentence_id`` 留空，读句表的脚本都会跳过这类行，所以它们只影响人工复核，
    不会污染下游对齐。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lookup = {str(chunk["id"]): chunk for chunk in chunks}

    def paper_of(chunk_id: str) -> str:
        return canonical_paper_id(
            str((lookup.get(str(chunk_id)) or {}).get("paper_id") or "")
        ) or paper_id

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SENTENCE_TABLE_FIELDS)
        writer.writeheader()
        for index, sentence in enumerate(sentences):
            chunk_id = sentence_to_chunk[index] if index < len(sentence_to_chunk) else ""
            writer.writerow(
                {
                    "sentence_id": index,
                    "chunk_id": chunk_id,
                    "text": sentence,
                    "paper_id": paper_of(chunk_id),
                    "status": "kept",
                    "drop_reason": "",
                }
            )
        for row in dropped or []:
            chunk_id = str(row.get("chunk_id") or "")
            writer.writerow(
                {
                    "sentence_id": "",
                    "chunk_id": chunk_id,
                    "text": str(row.get("text") or ""),
                    "paper_id": paper_of(chunk_id),
                    "status": "dropped",
                    "drop_reason": str(row.get("reason") or ""),
                }
            )
    return path


def load_sentence_table(path: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """读句表：入库句按 ``sentence_id`` 排序，筛掉行单独返回。"""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError("句表不存在: %s" % source)

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            text = str(row.get("text") or "")
            if not text.strip():
                continue
            status = str(row.get("status") or "").strip().lower()
            sid_raw = str(row.get("sentence_id") or "").strip()
            chunk_id = str(row.get("chunk_id") or "")
            row_paper = canonical_paper_id(str(row.get("paper_id") or ""))
            if status == "dropped" or not sid_raw:
                dropped.append(
                    {
                        "chunk_id": chunk_id,
                        "text": text,
                        "reason": str(row.get("drop_reason") or row.get("reason") or ""),
                        "paper_id": row_paper,
                    }
                )
                continue
            try:
                sentence_id = int(sid_raw)
            except ValueError as exc:
                raise ValueError("句表 %s 含非法 sentence_id: %r" % (source, sid_raw)) from exc
            kept.append(
                {
                    "sentence_id": sentence_id,
                    "chunk_id": chunk_id,
                    "text": text,
                    "paper_id": row_paper,
                }
            )

    kept.sort(key=lambda item: (int(item["sentence_id"]), str(item.get("chunk_id") or "")))
    return kept, dropped


def _assemble_from_sentence_rows(
    kept: list[dict[str, Any]],
    dropped: list[dict[str, Any]],
    *,
    paper_id: str = "",
    extra_chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把句表行组装成建库所需的句子序列、chunk 对照和筛掉列表。"""
    if not kept:
        raise ValueError("句表没有可入库句子（status=kept 且 sentence_id 非空）")

    ids = [int(row["sentence_id"]) for row in kept]
    if ids != list(range(len(ids))):
        print(
            "警告: sentence_id 不是从 0 连续编号（首=%s 末=%s 共%d），"
            "索引下标将按排序后的行号计，可能与句表 id 不一致"
            % (ids[0], ids[-1], len(ids))
        )

    paper_id = canonical_paper_id(paper_id) or canonical_paper_id(
        str(kept[0].get("paper_id") or "")
    )
    sentences = [str(row["text"]) for row in kept]
    sentence_to_chunk = [str(row.get("chunk_id") or "") for row in kept]
    sentence_offset: list[int] = []
    chunk_sentences: dict[str, list[str]] = {}
    for chunk_id, sentence in zip(sentence_to_chunk, sentences):
        own = chunk_sentences.setdefault(chunk_id, [])
        sentence_offset.append(len(own))
        own.append(sentence)

    chunk_lookup: dict[str, dict[str, Any]] = {}
    for chunk in extra_chunks or []:
        chunk_id = str(chunk.get("id") or "")
        if not chunk_id:
            continue
        payload = dict(chunk)
        if paper_id:
            payload["paper_id"] = paper_id
        chunk_lookup[chunk_id] = payload
    for chunk_id, own in chunk_sentences.items():
        if chunk_id in chunk_lookup:
            continue
        chunk_lookup[chunk_id] = {
            "id": chunk_id,
            "text": " ".join(own),
            "paper_id": paper_id,
        }

    return {
        "sentences": sentences,
        "sentence_to_chunk": sentence_to_chunk,
        "sentence_offset": sentence_offset,
        "chunk_sentences": chunk_sentences,
        "chunks": list(chunk_lookup.values()),
        "dropped_sentences": list(dropped),
        "paper_id": paper_id,
    }


def _candidate_chunk_files(
    paper_id: str,
    chunks_file: str | Path | None = None,
) -> list[Path]:
    found: list[Path] = []
    if chunks_file:
        found.append(Path(chunks_file))
    if paper_id:
        layout = layout_for(paper_id)
        found.append(layout.chunks_file.parent / "chunks_v2.json")
        found.append(layout.chunks_file)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in found:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _load_best_chunks(
    paper_id: str,
    csv_chunk_ids: set[str],
    chunks_file: str | Path | None = None,
) -> list[dict[str, Any]]:
    """在候选 chunks 文件里选覆盖句表 chunk_id 最多的一份。"""
    best: list[dict[str, Any]] = []
    best_cover = -1
    best_path: Path | None = None
    for path in _candidate_chunk_files(paper_id, chunks_file):
        if not path.is_file():
            continue
        try:
            chunks = load_chunks(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        cover = len(csv_chunk_ids & {str(chunk.get("id") or "") for chunk in chunks})
        if cover > best_cover:
            best = chunks
            best_cover = cover
            best_path = path
    if best_path is not None:
        print(
            "配套 chunks: %s（覆盖句表 chunk_id %d/%d）"
            % (best_path, best_cover, len(csv_chunk_ids))
        )
    return best


def _finalize_index(
    *,
    sentences: list[str],
    sentence_to_chunk: list[str],
    sentence_offset: list[int],
    chunk_sentences: dict[str, list[str]],
    chunks: list[dict[str, Any]],
    dropped_sentences: list[dict[str, Any]],
    output_dir: str | Path,
    paper_id: str,
    skip_embed: bool,
    min_sentence_chars: int,
    sentences_out: str | Path | None = None,
    source: str = "pdf",
) -> dict[str, Any]:
    """写句表（可选）、向量化并落盘 pkl / meta。"""
    built_at = time.strftime("%Y-%m-%d %H:%M:%S")
    fingerprint = sentence_fingerprint(sentences)
    index_version = make_index_version(built_at, fingerprint)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    chunk_lookup = {str(chunk["id"]): chunk for chunk in chunks}

    meta: dict[str, Any] = {
        "paper_id": paper_id,
        "index_version": index_version,
        "built_at": built_at,
        "fingerprint": fingerprint,
        "n_sentences": len(sentences),
        "n_chunks": len(chunks),
        "n_dropped": len(dropped_sentences),
        "drop_reasons": _count_reasons(dropped_sentences),
        "min_sentence_chars": min_sentence_chars,
        "embedded": (not skip_embed),
        "source": source,
    }

    if sentences_out:
        table = _write_sentence_table(
            Path(sentences_out),
            sentences,
            sentence_to_chunk,
            chunks,
            paper_id,
            dropped_sentences,
        )
        print("句表: %s" % table)

    if skip_embed:
        write_index_meta(output_path, meta)
        print("Skip embed: wrote %s (version=%s)" % (INDEX_META_FILENAME, index_version))
        print("  - paper_id: %s" % (paper_id or "(未指定)"))
        print("  - Chunks: %d" % len(chunks))
        print("  - Sentences: %d" % len(sentences))
        _print_drop_summary(dropped_sentences)
        return {
            "sentences": sentences,
            "sentence_to_chunk": sentence_to_chunk,
            "dropped_sentences": dropped_sentences,
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
        "dropped_sentences": dropped_sentences,
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
    _print_drop_summary(dropped_sentences)
    return index_data


def build_index_from_sentences(
    sentences_csv: str | Path,
    output_dir: str | Path,
    *,
    paper_id: str = "",
    chunks_file: str | Path | None = None,
    skip_embed: bool = False,
    overwrite_csv: bool = False,
    min_sentence_chars: int = 30,
    verbose: bool = True,
) -> dict[str, Any]:
    """以人工确认的句表为权威源建索引，默认不回写 CSV、不重新切 PDF。"""
    csv_path = Path(sentences_csv)
    paper_id = canonical_paper_id(paper_id)
    print("Loading sentences from: %s" % csv_path)
    kept, dropped = load_sentence_table(csv_path)
    csv_chunk_ids = {
        str(row.get("chunk_id") or "") for row in kept if str(row.get("chunk_id") or "")
    }
    extra = _load_best_chunks(paper_id, csv_chunk_ids, chunks_file)
    assembled = _assemble_from_sentence_rows(
        kept,
        dropped,
        paper_id=paper_id,
        extra_chunks=extra,
    )
    paper_id = assembled["paper_id"]
    if verbose:
        print(
            "Loaded %d kept / %d dropped sentences, %d chunks"
            % (len(assembled["sentences"]), len(dropped), len(assembled["chunks"]))
        )
    return _finalize_index(
        sentences=assembled["sentences"],
        sentence_to_chunk=assembled["sentence_to_chunk"],
        sentence_offset=assembled["sentence_offset"],
        chunk_sentences=assembled["chunk_sentences"],
        chunks=assembled["chunks"],
        dropped_sentences=assembled["dropped_sentences"],
        output_dir=output_dir,
        paper_id=paper_id,
        skip_embed=skip_embed,
        min_sentence_chars=min_sentence_chars,
        sentences_out=(csv_path if overwrite_csv else None),
        source="sentences_csv",
    )


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
    # 切块层跳过的行；与句级判决、清洗片段一起写进句表尾部，保证没有看不见的删除
    ingest_dropped: list[dict[str, Any]] = []
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
            dropped_lines=ingest_dropped,
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

    print("Extracting sentences...")
    sentences: list[str] = []
    sentence_to_chunk: list[str] = []
    sentence_offset: list[int] = []
    chunk_sentences: dict[str, list[str]] = {}
    dropped_sentences: list[dict[str, Any]] = list(ingest_dropped)

    for chunk in tqdm(chunks, desc="Processing chunks"):
        chunk_id = str(chunk["id"])
        own_sentences: list[str] = []
        for verdict in split_english_sentences_detailed(
            chunk.get("text", ""), min_chars=min_sentence_chars
        ):
            # 清洗阶段截掉的片段也要留痕，否则截断就是一次看不见的删除
            for fragment in verdict.removed:
                dropped_sentences.append(
                    {"chunk_id": chunk_id, "text": fragment, "reason": "cleaned_fragment"}
                )
            if verdict.keep:
                own_sentences.append(verdict.text)
                continue
            dropped_sentences.append(
                {
                    "chunk_id": chunk_id,
                    "text": verdict.text or verdict.original,
                    "reason": verdict.reason,
                }
            )
        chunk_sentences[chunk_id] = own_sentences
        for position, sentence in enumerate(own_sentences):
            sentences.append(sentence)
            sentence_to_chunk.append(chunk_id)
            sentence_offset.append(position)

    print("Total sentences: %d (筛掉 %d 句，见句表末尾)" % (len(sentences), len(dropped_sentences)))
    if not sentences:
        raise ValueError("没有抽出任何句子，请检查 PDF 解析结果或降低 --min-sentence-chars")

    return _finalize_index(
        sentences=sentences,
        sentence_to_chunk=sentence_to_chunk,
        sentence_offset=sentence_offset,
        chunk_sentences=chunk_sentences,
        chunks=chunks,
        dropped_sentences=dropped_sentences,
        output_dir=output_dir,
        paper_id=paper_id,
        skip_embed=skip_embed,
        min_sentence_chars=min_sentence_chars,
        sentences_out=sentences_out,
        source="pdf" if papers_dir else "chunks",
    )


def _read_index_meta(layout) -> dict[str, Any]:
    meta_file = layout.index_meta_file
    if not meta_file.is_file():
        return {}
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return meta if isinstance(meta, dict) else {}


def ensure_index(
    paper_id: str,
    *,
    rebuild: bool = False,
    skip_embed: bool = False,
    pdf: str | Path | None = None,
    target_chars: int = 1200,
    min_sentence_chars: int = 30,
    verbose: bool = True,
    from_sentences: bool | str | Path = False,
) -> dict[str, Any]:
    """陌生论文才建库；已有完整索引则直接复用。

    ``from_sentences=True`` 时以句表为权威源，不重新切 PDF、默认不覆盖 CSV。
    """
    paper_id = canonical_paper_id(paper_id)
    if not paper_id:
        raise ValueError("ensure_index 需要 paper_id")
    layout = layout_for(paper_id, pdf=pdf)

    sentences_csv: Path | None = None
    if from_sentences:
        if from_sentences is True:
            sentences_csv = layout.sentences_csv
        else:
            sentences_csv = Path(from_sentences)
        if not sentences_csv.is_file():
            raise FileNotFoundError("句表不存在，无法从 CSV 建库: %s" % sentences_csv)

    if (
        not rebuild
        and not skip_embed
        and is_index_ready(paper_id, index_dir=layout.index_dir)
    ):
        meta = _read_index_meta(layout)
        if sentences_csv is not None:
            kept, _dropped = load_sentence_table(sentences_csv)
            csv_fp = sentence_fingerprint([str(row["text"]) for row in kept])
            if str(meta.get("fingerprint") or "") != csv_fp:
                print("句表已变，按 CSV 重建索引: %s" % sentences_csv)
            else:
                print("复用已有索引（与句表指纹一致）: %s" % layout.index_dir)
                return {
                    "reused": True,
                    "paper_id": paper_id,
                    "index_dir": str(layout.index_dir),
                    "chunks_file": str(layout.chunks_file),
                    "index_version": str(meta.get("index_version") or ""),
                    "sentences_csv": str(sentences_csv),
                }
        else:
            print("复用已有索引: %s" % layout.index_dir)
            return {
                "reused": True,
                "paper_id": paper_id,
                "index_dir": str(layout.index_dir),
                "chunks_file": str(layout.chunks_file),
                "index_version": str(meta.get("index_version") or ""),
            }

    if skip_embed and is_index_ready(paper_id, index_dir=layout.index_dir) and not rebuild:
        if sentences_csv is None:
            print("已有完整索引，--skip-embed 不覆盖 pkl，仅确认句表路径: %s" % layout.sentences_csv)
            return {
                "reused": True,
                "paper_id": paper_id,
                "index_dir": str(layout.index_dir),
                "skipped_embed_preview": True,
            }

    if sentences_csv is not None:
        print("为 %s 从句表建索引 <- %s" % (paper_id, sentences_csv))
        result = build_index_from_sentences(
            sentences_csv,
            layout.index_dir,
            paper_id=paper_id,
            chunks_file=layout.chunks_file,
            skip_embed=skip_embed,
            overwrite_csv=False,
            min_sentence_chars=min_sentence_chars,
            verbose=verbose,
        )
    else:
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
    if sentences_csv is not None:
        result["sentences_csv"] = str(sentences_csv)
    return result
