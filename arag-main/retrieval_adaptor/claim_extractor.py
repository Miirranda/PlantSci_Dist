"""观点句提取（arag 前端）：规则分句 + 规则筛除 + LLM 角色核验。

任务口径：抽的是公众号里**转述本篇论文科学内容**的句子，供下游对照论文
做信息失真分类。不是「像不像科学事实」。

流程：
  1. 清洗 Markdown
  2. 按句号 / 问号 / 叹号 / 换行切句（编号清单不按分号切开）
  3. 规则粗滤标题、图注；合并跨行编号项
  4. 规则高精度筛除：纯发表元信息、残句、纯过渡套话
  5. LLM 按角色分类（paper_* keep；其余 drop）；失败时按启发式偏严
  6. 总结段与前文近重复去重
  7. 输出带上下文与 claim_role 的 claims
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from api_client import QwenClient, build_messages, extract_json
from api_client.config import get_env, get_float, get_int

from .pdf_ingest import is_claim_like

QWEN_MODEL = get_env("QWEN_MODEL", "qwen-plus") or "qwen-plus"
QWEN_TEMPERATURE = get_float("QWEN_TEMPERATURE", 0.0)
VERIFY_BATCH_SIZE = max(5, get_int("CLAIM_VERIFY_BATCH_SIZE", 25))

# 公众号常见小节名（整行匹配时更新 section）
_SECTION_LINE = re.compile(
    r"^\s*(研究背景|研究结果|研究结论|总结与讨论|总结|讨论|方法|引言|前言|"
    r"背景介绍|主要内容|未来展望|展望)\s*$"
)
_SECTION_NUMBER = re.compile(r"^\s*\d{1,2}\s*$")
_SUMMARY_SECTIONS = frozenset({"总结与讨论", "总结", "讨论", "研究结论"})
_INTRO_SECTIONS = frozenset({"研究背景", "引言", "前言", "背景介绍"})

# 文章切句：不用分号，避免把「（1）…；（2）…；（3）…」切成残句
_ARTICLE_SENTENCE_END = re.compile(r"(?<=[。！？!?])")

_NUMBERED_ITEM = re.compile(r"^\s*[（(]\s*\d+\s*[）)]\s*\S")
_CAPTION_LINE = re.compile(r"^\s*[（(]?[图表]\s*\d")
_QUOTED_SPAN = re.compile(r"[《「“\"].*?[》」”\"]")
_PUB_META_CUES = re.compile(
    r"(发表了题为|发表题为|刊于|期刊上发表|"
    r"在[\w\sA-Za-z]{0,40}(期刊|杂志|Nature|Science|Cell|Plant)[^。]{0,24}发表|"
    r"通讯作者|团队联合|DOI[:：]|doi\.org)"
)
_SCIENCE_PREDICATE = re.compile(
    r"(揭示|发现|表明|证明|显示|阐明|证实|暗示|敲除|突变体|表型|"
    r"调控|表达|共表达|机制|转录因子|基因|测序|聚类|原位杂交|切片|"
    r"发育|生长|融合|原基|分生组织|本研究|该研究|结果显示|作者发现|"
    r"首次|构建了|必要条件|分类学|性状|进化|模式植物)"
)
_DISCOURSE_ONLY = re.compile(
    r"^\s*(下面我们来看|接下来(?:我们)?(?:来看)?|值得注意的是|"
    r"综上所述|由此可见|总而言之|我们可以看出|让我们来看|如图所示)"
    r"\s*[，,。！!？?]?\s*$"
)
_DISCOURSE_PREFIX = re.compile(
    r"^\s*(下面我们来看|接下来(?:我们)?(?:来看)?|值得注意的是|"
    r"综上所述|由此可见|总而言之|我们可以看出|让我们来看)[，,]?"
)
_SIGNIFICANCE = re.compile(r"(首次|新靶点|育种|意义重大|为作物|突破|核心作用)")

KEEP_ROLES = frozenset(
    {
        "paper_result",
        "paper_method",
        "paper_conclusion",
        "paper_lead",
        "paper_intro",
    }
)
DROP_ROLES = frozenset(
    {
        "textbook_bg",
        "publication_meta",
        "discourse",
        "caption_heading",
        "fragment",
    }
)

CLAIM_VERIFY_SYSTEM = """你是植物科学领域的学术审稿人。系统已经把科普文章切成候选句子。
你的任务是给每句标一个角色，判断它是否在向读者转述**本篇论文的科学内容**（不是「像不像科学事实」）。

