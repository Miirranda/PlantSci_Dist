# -*- coding: utf-8 -*-
"""claim_extractor：规则分句、规则筛除与核验解析的单元测试（不调真实 LLM）。"""

from __future__ import annotations

from retrieval_adaptor.claim_extractor import (
    _build_claim_records,
    _parse_decisions,
    apply_rule_filters,
    default_keep,
    dedup_summary_claims,
    export_locked_claims_from_review,
    heuristic_role,
    rule_drop_role,
    save_claims_for_review,
    save_claims_json,
    split_article_candidates,
)


SAMPLE = """
研究背景
在开花植物中，子房相对于其他花器官的位置是重要分类学特征之一。根据子房与其他外轮花器官的相对位置关系，可分为上位子房和下位子房。

图1 黄瓜单性花与子房下位的进化和发育

研究结果
说明黄瓜子房下位形成的必要条件为：（1）花分生组织膨大；（2）花托迅速生长；（3）花托和心皮融合。
"""


def test_split_drops_wrapped_figure_caption():
    text = """研究结果
敲除KNAT2-like1导致下位子房转变为上位子房。
图7 黄瓜子房上位突变体与番茄花芽在细胞类型、
基因表达方面的比较分析
总结与讨论
本研究揭示了花托快速生长依赖FIM驱动。
"""
    cands = split_article_candidates(text)
    texts = [c["text"] for c in cands]
    assert any("敲除KNAT2-like1" in t for t in texts)
    assert any("FIM驱动" in t for t in texts)
    assert all("比较分析" not in t for t in texts)
    assert all(not t.startswith("图") for t in texts)


def test_split_article_candidates_filters_figure_caption():
    cands = split_article_candidates(SAMPLE)
    texts = [c["text"] for c in cands]
    assert all(not t.startswith("图") for t in texts)
    assert any("重要分类学特征" in t for t in texts)
    assert any("必要条件" in t for t in texts)


def test_split_article_candidates_tracks_section():
    cands = split_article_candidates(SAMPLE)
    background = [c for c in cands if "分类学特征" in c["text"]]
    results = [c for c in cands if "必要条件" in c["text"]]
    assert background and background[0]["section"] == "研究背景"
    assert results and results[0]["section"] == "研究结果"


def test_split_keeps_numbered_list_unsplit():
    cands = split_article_candidates(SAMPLE)
    results = [c for c in cands if "必要条件" in c["text"]]
    assert len(results) == 1
    assert "花托迅速生长" in results[0]["text"]
    assert "花托和心皮融合" in results[0]["text"]
    assert not any(c["text"].lstrip().startswith("（2）") for c in cands)


def test_split_merges_numbered_items_across_lines():
    text = """研究结果
说明黄瓜子房下位形成的必要条件为：
（1）花分生组织膨大，改变花器官轮相对位置；
（2）花托迅速生长；
（3）花托和心皮融合。
"""
    cands = split_article_candidates(text)
    assert len(cands) == 1
    assert "花托迅速生长" in cands[0]["text"]
    assert "花托和心皮融合" in cands[0]["text"]


def test_rule_drop_publication_meta_only():
    meta = (
        "2025年4月，中国农业科学院蔬菜花卉研究所杨学勇团队联合华大生命科学研究院"
        "在Nature Plants期刊上发表了题为“Developmental innovation of inferior ovaries”的研究论文。"
    )
    assert rule_drop_role(meta) == "publication_meta"
    assert default_keep(meta) is False


def test_rule_keep_mixed_meta_and_science():
    mixed = (
        "杨学勇团队在Nature Plants期刊上发表论文，首次揭示了黄瓜下位子房的发育机制。"
    )
    assert rule_drop_role(mixed) is None
    assert default_keep(mixed) is True


def test_rule_drop_fragment_and_discourse():
    assert rule_drop_role("（2）花托迅速生长") == "fragment"
    assert rule_drop_role("综上所述") == "discourse"
    assert rule_drop_role("下面我们来看") == "discourse"
    assert rule_drop_role("综上所述，KNAT2-like1在花托生长中起关键性作用。") is None


def test_apply_rule_filters_drops_meta_keeps_science():
    cands = [
        {"cand_id": 1, "text": "杨学勇团队联合华大在Nature Plants期刊上发表了题为“xxx”的研究论文。", "section": ""},
        {"cand_id": 2, "text": "该研究通过空间转录组技术首次揭示了黄瓜下位子房的发育机制。", "section": ""},
        {"cand_id": 3, "text": "（2）花托迅速生长", "section": "研究结果"},
    ]
    kept, dropped = apply_rule_filters(cands)
    assert [c["cand_id"] for c in kept] == [2]
    assert dropped.get("publication_meta") == 1
    assert dropped.get("fragment") == 1


def test_parse_decisions_uses_role_and_fail_closed_default():
    batch = [
        {"cand_id": 1, "text": "（2）花托迅速生长", "section": "研究结果"},
        {
            "cand_id": 2,
            "text": "敲除KNAT2-like1导致下位子房转变为上位子房。",
            "section": "研究结果",
        },
        {
            "cand_id": 3,
            "text": "该研究首次揭示了黄瓜下位子房的发育机制。",
            "section": "",
        },
    ]
    flags = _parse_decisions(
        {"decisions": [{"id": 3, "role": "paper_lead", "keep": True}]},
        batch,
    )
    assert flags[1]["keep"] is False
    assert flags[2]["keep"] is True
    assert flags[3]["keep"] is True
    assert flags[3]["role"] == "paper_lead"


