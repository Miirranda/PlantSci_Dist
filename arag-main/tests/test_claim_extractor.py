"""claim_extractor：规则分句与核验解析的单元测试（不调真实 LLM）。"""

from __future__ import annotations

from retrieval_adaptor.claim_extractor import (
    _build_claim_records,
    _parse_decisions,
    split_article_candidates,
)


SAMPLE = """
研究背景
在开花植物中，子房相对于其他花器官的位置是重要分类学特征之一。根据子房与其他外轮花器官的相对位置关系，可分为上位子房和下位子房。

图1 黄瓜单性花与子房下位的进化和发育

研究结果
说明黄瓜子房下位形成的必要条件为：（1）花分生组织膨大；（2）花托迅速生长；（3）花托和心皮融合。
"""


def test_split_article_candidates_filters_figure_caption():
    cands = split_article_candidates(SAMPLE)
    texts = [c["text"] for c in cands]
    assert all(not t.startswith("图") for t in texts)
    assert any("重要分类学特征" in t for t in texts)
    assert any("必要条件" in t for t in texts)


def test_split_article_candidates_tracks_section():
    cands = split_article_candidates(SAMPLE)
    by_kw = {c["text"][:8]: c["section"] for c in cands}
    # 含「分类学」的句应在研究背景
    background = [c for c in cands if "分类学特征" in c["text"]]
    results = [c for c in cands if "必要条件" in c["text"]]
    assert background and background[0]["section"] == "研究背景"
    assert results and results[0]["section"] == "研究结果"


def test_parse_decisions_defaults_missing_to_keep():
    flags = _parse_decisions(
        {"decisions": [{"id": 1, "keep": False}]},
        batch_ids=[1, 2, 3],
    )
    assert flags[1] is False
    assert flags[2] is True
    assert flags[3] is True


def test_build_claim_records_renumbers():
    kept = [
        {"cand_id": 3, "text": "句子甲。", "section": "研究背景"},
        {"cand_id": 7, "text": "句子乙。", "section": "研究结果"},
    ]
    records = _build_claim_records(kept, source_file="a.md")
    assert records[0]["claim_id"] == "C01"
    assert records[1]["claim_id"] == "C02"
    assert records[0]["context_after"] == "句子乙。"
    assert records[1]["context_before"] == "句子甲。"