删掉这句，读者对「这篇论文说了什么」的理解会不会变？会 → paper_*；只会少一个无关知识点 → 不抽。

## 角色（必须从下列选一个）

keep（进入检索与失真分类）：
- paper_lead：导语/标题句，概括本文发现、方法或「首次/核心作用」等意义评价
- paper_result：本文实验结果、机制、因果、表型、数值
- paper_method：本文方法、材料、实验设计（测序、切片、突变体构建等）
- paper_conclusion：本文结论、机制总括、应用/育种意义
- paper_intro：转述论文引言的科学框架（性状定义、进化背景、模式植物选择、研究空白）。研究背景/引言里对这些内容的陈述，即使读起来像科普，也标 paper_intro，不要标 textbook_bg

drop：
- publication_meta：纯发表元信息（团队、机构、期刊、题名报道、DOI），无科学转述
- textbook_bg：作者另加的学科常识，与本文研究对象/问题无关，不是在复述论文引言
- discourse：纯过渡套话（下面我们来看、综上所述），无可核查命题
- caption_heading：图注、分节标题
- fragment：过碎、无法独立核查的残句（如单独的「（2）花托迅速生长」）

## 正反例
- publication_meta / drop：「杨学勇团队在 Nature Plants 发表了题为……的研究论文」
- paper_lead / keep：「该研究……首次揭示了黄瓜下位子房的发育机制」
- paper_intro / keep：「下位子房被认为是被子植物多次独立进化出的关键创新性状」（引言框架）
- textbook_bg / drop：本文讲黄瓜子房，却插入「光合作用把光能转化为化学能」
- fragment / drop：「（2）花托迅速生长」
- 混合句（元信息+科学发现）→ 标 paper_lead 或 paper_result，keep，不要因单位名丢掉整句