def test_parse_decisions_role_overrides_keep_field():
    batch = [
        {"cand_id": 1, "text": "植物利用阳光进行光合作用。", "section": "研究背景"},
    ]
    flags = _parse_decisions(
        {"decisions": [{"id": 1, "role": "textbook_bg", "keep": True}]},
        batch,
    )
    assert flags[1]["keep"] is False
    assert flags[1]["role"] == "textbook_bg"


def test_parse_decisions_empty_result_uses_heuristic():
    batch = [
        {
            "cand_id": 1,
            "text": "敲除KNAT2-like1导致花托生长受阻并出现子房上位表型。",
            "section": "研究结果",
        },
        {"cand_id": 2, "text": "综上所述", "section": "总结与讨论"},
    ]
    flags = _parse_decisions({"decisions": []}, batch)
    assert flags[1]["keep"] is True
    assert flags[2]["keep"] is False


def test_dedup_summary_drops_near_duplicate_but_keeps_new_significance():
    kept = [
        {
            "text": "敲除KNAT2-like1，黄瓜花托生长受阻，导致下位子房转变为类似番茄的上位子房。",
            "section": "研究结果",
        },
        {
            "text": "敲除KNAT2-like1，黄瓜花托生长受阻，导致下位子房转变为类似番茄的上位子房。",
            "section": "总结与讨论",
        },
        {
            "text": "敲除KNAT2-like1，黄瓜花托生长受阻，导致下位子房转变为类似番茄的上位子房，为作物育种提供了新靶点。",
            "section": "总结与讨论",
        },
    ]
    result = dedup_summary_claims(kept)
    texts = [item["text"] for item in result]
    assert len(result) == 2
    assert texts[0].startswith("敲除KNAT2-like1，黄瓜花托生长受阻")
    assert "新靶点" in texts[1]


def test_heuristic_role_uses_section():
    intro = "在开花植物中，子房相对于其他花器官的位置是重要分类学特征之一。"
    assert rule_drop_role(intro) is None
    assert heuristic_role(intro, "研究背景") == "paper_intro"
    assert heuristic_role("以上结果说明花托快速生长由FIM驱动。", "总结与讨论") == "paper_conclusion"


def test_build_claim_records_includes_role():
    kept = [
        {"cand_id": 3, "text": "句子甲。", "section": "研究背景", "role": "paper_intro"},
        {"cand_id": 7, "text": "句子乙。", "section": "研究结果", "role": "paper_result"},
    ]
    records = _build_claim_records(kept, source_file="a.md")
    assert records[0]["claim_id"] == "C01"
    assert records[1]["claim_id"] == "C02"
    assert records[0]["claim_role"] == "paper_intro"
    assert records[1]["claim_role"] == "paper_result"
    assert records[0]["context_after"] == "句子乙。"
    assert records[1]["context_before"] == "句子甲。"


def test_save_claims_json_persists_role(tmp_path):
    path = tmp_path / "claims.json"
    save_claims_json(
        [
            {
                "claim_id": "C01",
                "claim_zh": "该研究首次揭示了发育机制。",
                "claim_role": "paper_lead",
                "section": "",
            }
        ],
        path,
    )
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[0]["claim_role"] == "paper_lead"


def test_save_claims_for_review_defaults_keep(tmp_path):
    import json

    path = tmp_path / "review.json"
    save_claims_for_review(
        [
            {
                "claim_id": "C01",
                "claim_zh": "该研究首次揭示了发育机制。",
                "claim_role": "paper_lead",
                "section": "",
            }
        ],
        path,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["samples"][0]["review_decision"] == "keep"
    assert not path.with_suffix(".md").exists()


def test_export_locked_claims_drops_and_merges(tmp_path):
    import json

    review = {
        "paper_id": "P001",
        "article_id": "A001",
        "samples": [
            {
                "claim_id": "C01",
                "claim_zh": "句甲。",
                "claim_role": "paper_result",
                "section": "研究结果",
                "review_decision": "keep",
                "merge_into": "",
            },
            {
                "claim_id": "C02",
                "claim_zh": "句乙。",
                "claim_role": "paper_result",
                "section": "研究结果",
                "review_decision": "drop",
                "merge_into": "",
            },
            {
                "claim_id": "C03",
                "claim_zh": "句丙。",
                "claim_role": "paper_result",
                "section": "研究结果",
                "review_decision": "merge",
                "merge_into": "C01",
            },
        ],
    }
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    locked = export_locked_claims_from_review(path)
    assert len(locked) == 1
    assert locked[0]["claim_id"] == "C01"
    assert "句甲" in locked[0]["claim_zh"]
    assert "句丙" in locked[0]["claim_zh"]
    assert "句乙" not in locked[0]["claim_zh"]


def test_extract_skip_llm_drops_meta_and_keeps_intro(tmp_path):
    article = tmp_path / "a.md"
    article.write_text(
        """# 标题
- 来源: 测试

---
2025年4月，杨学勇团队联合华大在Nature Plants期刊上发表了题为“xxx”的研究论文。该研究通过空间转录组技术首次揭示了黄瓜下位子房的发育机制。

研究背景
在开花植物中，子房相对于其他花器官的位置是重要分类学特征之一。

研究结果
说明黄瓜子房下位形成的必要条件为：（1）花分生组织膨大；（2）花托迅速生长；（3）花托和心皮融合。
""",
        encoding="utf-8",
    )
    from retrieval_adaptor.claim_extractor import extract_claims_from_article

    claims = extract_claims_from_article(article, skip_llm_verify=True)
    texts = [c["claim_zh"] for c in claims]
    assert all("发表了题为" not in t for t in texts)
    assert any("首次揭示" in t for t in texts)
    assert any("分类学特征" in t for t in texts)
    assert any("必要条件" in t and "花托迅速生长" in t for t in texts)
    assert all(not t.strip().startswith("（2）") for t in texts)
