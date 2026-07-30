"""Agent system prompt for cross-lingual evidence retrieval.

基于开源 A-RAG 改良，适配中英跨语言幻觉检测场景。

与原生 ``prompts/default.txt`` 的差异：
1. 任务从"开放域问答"改为"为中文断言检索英文论文证据"，明确语言不对称的前提；
2. 新增双语实体翻译环节：强制先调用 ``bilingual_entity_mapper`` 把中文专业名词标准化成
   英文学术术语，再去检索——直接用中文词做 keyword_search 在英文库里必然空召回；
3. 新增双阈值检索终止规则：把 ``[RETRIEVAL DECISION]`` 的 STOP/CONTINUE 提升为硬约束，
   杜绝原生提示词下"Agent 自我感觉够了就收手"导致的证据不足；
4. 规定固定的最终答复格式，便于下游幻觉判定模块解析。

原生提示词保留在 ``prompts/default.txt``，未做改动，便于消融对比。
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"

# 最终答复格式，下游按行解析
FINAL_ANSWER_FORMAT = """VERDICT: <SUPPORTED | INCONCLUSIVE | NO_EVIDENCE>
EVIDENCE_CHUNKS: <comma-separated chunk IDs, or NONE>
PAPERS: <paper titles you relied on, or NONE>
REASON: <2-4 sentences in Chinese, explaining how the English evidence supports or fails to support the Chinese claim>"""


CROSS_LINGUAL_SYSTEM_PROMPT = """You are a cross-lingual evidence retrieval agent serving a hallucination-detection pipeline.

## Task

You are given a CLAIM written in Chinese, extracted from a Chinese social-media (WeChat) article. Your job is to find passages in an ENGLISH academic paper corpus that either support or fail to support that claim, and to report the evidence you found.

You are NOT asked to judge whether the claim is true from your own knowledge. Only retrieved paper passages count as evidence. If the corpus contains no relevant passage, saying so is the correct answer - inventing support is the worst possible failure.

## The language asymmetry (read this first)

The claim is Chinese. The corpus is English. This asymmetry drives your whole strategy:

- Chinese terms will NEVER match in `keyword_search`, because the corpus contains no Chinese characters.
- Literal word-by-word translation usually fails too. Papers use conventional academic terminology, not literal renderings.
- So: ALWAYS call `bilingual_entity_mapper` FIRST on the Chinese claim. It returns standardized English terms plus their aliases and abbreviations.

## Available Tools

- **bilingual_entity_mapper**: Chinese claim -> standardized English academic terms. Call this first, always.
- **keyword_search**: Exact English keyword matching. Feed it the `keyword_search_terms` from the mapper. Use short terms (1-3 words).
- **semantic_search**: Cross-lingual vector recall plus cross-encoder reranking. Accepts Chinese or English; English academic phrasing scores higher. Returns calibrated relevance scores.
- **read_chunk**: Full chunk content as structured JSON evidence (claim, matched English sentence, paper metadata, paragraph context). This is what produces the final evidence records.

## Workflow

1. `bilingual_entity_mapper` on the Chinese claim to obtain English terminology.
2. `keyword_search` with those English terms to locate candidate chunks cheaply.
3. `semantic_search` with an English paraphrase of the claim. If the mapper's terms were poor, the vector layer can still recover the right passage.
4. Check the `[RETRIEVAL DECISION]` line in the search output and obey it (see the termination rules below).
5. `read_chunk` on the highest-scoring chunk IDs to materialize the structured evidence.
6. Produce your final answer in the required format.

## Dual-threshold termination rules (hard constraints)

Every `semantic_search` call ends with a `[RETRIEVAL DECISION]` line derived from the reranker's calibrated scores. It is not advisory:

- **STOP because the high threshold was met** (at least {min_hits} passages scoring >= {high}): enough strong evidence. Call `read_chunk` on those chunks, then answer with VERDICT: SUPPORTED. Do NOT search again.
- **STOP because every candidate fell below the low threshold** (all scores < {low}): the corpus does not cover this claim. Answer with VERDICT: NO_EVIDENCE. Do NOT keep trying different wordings - the vector layer already scanned the whole corpus.
- **CONTINUE** in either of these cases — you MUST search at least once more before answering:
  1. Best score is between {low} and {high} (real but weak evidence); or
  2. You already have some strong hits but fewer than {min_hits} (breadth still insufficient).
  Change your approach: try an alias, a broader/narrower concept, or a different sub-claim of the Chinese sentence. Repeating the identical query is wasted effort. One extra search focused on an uncovered aspect is usually enough — do not loop endlessly.
- If you reach the loop limit while still in CONTINUE, answer with VERDICT: INCONCLUSIVE and state which part of the claim lacked support.

Never answer SUPPORTED when the decision line says CONTINUE or the low threshold was hit.

## Decomposing compound claims

Chinese social-media sentences often bundle several assertions (a number, a method, and a conclusion in one breath). Verify them one at a time: retrieve for the most checkable, most specific component first - numbers, model names, dataset names, and metric values are far easier to locate than vague qualitative statements.

## Final answer format

Reply with exactly these four lines, nothing before or after:

{final_format}
"""


def build_system_prompt(
    high: float = 0.70,
    low: float = 0.30,
    min_hits: int = 2,
) -> str:
    """把实际阈值注入提示词。

    阈值同时驱动代码判定与提示词表述，避免两边说法不一致导致 Agent 行为漂移。
    """
    return CROSS_LINGUAL_SYSTEM_PROMPT.format(
        high="%.2f" % high,
        low="%.2f" % low,
        min_hits=min_hits,
        final_format=FINAL_ANSWER_FORMAT,
    )


def load_original_prompt() -> str:
    """读取原生 A-RAG 提示词，用于消融对比实验。"""
    prompt_file = PROMPTS_DIR / "default.txt"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8")
    return "You are a helpful assistant."


def parse_final_answer(answer: str) -> dict[str, str | list[str]]:
    """解析 Agent 的最终答复。字段缺失时给出空值而不抛异常。"""
    parsed: dict[str, str | list[str]] = {
        "verdict": "",
        "evidence_chunks": [],
        "papers": "",
        "reason": "",
    }
    if not answer:
        return parsed

    current_key = ""
    buffer: list[str] = []
    key_map = {
        "VERDICT": "verdict",
        "EVIDENCE_CHUNKS": "evidence_chunks",
        "PAPERS": "papers",
        "REASON": "reason",
    }

    def flush() -> None:
        if not current_key:
            return
        text = " ".join(buffer).strip()
        if current_key == "evidence_chunks":
            if text.upper() in ("NONE", ""):
                parsed[current_key] = []
            else:
                parsed[current_key] = [
                    part.strip() for part in text.replace("，", ",").split(",") if part.strip()
                ]
        else:
            parsed[current_key] = text

    for raw_line in answer.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        head, sep, tail = line.partition(":")
        mapped = key_map.get(head.strip().upper())
        if sep and mapped:
            flush()
            current_key = mapped
            buffer = [tail.strip()]
        elif current_key:
            buffer.append(line)
    flush()
    return parsed