## 硬性规则
1. 只对给定候选句判决，禁止改写原文，禁止发明新句子。
2. 每个输入 id 都必须给出一条决策。
3. keep 必须与角色一致：paper_* → true，其余 → false。
4. 严格输出 JSON：
{
  "decisions": [
    {"id": 1, "role": "paper_lead", "keep": true, "reason": "简短理由"},
    {"id": 2, "role": "publication_meta", "keep": false, "reason": "简短理由"}
  ]
}
"""


def _clean_markdown(text: str) -> str:
    """移除 Markdown 标记与文首元数据，保留正文。"""
    # 文首 YAML / 存档头：从文件开头到第一条单独的 --- 分隔线
    text = re.sub(r"\A[\s\S]*?^---\s*\n", "", text, count=1, flags=re.MULTILINE)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^---+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
    return text


def _strip_leading_meta(text: str) -> str:
    """去掉正文前残留的存档字段行（来源/链接等）。"""
    lines = text.split("\n")
    start = 0
    meta_prefix = re.compile(
        r"^\s*[-*]?\s*(来源|来源类型|发布日期|原文链接|对应论文|存档日期|作者)\s*[:：]"
    )
    while start < len(lines):
        line = lines[start].strip()
        if not line:
            start += 1
            continue
        if meta_prefix.match(line) or re.match(r"^\[A\d+\]", line):
            start += 1
            continue
        break
    return "\n".join(lines[start:])


def _is_section_header(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _SECTION_LINE.match(stripped):
        return True
    if _SECTION_NUMBER.match(stripped):
        return True
    return False


def _text_outside_quotes(text: str) -> str:
    return _QUOTED_SPAN.sub("", text)


def _is_publication_meta_only(text: str) -> bool:
    """高置信纯发表元信息。标题引号内的科学词不计入。混合句（含科学谓语）不丢。"""
    outside = _text_outside_quotes(text)
    if not _PUB_META_CUES.search(outside):
        return False
    return not bool(_SCIENCE_PREDICATE.search(outside))


def _is_fragment(text: str) -> bool:
    stripped = text.strip()
    compact = re.sub(r"\s+", "", stripped)
    if _NUMBERED_ITEM.match(stripped) and len(compact) < 24:
        return True
    if _DISCOURSE_PREFIX.match(stripped):
        leftover = _DISCOURSE_PREFIX.sub("", stripped).strip("，,。；; ")
        if len(leftover) < 16 and not _SCIENCE_PREDICATE.search(leftover):
            return True
    return False


def _is_discourse_only(text: str) -> bool:
    stripped = text.strip()
    if _DISCOURSE_ONLY.match(stripped):
        return True
    leftover = _DISCOURSE_PREFIX.sub("", stripped).strip("，,。；; ")
    return bool(leftover) is False


def rule_drop_role(text: str) -> str | None:
    """规则层高精度 drop。无法高置信判断时返回 None（交给 LLM）。"""
    if _is_publication_meta_only(text):
        return "publication_meta"
    if _is_discourse_only(text):
        return "discourse"
    if _is_fragment(text):
        return "fragment"
    return None


def default_keep(text: str, section: str = "") -> bool:
    """LLM 漏判或整批失败时的偏严默认：像论文转述的句子才留。"""
    if rule_drop_role(text):
        return False
    stripped = text.strip()
    if section in _INTRO_SECTIONS or section in _SUMMARY_SECTIONS:
        return len(stripped) >= 12
    if _SCIENCE_PREDICATE.search(stripped) and len(stripped) >= 20:
        return True
    return len(stripped) >= 40


def heuristic_role(text: str, section: str = "") -> str:
    """无 LLM 时的角色估计（规则筛除之后）。"""
    dropped = rule_drop_role(text)
    if dropped:
        return dropped
    if section in _INTRO_SECTIONS:
        return "paper_intro"
    if section in _SUMMARY_SECTIONS:
        return "paper_conclusion"
    if re.search(r"(测序|构建.{0,8}突变|CRISPR|切片|杂交|实验设计)", text):
        return "paper_method"
    if re.search(r"(首次|揭示了|以上结果说明|综上所述)", text):
        return "paper_conclusion" if section in _SUMMARY_SECTIONS else "paper_lead"
    return "paper_result"


def _looks_like_list_context(text: str) -> bool:
    stripped = text.strip()
    if _NUMBERED_ITEM.match(stripped):
        return True
    if stripped.endswith(("：", ":", "；", ";")):
        return True
    if "（1）" in stripped or "(1)" in stripped:
        return True
    if re.search(r"(必要条件|包括以下|分别为|包括：)", stripped):
        return True
    return False


def _merge_numbered_continuations(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把跨行的「（1）…（2）…」编号项并回前句，避免残句入选。"""
    merged: list[dict[str, Any]] = []
    for item in pieces:
        text = str(item["text"]).strip()
        if (
            merged
            and item.get("section") == merged[-1].get("section")
            and _NUMBERED_ITEM.match(text)
            and _looks_like_list_context(str(merged[-1]["text"]))
        ):
            prev = str(merged[-1]["text"]).rstrip("；;、，, ")
            merged[-1]["text"] = "%s；%s" % (prev, text)
            continue
        merged.append(dict(item))
    return merged


def _is_caption_continuation(line: str) -> bool:
    """图注换行后的第二行（无句末标点的短残片）。"""
    if line.endswith(("。", "！", "？", "!", "?")):
        return False
    compact = re.sub(r"\s+", "", line)
    return 0 < len(compact) <= 40


