"""论文注册表与按 paper_id 分库的路径约定。

权威清单：仓库根目录 ``data/papers/papers_index.json``。
一篇论文对应一座索引，禁止把多篇 PDF 打进同一套 sentence_id。

目录约定（均相对仓库根）::

    data/papers/papers_index.json
    data/papers/P001.pdf                  # 或注册表 pdf 字段指向的文件
    data/corpus/P001/chunks.json          # 中间产物，可再生成
    data/index/P001/sentence_index.pkl    # 该篇句向量
    data/index/P001/index_meta.json       # paper_id / 版本 / 是否已 embed
    data/annotations/P001/P001_sentences.csv
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .index_store import INDEX_FILENAME, INDEX_META_FILENAME, read_index_meta

PAPER_ID_RE = re.compile(r"(P\d+)", re.I)
ARTICLE_ID_RE = re.compile(r"(A\d+)", re.I)
PAIR_ID_RE = re.compile(r"(P\d+)[_\-]?(A\d+)", re.I)


def workspace_root() -> Path:
    """仓库根目录（``PlantSci_Hallu/``），可用 ``PLANTSCI_ROOT`` 覆盖。"""
    env = (os.environ.get("PLANTSCI_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return workspace_root() / "data"


def papers_index_path() -> Path:
    return data_dir() / "papers" / "papers_index.json"


def canonical_paper_id(raw: str) -> str:
    """从 ``P001`` / ``P001_2025_...`` / 路径中抽出短 id；抽不到则原样返回。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    probe = Path(text).stem if ("/" in text or "\\" in text or text.lower().endswith(".pdf")) else text
    match = PAPER_ID_RE.search(probe) or PAPER_ID_RE.search(text)
    return match.group(1).upper() if match else text


def infer_ids(*paths: str | Path) -> tuple[str, str]:
    """从文章/论文路径猜测 ``(paper_id, article_id)``。"""
    paper_id, article_id = "", ""
    for raw in paths:
        if not raw:
            continue
        stem = Path(raw).stem
        pair = PAIR_ID_RE.search(stem)
        if pair:
            paper_id = pair.group(1).upper()
            article_id = pair.group(2).upper()
            break
        if not paper_id:
            found = PAPER_ID_RE.search(stem)
            if found:
                paper_id = found.group(1).upper()
        if not article_id:
            found = ARTICLE_ID_RE.search(stem)
            if found:
                article_id = found.group(1).upper()
    return paper_id, article_id


@dataclass(frozen=True)
class ArticleRef:
    article_id: str
    type: str = ""
    path: str = ""
    source: str = ""


@dataclass(frozen=True)
class PaperRecord:
    paper_id: str
    title: str = ""
    doi: str = ""
    journal: str = ""
    year: str = ""
    pdf: str = ""
    articles: tuple[ArticleRef, ...] = field(default_factory=tuple)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaperLayout:
    """一篇论文的全部落盘位置。"""

    paper_id: str
    pdf: Path
    chunks_file: Path
    index_dir: Path
    sentences_csv: Path
    annotations_dir: Path

    @property
    def index_file(self) -> Path:
        return self.index_dir / INDEX_FILENAME

    @property
    def index_meta_file(self) -> Path:
        return self.index_dir / INDEX_META_FILENAME


def load_registry(path: Path | None = None) -> dict[str, PaperRecord]:
    target = Path(path) if path is not None else papers_index_path()
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    records: dict[str, PaperRecord] = {}
    for key, value in raw.items():
        paper_id = canonical_paper_id(key) or str(key).strip().upper()
        if not paper_id:
            continue
        payload = value if isinstance(value, dict) else {}
        articles = []
        for item in payload.get("articles") or []:
            if not isinstance(item, dict):
                continue
            article_id = str(item.get("id") or item.get("article_id") or "").strip().upper()
            if not article_id:
                continue
            articles.append(
                ArticleRef(
                    article_id=article_id,
                    type=str(item.get("type") or ""),
                    path=str(item.get("path") or item.get("source_path") or ""),
                    source=str(item.get("source") or ""),
                )
            )
        records[paper_id] = PaperRecord(
            paper_id=paper_id,
            title=str(payload.get("title") or ""),
            doi=str(payload.get("doi") or ""),
            journal=str(payload.get("journal") or ""),
            year=str(payload.get("year") or ""),
            pdf=str(payload.get("pdf") or ""),
            articles=tuple(articles),
            extra={
                key_name: payload[key_name]
                for key_name in payload
                if key_name
                not in {"title", "doi", "journal", "year", "pdf", "articles"}
            },
        )
    return records


