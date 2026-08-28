"""检索充分性检查 + 关键词补检闭环的离线测试：全部用 mock 驱动，不发真实网络请求。

覆盖交接文档的 TC-01..04 与主要边界分支：
- C08 类石蜡切片漏检 -> 补检句入池（真实 rerank 打分）
- 简单句首轮 sufficient -> 零补检、零额外成本
- 两轮仍不足 -> 生成 retrieval_hint 交人工
- 语义低分 + keyword 命中 -> 不输出 NO_EVIDENCE
- 检查器失败 / 关键词为空 / 幻觉锚点 / rerank 降级分封顶 / 回滚开关
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from retrieval_adaptor import (
    VERDICT_INCONCLUSIVE,
    VERDICT_NO_EVIDENCE,
    VERDICT_SUPPORTED,
    Candidate,
    DualThresholdGate,
    EvidenceBoard,
    IndexStore,
    RetrievalConfig,
    RetrievalSufficiency,
    SufficiencyCheck,
    SufficiencyConfig,
    ThresholdConfig,
)
from retrieval_adaptor.pipeline import CrossLingualRetrievalPipeline
from retrieval_adaptor.sufficiency import (
    KeywordBackfill,
    SufficiencyChecker,
    _expand_keyword_variants,
    run_sufficiency_loop,
)
from tests.test_retrieval_adaptor import FakeSiliconFlow, build_fake_index

FIXTURES = Path(__file__).parent / "fixtures"

CLAIM_C08 = (
    "为探究葫芦科植物子房下位发育进程，对黄瓜花发育过程进行了石蜡切片观察（图1b），"
    "同时以子房上位的番茄作为对比进行观察（图1d）。"
)

# 句子布局（build_fake_index 按句进索引，下标即 sentence_id）：
#   chunk 0 -> sid 0, 1   （spatial transcriptomics，语义层能召回的"主题相关"句）
#   chunk 1 -> sid 2      （cucumber paraffin-embedded 面板句）
#   chunk 2 -> sid 3      （tomato paraffin-embedded 面板句）
#   chunk 3 -> sid 4      （microtome/deparaffinized 方法句）
PARAFFIN_CHUNKS = [
    {
        "id": "0",
        "text": (
            "Comparative spatial transcriptome mapping was performed for cucumber and "
            "tomato floral buds across all developmental stages. The resulting cell type "
            "clusters revealed distinct ovary position programs."
        ),
        "title": "Paper X",
        "authors": ["A. Author", "B. Author"],
        "year": "2025",
        "section": "Results",
        "page": "3",
    },
    {
        "id": "1",
        "text": (
            "b, The paraffin-embedded sections show the morphology of cucumber female "
            "floral development during developmental stages S1 to S8."
        ),
        "title": "Paper X",
        "authors": ["A. Author", "B. Author"],
        "year": "2025",
        "section": "Results",
        "page": "5",
    },
    {
        "id": "2",
        "text": (
            "d, The paraffin-embedded sections showing the morphology of tomato floral "
            "development during stages S1 to S8."
        ),
        "title": "Paper X",
        "authors": ["A. Author", "B. Author"],
        "year": "2025",
        "section": "Results",
        "page": "5",
    },
    {
        "id": "3",
        "text": (
            "The embedded samples were sectioned at 8 um using a microtome and then "
            "deparaffinized with xylene before further processing."
        ),
        "title": "Paper X",
        "authors": ["A. Author", "B. Author"],
        "year": "2025",
        "section": "Methods",
        "page": "12",
    },
]


def paraffin_embedded_ids(store: IndexStore) -> list[int]:
    return [i for i, s in enumerate(store.sentences) if "paraffin-embedded" in s.lower()]


def make_index(tmp_path: Path) -> IndexStore:
    return IndexStore(build_fake_index(tmp_path, PARAFFIN_CHUNKS))


# ---------------------------------------------------------------- fakes


class FakeSufficiencyLLM:
    """逐轮弹出的假 Qwen。每轮脚本项：

    - dict:              ``chat_json`` 直接返回该 payload（一轮检查）
    - Exception:         ``chat_json`` 抛，``ask`` 也失败 -> check.error
    - ("fallback", dict): ``chat_json`` 抛，``ask`` 返回 JSON 文本 -> 降级成功
    """

    model = "fake-qwen"

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[tuple[str, object]] = []
        self._pending = None

    def chat_json(self, messages, **kwargs):
        if not self.script:
            raise RuntimeError("script exhausted")
        item = self.script.pop(0)
        self.calls.append(("chat_json", messages))
        if isinstance(item, tuple):
            self._pending = item[1]
            raise RuntimeError("json mode unavailable")
        if isinstance(item, BaseException):
            raise item
        return dict(item)

    def ask(self, prompt, system=None, **kwargs):
        self.calls.append(("ask", prompt))
        if self._pending is not None:
            payload, self._pending = self._pending, None
            return json.dumps(payload, ensure_ascii=False)
        raise RuntimeError("ask fallback also failed")


class FailingRerank:
    """rerank 必然抛异常的假客户端，用于验证启发式降级分封顶。"""

    def __init__(self) -> None:
        self.rerank_calls = 0

    def rerank(self, query, documents, top_n=None, **kwargs):
        self.rerank_calls += 1
        raise RuntimeError("rerank api down")


class FakeMapper:
    """替换 pipeline 的术语映射器：零 API 调用。"""

    name = "bilingual_entity_mapper"

    def __init__(self) -> None:
        self.cache = self

    def extract_from_text(self, text: str) -> list:
        return []

    def stats(self) -> dict:
        return {}


class FakeBaseAgent:
    """替换原生 BaseAgent：不跑 ReAct 循环，直接向看板预填候选。

    通过 ``tools.get("read_chunk").board`` 拿到本次查询的看板。
    """

    prefill: list[Candidate] = []

    def __init__(self, llm_client=None, tools=None, system_prompt="", max_loops=12,
                 max_token_budget=128000, verbose=False):
        self.tools = tools
        self.board = tools.get("read_chunk").board

    def run(self, query):
        for cand in list(self.prefill):
            self.board.add_candidates([cand], count_as_round=True)
        return {
            "answer": "",
            "loops": 1,
            "total_cost": 0.0,
            "total_retrieved_tokens": 0,
            "chunks_read_ids": [],
        }


def make_pipeline(tmp_path, monkeypatch, *, sufficiency: SufficiencyConfig | None = None,
                  rerank=None, llm=None, prefill=None):
    """构造离线 pipeline：假索引 + 假 LLM + 假重排 + 假映射器。"""
    monkeypatch.setenv("QWEN_API_KEY", "fake")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "fake")
    store = make_index(tmp_path)
    config = RetrievalConfig(
        chunks_file=FIXTURES / "sample_chunks.json",
        index_dir=Path("unused"),
        term_cache_file=tmp_path / "terms.json",
        sufficiency=sufficiency or SufficiencyConfig(),
    )
    pipeline = CrossLingualRetrievalPipeline(config=config, index_store=store)
    pipeline.qwen_client = llm if llm is not None else FakeSufficiencyLLM([])
    pipeline.sf_client = rerank if rerank is not None else FakeSiliconFlow(
        np.zeros(2), {"paraffin": 0.85}
    )
    pipeline.mapper = FakeMapper()
    FakeBaseAgent.prefill = list(prefill or [])
    monkeypatch.setattr("arag.agent.base.BaseAgent", FakeBaseAgent)
    return pipeline, store


# ---------------------------------------------------------------- 充分性检查


def test_paraffin_backfill_enters_board_with_real_rerank(tmp_path):
    """TC-01：C08 类场景——语义层只召回 spatial 句，补检把 paraffin 句拉进看板。"""
    store = make_index(tmp_path)
    board = EvidenceBoard(CLAIM_C08, DualThresholdGate(ThresholdConfig()))
    board.add_candidates(
        [
            Candidate(chunk_id="0", sentence=store.sentences[0], sentence_index=0,
                      rerank_score=0.90),
            Candidate(chunk_id="0", sentence=store.sentences[1], sentence_index=1,
                      rerank_score=0.85),
        ],
        count_as_round=True,
    )
    llm = FakeSufficiencyLLM(
        [
            {
                "sufficient": False,
                "missing_point": "未找到石蜡切片/组织学观察的直接证据",
                "keywords": ["paraffin-embedded"],
                "anchors": ["1b", "1d"],
            },
            {"sufficient": True},
        ]
    )
    rerank = FakeSiliconFlow(np.zeros(2), {"paraffin": 0.85})
    result = run_sufficiency_loop(
        board, store=store, sf_client=rerank, qwen_client=llm,
        config=SufficiencyConfig(),
    )

    assert result.sufficient is True
    # 词级回退：paraffin-embedded 命中 [2,3]，同时 "paraffin/embedded" 词级命中
    # 制样方法句 4（deparaffinized/embedded samples）——真实 rerank 负责最终筛选
    assert result.checks[0].added_sentence_ids == [2, 3, 4]
    assert rerank.rerank_calls == 1
    assert result.retrieval_hint == ""
    backfilled = [c for c in board.all_candidates() if c.source == "keyword_retry"]
    assert {c.sentence_index for c in backfilled} == {2, 3, 4}


def test_sufficient_first_round_zero_backfill(tmp_path):
    """TC-02：简单句首轮 sufficient -> 零补检、零 rerank。"""
    store = make_index(tmp_path)
    board = EvidenceBoard(CLAIM_C08, DualThresholdGate(ThresholdConfig()))
    board.add_candidates(
        [
            Candidate(chunk_id="0", sentence=store.sentences[0], sentence_index=0,
                      rerank_score=0.90),
            Candidate(chunk_id="0", sentence=store.sentences[1], sentence_index=1,
                      rerank_score=0.85),
        ],
        count_as_round=True,
    )
    llm = FakeSufficiencyLLM([{"sufficient": True}])
    rerank = FakeSiliconFlow(np.zeros(2), {"paraffin": 0.85})
    result = run_sufficiency_loop(
        board, store=store, sf_client=rerank, qwen_client=llm,
        config=SufficiencyConfig(),
    )

    assert result.sufficient is True
    assert len(result.checks) == 1
    assert rerank.rerank_calls == 0
    assert result.retrieval_hint == ""
    assert board.search_rounds == 1  # 只有预填的那一轮


def test_two_rounds_insufficient_produce_hint_with_keywords(tmp_path):
    """TC-03：两轮检查 + 两轮补检仍不足 -> hint 含可搜索关键词。"""
    store = make_index(tmp_path)
    board = EvidenceBoard(CLAIM_C08, DualThresholdGate(ThresholdConfig()))
    llm = FakeSufficiencyLLM(
        [
            {
                "sufficient": False,
                "missing_point": "未找到石蜡切片观察的直接证据",
                "keywords": ["paraffin-embedded"],
                "anchors": [],
            },
            {
                "sufficient": False,
                "missing_point": "缺少制样方法的细节",
                "keywords": ["microtome"],
                "anchors": [],
            },
        ]
    )
    rerank = FakeSiliconFlow(np.zeros(2), {"paraffin": 0.9, "microtome": 0.9})
    result = run_sufficiency_loop(
        board, store=store, sf_client=rerank, qwen_client=llm,
        config=SufficiencyConfig(),
    )

    assert result.sufficient is False
    assert len(result.checks) == 2
    assert board.search_rounds == 2  # 两轮补检各计一轮
    assert "microtome" in result.retrieval_hint
    assert "缺少制样方法的细节" in result.retrieval_hint
    assert result.checks[0].added_sentence_ids == [2, 3, 4]
    assert result.checks[1].added_sentence_ids == []  # 4 已入板，不算新增


def test_checker_failure_stops_with_error_hint(tmp_path):
    """检查器两次调用都失败 -> 单轮即停，hint 提示人工复核。"""
    store = make_index(tmp_path)
    board = EvidenceBoard(CLAIM_C08, DualThresholdGate(ThresholdConfig()))
    llm = FakeSufficiencyLLM([RuntimeError("boom")])
    rerank = FakeSiliconFlow(np.zeros(2), {"paraffin": 0.85})
    result = run_sufficiency_loop(
        board, store=store, sf_client=rerank, qwen_client=llm,
        config=SufficiencyConfig(),
    )

    assert result.sufficient is False
    assert len(result.checks) == 1
    assert result.checks[0].error
    assert "人工复核" in result.retrieval_hint
    assert rerank.rerank_calls == 0


def test_chat_json_failure_falls_back_to_ask(tmp_path):
    """chat_json 失败但 ask 降级成功 -> 正常产出检查结果，不中断闭环。"""
    store = make_index(tmp_path)
    board = EvidenceBoard(CLAIM_C08, DualThresholdGate(ThresholdConfig()))
    llm = FakeSufficiencyLLM([("fallback", {"sufficient": True})])
    rerank = FakeSiliconFlow(np.zeros(2), {"paraffin": 0.85})
    result = run_sufficiency_loop(
        board, store=store, sf_client=rerank, qwen_client=llm,
        config=SufficiencyConfig(),
    )

    assert result.sufficient is True
    assert llm.calls[0][0] == "chat_json"
    assert llm.calls[1][0] == "ask"


def test_empty_keywords_stop_loop_without_rerank(tmp_path):
    """LLM 没给关键词 -> 跳过补检不编造，hint 由 missing_point 生成。"""
    store = make_index(tmp_path)
    board = EvidenceBoard(CLAIM_C08, DualThresholdGate(ThresholdConfig()))
    insufficient = {
        "sufficient": False,
        "missing_point": "缺少方法细节描述",
        "keywords": [],
    }
    llm = FakeSufficiencyLLM([insufficient, dict(insufficient)])
    rerank = FakeSiliconFlow(np.zeros(2), {"paraffin": 0.85})
    result = run_sufficiency_loop(
        board, store=store, sf_client=rerank, qwen_client=llm,
        config=SufficiencyConfig(),
    )

    assert result.sufficient is False
    assert len(result.checks) == 2
    assert rerank.rerank_calls == 0
    assert "缺少方法细节描述" in result.retrieval_hint


def test_invented_anchors_dropped(tmp_path):
    """不在 claim 原文里的锚点一律丢弃（防幻觉）。"""
    store = make_index(tmp_path)
    board = EvidenceBoard(CLAIM_C08, DualThresholdGate(ThresholdConfig()))
    llm = FakeSufficiencyLLM(
        [
            {
                "sufficient": False,
                "missing_point": "缺图注证据",
                "keywords": ["paraffin-embedded"],
                "anchors": ["1x", "9", "1b"],
            },
            {"sufficient": True},
        ]
    )
    rerank = FakeSiliconFlow(np.zeros(2), {"paraffin": 0.85})
    result = run_sufficiency_loop(
        board, store=store, sf_client=rerank, qwen_client=llm,
        config=SufficiencyConfig(),
    )

    assert result.checks[0].anchors == ["1b"]


def test_heuristic_fallback_capped_below_high(tmp_path):
    """rerank 失败时降级启发式分：封顶 high-0.05，永不参与 strong_hits。"""
    store = make_index(tmp_path)
    gate = DualThresholdGate(ThresholdConfig())
    board = EvidenceBoard(CLAIM_C08, gate)
    board.add_candidates(
        [
            Candidate(chunk_id="0", sentence=store.sentences[0], sentence_index=0,
                      rerank_score=0.90),
        ],
        count_as_round=True,
    )
    llm = FakeSufficiencyLLM(
        [
            {
                "sufficient": False,
                "missing_point": "缺石蜡切片证据",
                "keywords": ["paraffin-embedded"],
                "anchors": [],
            },
            {"sufficient": True},
        ]
    )
    failing = FailingRerank()
    result = run_sufficiency_loop(
        board, store=store, sf_client=failing, qwen_client=llm,
        config=SufficiencyConfig(),
    )

    assert failing.rerank_calls == 1
    backfilled = [c for c in board.all_candidates() if c.source == "keyword_retry"]
    assert backfilled
    for candidate in backfilled:
        assert gate.config.low <= candidate.rerank_score < gate.config.high
        assert gate.label(candidate.rerank_score) == VERDICT_INCONCLUSIVE
    # 降级分不伪造 SUPPORTED：board 里没有第二张牌能凑 min_hits
    decision = gate.evaluate(board.scores(), round_index=2, max_rounds=12)
    assert decision.strong_hits < gate.config.min_hits


def test_round2_prompt_contains_previous_and_wider_pool(tmp_path):
    """第 2 轮检查的 prompt 带前轮 missing_point 与更宽的候选池。"""
    store = make_index(tmp_path)
    board = EvidenceBoard(CLAIM_C08, DualThresholdGate(ThresholdConfig()))
    board.add_candidates(
        [
            Candidate(chunk_id="0", sentence=store.sentences[0], sentence_index=0,
                      rerank_score=0.90),
            Candidate(chunk_id="0", sentence=store.sentences[1], sentence_index=1,
                      rerank_score=0.85),
        ],
        count_as_round=True,
    )
    first = {
        "sufficient": False,
        "missing_point": "未找到石蜡切片观察的直接证据",
        "keywords": ["paraffin-embedded"],
        "anchors": [],
    }
    llm = FakeSufficiencyLLM([first, {"sufficient": True}])
    rerank = FakeSiliconFlow(np.zeros(2), {"paraffin": 0.85})
    run_sufficiency_loop(
        board, store=store, sf_client=rerank, qwen_client=llm,
        config=SufficiencyConfig(),
    )

    second_user = llm.calls[1][1][-1]["content"]
    assert "Previous round judged INSUFFICIENT" in second_user
    assert "未找到石蜡切片观察的直接证据" in second_user
    assert "TOP-" in second_user


# ---------------------------------------------------------------- 句级检索


def test_search_sentences_by_keywords(tmp_path):
    """句级关键词检索：大小写不敏感、多词合并排序、短锚点整词匹配、limit 生效。"""
    chunks = [
        {
            "id": "0",
            "text": (
                "We used a 1-based indexing scheme for the cell atlas annotations "
                "across samples."
            ),
            "title": "P", "authors": ["A"], "year": "2025", "section": "M", "page": "1",
        },
        {
            "id": "1",
            "text": (
                "The section shown in Fig 1b displays the PARAFFIN-EMBEDDED tissue "
                "morphology at high magnification."
            ),
            "title": "P", "authors": ["A"], "year": "2025", "section": "M", "page": "2",
        },
        {
            "id": "2",
            "text": (
                "Sections were deparaffinized with xylene before staining and imaging "
                "procedures."
            ),
            "title": "P", "authors": ["A"], "year": "2025", "section": "M", "page": "3",
        },
    ]
    store = IndexStore(build_fake_index(tmp_path, chunks))

    # 大小写不敏感 + 锚点整词匹配："1b" 命中 "Fig 1b" 句，不误中 "1-based"
    rows = store.search_sentences_by_keywords(
        ["paraffin-embedded"], anchors=["1b"], limit=10
    )
    assert [row[0] for row in rows] == [1]

    # 多词命中合并计分："xylene + paraffin" 双命中排在单命中之前
    rows = store.search_sentences_by_keywords(["paraffin", "xylene"], limit=10)
    assert [row[0] for row in rows] == [2, 1]

    # 多词短语回退词级匹配："paraffin section" 命中 "paraffin-embedded"（连字符差异）
    rows = store.search_sentences_by_keywords(["paraffin section"], limit=10)
    assert [row[0] for row in rows] == [1, 2]

    # 词干回退："sectioning" 命中 "sections"；"embedding" 命中 "embedded"
    rows = store.search_sentences_by_keywords(["paraffin sectioning"], limit=10)
    assert [row[0] for row in rows] == [1, 2]
    rows = store.search_sentences_by_keywords(["paraffin embedding"], limit=10)
    assert [row[0] for row in rows] == [1]

    # limit 截断
    rows = store.search_sentences_by_keywords(["paraffin", "xylene"], limit=1)
    assert len(rows) == 1

    # 关键词全空 -> 空结果
    assert store.search_sentences_by_keywords(["", "  "], limit=10) == []


def test_expand_keyword_variants():
    variants = _expand_keyword_variants(["paraffin-embedded", "flower bud"])
    assert "paraffin-embedded" in variants
    assert "paraffin embedded" in variants
    assert "flower-bud" in variants
    assert "flower bud" in variants


# ---------------------------------------------------------------- pipeline 级


def test_pipeline_backfill_rescues_low_semantic(tmp_path, monkeypatch):
    """TC-04：语义层全低分 + keyword 可命中 -> 不输出 NO_EVIDENCE，补检句入 evidences。"""
    llm = FakeSufficiencyLLM(
        [
            {
                "sufficient": False,
                "missing_point": "未找到石蜡切片观察的直接证据",
                "keywords": ["paraffin-embedded"],
                "anchors": ["1b", "1d"],
            },
            {"sufficient": True},
        ]
    )
    store = make_index(tmp_path)
    pipeline, _ = make_pipeline(
        tmp_path, monkeypatch, llm=llm,
        rerank=FakeSiliconFlow(np.zeros(2), {"paraffin": 0.85}),
        prefill=[
            Candidate(chunk_id="0", sentence=store.sentences[0], sentence_index=0,
                      rerank_score=0.10),
            Candidate(chunk_id="0", sentence=store.sentences[1], sentence_index=1,
                      rerank_score=0.10),
        ],
    )

    output = pipeline.retrieve(CLAIM_C08)
    data = output.to_dict()  # 端到端序列化必须可用

    assert output.verdict != VERDICT_NO_EVIDENCE
    ids = [ev.sentence_id for ev in output.evidences]
    assert set(paraffin_embedded_ids(store)) <= set(ids)
    assert output.retrieval_sufficiency is not None
    assert output.retrieval_sufficiency.sufficient is True
    assert output.stats["sufficiency_checks"] == 2
    assert output.stats["sufficiency_sufficient"] is True
    assert data["retrieval_sufficiency"]["sufficient"] is True


def test_pipeline_insufficient_overrides_no_evidence_to_inconclusive(tmp_path, monkeypatch):
    """TC-04 延伸：补检也全低分 -> gate 判 NO_EVIDENCE 被改写为 INCONCLUSIVE + hint。"""
    insufficient = {
        "sufficient": False,
        "missing_point": "未找到石蜡切片观察的直接证据",
        "keywords": ["paraffin-embedded"],
        "anchors": [],
    }
    llm = FakeSufficiencyLLM([dict(insufficient), dict(insufficient)])
    pipeline, _ = make_pipeline(
        tmp_path, monkeypatch, llm=llm,
        rerank=FakeSiliconFlow(np.zeros(2), score_map={}, default=0.20),
        prefill=[
            Candidate(chunk_id="0", sentence="low sentence one", sentence_index=0,
                      rerank_score=0.10),
        ],
    )

    output = pipeline.retrieve(CLAIM_C08)
    data = output.to_dict()

    assert output.verdict == VERDICT_INCONCLUSIVE
    assert output.stop_reason == "insufficient_retrieval_hint"
    assert output.retrieval_sufficiency is not None
    assert output.retrieval_sufficiency.sufficient is False
    assert "paraffin-embedded" in output.retrieval_sufficiency.retrieval_hint
    assert data["stop_reason"] == "insufficient_retrieval_hint"


def test_pipeline_empty_board_checks_and_hints(tmp_path, monkeypatch):
    """board 为空（Agent 零召回）-> 池渲染 NONE，补检可救回。"""
    llm = FakeSufficiencyLLM(
        [
            {
                "sufficient": False,
                "missing_point": "未找到任何对应证据",
                "keywords": ["paraffin-embedded"],
                "anchors": [],
            },
            {"sufficient": True},
        ]
    )
    store = make_index(tmp_path)
    pipeline, _ = make_pipeline(tmp_path, monkeypatch, llm=llm, prefill=[])

    output = pipeline.retrieve(CLAIM_C08)

    first_user = llm.calls[0][1][-1]["content"]
    assert "NONE (no sentences retrieved yet)" in first_user
    ids = [ev.sentence_id for ev in output.evidences]
    assert set(paraffin_embedded_ids(store)) <= set(ids)
    assert output.to_dict()["retrieval_sufficiency"] is not None


def test_pipeline_disabled_config_skips_loop(tmp_path, monkeypatch):
    """ARAG_SUFFICIENCY_ENABLED=0 回滚：零额外 LLM 调用，行为与改造前一致。"""
    llm = FakeSufficiencyLLM([])
    store = make_index(tmp_path)
    pipeline, _ = make_pipeline(
        tmp_path, monkeypatch, llm=llm,
        sufficiency=SufficiencyConfig(enabled=False),
        prefill=[
            Candidate(chunk_id="0", sentence=store.sentences[0], sentence_index=0,
                      rerank_score=0.80),
            Candidate(chunk_id="0", sentence=store.sentences[1], sentence_index=1,
                      rerank_score=0.75),
        ],
    )

    output = pipeline.retrieve(CLAIM_C08)

    assert llm.calls == []
    assert output.verdict == VERDICT_SUPPORTED
    assert output.retrieval_sufficiency is None
    assert "sufficiency_checks" not in output.stats
    assert output.to_dict()["retrieval_sufficiency"] is None


# ---------------------------------------------------------------- schema 与清洗


def test_sufficiency_schema_shapes():
    check = SufficiencyCheck(
        round=1, sufficient=False, missing_point="缺石蜡切片证据",
        keywords=["paraffin"], anchors=["1b"], added_sentence_ids=[15, 27],
    )
    assert set(check.to_dict()) == {
        "round", "sufficient", "missing_point", "keywords", "anchors",
        "added_sentence_ids", "error",
    }
    sufficiency = RetrievalSufficiency(sufficient=False, checks=[check],
                                       retrieval_hint="建议搜索 paraffin")
    assert set(sufficiency.to_dict()) == {"sufficient", "checks", "retrieval_hint"}

    failed = RetrievalSufficiency.failed("boom")
    assert failed.sufficient is False
    assert failed.checks[0].error == "boom"
    assert "人工复核" in failed.retrieval_hint

    from retrieval_adaptor import RetrievalOutput as Output

    output = Output(claim_zh="测试", retrieval_sufficiency=sufficiency)
    assert output.to_dict()["retrieval_sufficiency"]["retrieval_hint"] == "建议搜索 paraffin"


def test_clean_retrieval_output_passes_retrieval_hint():
    from clean_retrieval_output import clean_record

    base = {
        "claim_id": "C08",
        "claim_zh": "石蜡切片观察。",
        "evidences": [{"evidence_en": "some sentence about paraffin", "sentence_id": 15}],
    }
    without = clean_record(dict(base))
    assert "retrieval_hint" not in without

    with_hint = dict(base)
    with_hint["retrieval_sufficiency"] = {
        "sufficient": False,
        "checks": [],
        "retrieval_hint": "缺少石蜡切片证据；建议搜索 paraffin-embedded。",
    }
    cleaned = clean_record(with_hint)
    assert cleaned["retrieval_hint"] == "缺少石蜡切片证据；建议搜索 paraffin-embedded。"

    # 空 hint 不产生字段
    with_hint["retrieval_sufficiency"]["retrieval_hint"] = ""
    assert "retrieval_hint" not in clean_record(with_hint)


def test_sufficiency_prompt_uses_relaxed_criteria():
    """方向 A：充分性口径为「主要断言覆盖 + 疑罪从无」，非「逐断言完备核对 + 疑罪从有」。"""
    from retrieval_adaptor.sufficiency import SUFFICIENCY_SYSTEM_PROMPT

    assert "MAIN assertions" in SUFFICIENCY_SYSTEM_PROMPT
    assert "prefer sufficient=true" in SUFFICIENCY_SYSTEM_PROMPT
    assert "every checkable sub-claim" not in SUFFICIENCY_SYSTEM_PROMPT
    assert "prefer sufficient=false" not in SUFFICIENCY_SYSTEM_PROMPT