def split_article_candidates(text: str, *, min_chars: int = 8) -> list[dict[str, Any]]:
    """规则分句 + 图注粗滤 + 编号项合并，返回高召回候选。

    每项含：cand_id(从1起)、text、section。
    不在这里丢发表元信息 / 教材句，那些由 rule_drop_role 与 LLM 处理。
    """
    normalized = re.sub(r"[ \t]+", " ", text.replace("\r", "\n"))
    pieces: list[dict[str, Any]] = []
    section = ""
    pending_section = ""
    awaiting_caption_cont = False

    for block in normalized.split("\n"):
        line = block.strip()
        if not line:
            continue
        if _is_section_header(line):
            awaiting_caption_cont = False
            if _SECTION_NUMBER.match(line):
                pending_section = line
            else:
                section = line
                pending_section = ""
            continue
        if pending_section and _SECTION_LINE.match(line):
            awaiting_caption_cont = False
            section = line
            pending_section = ""
            continue
        if _CAPTION_LINE.match(line):
            awaiting_caption_cont = not line.endswith(("。", "！", "？", "!", "?"))
            continue
        if awaiting_caption_cont:
            awaiting_caption_cont = False
            if _is_caption_continuation(line):
                continue

        for piece in _ARTICLE_SENTENCE_END.split(line):
            candidate = piece.strip()
            if not is_claim_like(candidate, min_chars=min_chars):
                continue
            pieces.append({"text": candidate, "section": section})

    pieces = _merge_numbered_continuations(pieces)
    candidates: list[dict[str, Any]] = []
    for item in pieces:
        candidates.append(
            {
                "cand_id": len(candidates) + 1,
                "text": item["text"],
                "section": item.get("section") or "",
            }
        )
    return candidates


def apply_rule_filters(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """去掉规则能高置信判定的噪声。返回 (保留列表, 按角色计的筛除数)。"""
    kept: list[dict[str, Any]] = []
    dropped: dict[str, int] = {}
    for item in candidates:
        role = rule_drop_role(str(item["text"]))
        if role:
            dropped[role] = dropped.get(role, 0) + 1
            continue
        kept.append(item)
    return kept, dropped


def _char_ngrams(text: str, n: int = 2) -> set[str]:
    compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text)
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i : i + n] for i in range(len(compact) - n + 1)}


def _containment_overlap(a: str, b: str) -> float:
    grams_a, grams_b = _char_ngrams(a), _char_ngrams(b)
    if not grams_a or not grams_b:
        return 0.0
    inter = len(grams_a & grams_b)
    return inter / min(len(grams_a), len(grams_b))


def _has_new_significance(text: str, earlier: str) -> bool:
    current = set(_SIGNIFICANCE.findall(text))
    prev = set(_SIGNIFICANCE.findall(earlier))
    return bool(current - prev)


def dedup_summary_claims(
    kept: list[dict[str, Any]],
    *,
    overlap_threshold: float = 0.75,
) -> list[dict[str, Any]]:
    """总结/讨论段若与前文近重复则丢掉，除非补了新的意义/机制总括。"""
    retained: list[dict[str, Any]] = []
    earlier_texts: list[str] = []
    for item in kept:
        text = str(item["text"])
        section = str(item.get("section") or "")
        if section in _SUMMARY_SECTIONS and earlier_texts:
            best_prev = max(earlier_texts, key=lambda prev: _containment_overlap(text, prev))
            if _containment_overlap(text, best_prev) >= overlap_threshold:
                if not _has_new_significance(text, best_prev):
                    continue
        retained.append(item)
        earlier_texts.append(text)
    return retained


def _coerce_keep(raw: Any) -> bool | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in ("1", "true", "yes", "y", "keep"):
            return True
        if value in ("0", "false", "no", "n", "drop"):
            return False
    return None


def _keep_from_role(role: str) -> bool | None:
    if role in KEEP_ROLES:
        return True
    if role in DROP_ROLES:
        return False
    return None


