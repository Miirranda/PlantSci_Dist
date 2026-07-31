"""阶段 2 跨语言检索模块的离线测试：全部用 mock 驱动，不发真实网络请求。

覆盖重点是那些线上不好复现的分支：双阈值三分支、候选去重、段落上下文还原、
术语缓存命中后不调用 API、固定 JSON 输出结构。
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_client.schemas import RerankItem, RerankResult
from arag.agent.prompt import build_system_prompt, parse_final_answer
from arag.core.context import AgentContext
from retrieval_adaptor import (
    VERDICT_INCONCLUSIVE,
    VERDICT_NO_EVIDENCE,
    VERDICT_SUPPORTED,
    BilingualTerm,
    Candidate,
    DualThresholdGate,
    EvidenceBoard,
    IndexStore,
    PaperMetadata,
    ParagraphContext,
    RetrievalOutput,
    ThresholdConfig,
)
from retrieval_adaptor.thresholds import (
    CONTINUE_SEARCH,
    STOP_HIGH_THRESHOLD,
    STOP_LOW_THRESHOLD,
    STOP_NO_CANDIDATE,
    STOP_ROUND_LIMIT,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------- 测试脚手架


def load_sample_chunks() -> list[dict]:
    return json.loads((FIXTURES / "sample_chunks.json").read_text(encoding="utf-8"))


def build_fake_index(tmp_path: Path, chunks: list[dict], *, drop_extras: bool = False) -> Path:
    """用单位向量造一个可预测的索引：第 i 句的向量是第 i 个基向量。

    这样查询 e_k 时 top1 必然是第 k 句，检索逻辑的正确性可以被精确断言。
    """
    from scripts.build_index import split_sentences

    sentences: list[str] = []
    sentence_to_chunk: list[str] = []
    sentence_offset: list[int] = []
    chunk_sentences: dict[str, list[str]] = {}

    for chunk in chunks:
        own = split_sentences(chunk["text"], min_chars=30)
        chunk_sentences[str(chunk["id"])] = own
        for position, sentence in enumerate(own):
            sentences.append(sentence)
            sentence_to_chunk.append(str(chunk["id"]))
            sentence_offset.append(position)

    embeddings = np.eye(len(sentences), dtype=np.float32)
    payload = {
        "sentences": sentences,
        "embeddings": embeddings,
        "sentence_to_chunk": sentence_to_chunk,
        "chunks": {str(c["id"]): c for c in chunks},
        "model_name": "BAAI/bge-m3",
    }
    if not drop_extras:
        payload.update(
            {
                "sentence_offset": sentence_offset,
                "chunk_sentences": chunk_sentences,
                "provider": "siliconflow",
                "dim": len(sentences),
            }
        )

    index_dir = tmp_path / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    with (index_dir / "sentence_index.pkl").open("wb") as handle:
        pickle.dump(payload, handle)
    return index_dir


class FakeSiliconFlow:
    """按"文档里出现的关键词"给分的假重排器，行为可预测。"""

    def __init__(self, query_vector: np.ndarray, score_map: dict[str, float], default: float = 0.05):
        self.query_vector = query_vector
        self.score_map = score_map
        self.default = default
        self.embed_calls = 0
        self.rerank_calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.embed_calls += 1
        return list(self.query_vector)

    def rerank(self, query, documents, top_n=None, **kwargs) -> RerankResult:
        self.rerank_calls += 1
        items = []
        for index, document in enumerate(documents):
            score = self.default
            for keyword, value in self.score_map.items():
                if keyword.lower() in document.lower():
                    score = max(score, value)
            items.append(RerankItem(index=index, score=score, document=document))
        items.sort(key=lambda item: item.score, reverse=True)
        if top_n:
            items = items[:top_n]
        return RerankResult(items=items, model="BAAI/bge-reranker-v2-m3")


class FakeLLM:
    """记录调用次数的假 Qwen，用于验证缓存是否真的省掉了 API 调用。"""

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def extract_json(self, prompt, system=None, strict=False) -> dict:
        self.calls += 1
        return self.payload


# ---------------------------------------------------------------- 双阈值规则


def test_gate_high_threshold_stops_with_supported():
    gate = DualThresholdGate(ThresholdConfig(high=0.7, low=0.3, min_hits=1))
    decision = gate.evaluate([0.82, 0.41, 0.05])

    assert decision.verdict == VERDICT_SUPPORTED
    assert decision.should_stop is True
    assert decision.reason == STOP_HIGH_THRESHOLD
    assert decision.strong_hits == 1
    assert "STOP" in decision.describe()


def test_gate_requires_enough_strong_hits():
    """min_hits=2 时单条强证据不足以终止。"""
    gate = DualThresholdGate(ThresholdConfig(high=0.7, low=0.3, min_hits=2))
    decision = gate.evaluate([0.91, 0.44])

    assert decision.verdict == VERDICT_INCONCLUSIVE
    assert decision.should_stop is False
    assert decision.reason == CONTINUE_SEARCH
    assert "强证据" in decision.describe()


def test_default_min_hits_is_two():
    """默认配置要求至少两条强证据才 STOP，以适度拉宽多轮。"""
    from retrieval_adaptor.config import ThresholdConfig as LiveConfig

    assert LiveConfig().min_hits == 2
    gate = DualThresholdGate()
    one_strong = gate.evaluate([0.95, 0.40])
    assert one_strong.should_stop is False
    two_strong = gate.evaluate([0.95, 0.80])
    assert two_strong.should_stop is True
    assert two_strong.reason == STOP_HIGH_THRESHOLD


def test_gate_all_below_low_stops_with_no_evidence():
    gate = DualThresholdGate(ThresholdConfig(high=0.7, low=0.3))
    decision = gate.evaluate([0.21, 0.08, 0.01])

    assert decision.verdict == VERDICT_NO_EVIDENCE
    assert decision.should_stop is True
    assert decision.reason == STOP_LOW_THRESHOLD
    assert "无支撑证据" in decision.describe()


def test_gate_middle_band_continues():
    gate = DualThresholdGate(ThresholdConfig(high=0.7, low=0.3))
    decision = gate.evaluate([0.55, 0.33])

    assert decision.verdict == VERDICT_INCONCLUSIVE
    assert decision.should_stop is False
    assert "CONTINUE" in decision.describe()


def test_gate_middle_band_stops_at_round_limit():
    gate = DualThresholdGate(ThresholdConfig(high=0.7, low=0.3))
    decision = gate.evaluate([0.55], round_index=3, max_rounds=3)

    assert decision.should_stop is True
    assert decision.reason == STOP_ROUND_LIMIT
    assert decision.verdict == VERDICT_INCONCLUSIVE


def test_gate_no_candidate():
    decision = DualThresholdGate().evaluate([])

    assert decision.reason == STOP_NO_CANDIDATE
    assert decision.verdict == VERDICT_NO_EVIDENCE
    assert decision.should_stop is True


@pytest.mark.parametrize(
    "score,expected",
    [(0.95, VERDICT_SUPPORTED), (0.70, VERDICT_SUPPORTED), (0.5, VERDICT_INCONCLUSIVE),
     (0.30, VERDICT_INCONCLUSIVE), (0.29, VERDICT_NO_EVIDENCE)],
)
def test_gate_labels(score, expected):
    assert DualThresholdGate(ThresholdConfig(high=0.7, low=0.3)).label(score) == expected


@pytest.mark.parametrize(
    "config",
    [
        ThresholdConfig(high=0.3, low=0.7),
        ThresholdConfig(high=0.5, low=0.5),
        ThresholdConfig(high=1.5, low=0.3),
        ThresholdConfig(high=0.7, low=0.3, min_hits=0),
    ],
)
def test_invalid_threshold_config_rejected(config):
    with pytest.raises(ValueError):
        DualThresholdGate(config)


# ---------------------------------------------------------------- Schema


def test_retrieval_output_json_shape():
    output = RetrievalOutput(
        claim_zh="检索增强可以降低幻觉率",
        verdict=VERDICT_SUPPORTED,
        stop_reason=STOP_HIGH_THRESHOLD,
        bilingual_terms=[BilingualTerm(zh="幻觉", en="hallucination", aliases=["hallucinate"])],
    )
    data = output.to_dict()

    assert set(data) == {
        "schema_version",
        "claim_zh",
        "verdict",
        "stop_reason",
        "bilingual_terms",
        "evidence_count",
        "evidences",
        "stats",
    }
    assert data["evidence_count"] == 0
    # 中文不能被转义成 \uXXXX，下游要能直接读
    assert "检索增强" in output.to_json()


def test_paper_metadata_accepts_semicolon_authors():
    paper = PaperMetadata.from_dict({"authors": "Wei Chen; Maria Lopez", "title": "T"})
    assert paper.authors == ["Wei Chen", "Maria Lopez"]
    assert "Wei Chen et al." in paper.citation()


def test_paper_metadata_missing_fields_become_empty_strings():
    paper = PaperMetadata.from_dict(None)
    assert paper.to_dict()["title"] == ""
    assert paper.to_dict()["authors"] == []


def test_bilingual_term_search_terms_dedupe():
    term = BilingualTerm(zh="大语言模型", en="large language model", aliases=["LLM", "large language model"])
    assert term.search_terms() == ["large language model", "LLM"]


def test_paragraph_context_full_text():
    context = ParagraphContext(prev_text="A.", target_text="B.", next_text="C.")
    assert context.full_text() == "A. B. C."


# ---------------------------------------------------------------- 证据看板


def test_board_dedupes_and_keeps_highest_score():
    board = EvidenceBoard("claim", DualThresholdGate())
    board.add_candidates([Candidate(chunk_id="1", sentence="s", sentence_index=0, rerank_score=0.4)])
    board.add_candidates([Candidate(chunk_id="1", sentence="s", sentence_index=0, rerank_score=0.9)])
    board.add_candidates([Candidate(chunk_id="1", sentence="t", sentence_index=1, rerank_score=0.2)])

    assert len(board.candidates) == 2
    assert board.best_for_chunk("1").rerank_score == 0.9
    assert board.scores() == [0.9, 0.2]
    assert board.search_rounds == 3


def test_board_output_reports_thresholds_and_rounds():
    board = EvidenceBoard(
        "claim", DualThresholdGate(ThresholdConfig(high=0.7, low=0.3, min_hits=1))
    )
    board.add_candidates([Candidate(chunk_id="1", sentence="s", sentence_index=0, rerank_score=0.85)])
    board.record_decision(board.gate.evaluate(board.scores()))

    output = board.build_output([])
    assert output.verdict == VERDICT_SUPPORTED
    assert output.stop_reason == STOP_HIGH_THRESHOLD
    assert output.stats["thresholds"]["high"] == 0.7
    assert output.stats["search_rounds"] == 1


def test_board_output_stop_reason_is_consistent_with_verdict():
    """回归：verdict 与 stop_reason 曾出自不同判定，出现过 SUPPORTED + continue_search。"""
    board = EvidenceBoard(
        "claim", DualThresholdGate(ThresholdConfig(high=0.7, low=0.3, min_hits=1))
    )
    # 只加候选、不记录判定（read_chunk 补分就是这种情形）
    board.add_candidates([Candidate(chunk_id="1", sentence="s", sentence_index=0, rerank_score=0.9)])

    output = board.build_output([])
    assert output.verdict == VERDICT_SUPPORTED
    assert output.stop_reason == STOP_HIGH_THRESHOLD

    empty = EvidenceBoard("claim", DualThresholdGate())
    assert empty.build_output([]).stop_reason == STOP_NO_CANDIDATE


def test_rescoring_does_not_count_as_search_round():
    board = EvidenceBoard("claim", DualThresholdGate())
    board.add_candidates([Candidate(chunk_id="1", sentence="a", sentence_index=0)])
    board.add_candidates(
        [Candidate(chunk_id="2", sentence="b", sentence_index=1)], count_as_round=False
    )
    assert board.search_rounds == 1
    assert len(board.candidates) == 2


# ---------------------------------------------------------------- 索引


def test_index_store_search_and_context(tmp_path):
    chunks = load_sample_chunks()
    index_dir = build_fake_index(tmp_path, chunks)
    store = IndexStore(index_dir)

    assert len(store) > 0
    assert store.model_name == "BAAI/bge-m3"

    # 查询第 3 个基向量，top1 必然是第 3 句
    query = np.zeros(len(store), dtype=np.float32)
    query[3] = 1.0
    hits = store.search(query, top_n=2)
    assert hits[0][0] == 3
    assert hits[0][1] == pytest.approx(1.0, abs=1e-5)

    prev_text, target, next_text = store.paragraph_context(3, window=1)
    assert target == store.sentence(3)
    # 第 3 句不是所属 chunk 的首句时应该有前文
    assert isinstance(prev_text, str) and isinstance(next_text, str)


def test_index_store_global_index_roundtrip(tmp_path):
    store = IndexStore(build_fake_index(tmp_path, load_sample_chunks()))
    for index in range(min(5, len(store))):
        chunk_id = store.chunk_id_of(index)
        offset = store.sentence_offset[index]
        assert store.global_index(chunk_id, offset) == index
    assert store.global_index("no-such-chunk", 0) == -1


def test_index_store_rebuilds_missing_fields_for_legacy_index(tmp_path):
    """旧索引没有 sentence_offset / chunk_sentences，应能现场重建。"""
    store = IndexStore(build_fake_index(tmp_path, load_sample_chunks(), drop_extras=True))
    assert store.sentence_offset
    assert store.chunk_sentences
    assert store.global_index(store.chunk_id_of(0), 0) == 0


# ---------------------------------------------------------------- 分句


@pytest.mark.parametrize(
    "text,expected_count",
    [
        # et al. 不应该被当作句子边界
        ("Prior work by Vaswani et al. 2017 introduced the transformer architecture for translation.", 1),
        # e.g. 同理
        ("Several encoders, e.g. BERT and RoBERTa, are pre-trained on large unlabeled corpora today.", 1),
        ("The model reaches 92.4 percent accuracy on the benchmark we constructed for evaluation.", 1),
    ],
)
def test_split_sentences_protects_abbreviations(text, expected_count):
    from scripts.build_index import split_sentences

    assert len(split_sentences(text)) == expected_count


def test_split_sentences_separates_real_boundaries():
    from scripts.build_index import split_sentences

    text = (
        "Retrieval reduces hallucination substantially in our experiments. "
        "The reranking stage contributes an additional six accuracy points."
    )
    assert len(split_sentences(text)) == 2


def test_split_sentences_drops_short_fragments():
    from scripts.build_index import split_sentences

    assert split_sentences("Table 2. 41.2 92.") == []


# ---------------------------------------------------------------- PDF / 中文预处理


def test_split_chinese_sentences():
    from retrieval_adaptor.pdf_ingest import split_chinese_sentences

    text = "检索增强生成可以降低幻觉率。重排序模型进一步提升了精度！那么代价是什么呢？短句"
    sentences = split_chinese_sentences(text, min_chars=6)
    assert len(sentences) == 3
    assert sentences[0].startswith("检索增强")


@pytest.mark.parametrize(
    "line,expected",
    [
        ("敲除KNAT2-like1导致下位子房转变为上位子房", True),
        ("本研究首先构建了502个被子植物物种进化树", True),
        ("图1 黄瓜单性花与子房下位的进化和发育", False),
        ("（图2b）黄瓜花发育过程空间转录组", False),
        ("表3 各细胞聚类的标记基因", False),
        ("1.黄瓜花、子房的进化和发育", False),
        ("公众号：iPlants", False),
        ("Nature Plants 2025", False),
        ("参考文献", False),
    ],
)
def test_is_claim_like_filters_captions_and_headings(line, expected):
    from retrieval_adaptor.pdf_ingest import is_claim_like

    assert is_claim_like(line, min_chars=8) is expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Received: 1 October 2024", True),
        ("Accepted: 17 January 2025", True),
        ("Check for updates", True),
        ("Wenwen Shao", True),
        ("1Institute of Vegetables and Flowers, Chinese Academy of Agricultural Sciences", True),
        ("e-mail: yangxueyong@caas.cn", True),
        ("Inferior ovaries are located below the attachment points of the sepals.", False),
        ("The cucumber floral meristem enlarges from the perimeter.", False),
    ],
)
def test_front_matter_noise_is_excluded_from_body(line, expected):
    """回归：作者名与投稿日期曾被拼进正文句子，污染证据。"""
    from retrieval_adaptor.pdf_ingest import _is_front_matter_noise

    assert _is_front_matter_noise(line) is expected


def test_wechat_claims_drop_non_claim_lines(tmp_path):
    from retrieval_adaptor.pdf_ingest import load_wechat_claims

    wechat = tmp_path / "wechat"
    wechat.mkdir()
    (wechat / "a.txt").write_text(
        "公众号：iPlants\n1.研究背景与进展概述\n敲除该基因后花托生长受阻并转为上位子房。\n"
        "图1 黄瓜单性花与子房下位的进化和发育\n",
        encoding="utf-8",
    )

    claims = load_wechat_claims(wechat, min_chars=8)
    assert len(claims) == 1
    assert claims[0]["claim_zh"].startswith("敲除该基因")


def test_detect_section_recognizes_numbered_and_named():
    from retrieval_adaptor.pdf_ingest import _detect_section

    assert _detect_section("3 Method") == "3 Method"
    assert _detect_section("4.1 Ablation Study") == "4.1 Ablation Study"
    assert _detect_section("Abstract") == "Abstract"
    assert _detect_section("we present a new approach in this paper") == ""


@pytest.mark.parametrize(
    "heading,expected",
    [
        ("References", True),
        ("6 References", True),
        ("Bibliography", True),
        ("Acknowledgments", True),
        ("Acknowledgements", True),
        ("4 Ablation", False),
        ("Introduction", False),
    ],
)
def test_is_stop_section(heading, expected):
    from retrieval_adaptor.pdf_ingest import _is_stop_section

    assert _is_stop_section(heading) is expected


def write_sample_pdf(path: Path) -> None:
    """用 PyMuPDF 生成一份结构接近真实论文的 PDF，用于验证解析链路。"""
    fitz = pytest.importorskip("fitz")

    doc = fitz.open()
    page = doc.new_page()
    # 标题用最大字号，解析器靠字号识别标题
    page.insert_text((60, 80), "Calibrated Reranking for Evidence Retrieval", fontsize=20)
    page.insert_text((60, 110), "Kenji Watanabe, Ana Silva", fontsize=11)
    page.insert_text((60, 130), "arXiv:2501.01234  doi:10.5555/tacl.2025.0042", fontsize=9)

    # 小标题用加粗大字号，与真实期刊排版一致（解析器靠字号/字重识别标题层级）
    y = 170
    for line, heading in [
        ("Abstract", True),
        ("We study cross-lingual evidence retrieval for hallucination detection tasks.", False),
        ("3 Method", True),
        ("A multilingual encoder maps the query and the document into one shared space.", False),
        ("The reranker then rescores every candidate passage returned by the retriever.", False),
        ("Removing the reranker drops precision at five from 0.71 down to 0.48 overall.", False),
        ("References", True),
        ("Chen et al. A cited paper that must not be indexed as evidence at all.", False),
    ]:
        if heading:
            page.insert_text((60, y), line, fontsize=13, fontname="hebo")
        else:
            page.insert_text((60, y), line, fontsize=10)
        y += 16

    doc.save(str(path))
    doc.close()


def test_ingest_papers_from_real_pdf(tmp_path):
    from retrieval_adaptor.pdf_ingest import ingest_papers

    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    write_sample_pdf(papers_dir / "watanabe2025.pdf")

    chunks = ingest_papers(papers_dir, target_chars=100, verbose=False)

    assert chunks
    assert all("id" in chunk and "text" in chunk for chunk in chunks), "必须兼容原生 chunk 格式"
    # 只保留必需元数据：标题、前两位作者、发表年份、来源文件
    assert "Calibrated Reranking" in chunks[0]["title"]
    assert chunks[0]["year"] == "2025", "应取出版年而非投稿年"
    assert chunks[0]["authors"] == ["Kenji Watanabe", "Ana Silva"]
    assert chunks[0]["source_file"] == "watanabe2025.pdf"
    # 参考文献不得入库
    assert all("must not be indexed" not in chunk["text"] for chunk in chunks)
    # 章节标签
    assert {chunk["section"] for chunk in chunks} & {"Abstract", "3 Method"}


def test_chunking_keeps_content_when_min_chars_exceeds_target(tmp_path):
    """回归：保留下限高于切块目标时，正文曾被整段静默丢弃。"""
    from retrieval_adaptor.pdf_ingest import ingest_papers

    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    write_sample_pdf(papers_dir / "paper.pdf")

    chunks = ingest_papers(papers_dir, target_chars=100, verbose=False)
    body = " ".join(chunk["text"] for chunk in chunks)
    assert "multilingual encoder" in body
    assert "Removing the reranker" in body


def test_pdf_metadata_sidecar_overrides_autodetection(tmp_path):
    """自动识别难免有偏差，同名 .meta.json 应能人工覆盖。"""
    from retrieval_adaptor.pdf_ingest import ingest_papers

    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    write_sample_pdf(papers_dir / "paper.pdf")
    (papers_dir / "paper.meta.json").write_text(
        json.dumps({"title": "Corrected Title", "year": "2099", "authors": ["A. Author"]}),
        encoding="utf-8",
    )

    chunks = ingest_papers(papers_dir, target_chars=100, verbose=False)
    assert chunks[0]["title"] == "Corrected Title"
    assert chunks[0]["year"] == "2099"
    assert chunks[0]["authors"] == ["A. Author"]


def test_ingest_papers_rejects_empty_directory(tmp_path):
    from retrieval_adaptor.pdf_ingest import ingest_papers

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        ingest_papers(empty)


def test_load_wechat_claims(tmp_path):
    from retrieval_adaptor.pdf_ingest import load_wechat_claims

    wechat = tmp_path / "wechat"
    wechat.mkdir()
    (wechat / "article.txt").write_text(
        "检索增强生成可以显著降低幻觉率。重排序在跨语言场景下尤其重要。太短",
        encoding="utf-8",
    )

    claims = load_wechat_claims(wechat, min_chars=8)
    assert len(claims) == 2
    assert claims[0]["claim_id"] == "article#0"
    assert claims[0]["source_file"] == "article.txt"


def test_paper_chunking_stops_at_references():
    from retrieval_adaptor.pdf_ingest import PaperDoc, split_paper_into_chunks

    body = " ".join(["This sentence is part of the method section."] * 12)
    tail = " ".join(["Chen et al. Some cited paper title here."] * 12)
    doc = PaperDoc(
        paper_id="p1",
        title="T",
        pages=[(1, "3 Method\n%s\n\nReferences\n%s" % (body, tail))],
    )
    chunks = split_paper_into_chunks(doc, target_chars=200, min_chars=50)

    assert chunks
    assert all("cited paper title" not in chunk["text"] for chunk in chunks)
    assert chunks[0]["section"] == "3 Method"
    assert chunks[0]["paper_id"] == "p1"


# ---------------------------------------------------------------- 提示词


def test_build_system_prompt_injects_thresholds():
    prompt = build_system_prompt(high=0.75, low=0.25, min_hits=2)
    assert "0.75" in prompt
    assert "0.25" in prompt
    assert "at least 2 passage" in prompt
    assert "bilingual_entity_mapper" in prompt


def test_parse_final_answer():
    answer = (
        "VERDICT: SUPPORTED\n"
        "EVIDENCE_CHUNKS: 1, 2\n"
        "PAPERS: Grounding Language Model Generation\n"
        "REASON: 论文报告幻觉率从 31.2% 降到 14.4%，\n相对降幅 53.8%。"
    )
    parsed = parse_final_answer(answer)

    assert parsed["verdict"] == "SUPPORTED"
    assert parsed["evidence_chunks"] == ["1", "2"]
    assert "53.8%" in parsed["reason"]


def test_parse_final_answer_handles_none_and_garbage():
    parsed = parse_final_answer("VERDICT: NO_EVIDENCE\nEVIDENCE_CHUNKS: NONE")
    assert parsed["evidence_chunks"] == []
    assert parse_final_answer("")["verdict"] == ""


# ---------------------------------------------------------------- 双语术语映射


def test_mapper_caches_document_and_skips_second_llm_call(tmp_path):
    from arag.tools.bilingual_entity_mapper import BilingualEntityMapperTool

    llm = FakeLLM({"terms": [{"zh": "幻觉", "en": "hallucination", "aliases": ["hallucinate"]}]})
    mapper = BilingualEntityMapperTool(llm=llm, cache_file=tmp_path / "terms.json")

    first = mapper.extract_from_text("大模型的幻觉问题")
    second = mapper.extract_from_text("大模型的幻觉问题")

    assert first == second
    assert llm.calls == 1, "同一句话应命中缓存，不再调用 API"
    assert (tmp_path / "terms.json").exists()


def test_mapper_reuses_cache_across_process_restart(tmp_path):
    from arag.tools.bilingual_entity_mapper import BilingualEntityMapperTool

    payload = {"terms": [{"zh": "重排序", "en": "reranking", "aliases": []}]}
    cache_file = tmp_path / "terms.json"

    first_llm = FakeLLM(payload)
    BilingualEntityMapperTool(llm=first_llm, cache_file=cache_file).extract_from_text("重排序效果")
    assert first_llm.calls == 1

    # 新实例重新读盘，应直接命中
    second_llm = FakeLLM(payload)
    terms = BilingualEntityMapperTool(llm=second_llm, cache_file=cache_file).extract_from_text("重排序效果")
    assert second_llm.calls == 0
    assert terms[0]["en"] == "reranking"


def test_mapper_translate_terms_hits_term_cache(tmp_path):
    from arag.tools.bilingual_entity_mapper import BilingualEntityMapperTool

    llm = FakeLLM({"terms": [{"zh": "注意力机制", "en": "attention mechanism", "aliases": []}]})
    mapper = BilingualEntityMapperTool(llm=llm, cache_file=tmp_path / "terms.json")

    mapper.translate_terms(["注意力机制"])
    assert llm.calls == 1
    mapper.translate_terms(["注意力机制"])
    assert llm.calls == 1, "术语级缓存应跨语句复用"


def test_mapper_falls_back_to_latin_tokens_when_llm_fails(tmp_path):
    from arag.tools.bilingual_entity_mapper import BilingualEntityMapperTool

    class BrokenLLM:
        def extract_json(self, *args, **kwargs):
            raise RuntimeError("LLM 不可用")

    mapper = BilingualEntityMapperTool(llm=BrokenLLM(), cache_file=tmp_path / "terms.json")
    terms = mapper.extract_from_text("我们在 FactCheck-QA 上评测了 BERT 模型")

    english = {term["en"] for term in terms}
    assert "FactCheck-QA" in english
    assert "BERT" in english


def test_mapper_search_terms_flattens_aliases():
    from arag.tools.bilingual_entity_mapper import BilingualEntityMapperTool

    terms = [
        {"zh": "大语言模型", "en": "large language model", "aliases": ["LLM"]},
        {"zh": "幻觉", "en": "hallucination", "aliases": ["LLM"]},
    ]
    assert BilingualEntityMapperTool.search_terms(terms) == [
        "large language model",
        "LLM",
        "hallucination",
    ]


def test_mapper_tool_output_is_valid_json(tmp_path):
    from arag.tools.bilingual_entity_mapper import BilingualEntityMapperTool

    llm = FakeLLM({"terms": [{"zh": "幻觉", "en": "hallucination", "aliases": []}]})
    mapper = BilingualEntityMapperTool(llm=llm, cache_file=tmp_path / "terms.json")

    result, log = mapper.execute(AgentContext(), chinese_text="幻觉问题")
    payload = json.loads(result)

    assert payload["keyword_search_terms"] == ["hallucination"]
    assert log["term_count"] == 1


# ---------------------------------------------------------------- 检索工具


def _make_semantic_tool(
    tmp_path,
    board,
    score_map,
    target_sentence_index=1,
    *,
    min_hits: int = 1,
    neighbor_window: int = 0,
    multi_query_from_terms: bool = False,
    diversity_lambda: float = 0.0,
):
    from arag.tools.semantic_search import SemanticSearchTool

    chunks = load_sample_chunks()
    index_dir = build_fake_index(tmp_path, chunks)
    store = IndexStore(index_dir)

    query = np.zeros(len(store), dtype=np.float32)
    query[target_sentence_index] = 1.0
    client = FakeSiliconFlow(query, score_map)

    tool = SemanticSearchTool(
        index_store=store,
        sf_client=client,
        gate=DualThresholdGate(ThresholdConfig(high=0.7, low=0.3, min_hits=min_hits)),
        board=board,
        default_top_k=10,
        recall_multiplier=4,
        neighbor_window=neighbor_window,
        multi_query_from_terms=multi_query_from_terms,
        diversity_lambda=diversity_lambda,
    )
    return tool, store, client


def test_semantic_search_reranks_and_reports_stop(tmp_path):
    board = EvidenceBoard(
        "检索增强能降低幻觉率",
        DualThresholdGate(ThresholdConfig(high=0.7, low=0.3, min_hits=1)),
    )
    tool, store, client = _make_semantic_tool(
        tmp_path, board, {"hallucination rate": 0.93}
    )

    result, log = tool.execute(AgentContext(), query="hallucination reduction", top_k=3)

    assert client.embed_calls == 1
    assert client.rerank_calls == 1
    assert "[RETRIEVAL DECISION]" in result
    assert log["verdict"] == VERDICT_SUPPORTED
    assert log["should_stop"] is True
    # 重排把含关键词的句子提到首位，而向量层的 top1 是别的句子
    assert "hallucination rate" in result.lower()
    assert board.scores()[0] == pytest.approx(0.93)


def test_semantic_search_reports_continue_in_middle_band(tmp_path):
    board = EvidenceBoard(
        "claim", DualThresholdGate(ThresholdConfig(high=0.7, low=0.3, min_hits=1))
    )
    tool, _, _ = _make_semantic_tool(tmp_path, board, {"hallucination": 0.52})

    result, log = tool.execute(AgentContext(), query="hallucination", top_k=3)

    assert log["verdict"] == VERDICT_INCONCLUSIVE
    assert log["should_stop"] is False
    assert "CONTINUE" in result


def test_semantic_search_reports_no_evidence_when_all_weak(tmp_path):
    board = EvidenceBoard(
        "claim", DualThresholdGate(ThresholdConfig(high=0.7, low=0.3, min_hits=1))
    )
    tool, _, _ = _make_semantic_tool(tmp_path, board, {})

    result, log = tool.execute(AgentContext(), query="great wall ming dynasty", top_k=3)

    assert log["verdict"] == VERDICT_NO_EVIDENCE
    assert log["should_stop"] is True
    assert "无支撑证据" in result


def test_read_chunk_emits_fixed_json_schema(tmp_path):
    from arag.tools.read_chunk import ReadChunkTool

    board = EvidenceBoard(
        "检索增强能把幻觉率降低一半",
        DualThresholdGate(ThresholdConfig(high=0.7, low=0.3, min_hits=1)),
    )
    semantic, store, client = _make_semantic_tool(tmp_path, board, {"hallucination rate": 0.93})
    context = AgentContext()
    semantic.execute(context, query="hallucination reduction", top_k=3)

    reader = ReadChunkTool(
        index_store=store,
        board=board,
        gate=semantic.gate,
        sf_client=client,
        context_window=1,
    )
    result, log = reader.execute(context, chunk_ids=["1"])
    payload = json.loads(result)

    assert payload["schema_version"] == "1.1"
    assert payload["claim_zh"] == "检索增强能把幻觉率降低一半"
    assert payload["verdict"] == VERDICT_SUPPORTED
    assert payload["evidence_count"] == 1

    evidence = payload["evidences"][0]
    assert evidence["chunk_id"] == "1"
    assert evidence["rerank_score"] == pytest.approx(0.93)
    assert evidence["verdict"] == VERDICT_SUPPORTED
    # 论文元数据：只含标题、前两位作者、年份、来源文件
    assert set(evidence["paper"]) == {"paper_id", "title", "authors", "year", "source_file"}
    assert evidence["paper"]["title"].startswith("Grounding Language Model")
    assert evidence["paper"]["year"] == "2024"
    assert len(evidence["paper"]["authors"]) == 2, "作者只保留前两位"
    # 段落上下文三段齐全
    assert set(evidence["context"]) == {"section", "page", "prev_text", "target_text", "next_text"}
    assert evidence["context"]["section"] == "4 Results"
    assert log["evidence_count"] == 1

    # 命中句是 chunk 的第二句，前文必须真的填上（回归：曾因全局/块内下标混用而恒为空）
    assert "closed-book baseline" in evidence["context"]["prev_text"]
    assert evidence["context"]["target_text"] == evidence["evidence_en"]


def test_semantic_search_candidate_index_is_global(tmp_path):
    """看板里的 sentence_index 必须是全局下标，能直接还原上下文。"""
    board = EvidenceBoard(
        "claim", DualThresholdGate(ThresholdConfig(high=0.7, low=0.3, min_hits=1))
    )
    tool, store, _ = _make_semantic_tool(tmp_path, board, {"hallucination rate": 0.93})
    tool.execute(AgentContext(), query="hallucination reduction", top_k=3)

    candidate = board.all_candidates()[0]
    assert store.sentence(candidate.sentence_index) == candidate.sentence
    assert store.chunk_id_of(candidate.sentence_index) == candidate.chunk_id

    prev_text, target, _ = store.paragraph_context(candidate.sentence_index, window=1)
    assert target == candidate.sentence
    assert prev_text


def test_read_chunk_rescores_chunk_not_seen_by_semantic_search(tmp_path):
    """keyword_search 命中但未经重排的 chunk，read_chunk 应就地补分。"""
    from arag.tools.read_chunk import ReadChunkTool

    chunks = load_sample_chunks()
    store = IndexStore(build_fake_index(tmp_path, chunks))
    board = EvidenceBoard(
        "跨语言检索里重排很关键",
        DualThresholdGate(ThresholdConfig(high=0.7, low=0.3, min_hits=1)),
    )
    client = FakeSiliconFlow(np.zeros(len(store), dtype=np.float32), {"cross-encoder": 0.88})

    reader = ReadChunkTool(index_store=store, board=board, gate=board.gate, sf_client=client)
    result, log = reader.execute(AgentContext(), chunk_ids=["4"])
    payload = json.loads(result)

    assert client.rerank_calls == 1
    evidence = payload["evidences"][0]
    assert evidence["rerank_score"] > 0
    assert payload["stats"]["rescored_chunks"] == ["4"]
    # 补分路径也要能还原段落上下文（其拿到的是块内句序，需换算成全局下标）
    assert evidence["context"]["target_text"] == evidence["evidence_en"]
    assert "cross-encoder" in evidence["evidence_en"].lower()


def test_read_chunk_marks_already_read_chunks(tmp_path):
    from arag.tools.read_chunk import ReadChunkTool

    store = IndexStore(build_fake_index(tmp_path, load_sample_chunks()))
    board = EvidenceBoard("claim", DualThresholdGate())
    reader = ReadChunkTool(index_store=store, board=board, gate=board.gate)
    context = AgentContext()

    first_result, first_log = reader.execute(context, chunk_ids=["2"])
    second_result, second_log = reader.execute(context, chunk_ids=["2"])

    assert first_log["new_chunks_count"] == 1
    assert second_log["already_read_count"] == 1
    # 已读过的 chunk 不再重复计费
    assert second_log["retrieved_tokens"] == 0
    assert "already read" in json.loads(second_result)["evidences"][0]["context"]["target_text"]


def test_read_chunk_reports_missing_chunk(tmp_path):
    from arag.tools.read_chunk import ReadChunkTool

    store = IndexStore(build_fake_index(tmp_path, load_sample_chunks()))
    board = EvidenceBoard("claim", DualThresholdGate())
    reader = ReadChunkTool(index_store=store, board=board, gate=board.gate)

    result, _ = reader.execute(AgentContext(), chunk_ids=["9999"])
    payload = json.loads(result)

    assert payload["stats"]["missing_chunks"] == ["9999"]
    assert payload["evidence_count"] == 0


# ---------------------------------------------------------------- 证据广度：多查询 / 邻句 / 多样性


def test_merge_hits_keeps_highest_score_per_sentence():
    from arag.tools.semantic_search import merge_hits

    merged = merge_hits([(1, 0.5), (2, 0.8)], [(1, 0.9), (3, 0.4)])
    assert merged == [(1, 0.9), (2, 0.8), (3, 0.4)]


def test_diversify_select_prefers_distinct_sentences():
    from arag.tools.semantic_search import diversify_select

    ranked = [
        (0, 0.95, 0.9, "inferior ovary formation in cucumber plants"),
        (1, 0.94, 0.8, "inferior ovary formation among cucumber plants"),
        (2, 0.80, 0.7, "sex determination linked to receptacle growth"),
        (3, 0.79, 0.6, "spatial transcriptome mapping of floral buds"),
    ]
    # 纯按分数会先拿两句近义句；多样性应跳过近重复，换上不同方面的句
    selected = diversify_select(ranked, top_k=2, diversity_lambda=0.5)
    texts = [item[3] for item in selected]
    assert texts[0].startswith("inferior ovary")
    assert "sex determination" in texts[1] or "spatial transcriptome" in texts[1]


def test_semantic_search_expands_neighbors_into_board(tmp_path):
    """邻句扩展：命中句同 chunk 前后句也进入候选池。"""
    board = EvidenceBoard(
        "claim", DualThresholdGate(ThresholdConfig(high=0.7, low=0.3, min_hits=1))
    )
    tool, store, _ = _make_semantic_tool(
        tmp_path,
        board,
        {"hallucination rate": 0.93},
        neighbor_window=1,
    )
    tool.execute(AgentContext(), query="hallucination reduction", top_k=5)

    # 样本 chunk1 至少有 2 句，邻句扩展后候选应多于纯 top 命中
    assert len(board.candidates) >= 2
    indices = {item.sentence_index for item in board.all_candidates()}
    # 至少有一对全局下标相邻（同 chunk 内）
    assert any(abs(a - b) == 1 for a in indices for b in indices if a != b)


def test_semantic_search_multi_query_uses_board_terms(tmp_path):
    """看板有英文学术术语时，额外并集一路向量召回。"""
    from retrieval_adaptor.schemas import BilingualTerm

    board = EvidenceBoard(
        "claim", DualThresholdGate(ThresholdConfig(high=0.7, low=0.3, min_hits=1))
    )
    board.set_terms([BilingualTerm(zh="幻觉", en="hallucination", aliases=["factual error"])])
    tool, _, client = _make_semantic_tool(
        tmp_path,
        board,
        {"hallucination rate": 0.93},
        multi_query_from_terms=True,
    )
    tool.execute(AgentContext(), query="hallucination reduction", top_k=3)
    assert client.embed_calls == 2


def test_prompt_mentions_breadth_continue_when_strong_hits_insufficient():
    prompt = build_system_prompt(high=0.70, low=0.30, min_hits=2)
    assert "fewer than 2" in prompt or "fewer than {min_hits}" not in prompt
    assert "breadth still insufficient" in prompt
    assert "One extra search" in prompt


def test_read_chunk_without_board_still_returns_schema(tmp_path):
    """独立使用（不走流水线）时输出结构必须保持一致。"""
    from arag.tools.read_chunk import ReadChunkTool

    store = IndexStore(build_fake_index(tmp_path, load_sample_chunks()))
    reader = ReadChunkTool(index_store=store)

    result, _ = reader.execute(AgentContext(), chunk_ids=["0"])
    payload = json.loads(result)
    assert payload["schema_version"] == "1.1"
    assert payload["evidence_count"] == 1


# ---------------------------------------------------------------- Qwen 适配器


def test_qwen_adapter_matches_llmclient_contract():
    from api_client.schemas import ChatResult
    from retrieval_adaptor.qwen_agent_adapter import QwenAgentAdapter

    class StubQwen:
        model = "qwen-plus"

        def chat(self, messages, **kwargs):
            self.last_kwargs = kwargs
            return ChatResult(
                content="hello",
                model="qwen-plus",
                prompt_tokens=100,
                completion_tokens=20,
                raw={
                    "model": "qwen-plus",
                    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                },
            )

    stub = StubQwen()
    adapter = QwenAgentAdapter(stub)
    result = adapter.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])

    # 与 arag.core.llm.LLMClient.chat 的返回键完全一致
    assert set(result) == {"message", "input_tokens", "output_tokens", "cost", "raw_response"}
    assert result["message"]["content"] == "hello"
    assert result["input_tokens"] == 100
    assert result["cost"] > 0
    # 携带 tools 时必须开启 Function Calling
    assert stub.last_kwargs["tool_choice"] == "auto"


def test_qwen_adapter_cost_uses_model_pricing():
    from retrieval_adaptor.qwen_agent_adapter import QwenAgentAdapter

    class StubQwen:
        model = "qwen-max"

        def chat(self, messages, **kwargs):
            raise AssertionError("不该被调用")

    adapter = QwenAgentAdapter(StubQwen())
    # qwen-max: 输入 2.4 元/百万，输出 9.6 元/百万
    assert adapter.calculate_cost({"prompt_tokens": 1_000_000, "completion_tokens": 0}) == 2.4
    assert adapter.calculate_cost({"prompt_tokens": 0, "completion_tokens": 1_000_000}) == 9.6


def test_clean_retrieval_output_splits_classify_and_review_pools(tmp_path):
    """后处理清洗：同一次检索拆成分类 top-5 + 审核池 10，去重保序。"""
    from clean_retrieval_output import CLASSIFY_TOP_K, REVIEW_POOL_SIZE, clean_file, clean_record

    evidences = [
        {
            "evidence_en": "Sentence %d about KNOX1 and ovary position." % i,
            "sentence_id": i,
            "rerank_score": 1.0 - i * 0.01,
        }
        for i in range(12)
    ]
    # 重复句应被去重
    evidences.insert(2, dict(evidences[0]))
    full = {
        "claim_id": "C01",
        "claim_zh": "敲除 KNOX1 导致下位子房变为上位子房。",
        "verdict": "SUPPORTED",
        "evidences": evidences,
    }
    cleaned = clean_record(full)
    assert cleaned["claim_id"] == "C01"
    assert cleaned["claim_zh"] == full["claim_zh"]
    assert len(cleaned["review_evidences"]) == REVIEW_POOL_SIZE
    assert len(cleaned["classify_evidences"]) == CLASSIFY_TOP_K
    assert cleaned["classify_evidences"] == cleaned["review_evidences"][:CLASSIFY_TOP_K]
    assert cleaned["classify_evidences"][0]["sentence_id"] == 0
    assert cleaned["classify_evidences"][0]["rank"] == 1
    assert cleaned["paper_sentences"] == [ev["text"] for ev in cleaned["review_evidences"]]
    assert len(cleaned["evidences"]) == REVIEW_POOL_SIZE

    src = tmp_path / "evidences.jsonl"
    dst = tmp_path / "claim_evidence_pairs.jsonl"
    src.write_text(json.dumps(full, ensure_ascii=False) + "\n", encoding="utf-8")
    assert clean_file(src, dst) == 1
    row = json.loads(dst.read_text(encoding="utf-8").strip())
    assert row == cleaned


def test_batch_resume_skips_completed_claim_ids(tmp_path):
    """续跑应跳过 evidences.jsonl 里已有的 claim_id。"""
    from batch_retrieval import claim_key, load_completed_keys

    path = tmp_path / "evidences.jsonl"
    path.write_text(
        json.dumps({"claim_id": "P001_A01#0", "claim_zh": "已完成"}, ensure_ascii=False)
        + "\n"
        + json.dumps({"claim_id": "P001_A01#1", "claim_zh": "也完成"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    done = load_completed_keys(path)
    assert done == {"P001_A01#0", "P001_A01#1"}
    pending = [
        {"claim_id": "P001_A01#0", "claim_zh": "已完成"},
        {"claim_id": "P001_A01#2", "claim_zh": "待跑"},
    ]
    left = [item for item in pending if claim_key(item) not in done]
    assert len(left) == 1
    assert left[0]["claim_id"] == "P001_A01#2"
