"""检索充分性检查 + 程序化关键词补检闭环（原创，非 A-RAG 开源部分）。

位置：``CrossLingualRetrievalPipeline`` 中 ``agent.run`` 结束之后、``_finalize`` 之前。
Agent 主循环一行不改；本模块在其产出上追加闭环：

    LLM 判断「现有 top-N 候选能否判断观点句是否存在信息失真」
      -> 不足则输出 missing_point + keywords(+anchors)
      -> 程序化句级关键词补检，命中句直接入 EvidenceBoard（source=keyword_retry）
      -> 最多 ``SufficiencyConfig.max_rounds`` 轮（默认 2）
      -> 仍不足则输出 INCONCLUSIVE 覆盖 + retrieval_hint 交给人工，不再消耗 token

判分原则：补检命中句用 bge-reranker 打**真实分**（与 ``ReadChunkTool._score_unranked``
同范式），避免启发式高分污染双阈值门的 strong_hits 判定；rerank API 失败时才降级为
启发式分，且封顶在 high 阈值之下——降级分只改变句子的可见性，永不参与
SUPPORTED / NO_EVIDENCE 的强判定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from api_client.openai_compat import build_messages
from api_client.schemas import extract_json

from .evidence_board import Candidate, EvidenceBoard
from .index_store import IndexStore
from .schemas import RetrievalSufficiency, SufficiencyCheck

# --------------------------------------------------------------------------- 提示词

SUFFICIENCY_SYSTEM_PROMPT = """You are a retrieval-sufficiency auditor for a plant-science hallucination-detection pipeline. A Chinese claim from a WeChat article is verified against an ENGLISH paper corpus. You see the top-ranked sentences currently retrieved for the claim.

Your single question: are these sentences SUFFICIENT to judge whether the claim DISTORTS the paper (not merely whether they share a topic)?

A claim is insufficiently covered when a CORE checkable statement is missing from the retrieved sentences - for example: a figure/panel the claim cites as its main evidence (Fig. 1b), a concrete method it names (paraffin-embedded sections, microtome), a number/metric, a species comparison, a gene name. Topic overlap without the specific statement does NOT count as coverage, but auxiliary details (e.g. the exact image content inside a cited figure, secondary qualifiers) do NOT require their own candidate sentence.

Output STRICT JSON only, no markdown:
{"sufficient": bool,
 "missing_point": "<=40 Chinese characters naming the single most checkable CORE statement the retrieved sentences fail to cover; empty string if sufficient",
 "keywords": ["2-4 concrete English search terms for the missing point (method terms, figure panel words, gene/species names); do NOT translate the whole claim; do NOT repeat generic topic words already covered"],
 "anchors": ["0-2 short literal strings that appear VERBATIM in the claim: figure/panel numbers like 1b, gene names, species names; never invent or generalize"]}

Rules:
- sufficient=true if the claim's MAIN assertions are covered by at least one candidate each. Do NOT demand a separate sentence for every auxiliary detail; if the core claim can already be judged (distorted vs. faithful), answer sufficient=true.
- The absence of a sentence in the retrieved pool does NOT mean the paper lacks that content - it only means the current retrieval is insufficient.
- When in doubt, prefer sufficient=true unless a concrete checkable statement (figure/panel, method, number, gene name) is clearly absent from the pool.
- keywords must be searchable in an English academic corpus."""

SUFFICIENCY_USER_TEMPLATE = """CLAIM (Chinese): {claim_zh}