def _parse_decisions(
    result: Any,
    batch: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """把 LLM JSON 解析成 {cand_id: {keep, role}}。缺省 id 按 default_keep 偏严补全。"""
    parsed: dict[int, dict[str, Any]] = {}
    rows: list[Any] = []
    if isinstance(result, dict):
        raw_rows = result.get("decisions") or result.get("results") or []
        if isinstance(raw_rows, list):
            rows = raw_rows

    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("id", row.get("cand_id"))
        try:
            cid = int(raw_id)
        except (TypeError, ValueError):
            continue
        role = str(row.get("role") or "").strip()
        keep = _keep_from_role(role)
        if keep is None:
            keep = _coerce_keep(row.get("keep", row.get("retain")))
        parsed[cid] = {"keep": keep, "role": role}

    decisions: dict[int, dict[str, Any]] = {}
    for item in batch:
        cid = int(item["cand_id"])
        text = str(item["text"])
        section = str(item.get("section") or "")
        found = parsed.get(cid, {})
        keep = found.get("keep")
        role = str(found.get("role") or "")
        if keep is None:
            keep = default_keep(text, section)
            role = role or heuristic_role(text, section)
        elif not role:
            role = heuristic_role(text, section) if keep else "fragment"
        elif _keep_from_role(role) is None and keep:
            role = heuristic_role(text, section)
        decisions[cid] = {"keep": bool(keep), "role": role or heuristic_role(text, section)}
    return decisions


def verify_candidates_with_llm(
    candidates: list[dict[str, Any]],
    *,
    client: QwenClient,
    model: str,
    batch_size: int = VERIFY_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """按批调用 LLM 角色核验，返回 keep 的候选（保持原文顺序）。"""
    if not candidates:
        return []

    kept: list[dict[str, Any]] = []
    total = len(candidates)
    for start in range(0, total, batch_size):
        batch = candidates[start : start + batch_size]
        lines = []
        for item in batch:
            section = str(item.get("section") or "").strip() or "正文"
            lines.append("%d. [%s] %s" % (item["cand_id"], section, item["text"]))
        prompt = (
            "请对下列候选句逐条给出 role，并据此 keep（true/false）。"
            "禁止改写，禁止新增句子。\n\n"
            + "\n".join(lines)
        )
        print(
            "  [arag.claim_extractor] 核验批次 %d–%d / %d"
            % (start + 1, start + len(batch), total)
        )
        try:
            result = client.chat_json(
                build_messages(prompt, system=CLAIM_VERIFY_SYSTEM),
                temperature=QWEN_TEMPERATURE,
                model=model,
            )
        except Exception as exc:
            print("  [arag.claim_extractor] 核验失败，尝试降级解析: %s" % exc)
            try:
                raw = client.ask(prompt, system=CLAIM_VERIFY_SYSTEM, model=model)
                result = extract_json(raw)
            except Exception as exc2:
                print("  [arag.claim_extractor] 降级解析也失败，本批按启发式偏严保留: %s" % exc2)
                result = {"decisions": []}

        flags = _parse_decisions(result, batch)
        for item in batch:
            decision = flags.get(int(item["cand_id"]), {})
            if decision.get("keep"):
                enriched = dict(item)
                enriched["role"] = decision.get("role") or heuristic_role(
                    str(item["text"]), str(item.get("section") or "")
                )
                kept.append(enriched)

    return kept


def _build_claim_records(
    kept: list[dict[str, Any]],
    *,
    source_file: str,
) -> list[dict[str, Any]]:
    """为保留句补全上下文、角色与稳定 claim_id。"""
    texts = [str(item["text"]) for item in kept]
    records: list[dict[str, Any]] = []
    for index, item in enumerate(kept):
        claim_id = "C%02d" % (index + 1)
        section = str(item.get("section") or "").strip()
        role = str(item.get("role") or item.get("claim_role") or heuristic_role(str(item["text"]), section))
        records.append(
            {
                "claim_id": claim_id,
                "claim_zh": item["text"],
                "claim_role": role,
                "context_before": texts[index - 1] if index > 0 else "",
                "context_after": texts[index + 1] if index + 1 < len(texts) else "",
                "section": section,
                "source_file": source_file,
            }
        )
    return records


def extract_claims_from_article(
    article_path: str | Path,
    *,
    client: QwenClient | None = None,
    model: str | None = None,
    batch_size: int | None = None,
    skip_llm_verify: bool = False,
    min_chars: int = 8,
) -> list[dict[str, Any]]:
    """从 Markdown 文章提取转述本篇论文科学内容的观点句。

    Args:
        skip_llm_verify: True 时只做规则切分 + 规则筛除 + 总结去重（调试/离线）。
        batch_size: LLM 核验批大小，默认读环境变量 CLAIM_VERIFY_BATCH_SIZE。

    Returns:
        列表元素含 claim_id / claim_zh / claim_role，以及 context_before / context_after / section。
    """
    article_path = Path(article_path)
    raw_text = article_path.read_text(encoding="utf-8")

    cleaned = _clean_markdown(raw_text)
    cleaned = _strip_leading_meta(cleaned)
    cleaned = re.sub(r"^---[\s\S]*?---\s*", "", cleaned)

    print("  [arag.claim_extractor] 文章长度: %d 字符" % len(cleaned))
    candidates = split_article_candidates(cleaned, min_chars=min_chars)
    print("  [arag.claim_extractor] 规则候选: %d 句" % len(candidates))

    filtered, dropped = apply_rule_filters(candidates)
    if dropped:
        detail = ", ".join("%s=%d" % item for item in sorted(dropped.items()))
        print("  [arag.claim_extractor] 规则筛除: %d（%s）" % (sum(dropped.values()), detail))
    print("  [arag.claim_extractor] 送核验: %d 句" % len(filtered))

    if not filtered:
        return []

    if skip_llm_verify:
        kept = []
        for item in filtered:
            enriched = dict(item)
            enriched["role"] = heuristic_role(str(item["text"]), str(item.get("section") or ""))
            kept.append(enriched)
        print("  [arag.claim_extractor] 跳过 LLM 核验，保留规则通过的候选")
    else:
        if client is None:
            client = QwenClient(verbose=False)
        used_model = model or QWEN_MODEL
        print("  [arag.claim_extractor] 核验模型: %s" % used_model)
        kept = verify_candidates_with_llm(
            filtered,
            client=client,
            model=used_model,
            batch_size=batch_size or VERIFY_BATCH_SIZE,
        )

    before_dedup = len(kept)
    kept = dedup_summary_claims(kept)
    if len(kept) < before_dedup:
        print(
            "  [arag.claim_extractor] 总结段去重: %d → %d"
            % (before_dedup, len(kept))
        )

    claims = _build_claim_records(kept, source_file=article_path.name)
    print(
        "  [arag.claim_extractor] 最终观点句: %d 条（候选 %d → 规则后 %d → 保留 %d）"
        % (len(claims), len(candidates), len(filtered), len(kept))
    )
    return claims


def save_claims_jsonl(claims: list[dict[str, Any]], path: str | Path) -> Path:
    """写出 arag 可读的 claims.jsonl。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for claim in claims:
            row = {
                "claim_id": claim.get("claim_id") or claim.get("id"),
                "claim_zh": claim.get("claim_zh") or claim.get("claim_text") or "",
            }
            for key in ("claim_role", "context_before", "context_after", "section", "source_file"):
                if claim.get(key):
                    row[key] = claim[key]
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out


def save_claims_json(claims: list[dict[str, Any]], path: str | Path) -> Path:
    """写出可读的 claims.json（hallu/人工检查用）。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for claim in claims:
        payload.append(
            {
                "id": claim.get("claim_id") or claim.get("id"),
                "claim_text": claim.get("claim_zh") or claim.get("claim_text") or "",
                "claim_role": claim.get("claim_role", ""),
                "context_before": claim.get("context_before", ""),
                "context_after": claim.get("context_after", ""),
                "section": claim.get("section", ""),
            }
        )
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def save_claims_for_review(
    claims: list[dict[str, Any]],
    path: str | Path,
    *,
    paper_id: str = "P001",
    article_id: str = "A001",
    guideline: str = "观点句抽取细则.md",
) -> Path:
    """写出审 A 用 JSON：review_decision 默认 keep，人工只改 drop/merge。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    samples = []
    for claim in claims:
        samples.append(
            {
                "claim_id": claim.get("claim_id") or claim.get("id"),
                "claim_zh": claim.get("claim_zh") or claim.get("claim_text") or "",
                "claim_role": claim.get("claim_role", ""),
                "section": claim.get("section", ""),
                "system_keep": True,
                "review_decision": "keep",
                "review_note": "",
                "merge_into": "",
            }
        )
    payload = {
        "paper_id": paper_id,
        "article_id": article_id,
        "guideline": guideline,
        "task": "review_A_inclusion",
        "instructions": (
            "review_decision 默认 keep；发现不该进 Benchmark 时改为 drop 或 merge。"
            "不要改写 claim_zh。"
        ),
        "sample_count": len(samples),
        "samples": samples,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def export_locked_claims_from_review(
    review_path: str | Path,
    *,
    source_claims: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """按审 A 的 review_decision 导出锁定名单。

    - keep：保留
    - drop：丢弃
    - merge：把原文并入 merge_into 指向的句子（追加到目标 claim_zh），自身不单独保留
    - 空或未知：按 keep（与默认策略一致）

    若提供 source_claims（含 context_*），按 claim_id 对齐补全上下文；否则只输出审 A 字段。
    输出重新编号为 C01..Cn，并刷新 context_before/after。
    """
    review_path = Path(review_path)
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    samples = payload.get("samples") or []
    by_id = {str(s.get("claim_id")): s for s in samples if isinstance(s, dict)}
    source_by_id: dict[str, dict[str, Any]] = {}
    if source_claims:
        for claim in source_claims:
            cid = str(claim.get("claim_id") or claim.get("id") or "")
            if cid:
                source_by_id[cid] = claim

    # 先处理 merge：把源句文本并入目标
    merged_texts: dict[str, list[str]] = {}
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        decision = str(sample.get("review_decision") or "keep").strip().lower()
        if decision != "merge":
            continue
        source_id = str(sample.get("claim_id") or "")
        target_id = str(sample.get("merge_into") or "").strip()
        text = str(sample.get("claim_zh") or "").strip()
        if not target_id or target_id not in by_id or not text:
            continue
        if target_id == source_id:
            continue
        merged_texts.setdefault(target_id, []).append(text)

    kept_raw: list[dict[str, Any]] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        cid = str(sample.get("claim_id") or "")
        decision = str(sample.get("review_decision") or "keep").strip().lower()
        if decision in ("drop", "merge"):
            continue
        if decision not in ("", "keep"):
            # 未知取值：保守当作 keep，避免误删
            pass
        text = str(sample.get("claim_zh") or "").strip()
        extras = merged_texts.get(cid) or []
        if extras:
            text = "；".join([text] + extras) if text else "；".join(extras)
        if not text:
            continue
        src = source_by_id.get(cid) or {}
        kept_raw.append(
            {
                "text": text,
                "section": sample.get("section") or src.get("section") or "",
                "role": sample.get("claim_role") or src.get("claim_role") or "",
                "source_file": src.get("source_file") or "",
                "review_note": sample.get("review_note") or "",
                "original_claim_id": cid,
            }
        )

    # 重新编号并写上下文
    records: list[dict[str, Any]] = []
    texts = [str(item["text"]) for item in kept_raw]
    for index, item in enumerate(kept_raw):
        records.append(
            {
                "claim_id": "C%02d" % (index + 1),
                "claim_zh": item["text"],
                "claim_role": item.get("role") or "",
                "context_before": texts[index - 1] if index > 0 else "",
                "context_after": texts[index + 1] if index + 1 < len(texts) else "",
                "section": item.get("section") or "",
                "source_file": item.get("source_file") or "",
                "original_claim_id": item.get("original_claim_id") or "",
            }
        )
    return records

