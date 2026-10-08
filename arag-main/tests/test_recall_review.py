# -*- coding: utf-8 -*-
"""召回审核的纯逻辑测试。不调用真实千问接口。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from retrieval_adaptor.recall_review import (
    ContextWindow,
    EvidenceSpan,
    SentenceTable,
    contiguous_runs,
    fill_anchor_ids,
    find_sentence_by_text,
    integrate_spans,
    load_sentence_table,
    locate_anchor,
    merge_overlapping_windows,
    parse_extracted_spans,
    review_sample,
    run_review,
    window_sentence_ids,
)


class FakeClient:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[str] = []
        self.parallel_sizes: list[int] = []

    def ask_json(self, prompt, system=None, **kwargs):
        self.calls.append(system or "")
        return self.handler(prompt, system or "")

    def map_parallel(self, func, items, max_workers=None):
        self.parallel_sizes.append(len(items))
        return [func(item) for item in items]


def _table(pairs: list[tuple[int, str]]) -> SentenceTable:
    from retrieval_adaptor.recall_review import KeptSentence

    sentences = [KeptSentence(sentence_id=sid, text=text) for sid, text in pairs]
    order = [item.sentence_id for item in sentences]
    return SentenceTable(
        sentences=sentences,
        by_id={item.sentence_id: item for item in sentences},
        order=order,
        order_index={sid: index for index, sid in enumerate(order)},
    )


def _write_csv(path: Path, rows: list[str]) -> None:
    body = "sentence_id,chunk_id,text,paper_id,status,drop_reason\n" + "\n".join(rows) + "\n"
    path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))


def test_load_sentence_table_bom_and_dropped(tmp_path: Path):
    csv_path = tmp_path / "P_sentences.csv"
    _write_csv(
        csv_path,
        [
            '0,1,"Alpha sentence.",P,kept,',
            '1,1,"Beta sentence for matching.",P,kept,',
            '2,1,"Gamma sentence.",P,kept,',
            ',1,"layout noise",P,dropped,front_matter_line',
            '9,1,"numbered but dropped",P,dropped,too_short',
        ],
    )
    table = load_sentence_table(csv_path)
    assert table.order == [0, 1, 2]
    assert 9 not in table.by_id
    assert "\ufeff" not in table.sentences[0].text


def test_window_stops_at_boundaries():
    table = _table([(index, "Sentence %d." % index) for index in range(25)])
    assert window_sentence_ids(table, 0) == list(range(0, 11))
    assert window_sentence_ids(table, 24) == list(range(14, 25))
    assert window_sentence_ids(table, 12) == list(range(2, 23))
    assert len(window_sentence_ids(table, 12)) == 21


def test_text_fallback_when_sentence_id_missing():
    table = _table(
        [
            (0, "Alpha sentence stays put."),
            (1, "\x01Beta sentence for matching."),
            (2, "Gamma sentence is different."),
        ]
    )
    located, how = locate_anchor(table, 9999, "Beta   sentence for matching.")
    assert (located, how) == (1, "text")
    located, how = locate_anchor(table, 0, "this text does not match the anchor")
    assert (located, how) == (0, "id")
    assert find_sentence_by_text(table, "no such sentence exists here") is None
    assert locate_anchor(table, None, "short") == (None, "missing")


def test_ambiguous_substring_is_not_a_match():
    table = _table(
        [
            (0, "Shared clause about nitrogen uptake in leaves."),
            (1, "Shared clause about nitrogen uptake in roots."),
        ]
    )
    assert find_sentence_by_text(table, "Shared clause about nitrogen uptake") is None


def test_merge_overlapping_windows_keeps_disjoint_windows():
    windows = [
        ContextWindow([1, 2, 3], [1]),
        ContextWindow([10, 11], [10]),
        ContextWindow([3, 4, 10], [4]),
        ContextWindow([30, 31], [30]),
    ]
    merged = merge_overlapping_windows(windows)
    assert len(merged) == 2
    assert merged[0].sentence_ids == [1, 2, 3, 4, 10, 11]
    assert merged[0].anchor_ids == [1, 10, 4]
    assert merged[1].sentence_ids == [30, 31]
    assert merged[1].anchor_ids == [30]


def test_split_noncontiguous_interval_and_merge_adjacent_spans():
    table = _table([(index, "S%d." % index) for index in (1, 2, 3, 4, 5, 10, 11)])
    window = ContextWindow([1, 2, 3, 10, 11], [1])
    spans = parse_extracted_spans(
        {"spans": [{"sentence_id_start": 1, "sentence_id_end": 11, "confidence": 0.5}]},
        window,
        table,
    )
    assert [span.sentence_ids for span in spans] == [[1, 2, 3], [10, 11]]

    explicit = parse_extracted_spans(
        {"spans": [{"sentence_ids": [1, 2, 5], "confidence": 1.2}]},
        ContextWindow([1, 2, 3, 4, 5], [2]),
        table,
    )
    assert [span.sentence_ids for span in explicit] == [[1, 2], [5]]
    assert explicit[0].confidence == 1.0

    merged = integrate_spans(
        [
            EvidenceSpan([1, 2], 0.2, [1]),
            EvidenceSpan([3, 4], 0.6, [8]),
            EvidenceSpan([10, 11], 0.4, [10]),
        ],
        table.order_index,
    )
    assert [span.sentence_ids for span in merged] == [[1, 2, 3, 4], [10, 11]]
    assert merged[0].confidence == 0.6
    assert merged[0].anchor_source == [1, 8]
    assert merged[1].anchor_source == [10]


def test_fill_anchor_ids_drops_invalid_then_uses_rank_order():
    assert fill_anchor_ids([5, 1, 8, 3], [99, 8]) == [8, 5, 1]
    assert fill_anchor_ids([4, 7], [1, 2, 3]) == [4, 7]


def test_empty_candidates_skip_the_model():
    client = FakeClient(lambda prompt, system: pytest.fail("空召回不应调用模型"))
    table = _table([(0, "Alpha sentence.")])
    result = review_sample(
        client,
        {
            "sample_id": "S0",
            "claim_zh": "观点句。",
            "system_retrieval": {"review_evidences": []},
        },
        table,
    )
    assert result["verdict"] == "no_evidence"
    assert result["evidences"] == []
    assert result["need_human_review"] is True
    assert "召回句为空" in result["explanation"]
    assert client.calls == []


def test_unlocated_anchor_does_not_extract():
    client = FakeClient(lambda prompt, system: pytest.fail("定位失败不应提取"))
    table = _table([(0, "Alpha sentence stays in the table.")])
    result = review_sample(
        client,
        {
            "sample_id": "S1",
            "claim_zh": "观点句。",
            "system_retrieval": {
                "review_evidences": [
                    {"rank": 1, "sentence_id": 404, "text": "not present in this paper table"}
                ]
            },
        },
        table,
    )
    assert result["verdict"] == "no_evidence"
    assert "均未找到" in result["explanation"]
    assert client.calls == []


def test_review_rebuilds_text_from_the_sentence_table():
    table = _table([(index, "TABLE_%d." % index) for index in range(30)])

    def handler(prompt, system):
        if "提取" in system:
            data = json.loads(prompt)
            first = data["window"][0]["sentence_id"]
            return {
                "spans": [
                    {
                        "sentence_id_start": first,
                        "sentence_id_end": first,
                        "confidence": 0.4,
                        "evidence_text": "MODEL PARAPHRASE",
                    }
                ]
            }
        data = json.loads(prompt)
        return {
            "selected": [
                {"index": index, "confidence": 0.8, "evidence_text": "FAKE"}
                for index in range(len(data["candidates"]))
            ],
            "verdict": "partial",
            "explanation": "已覆盖表格编号，缺少观点句后半的条件。",
        }

    client = FakeClient(handler)
    result = review_sample(
        client,
        {
            "sample_id": "S2",
            "claim_zh": "观点句提到表格。",
            "system_retrieval": {
                "review_evidences": [
                    {"rank": 1, "sentence_id": 0, "text": "TABLE_0.", "text_zh": "译"},
                    {"rank": 2, "sentence_id": 25, "text": "TABLE_25.", "text_zh": "译"},
                ]
            },
        },
        table,
    )
    assert client.parallel_sizes == [2]
    assert result["verdict"] == "partial"
    assert result["need_human_review"] is True
    assert [item["evidence_text"] for item in result["evidences"]] == ["TABLE_0.", "TABLE_15."]
    assert result["evidences"][0]["anchor_source"] == [0]
    assert result["evidences"][1]["sentence_id_start"] == 15
    assert result["evidences"][1]["anchor_source"] == [25]
    assert all("FAKE" not in item["evidence_text"] for item in result["evidences"])
    assert all("PARAPHRASE" not in item["evidence_text"] for item in result["evidences"])
    assert "缺少" in result["explanation"]


def test_supported_verdict_clears_human_review():
    table = _table([(0, "Only sentence."), (1, "Next sentence.")])

    def handler(prompt, system):
        if "提取" in system:
            return {"spans": [{"sentence_id_start": 0, "sentence_id_end": 1, "confidence": 0.2}]}
        return {
            "selected": [{"index": 0, "confidence": 0.91}],
            "verdict": "supported",
            "explanation": "两句连续写出了该结论。",
        }

    result = review_sample(
        FakeClient(handler),
        {
            "sample_id": "S3",
            "claim_zh": "结论。",
            "system_retrieval": {
                "review_evidences": [{"rank": 1, "sentence_id": 0, "text": "Only sentence."}]
            },
        },
        table,
    )
    assert result["verdict"] == "supported"
    assert result["need_human_review"] is False
    assert result["evidences"][0]["evidence_text"] == "Only sentence. Next sentence."
    assert result["evidences"][0]["confidence"] == 0.91
    assert result["evidences"][0]["sentence_id_start"] == 0
    assert result["evidences"][0]["sentence_id_end"] == 1


def test_supported_becomes_partial_when_explanation_admits_a_gap():
    table = _table([(0, "Iron is required for plant respiration.")])

    def handler(prompt, system):
        if "提取" in system:
            return {"spans": [{"sentence_id_start": 0, "sentence_id_end": 0, "confidence": 0.7}]}
        return {
            "selected": [
                {"index": 0, "sentence_id_start": 0, "sentence_id_end": 0, "confidence": 0.8}
            ],
            "verdict": "supported",
            "explanation": "这句写出了植物侧，但未写出微生物也必需铁。",
        }

    result = review_sample(
        FakeClient(handler),
        {
            "sample_id": "S8",
            "claim_zh": "铁是植物和微生物所必需的。",
            "system_retrieval": {
                "review_evidences": [
                    {"rank": 1, "sentence_id": 0, "text": "Iron is required for plant respiration."}
                ]
            },
        },
        table,
    )
    assert result["verdict"] == "partial"
    assert result["need_human_review"] is True
    assert result["evidences"][0]["evidence_text"] == "Iron is required for plant respiration."


def test_supported_stays_when_explanation_states_full_coverage():
    table = _table([(0, "Iron regulates nitrogenase activity.")])

    def handler(prompt, system):
        if "提取" in system:
            return {"spans": [{"sentence_id_start": 0, "sentence_id_end": 0, "confidence": 0.7}]}
        return {
            "selected": [{"index": 0, "confidence": 0.9}],
            "verdict": "supported",
            "explanation": "这句连续写出了铁调控固氮酶活性，各命题都在原文中。",
        }

    result = review_sample(
        FakeClient(handler),
        {
            "sample_id": "S9",
            "claim_zh": "铁调控固氮酶活性。",
            "system_retrieval": {
                "review_evidences": [
                    {"rank": 1, "sentence_id": 0, "text": "Iron regulates nitrogenase activity."}
                ]
            },
        },
        table,
    )
    assert result["verdict"] == "supported"
    assert result["need_human_review"] is False


def test_final_model_can_reject_every_span():
    table = _table([(0, "Only sentence in the window.")])

    def handler(prompt, system):
        if "提取" in system:
            return {"spans": [{"sentence_id_start": 0, "sentence_id_end": 0, "confidence": 0.3}]}
        return {"selected": [], "verdict": "no_evidence", "explanation": "窗口内容与观点无关。"}

    result = review_sample(
        FakeClient(handler),
        {
            "sample_id": "S4",
            "claim_zh": "另一件事。",
            "system_retrieval": {
                "review_evidences": [
                    {"rank": 1, "sentence_id": 0, "text": "Only sentence in the window."}
                ]
            },
        },
        table,
    )
    assert result["verdict"] == "no_evidence"
    assert result["evidences"] == []
    assert result["need_human_review"] is True
    assert "无关" in result["explanation"]


def test_final_can_trim_a_candidate_to_a_shorter_span():
    table = _table([(0, "Lead in."), (1, "The claim itself."), (2, "Unrelated aside.")])

    def handler(prompt, system):
        if "提取" in system:
            return {"spans": [{"sentence_id_start": 0, "sentence_id_end": 2, "confidence": 0.4}]}
        return {
            "selected": [
                {
                    "index": 0,
                    "sentence_id_start": 1,
                    "sentence_id_end": 1,
                    "confidence": 0.8,
                }
            ],
            "verdict": "supported",
            "explanation": "只保留写出命题的那一句。",
        }

    result = review_sample(
        FakeClient(handler),
        {
            "sample_id": "S6",
            "claim_zh": "命题。",
            "system_retrieval": {
                "review_evidences": [{"rank": 1, "sentence_id": 1, "text": "The claim itself."}]
            },
        },
        table,
    )
    assert result["evidences"][0]["evidence_text"] == "The claim itself."
    assert result["evidences"][0]["sentence_id_start"] == 1
    assert result["evidences"][0]["sentence_id_end"] == 1


def test_empty_selection_retries_when_explanation_still_claims_support():
    table = _table([(0, "Iron drives nodulation.")])
    systems: list[str] = []

    def handler(prompt, system):
        systems.append(system)
        if "提取" in system:
            return {"spans": [{"sentence_id_start": 0, "sentence_id_end": 0, "confidence": 0.6}]}
        if "无效输出" in system:
            return {
                "selected": [
                    {
                        "index": 0,
                        "sentence_id_start": 0,
                        "sentence_id_end": 0,
                        "confidence": 0.9,
                    }
                ],
                "verdict": "supported",
                "explanation": "这句写出了铁驱动结瘤。",
            }
        return {
            "selected": [],
            "verdict": "no_evidence",
            "explanation": (
                "候选0直接支撑该观点，并完整支撑了铁是结瘤驱动力这一命题，"
                "原文已经写出该判断，不应留空。"
            ),
        }

    result = review_sample(
        FakeClient(handler),
        {
            "sample_id": "S7",
            "claim_zh": "铁驱动结瘤。",
            "system_retrieval": {
                "review_evidences": [
                    {"rank": 1, "sentence_id": 0, "text": "Iron drives nodulation."}
                ]
            },
        },
        table,
    )
    assert any("无效输出" in system for system in systems)
    assert result["verdict"] == "supported"
    assert result["evidences"][0]["evidence_text"] == "Iron drives nodulation."


def test_sample_error_does_not_raise():
    table = _table([(0, "Only sentence.")])

    def handler(prompt, system):
        raise RuntimeError("boom")

    result = review_sample(
        FakeClient(handler),
        {
            "sample_id": "S5",
            "claim_zh": "观点。",
            "system_retrieval": {
                "review_evidences": [
                    {"rank": 1, "sentence_id": 0, "text": "Only sentence."},
                    {"rank": 2, "sentence_id": 1, "text": "missing"},
                    {"rank": 3, "sentence_id": 2, "text": "missing"},
                    {"rank": 4, "sentence_id": 3, "text": "missing"},
                ]
            },
        },
        table,
    )
    assert result["verdict"] == "no_evidence"
    assert result["need_human_review"] is True
    assert result["error"] == "RuntimeError: boom"
    assert result["evidences"] == []


def test_run_review_writes_new_file_and_resumes(tmp_path: Path):
    draft_path = tmp_path / "P006_A001_annotation_draft.json"
    sentence_path = tmp_path / "P006_sentences.csv"
    output_path = tmp_path / "P006_A001_recall_review.json"
    draft = {
        "paper_id": "P006",
        "article_id": "A001",
        "samples": [
            {
                "sample_id": "C01",
                "claim_zh": "第一条。",
                "system_retrieval": {"review_evidences": []},
            },
            {
                "sample_id": "C02",
                "claim_zh": "第二条。",
                "system_retrieval": {"review_evidences": []},
            },
            {
                "sample_id": "C03",
                "claim_zh": "第三条。",
                "system_retrieval": {"review_evidences": []},
            },
        ],
    }
    original = json.dumps(draft, ensure_ascii=False)
    draft_path.write_text(original, encoding="utf-8")
    _write_csv(sentence_path, ['0,1,"Alpha.",P006,kept,'])
    client = FakeClient(lambda prompt, system: pytest.fail("空召回不应调用模型"))

    first = run_review(
        client,
        draft_path,
        sentence_path,
        output_path,
        pdf_path="data/papers/P006.pdf",
        limit=1,
    )
    assert [item["sample_id"] for item in first["samples"]] == ["C01"]
    assert first["pdf_path"] == "data/papers/P006.pdf"
    assert draft_path.read_text(encoding="utf-8") == original
    assert sentence_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert not output_path.read_bytes().startswith(b"\xef\xbb\xbf")

    second = run_review(
        client,
        draft_path,
        sentence_path,
        output_path,
        pdf_path="data/papers/P006.pdf",
        limit=1,
    )
    assert [item["sample_id"] for item in second["samples"]] == ["C01", "C02"]

    with pytest.raises(ValueError, match="不能覆盖召回初稿"):
        run_review(client, draft_path, sentence_path, draft_path)


def test_contiguous_runs_follow_kept_order_not_numeric_gaps():
    order_index = {0: 0, 2: 1, 5: 2}
    assert contiguous_runs([5, 0, 2], order_index) == [[0, 2, 5]]
    assert contiguous_runs([0, 5], order_index) == [[0], [5]]