def get_paper(paper_id: str) -> PaperRecord | None:
    paper_id = canonical_paper_id(paper_id)
    if not paper_id:
        return None
    return load_registry().get(paper_id)


def resolve_pdf(paper_id: str, *, pdf: str | Path | None = None) -> Path:
    """按注册表与约定位置解析 PDF，找不到则报错。"""
    paper_id = canonical_paper_id(paper_id)
    candidates: list[Path] = []
    if pdf:
        given = Path(pdf)
        if not given.is_absolute():
            candidates.append((data_dir() / "papers" / given).resolve())
            candidates.append((workspace_root() / given).resolve())
            candidates.append(given.resolve())
        else:
            candidates.append(given)
    record = get_paper(paper_id)
    if record and record.pdf:
        listed = Path(record.pdf)
        if listed.is_absolute():
            candidates.append(listed)
        else:
            candidates.append((data_dir() / "papers" / listed).resolve())
            candidates.append((workspace_root() / listed).resolve())
    papers_dir = data_dir() / "papers"
    candidates.append(papers_dir / ("%s.pdf" % paper_id))
    candidates.extend(sorted(papers_dir.glob("%s*.pdf" % paper_id)))
    candidates.extend(sorted(workspace_root().glob("%s*.pdf" % paper_id)))

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    raise FileNotFoundError(
        "找不到 %s 的 PDF。请在 data/papers/papers_index.json 填写 pdf，"
        "或把文件放到 data/papers/%s.pdf" % (paper_id, paper_id)
    )


def layout_for(paper_id: str, *, pdf: str | Path | None = None) -> PaperLayout:
    paper_id = canonical_paper_id(paper_id)
    if not paper_id:
        raise ValueError("paper_id 不能为空")
    annotations_dir = data_dir() / "annotations" / paper_id
    try:
        pdf_path = resolve_pdf(paper_id, pdf=pdf)
    except FileNotFoundError:
        pdf_path = data_dir() / "papers" / ("%s.pdf" % paper_id)
    return PaperLayout(
        paper_id=paper_id,
        pdf=pdf_path,
        chunks_file=data_dir() / "corpus" / paper_id / "chunks.json",
        index_dir=data_dir() / "index" / paper_id,
        sentences_csv=annotations_dir / ("%s_sentences.csv" % paper_id),
        annotations_dir=annotations_dir,
    )


def is_index_ready(paper_id: str, *, index_dir: Path | None = None) -> bool:
    """已有该篇的完整向量索引（pkl + embedded + paper_id 一致或未写）。"""
    paper_id = canonical_paper_id(paper_id)
    directory = Path(index_dir) if index_dir is not None else layout_for(paper_id).index_dir
    if not (directory / INDEX_FILENAME).is_file():
        return False
    meta = read_index_meta(directory)
    if meta and meta.get("embedded") is False:
        return False
    meta_pid = canonical_paper_id(str(meta.get("paper_id") or ""))
    if meta_pid and meta_pid != paper_id:
        return False
    return True


def apply_layout(config: Any, paper_id: str, *, pdf: str | Path | None = None) -> Any:
    """把 RetrievalConfig 的 index_dir / chunks_file 指到该篇分库。"""
    layout = layout_for(paper_id, pdf=pdf)
    config.paper_id = layout.paper_id
    config.index_dir = layout.index_dir
    config.chunks_file = layout.chunks_file
    return config
