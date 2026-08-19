"""按 paper_id 分库：注册表、路径、拒混库、已有索引则复用。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_retrieval_adaptor import write_sample_pdf  # noqa: E402

from retrieval_adaptor.index_builder import build_index, ensure_index
from retrieval_adaptor.index_store import write_index_meta
from retrieval_adaptor.paper_registry import (
    canonical_paper_id,
    infer_ids,
    is_index_ready,
    layout_for,
    load_registry,
)
from retrieval_adaptor.pdf_ingest import ingest_papers


def test_canonical_paper_id_from_long_stem():
    assert canonical_paper_id("P001") == "P001"
    assert canonical_paper_id("P001_2025_NatPlants_cucurbits-KNOX1-ovary") == "P001"
    assert canonical_paper_id("data/papers/P012.pdf") == "P012"
    assert canonical_paper_id("watanabe2025") == "watanabe2025"


def test_infer_ids_from_article_and_paper():
    paper_id, article_id = infer_ids(
        "data/articles/high_quality/P001_A001_黄瓜.md",
        "P001_2025_NatPlants.pdf",
    )
    assert paper_id == "P001"
    assert article_id == "A001"


def test_load_registry_and_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANTSCI_ROOT", str(tmp_path))
    papers = tmp_path / "data" / "papers"
    papers.mkdir(parents=True)
    pdf = papers / "P009.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    (papers / "papers_index.json").write_text(
        json.dumps(
            {
                "P009": {
                    "title": "Demo",
                    "pdf": "P009.pdf",
                    "articles": [{"id": "A001", "type": "high_quality", "path": "articles/x.md"}],
                }
            }
        ),
        encoding="utf-8",
    )

    registry = load_registry()
    assert "P009" in registry
    assert registry["P009"].title == "Demo"
    assert registry["P009"].articles[0].article_id == "A001"

    layout = layout_for("P009")
    assert layout.paper_id == "P009"
    assert layout.pdf == pdf.resolve()
    assert layout.index_dir == tmp_path / "data" / "index" / "P009"
    assert layout.chunks_file == tmp_path / "data" / "corpus" / "P009" / "chunks.json"
    assert layout.sentences_csv.name == "P009_sentences.csv"


def test_is_index_ready_requires_matching_paper(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANTSCI_ROOT", str(tmp_path))
    layout = layout_for("P010")
    layout.index_dir.mkdir(parents=True)
    (layout.index_dir / "sentence_index.pkl").write_bytes(b"pkl")
    write_index_meta(
        layout.index_dir,
        {"paper_id": "P010", "embedded": True, "index_version": "t#abc"},
    )
    assert is_index_ready("P010")
    write_index_meta(
        layout.index_dir,
        {"paper_id": "P011", "embedded": True},
    )
    assert not is_index_ready("P010")


def test_ensure_index_reuses_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANTSCI_ROOT", str(tmp_path))
    papers = tmp_path / "data" / "papers"
    papers.mkdir(parents=True)
    (papers / "P008.pdf").write_bytes(b"%PDF-1.4")
    (papers / "papers_index.json").write_text(
        json.dumps({"P008": {"pdf": "P008.pdf"}}), encoding="utf-8"
    )
    layout = layout_for("P008")
    layout.index_dir.mkdir(parents=True)
    (layout.index_dir / "sentence_index.pkl").write_bytes(b"pkl")
    write_index_meta(
        layout.index_dir,
        {"paper_id": "P008", "embedded": True, "index_version": "keep-me"},
    )

    result = ensure_index("P008")
    assert result["reused"] is True
    assert result["index_version"] == "keep-me"


def test_build_index_from_sentences_csv_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANTSCI_ROOT", str(tmp_path))
    from retrieval_adaptor.index_builder import build_index_from_sentences, load_sentence_table

    csv_path = tmp_path / "P007_sentences.csv"
    csv_path.write_text(
        "sentence_id,chunk_id,text,paper_id,status,drop_reason\n"
        "0,0,Alpha sentence is long enough for indexing here.,P007,kept,\n"
        "1,0,Beta sentence is long enough for indexing here too.,P007,kept,\n"
        ",0,noise fragment that should stay dropped,P007,dropped,front_matter\n",
        encoding="utf-8",
    )
    original = csv_path.read_text(encoding="utf-8")
    index_dir = tmp_path / "index"
    result = build_index_from_sentences(
        csv_path,
        index_dir,
        paper_id="P007",
        skip_embed=True,
    )
    assert result["reused"] is False
    assert len(result["sentences"]) == 2
    assert result["sentences"][0].startswith("Alpha")
    assert len(result["dropped_sentences"]) == 1
    assert result["dropped_sentences"][0]["reason"] == "front_matter"
    assert csv_path.read_text(encoding="utf-8") == original
    kept, dropped = load_sentence_table(csv_path)
    assert [row["sentence_id"] for row in kept] == [0, 1]
    assert len(dropped) == 1
    meta = json.loads((index_dir / "index_meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == "sentences_csv"
    assert meta["n_sentences"] == 2
    assert meta["embedded"] is False


def test_build_index_rejects_mixed_paper_ids(tmp_path):
    chunks = tmp_path / "chunks.json"
    chunks.write_text(
        json.dumps(
            [
                {"id": "0", "text": "Alpha sentence is long enough for indexing here.", "paper_id": "P001"},
                {"id": "1", "text": "Beta sentence is long enough for indexing here.", "paper_id": "P002"},
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="混有多篇"):
        build_index(
            chunks_file=chunks,
            output_dir=tmp_path / "index",
            skip_embed=True,
        )


def test_ingest_rejects_multiple_pdfs_when_not_allowed(tmp_path):
    folder = tmp_path / "papers"
    folder.mkdir()
    (folder / "P001.pdf").write_bytes(b"%PDF-1.4")
    (folder / "P002.pdf").write_bytes(b"%PDF-1.4")
    with pytest.raises(ValueError, match="一篇"):
        ingest_papers(folder, allow_multiple=False, verbose=False)


def test_ingest_uses_short_paper_id_from_filename(tmp_path):
    pdf = tmp_path / "P001_2025_NatPlants_demo.pdf"
    write_sample_pdf(pdf)
    chunks = ingest_papers(pdf, target_chars=100, verbose=False)
    assert chunks
    assert {chunk["paper_id"] for chunk in chunks} == {"P001"}

    chunks = ingest_papers(pdf, target_chars=100, verbose=False, paper_id="P099")
    assert {chunk["paper_id"] for chunk in chunks} == {"P099"}