TOP-{pool_size} RETRIEVED (sentence_id | English text):
{pool_lines}
{previous_block}"""

PREVIOUS_BLOCK_TEMPLATE = """Previous round judged INSUFFICIENT. missing_point: {missing_point}; keywords already tried: {keywords}.
Re-check with the wider pool below; do not repeat the same keywords unless unavoidable."""


class SufficiencyChecker:
    """调共享 QwenClient 做一轮充分性判断（不走 Agent 多轮循环）。"""

    def __init__(self, client: Any, *, top_n: int = 5, verbose: bool = False) -> None:
        self.client = client
        self.top_n = top_n
        self.verbose = verbose

    def check(
        self,
        claim_zh: str,
        pool: list[dict[str, Any]],
        *,
        round_no: int,
        previous: Sequence[SufficiencyCheck] | None = None,
    ) -> SufficiencyCheck:
        prompt = self._build_prompt(claim_zh, pool, round_no=round_no, previous=previous)
        try:
            payload = self.client.chat_json(
                build_messages(prompt, system=SUFFICIENCY_SYSTEM_PROMPT), temperature=0.0
            )
        except Exception as exc:
            # 降级：原生 JSON 模式不可用/失败时，改用宽松解析（同 claim_extractor 范式）
            try:
                raw = self.client.ask(prompt, system=SUFFICIENCY_SYSTEM_PROMPT)
                payload = extract_json(raw)
            except Exception as exc2:
                if self.verbose:
                    print("充分性检查调用失败: %s; %s" % (type(exc).__name__, exc2))
                return SufficiencyCheck(round=round_no, sufficient=False, error=str(exc2))
        return _normalize_check(payload, claim_zh, round_no)

    def _build_prompt(
        self,
        claim_zh: str,
        pool: list[dict[str, Any]],
        *,
        round_no: int,
        previous: Sequence[SufficiencyCheck] | None,
    ) -> str:
        pool_size = len(pool)
        if pool:
            pool_lines = "\n".join(
                "%s | %s" % (item.get("sentence_id"), item.get("text")) for item in pool
            )
        else:
            pool_lines = "NONE (no sentences retrieved yet)"
        previous_block = ""
        if round_no > 1 and previous:
            last = previous[-1]
            previous_block = "\n" + PREVIOUS_BLOCK_TEMPLATE.format(
                missing_point=last.missing_point or "(none)",
                keywords=", ".join(last.keywords) or "(none)",
            )
        return SUFFICIENCY_USER_TEMPLATE.format(
            claim_zh=claim_zh,
            pool_size=pool_size,
            pool_lines=pool_lines,
            previous_block=previous_block,
        )


def _normalize_check(payload: Any, claim_zh: str, round_no: int) -> SufficiencyCheck:
    """把 LLM 的 JSON 输出规整为 SufficiencyCheck：截断、去幻觉、去重。"""
    if not isinstance(payload, dict):
        return SufficiencyCheck(round=round_no, sufficient=False, error="invalid_json_object")

    sufficient = bool(payload.get("sufficient"))

    missing_point = str(payload.get("missing_point") or "").strip()[:40]
    if sufficient:
        missing_point = ""

    claim_lower = claim_zh.lower()
    keywords: list[str] = []
    for raw in payload.get("keywords") or []:
        term = str(raw).strip()[:40]
        if not term or term.lower() in keywords:
            continue
        if not any(char.isascii() and char.isalpha() for char in term):
            continue
        keywords.append(term)
        if len(keywords) >= 4:
            break

    anchors: list[str] = []
    for raw in payload.get("anchors") or []:
        anchor = str(raw).strip()[:16]
        if not anchor:
            continue
        # 锚点必须是 claim 的逐字子串，否则视为幻觉直接丢弃
        if anchor.lower() not in claim_lower:
            continue
        if anchor.lower() not in [item.lower() for item in anchors]:
            anchors.append(anchor)
        if len(anchors) >= 2:
            break

    return SufficiencyCheck(
        round=round_no,
        sufficient=sufficient,
        missing_point=missing_point,
        keywords=keywords,
        anchors=anchors,
    )


# --------------------------------------------------------------------------- 句级补检


@dataclass
class MatchedSentence:
    sentence_id: int
    chunk_id: str
    text: str
    matched_terms: list[str]


def _expand_keyword_variants(keywords: Sequence[str]) -> list[str]:
    """'-' 与空格互变（paraffin-embedded <-> paraffin embedded），提高句级命中率。"""
    variants: list[str] = []
    for keyword in keywords:
        term = str(keyword).strip()
        if not term:
            continue
        if term not in variants:
            variants.append(term)
        if "-" in term:
            swapped = term.replace("-", " ")
            if swapped not in variants:
                variants.append(swapped)
        elif " " in term:
            swapped = term.replace(" ", "-")
            if swapped not in variants:
                variants.append(swapped)
    return variants


class KeywordBackfill:
    """句级关键词补检：命中句直接构造 Candidate 入看板，不依赖 Agent read_chunk。"""

    def __init__(self, store: IndexStore, sf_client: Any, *, verbose: bool = False) -> None:
        self.store = store
        self.client = sf_client
        self.verbose = verbose

    def match(
        self,
        keywords: Sequence[str],
        anchors: Sequence[str],
        *,
        limit: int = 30,
    ) -> list[MatchedSentence]:
        variants = _expand_keyword_variants(keywords)
        rows = self.store.search_sentences_by_keywords(
            variants, anchors=anchors, limit=limit
        )
        return [
            MatchedSentence(sentence_id=sentence_id, chunk_id=chunk_id, text=text, matched_terms=terms)
            for sentence_id, chunk_id, text, terms in rows
        ]

    def score_and_build(
        self, claim_zh: str, matched: list[MatchedSentence], board: EvidenceBoard
    ) -> list[Candidate]:
        if not matched:
            return []
        gate = board.gate
        scores = self._rerank_scores(claim_zh, matched)
        if scores is None:
            # 降级启发式分：封顶在 high-0.05，只改变可见性，不参与 strong_hits 判定
            top = max(board.scores(), default=0.0)
            floor = min(gate.config.high - 0.05, max(top * 0.9, gate.config.low + 0.05))
            scores = [floor] * len(matched)
            if self.verbose:
                print("补检 rerank 失败，降级启发式分 %.4f（不参与强判定）" % floor)
        return [
            Candidate(
                chunk_id=item.chunk_id,
                sentence=item.text,
                sentence_index=item.sentence_id,
                rerank_score=scores[index],
                embed_score=0.0,
                matched_terms=list(item.matched_terms),
                round_index=board.search_rounds + 1,
                source="keyword_retry",
            )
            for index, item in enumerate(matched)
        ]

    def _rerank_scores(
        self, claim_zh: str, matched: list[MatchedSentence]
    ) -> list[float] | None:
        """一次批量 rerank 给全部命中句打分；失败返回 None（由调用方降级）。"""
        try:
            result = self.client.rerank(
                claim_zh, [item.text for item in matched], top_n=len(matched)
            )
        except Exception:
            return None
        scores = [0.0] * len(matched)
        for item in result.items:
            if 0 <= item.index < len(matched):
                scores[item.index] = float(item.score)
        return scores


# --------------------------------------------------------------------------- 循环编排


def _top_pool(board: EvidenceBoard, n: int) -> list[dict[str, Any]]:
    """取看板里 rerank 最高的 n 条可定位句（只带 sentence_id + 英文原句）。"""
    pool: list[dict[str, Any]] = []
    for candidate in board.all_candidates():
        if candidate.sentence_index < 0:
            continue
        pool.append({"sentence_id": candidate.sentence_index, "text": candidate.sentence})
        if len(pool) >= n:
            break
    return pool


def _build_hint(checks: Sequence[SufficiencyCheck], sufficient: bool) -> str:
    """程序生成人工提示（≤100 中文字符），不再调 LLM。"""
    if not checks:
        return ""
    last = checks[-1]
    if last.error:
        return "检索充分性检查失败（%s），请人工复核该条证据。" % last.error[:60]
    if sufficient:
        return ""
    parts = ["语义检索未覆盖「%s」" % (last.missing_point or "关键表述")]
    if last.keywords:
        parts.append("建议英文关键词 %s 手工复核" % ", ".join(last.keywords[:4]))
    if last.anchors:
        parts.append("优先图号/锚点 %s" % ", ".join(last.anchors))
    return "；".join(parts)[:100]


def run_sufficiency_loop(
    board: EvidenceBoard,
    *,
    store: IndexStore,
    sf_client: Any,
    qwen_client: Any,
    config: Any,
    verbose: bool = False,
) -> RetrievalSufficiency:
    """充分性检查 -> 关键词补检，最多 max_rounds 轮；返回结构化结论。"""
    checker = SufficiencyChecker(qwen_client, top_n=config.top_n, verbose=verbose)
    backfill = KeywordBackfill(store, sf_client, verbose=verbose)
    checks: list[SufficiencyCheck] = []

    for round_no in range(1, int(config.max_rounds) + 1):
        pool_size = config.top_n if round_no == 1 else min(config.top_n * 2, 20)
        pool = _top_pool(board, pool_size)
        check = checker.check(board.claim_zh, pool, round_no=round_no, previous=checks)
        if check.error:
            # 硬停止：检查器不可用，不再空转
            checks.append(check)
            break
        checks.append(check)
        if check.sufficient:
            # 硬停止 1：现有候选已足以判断失真
            break

        if not check.keywords:
            # LLM 没给词就不编造补检；missing_point 已足够写 hint
            continue

        matched = backfill.match(check.keywords, check.anchors, limit=config.backfill_max_hits)
        if matched:
            candidates = backfill.score_and_build(board.claim_zh, matched, board)
            existing_ids = {c.sentence_index for c in board.all_candidates()}
            added_ids = [
                candidate.sentence_index
                for candidate in candidates
                if candidate.sentence_index not in existing_ids
            ]
            check.added_sentence_ids = added_ids
            # 补检是一次真实检索：计轮次，让 build_output 的轮次判定统计诚实
            board.add_candidates(candidates, count_as_round=True)
            if verbose:
                print(
                    "补检第 %d 轮: 命中 %d 句，新入池 %s"
                    % (round_no, len(matched), added_ids)
                )

    sufficient = bool(checks) and any(c.sufficient for c in checks) and not any(
        c.error for c in checks
    )
    return RetrievalSufficiency(
        sufficient=sufficient,
        checks=checks,
        retrieval_hint=_build_hint(checks, sufficient),
    )
