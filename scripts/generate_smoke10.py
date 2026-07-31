#!/usr/bin/env python3
"""Generate smoke-test annotation draft (first 10 claims)."""
import json
from pathlib import Path

BASE = Path(__file__).parent.parent
INPUT = BASE / "outputs/P001/A001/claim_evidence_pairs.jsonl"
OUTPUT = BASE / "data/annotations/P001/P001_A001_annotation_draft_smoke10.json"

def load_claims(path, n=10):
    claims = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= n:
                break
            claims.append(json.loads(line))
    return claims

def get_ev(claim):
    """Extract classify and review evidences from claim."""
    ce = claim.get("classify_evidences", claim.get("evidences", []))
    if len(ce) > 5:
        ce = ce[:5]
    re = claim.get("review_evidences", claim.get("evidences", []))
    if len(re) > 10:
        re = re[:10]
    return ce, re

def find_text(re, sid):
    for e in re:
        if e["sentence_id"] == sid:
            return e["text"]
    return None

def build_sample(cid, claim_zh, ce, re, gold_sids, is_answerable, ev_level,
                 primary, secondary, is_acc, severity,
                 ev_judge, cls_reason, key_diffs,
                 unsup_verdict, unsup_reason, unsup_kw, unsup_range,
                 hints, needs_review, review_focus, ai_conf,
                 top5_best, better_in_pool, rag_notes,
                 paper_id="P001", article_id="A001", source_type="high_quality"):

    # Build gold retrieval evidences
    gold_evs = []
    for sid in gold_sids:
        txt = find_text(re, sid)
        if txt:
            gold_evs.append({"sentence_id": sid, "text": txt})

    return {
        "sample_id": f"{paper_id}-{article_id}-{cid}",
        "paper_id": paper_id,
        "article_id": article_id,
        "article_source_type": source_type,
        "claim_zh": claim_zh,
        "system_retrieval": {
            "classify_evidences": [{"rank": e["rank"], "sentence_id": e["sentence_id"], "text": e["text"]} for e in ce],
            "review_evidences": [{"rank": e["rank"], "sentence_id": e["sentence_id"], "text": e["text"]} for e in re]
        },
        "gold_retrieval": {
            "evidences": gold_evs,
            "sentence_ids": gold_sids if is_answerable else [],
            "is_answerable": is_answerable
        },
        "gold_classification": {
            "evidence_level": ev_level,
            "primary_type": primary if ev_level != "No_Evidence" else "",
            "secondary_types": secondary if ev_level != "No_Evidence" else [],
            "is_accurate": is_acc,
            "severity": severity
        },
        "analysis": {
            "evidence_judgement": ev_judge,
            "classification_reason": cls_reason,
            "key_differences": key_diffs,
            "rag_review": {
                "top5_is_best": top5_best,
                "better_in_review_pool": better_in_pool,
                "notes": rag_notes
            },
            "unsupported_diagnosis": {
                "verdict": unsup_verdict,
                "reasoning": unsup_reason,
                "suggested_keywords": unsup_kw,
                "suggested_sentence_ranges": unsup_range
            },
            "manual_check_hints": hints,
            "needs_manual_review": needs_review,
            "review_focus": review_focus,
            "ai_confidence": ai_conf
        },
        "human_verified": False
    }

# ── C01 ──
C01 = lambda ce, re, zh: build_sample(
    "C01", zh, ce, re,
    gold_sids=[3, 4, 43, 329, 2], is_answerable=True, ev_level="With_Evidence",
    primary="fact_addition", secondary=["certainty_amplification"], is_acc=False, severity="moderate",
    ev_judge=(
        "拆为4个可核查分句：\n"
        "(1)「通过空间转录组技术和细胞谱系追踪」→ s3/s329 明确支撑空间转录组+细胞谱系方法，"
        "s60/s115 进一步说明方法细节。论文用的是 'spatial transcriptome mapping' 和 "
        "'cell lineage reconstructions'，非严格意义上的 'lineage tracing'（遗传标记示踪），"
        "术语有轻微偏移但主体可确认；\n"
        "(2)「首次揭示了黄瓜下位子房的发育机制」→ s3 揭示 receptacle growth 驱动下位子房形成，"
        "s43 确认 FIM 持续活跃→receptacle 快速生长→下位子房的发育链条，机制层面有坚实证据。"
        "但「首次」在论文中无任何对应措辞——s2 反而说 'remain largely unknown'，"
        "论文标题 'Developmental innovation' 指性状的演化创新，非研究的首创性声称。"
        "论文全文未出现 'first'/'first time' 声明，'首次' 为公众号自行添加的事实；\n"
        "(3)「阐明了KNOX1转录因子在…花器官发育…中的核心作用」→ "
        "s4 确认 KNOX1(KNAT2-like1) 敲除导致 receptacle 生长停滞→上位子房+两性花，"
        "但论文将 KNOX1 定位为 'receptacle-specific'，并未声称其在整个「花器官发育」中的核心作用。"
        "论文标题用 'orchestrated by KNOX1' 暗示协调角色，但 claim 的「阐明…核心作用」力度更强；\n"
        "(4)「性别决定中的核心作用」→ s337 确认性决定基因在 receptacle 中表达，"
        "s277 表明 KNAT2-like1 突变同时影响花性别和子房位置，存在 linkage，"
        "但「核心作用」的措辞将论文的 'orchestrated by' 和 'suggesting…a direct link' 升级为定论。\n"
        "top-5 覆盖核心方法(s329)、机制(s3,s43)、KO证据(s4)和知识空白(s2)，检索质量好。"
    ),
    cls_reason=(
        "primary_type=fac_addition：论文未在任何位置声称 'first'/'first time'/'首次'，"
        "s2 反而明确说此前机制 'remain largely unknown'，论文标题 'Developmental innovation' "
        "指性状的演化创新而非研究首发声明。公众号添加 '首次' 构成事实添加。\n"
        "secondary=certainty_amplification：论文用 'orchestrated by'（协调）、"
        "'suggesting…a direct link'（暗示关联）、'provide…insights into'（提供见解），"
        "claim 用「阐明…核心作用」。确定性逐级抬升：insights→揭示，orchestrated→核心作用。"
        "且论文限定 KNOX1 为 receptacle-specific，claim 将其作用推广至整体「花器官发育」。\n"
        "为何不是 scope_generalization：范围泛化（黄瓜→葫芦科植物、花托→花器官整体）"
        "在此是次要维度；主因是添加了论文未声称的 '首次' 和将审慎措辞升级为定论。"
    ),
    key_diffs=[
        {"type": "fact_addition", "paper_expression": "However, the developmental mechanisms underlying inferior ovary formation remain largely unknown.",
         "article_expression": "首次揭示了黄瓜下位子房的发育机制",
         "description": "论文明确说此前机制 largely unknown，自身未提首次；公众号添加 primacy claim"},
        {"type": "certainty_amplification", "paper_expression": "provide developmental and mechanistic insights into",
         "article_expression": "首次揭示了…阐明了核心作用",
         "description": "论文用审慎措辞 insights into，公众号升级为揭示+阐明+核心作用"},
        {"type": "scope_generalization", "paper_expression": "receptacle-specific KNOX1 transcription factor in cucumber",
         "article_expression": "KNOX1转录因子在葫芦科植物花器官发育…中的核心作用",
         "description": "论文限定 receptacle-specific + cucumber，claim 推广至葫芦科植物整体花器官发育"}
    ],
    unsup_verdict="not_applicable", unsup_reason="核心断言均有证据支撑；失真是措辞层面的放大和添加，非证据缺失。",
    unsup_kw=[], unsup_range="",
    hints=(
        "重点核对：(1)论文全文(Discussion/Methods 开头)是否在别处有 'first' 或 '首次' 声明（大概率没有）；"
        "(2)KNOX1 的作用是否可合理描述为「核心」——若审稿人认为论文标题已用 'orchestrated' 暗示核心地位，"
        "可下调 severity 至 mild 或改 primary_type 为 certainty_amplification；"
        "(3)「葫芦科植物」vs「黄瓜」的范围推广是否在上下文中可接受（论文标题提及 cucurbits）。"
    ),
    needs_review=True, review_focus=["primary_type", "secondary_types"], ai_conf="medium",
    top5_best=True, better_in_pool=[], rag_notes="top-5 包含最关键的机制句(s3,s43)和KO证据(s4)，检索质量好。rank6(s0)为标题/作者脏句应忽略。"
)

# ── C02 ──
C02 = lambda ce, re, zh: build_sample(
    "C02", zh, ce, re,
    gold_sids=[8, 9, 1], is_answerable=True, ev_level="With_Evidence",
    primary="accurate", secondary=[], is_acc=True, severity="none",
    ev_judge=(
        "该句为背景知识陈述。可核查分句：\n"
        "(1)「子房相对于其他花器官的位置」→ s8 明确定义 'spatial arrangement of the outer "
        "floral organs relative to the ovary' 决定分类结果；\n"
        "(2)「是重要分类学特征之一」→ s9 区分上位/下位子房的分类标准即基于与萼片花瓣雄蕊的"
        "相对位置；s1 称下位子房为 'key morphological innovations'，佐证其分类学重要性。\n"
        "top-5 中 s8(rank2)和 s9(rank1)直接对应定义，覆盖充分。s7 为花器官轮生排列的间接背景。"
    ),
    cls_reason=(
        "准确传达。s8 'determines whether the ovary is classified as superior or inferior' "
        "与 claim「重要分类学特征」完全一致；s9 具体列出了分类所依据的花器官（sepals, petals, "
        "stamens），与 claim 的背景知识陈述精确匹配。无措辞放大或事实添加。"
    ),
    key_diffs=[],
    unsup_verdict="not_applicable", unsup_reason="所有可核查分句均有明确证据支撑。", unsup_kw=[], unsup_range="",
    hints="基本准确，人工确认「分类学特征」与论文 'classified as superior or inferior' 的语义对应即可。",
    needs_review=False, review_focus=["none"], ai_conf="high",
    top5_best=True, better_in_pool=[],
    rag_notes="top-5 覆盖充分，s8/s9 直接对应。rank3(s1)含标题/作者粘连但正文部分有用。"
)

# ── C03 ──
C03 = lambda ce, re, zh: build_sample(
    "C03", zh, ce, re,
    gold_sids=[9, 8], is_answerable=True, ev_level="With_Evidence",
    primary="accurate", secondary=[], is_acc=True, severity="none",
    ev_judge=(
        "教科书级定义陈述，仅需确认措辞对应。s9 逐字对应：'Inferior ovaries are located below "
        "the attachment points of the sepals, petals and stamens, whereas superior ovaries are "
        "positioned above these other floral organs.' ——萼片/花瓣/雄蕊=sepals/petals/stamens，"
        "位置的上下=located below/above，上位/下位=superior/inferior。完美对应。\n"
        "s8 补充说明该位置关系决定分类结果。top-5 中 s9(rank1)和 s8(rank3)直接覆盖。\n"
        "注意 rank4(s303)为图注残片，rank5(s37)为图注/标签噪声，不应纳入金标。"
    ),
    cls_reason=(
        "无失真。claim 是 s9 的准确中文化转述，所有术语一一对应："
        "萼片=sepals、花瓣=petals、雄蕊=stamens、上位子房=superior ovary、下位子房=inferior ovary。"
        "位置关系的描述（根据…相对位置→relative to, located below/above）准确。"
    ),
    key_diffs=[],
    unsup_verdict="not_applicable", unsup_reason="claim 完整对应 s9 内容。", unsup_kw=[], unsup_range="",
    hints="无需特别核对，准确转述教科书定义。",
    needs_review=False, review_focus=["none"], ai_conf="high",
    top5_best=True, better_in_pool=[],
    rag_notes="s9(rank1)即最佳匹配。rank2(s10)偏演化方向而非定义，rank4/5 含图注噪声但不影响 top-5 有最佳匹配。"
)

# ── C04 ──
C04 = lambda ce, re, zh: build_sample(
    "C04", zh, ce, re,
    gold_sids=[12, 10, 49, 1], is_answerable=True, ev_level="With_Evidence",
    primary="accurate", secondary=[], is_acc=True, severity="none",
    ev_judge=(
        "拆为3个分句，均有直接对应：\n"
        "(1)「下位子房是…关键创新性状」→ s1 'inferior ovaries are key morphological "
        "innovations' 逐字对应；s328 'final derived feature of the gynoecium is the "
        "evolution of an inferior ovary' 佐证其演化重要性；\n"
        "(2)「多次独立进化」→ s10 'independently evolved from superior ovaries multiple "
        "times throughout angiosperm evolution' 完整覆盖，s49 重复确认 'originated "
        "independently multiple times during angiosperm evolution'；\n"
        "(3)「更好地保护雌蕊并为胚珠发育分配更多能量」→ s12 是这一分句的英文本源："
        "'The adaptive advantage of an inferior ovary includes providing increased "
        "protection to developing gynoecia and allocating more energy to developing "
        "ovules'，且带有引用标记 citation 4，表明这是引用前人文献。claim 用「被认为」"
        "准确反映了这是学界共识而非论文原创主张。\n"
        "top-5 中 s12(rank1)完美覆盖适应意义，s10(rank2)覆盖独立进化，覆盖极佳。"
    ),
    cls_reason=(
        "无失真。claim 用「被认为」对应论文引用前人文献（citation 4）的审慎态度；"
        "「关键创新性状」= 'key morphological innovations'；「多次独立进化」= "
        "'independently evolved…multiple times throughout angiosperm evolution'；"
        "「保护雌蕊」= 'protection to developing gynoecia'；「分配更多能量」= "
        "'allocating more energy to developing ovules'。全部准确转述。"
    ),
    key_diffs=[],
    unsup_verdict="not_applicable", unsup_reason="所有分句均有直接对应的论文原文。", unsup_kw=[], unsup_range="",
    hints="准确。注意 s12 带有 citation 标记 '4'，确认引用来源可追溯。",
    needs_review=False, review_focus=["none"], ai_conf="high",
    top5_best=True, better_in_pool=[],
    rag_notes="rank1(s12)即最佳句覆盖适应意义；rank2(s10)覆盖演化模式。检索质量优秀。rank5(s11)为不完整句('and are present in several major angiosperm clades3')属于碎片，但由于最佳句已在前2位，不影响top-5质量。"
)

# ── C05 ──
C05 = lambda ce, re, zh: build_sample(
    "C05", zh, ce, re,
    gold_sids=[2, 30, 31, 331, 332], is_answerable=True, ev_level="With_Evidence",
    primary="accurate", secondary=[], is_acc=True, severity="none",
    ev_judge=(
        "拆为5个可核查分句：\n"
        "(1)「葫芦科植物（如黄瓜、西瓜、甜瓜等）」→ s30 列举 'cucumber, melon, watermelon "
        "and pumpkin'，对应准确（甜瓜=melon）；\n"
        "(2)「果实均来源于下位子房」→ s31 'their fleshy fruits develop from inferior "
        "ovaries that originate from female floral buds' 直接支撑，括号注 'melon fruit "
        "may develop from bisexual flowers' 提示甜瓜有例外，'均'字略有绝对化但论文主体支持；\n"
        "(3)「多为单性花」→ s30 'has numerous species with predominantly unisexual "
        "flowers'，predominantly=多为，准确对应；\n"
        "(4)「下位子房形成机制…还尚未明确」→ s2 'remain largely unknown' 直接支撑；\n"
        "(5)「与性别决定机制的关系还尚未明确」→ s2 仅提下位子房机制 unknown，未直接提关系 unknown，"
        "但 s332 说两者 'appear to have coevolved'（使用审慎的 appear to），s331 说 'provided "
        "new insights'（仅insights非定论），说明此关系此前确实不够明确，作为背景设问合理。\n"
        "注意 s277（审核池 rank6）提出 KNAT2-like1 突变同时影响两性状 'suggesting…a direct "
        "link'——论文确实提出了关联模型，但这是论文自己的发现，而 claim 是在描述此发现之前的"
        "知识空白状态，属于合理的背景铺垫。top-5 覆盖所有可核查分句。"
    ),
    cls_reason=(
        "准确。各分句均有论文原文支撑：s2='remain largely unknown'→「尚未明确」；"
        "s30='predominantly unisexual flowers'→「多为单性花」；"
        "s31='fleshy fruits develop from inferior ovaries'→「果实来源于下位子房」。\n"
        "关于甜瓜例外（bisexual flowers），论文正文仍将其归为从 inferior ovary 发育（雌花），"
        "'均'字的轻微泛化属 mild 级别，不影响整体准确性判定。\n"
        "关于「关系还尚未明确」：论文 s277/s331/s332 表明研究发现了关联，"
        "但 claim 这一句是作为研究背景/动机陈述，描述的是该研究开展前的知识状态，"
        "与 s2 'remain largely unknown' 的定位一致，非失真。"
    ),
    key_diffs=[],
    unsup_verdict="not_applicable", unsup_reason="所有核心分句均有支撑。", unsup_kw=[], unsup_range="",
    hints=(
        "轻量核对：(1)甜瓜的 bisexual flowers 例外是否削弱「均来源于下位子房」——"
        "若需精确表述可标注 mild scope_generalization；"
        "(2)确认论文是否有葫芦科果实不来源于下位子房的明确反例。"
    ),
    needs_review=False, review_focus=["none"], ai_conf="high",
    top5_best=True, better_in_pool=[],
    rag_notes="top-5 质量好：s2覆盖知识空白、s30覆盖物种与花性别、s31覆盖果实来源、s331/s332覆盖性别决定与下位子房的关联。s6(s277)在审核池中补充了 linkage 证据，但 top-5 已足以支撑 claim。"
)

# ── C06 ──
C06 = lambda ce, re, zh: build_sample(
    "C06", zh, ce, re,
    gold_sids=[52, 3, 4], is_answerable=True, ev_level="With_Evidence",
    primary="accurate", secondary=[], is_acc=True, severity="none",
    ev_judge=(
        "s52 是最关键句：'Cucumber and tomato have a similar floral structure, "
        "but their differences in flower sex and ovary position make them excellent "
        "subjects for comparison'——几乎逐词对应 claim 全文。\n"
        "拆为3个分句：(1)「黄瓜和番茄具有相似花器官结构」= s52 'similar floral structure'；\n"
        "(2)「前者为下位子房和单性花，后者为上位子房和两性花」→ s3 确认 cucumber=inferior ovary, "
        "tomato=superior ovary；s4 确认 cucumber knockout→bisexual flowers with superior "
        "ovaries（反向证明 WT cucumber 为单性花+下位子房）；s58 确认 tomato 心皮向上生长形成 "
        "superior ovary，且 tomato 为两性花（论文多处确认）；\n"
        "(3)「是研究…理想模式植物」→ s52 'excellent subjects for comparison'，"
        "「理想模式植物」较 'excellent subjects' 略强但语义等价，属合理转述。\n"
        "关键发现：s52 在 classify top-5 之外（rank=10），仅在审核池中出现——"
        "说明 RAG 排序将最佳总括句排到了第10位，但检索本身命中了该句。"
    ),
    cls_reason=(
        "准确。s52 直接对应全文主旨，措辞 'excellent subjects for comparison' → "
        "「理想模式植物」力度略增但未改变含义。所有分句均有证据支撑。\n"
        "RAG 问题：s52(rank10)是最佳总括句但未进入 classify top-5——这属于检索排序问题，"
        "不影响 claim 准确性判定，但需在 rag_review 中标注。"
    ),
    key_diffs=[],
    unsup_verdict="not_applicable", unsup_reason="所有分句有支撑，最佳总括句在审核池内。", unsup_kw=[], unsup_range="",
    hints=(
        "RAG 重点核对：s52(rank=10) 是最佳总括句但未进 classify top-5。"
        "人工确认 s52 是否应列为 gold sentence_ids 之首，并在 rag_review 标注 top5_is_best=false。"
    ),
    needs_review=True, review_focus=["rag_top5"], ai_conf="high",
    top5_best=False, better_in_pool=[52],
    rag_notes="top-5 中 s3/s4/s44 提供物种对比信息但分散；审核池 rank10 的 s52 是最佳总括句，包含 'similar floral structure' + 'excellent subjects for comparison' 的完整体现，应进 top-5 但未进。"
)

# ── C07 ──
C07 = lambda ce, re, zh: build_sample(
    "C07", zh, ce, re,
    gold_sids=[49, 332], is_answerable=True, ev_level="Weak_Evidence",
    primary="accurate", secondary=[], is_acc=True, severity="none",
    ev_judge=(
        "拆为5个可核查分句：\n"
        "(1)「构建了502个被子植物物种进化树」→ 检索结果中无直接证据提及502这个具体数字\n"
        "或建树方法细节——审核池仅2条，无法确认；\n"
        "(2)「包含花性别和子房位置信息（图1a）」→ s332 引用了 Fig 参考，提示图1a存在，\n"
        "但未确认具体样本量和性状编码细节；\n"
        "(3)「单性花和子房下位性状在进化过程中独立出现多次」→ s49 精确支撑；\n"
        "(4)「在葫芦科植物及其近缘种中连锁出现」→ s332 'in the Cucurbitaceae, both traits\n"
        "appear to have coevolved' 支撑了葫芦科的连锁，但 '近缘种'（Tetramelaceae,\n"
        "Begoniaceae）未被检索到；\n"
        "(5)「这两个性状可能来自于同一进化事件」→ s332 'appear to have coevolved' 为\n"
        "间接支撑（共进化暗示共同起源）。注意：精确对应 'single evolutionary event' 的\n"
        "最佳句 s51（'may have originated from a single evolutionary event in the\n"
        "Cucurbitaceae'）不在审核池中——审核池仅2条，s51未被检索到。\n"
        "⚠ 系统检索仅返回2条证据（review 也仅2条）——严重检索覆盖不足。\n"
        "核心演化模式（独立进化+葫芦科共进化）被 s49/s332 支撑，\n"
        "但方法论细节（502物种）和精确结论句（s51）无法在现有证据中核查。"
    ),
    cls_reason=(
        "Weak_Evidence：演化模式的核心断言（独立进化多次+葫芦科连锁共进化→可能来自同一事件）"
        "被 s49/s332/s51 交叉支撑，且 claim 用「可能」表达了适当的确定性。"
        "但「502个物种」这一具体数值和方法细节无对应证据，"
        "部分原因是系统检索仅返回2条，覆盖严重不足（论文 Methods/Results 开头的系统发育段落"
        "极可能有502物种的描述但未被检索命中）。\n"
        "不应判为 claim 错误——更可能是检索遗漏而非公众号编造。"
        "在现有证据下暂标 accurate（核心结论有支撑），evidence_level=Weak_Evidence，"
        "人工应补充检索502物种的出处。"
    ),
    key_diffs=[],
    unsup_verdict="likely_retrieval_miss",
    unsup_reason=(
        "论文 Methods 或 Results 开头极大概率有502个物种的系统发育描述"
        "（大规模 ancestral state reconstruction 的常规报告格式）。"
        "当前检索仅命中2条，且精确对应 'single evolutionary event' 的最佳句 s51\n"
        "完全未被检索到——这是系统检索严重不足的明确信号。"
        "需人工在论文全文搜索502、phylogeny、ancestral state、single evolutionary event。"
    ),
    unsup_kw=["502", "species", "phylogeny", "ancestral state", "character evolution"],
    unsup_range="Methods/Results 开头部分（约 sentence 20–55），及 Fig 1 legend",
    hints=(
        "关键核对步骤：\n"
        "(1)论文 Results 开头是否明确提到 '502 species' 或 '502 angiosperm species'？"
        "扫 Methods 和 Fig 1 legend；\n"
        "(2)审核池仅2条严重不足——建议打开论文全文搜索 '502'、'phylogeny'、"
        "'ancestral state' 补充更多支撑句到 gold；\n"
        "(3)确认 s51 'may have originated from a single evolutionary event' 即可覆盖 claim 核心结论；\n"
        "(4)若找到502物种出处，将 evidence_level 改为 With_Evidence；"
        "若论文确实没有502这个数字，则需标 primary_type=numerical_distortion。"
    ),
    needs_review=True, review_focus=["evidence_level", "gold_sentence_ids", "rag_top5"], ai_conf="low",
    top5_best=False, better_in_pool=[],
    rag_notes="系统检索仅返回2条证据（classify=review=2条），严重不足。精确对应 'single evolutionary event' 的最佳句 s51 完全未被检索到（不在审核池中）。检索覆盖面极差——502物种建树的方法学段落大概率在论文 Methods/Results 头部但完全未被命中。"
)

# ── C08 ──
C08 = lambda ce, re, zh: build_sample(
    "C08", zh, ce, re,
    gold_sids=[3, 52, 4], is_answerable=True, ev_level="Weak_Evidence",
    primary="accurate", secondary=[], is_acc=True, severity="none",
    ev_judge=(
        "拆为4个可核查分句：\n"
        "(1)「探究葫芦科植物子房下位发育进程」→ s3 明确描述使用空间转录组和细胞谱系方法\n"
        "重建黄瓜花芽发育过程，覆盖研究目的；\n"
        "(2)「对黄瓜花发育过程进行了石蜡切片观察（图1b）」→ 检索结果中未出现 'paraffin\n"
        "section' 或 'histological section' 的明确措辞。当前审核池中最佳的相关句为 s4\n"
        "（KNOX1敲除导致花托生长停滞→上位子房+两性花），但 s4 是遗传学证据而非组织学\n"
        "方法描述。描述黄瓜分生组织形态变化的最佳句 s54（'enlarges from the perimeter\n"
        "and becomes concave'）未被检索到，不在审核池中；\n"
        "(3)「以子房上位的番茄作为对比进行观察（图1d）」→ s52 提及 'Cucumber and tomato\n"
        "have a similar floral structure' 和 'make them excellent subjects for comparison'，\n"
        "支撑了黄瓜-番茄对比的研究框架。但描述番茄花分生组织凸起形态的最佳句 s58\n"
        "（'the floral meristem of tomato is convex'）同样未被检索到，不在审核池中；\n"
        "(4)「石蜡切片」→ 此为植物发育生物学常规方法，论文 Figure 1 的显微图像\n"
        "极可能基于石蜡或树脂切片，但系统检索完全未命中 Methods 中的方法描述句。\n"
        "判 Weak_Evidence：整体研究策略（黄瓜-番茄对比发育形态学）有部分证据支撑\n"
        "（s3/s52/s4），但「石蜡切片」这一具体技术细节和最佳形态描述句（s54/s58）\n"
        "均不在审核池中——这是检索覆盖不足的典型表现。"
    ),
    cls_reason=(
        "Weak_Evidence：核心研究方法（比较发育形态学）被多项证据交叉支撑，"
        "但「石蜡切片」这一技术术语在现有检索中无对应——可能是 likely_retrieval_miss"
        "（论文 Methods 段落大概率提及但未被检索），也可能论文使用了其他切片方法"
        "（如树脂半薄切片、冷冻切片）。在未确认的情况下，暂标 accurate"
        "（论文的 Figure 1 组织学图像确实需要某种切片技术），evidence_level=Weak_Evidence。\n"
        "与 C07 不同：C07 的核心科学结论有支撑但方法细节缺失；C08 的整体研究方法有支撑，"
        "仅技术名称未确认——两者均为 Weak_Evidence 但原因不同（方法论细节 vs 研究策略）。"
    ),
    key_diffs=[],
    unsup_verdict="likely_retrieval_miss",
    unsup_reason=(
        "论文 Methods 部分很可能描述了组织学方法（paraffin embedding/sectioning、"
        "staining 等），但未出现在系统检索结果中。此外，描述黄瓜分生组织形态变化的最佳句"
        "s54（'enlarges from the perimeter and becomes concave'）和番茄形态对比的最佳句"
        "s58（'the floral meristem of tomato is convex'）均完全未被检索到——"
        "这些句子很可能在论文正文中（s54≈句子54附近，s58≈句子58附近），但不在审核池中。"
    ),
    unsup_kw=["paraffin", "section", "staining", "histology", "microtome", "toluidine blue", "semi-thin"],
    unsup_range="Methods 段落（约 sentence 240–290），及 Fig 1 legend",
    hints=(
        "核对论文 Methods：(1)是否明确描述了 paraffin sectioning 或 resin embedding 方法？\n"
        "(2)图1b/d 的 legend 中是否标注了切片类型/厚度/染色方法？\n"
        "(3)强烈建议人工补充检索：s54（黄瓜分生组织形态变化描述）和 s58（番茄分生组织形态描述）"
        "大概率在论文中但未被检索到——手动找到后将 sentence_id 加入 gold，改 evidence_level=With_Evidence；\n"
        "(4)若论文确实未明确说明切片方法（仅说 'morphological observation'），"
        "则 '石蜡切片' 可能为公众号自行添加的细节（fact_addition）——需评估。"
    ),
    needs_review=True, review_focus=["evidence_level", "gold_sentence_ids", "rag_top5"], ai_conf="medium",
    top5_best=False, better_in_pool=[],
    rag_notes="s3(rank1)为综合性摘要，s52(rank5)提供对比框架，s4(rank4)提供KO遗传学证据。但描述黄瓜分生组织形态变化的 s54 和番茄形态的 s58 均未被检索到，不在审核池中。top-5 中存在噪声：s2（知识空白陈述）与此 claim 的描述性内容无关。检索对组织学方法的覆盖严重不足。"
)

# ── C09 ──
C09 = lambda ce, re, zh: build_sample(
    "C09", zh, ce, re,
    gold_sids=[54, 55, 57], is_answerable=True, ev_level="With_Evidence",
    primary="accurate", secondary=[], is_acc=True, severity="none",
    ev_judge=(
        "拆为3个可核查分句，均有精确对应：\n"
        "(1)「分生组织边缘膨大、中间凹陷」→ s54 'the cucumber floral meristem enlarges "
        "from the perimeter and becomes concave'——'边缘膨大'=enlarges from perimeter，"
        "'中间凹陷'=becomes concave，逐词对应；\n"
        "(2)「分化出萼片、花瓣、雄蕊和心皮原基」→ s55 'sepal, petal, stamen and carpel "
        "primordia are initiated sequentially on the inner regions of this enlarged "
        "floral meristem (EFM)'，四种原基名称和顺序完全吻合，'inner regions' 对应 "
        "claim 的「内部分化出」；\n"
        "(3)「图1c」→ s54/s55/s57 均引用 Fig. 1b/1c，图中编号对应正确。\n"
        "s57 补充确认黄瓜雄花也出现 'enlarged and concave floral meristem'，"
        "与 Fig. 1c 的形态描述一致。\n"
        "注意：s54 在审核池 rank7 才出现——classify top-5 未包含最佳形态描述句。"
    ),
    cls_reason=(
        "准确。s54 和 s55 分别精确对应形态变化和器官分化两个层面：\n"
        "'enlarges from the perimeter and becomes concave' = 「边缘膨大、中间凹陷」；\n"
        "'sepal, petal, stamen and carpel primordia are initiated sequentially on the "
        "inner regions' = 「分化出萼片、花瓣、雄蕊和心皮原基」。\n"
        "术语翻译和顺序完全一致，无任何失真。"
    ),
    key_diffs=[],
    unsup_verdict="not_applicable", unsup_reason="所有核心断言均有精确对应的论文原文支撑。", unsup_kw=[], unsup_range="",
    hints=(
        "RAG 核对：(1)s54(rank7)是最佳形态描述句（'enlarges from the perimeter and becomes concave'），"
        "确认是否应将 s54 作为 gold 首条；\n"
        "(2)确认 s37(rank3,classify)为图注/标签噪声（器官名称列表），不应入 gold；\n"
        "(3)s120(rank1,classify)描述 'sepal primordia was not captured' 是从另一角度"
        "（cell lineage分析的局限性）讨论，非对 claim 的最佳支撑。"
    ),
    needs_review=True, review_focus=["rag_top5", "gold_sentence_ids"], ai_conf="high",
    top5_best=False, better_in_pool=[54],
    rag_notes="s54（审核池 rank7）是描述分生组织形态变化的最精确句（'enlarges from the perimeter and becomes concave'），但未进 classify top-5。top-5 中 s55 覆盖原基分化，s57 覆盖形态特征，但最佳形态描述句被排到了第7位。s120(rank1)的视角（'sepal primordia was not captured'）与 claim 角度不完全匹配。"
)

# ── C10 ──
C10 = lambda ce, re, zh: build_sample(
    "C10", zh, ce, re,
    gold_sids=[58], is_answerable=True, ev_level="With_Evidence",
    primary="accurate", secondary=[], is_acc=True, severity="none",
    ev_judge=(
        "s58 是该 claim 的精确原文来源：'By contrast, the floral meristem of tomato is "
        "convex, and the floral primordia sequentially initiate on its flank, with the "
        "carpels growing upwards to form superior ovaries (Fig.'——\n"
        "拆为2个可核查分句：(1)「番茄花分生组织中间凸起」→ s58 'floral meristem of tomato "
        "is convex'，convex=凸起，准确；\n"
        "(2)「在其侧面形成各花器官原基」→ s58 'floral primordia sequentially initiate on "
        "its flank'，flank=侧面，sequentially initiate=依次形成，准确。\n"
        "s58 几乎逐词对应 claim 全文。top-5 中 s58(rank1)即为最佳匹配（'By contrast'开头，"
        "与前一句 C09 的黄瓜描述形成对照）。\n"
        "注意 s58 的 Fig 引用在文本中被截断（原句末尾可能是 'Fig. 1e'），需人工核对。"
    ),
    cls_reason=(
        "准确。'convex'=凸起，'flank'=侧面，'floral primordia sequentially initiate'="
        "依次形成各花器官原基。与 C09 同理，为论文原文的准确转述。无放大、无添加。"
    ),
    key_diffs=[],
    unsup_verdict="not_applicable", unsup_reason="claim 完全对应 s58 内容。", unsup_kw=[], unsup_range="",
    hints=(
        "几乎不需核对。唯一点：确认图1e 的编号对应正确——s58 的 Fig 引用在原文中被截断，"
        "论文原文可能是 'Fig. 1e' 或 'Fig. 1d,e'。"
    ),
    needs_review=False, review_focus=["none"], ai_conf="high",
    top5_best=True, better_in_pool=[],
    rag_notes="s58(rank1)是精确匹配句，检索质量好。其余 top-5 句子(s57/s144/s40/s55)偏向黄瓜侧或为图注碎片，但不影响最佳匹配已在 rank1 的事实。"
)

# ── Main ──
ANALYZERS = {"C01": C01, "C02": C02, "C03": C03, "C04": C04, "C05": C05,
             "C06": C06, "C07": C07, "C08": C08, "C09": C09, "C10": C10}

def main():
    claims = load_claims(INPUT, 10)
    print(f"Loaded {len(claims)} claims")

    samples = []
    for c in claims:
        cid = c["claim_id"]
        ce, re = get_ev(c)
        if cid in ANALYZERS:
            sample = ANALYZERS[cid](ce, re, c["claim_zh"])
            samples.append(sample)
            print(f"  {cid}: primary={sample['gold_classification']['primary_type']}, "
                  f"level={sample['gold_classification']['evidence_level']}, "
                  f"needs_review={sample['analysis']['needs_manual_review']}")
        else:
            print(f"  {cid}: NO ANALYZER, skipped")

    must_review = [s["sample_id"] for s in samples if s["analysis"]["needs_manual_review"]]

    output = {
        "schema_version": "1.1",
        "status": "draft",
        "paper_id": "P001",
        "article_id": "A001",
        "article_source_type": "high_quality",
        "generated_date": "2026-07-31",
        "generation_mode": "smoke",
        "limit": "10",
        "sample_count": len(samples),
        "_description": (
            "标注草稿：评测字段(gold_retrieval/gold_classification) + system_retrieval 对照 + analysis；"
            "人工审核后导出终稿。本文件为冒烟测试，仅含前10条观点句。\n"
            "人工审核顺序：1.读claim_zh → 2.看classify(top-5) → 3.扫review第6-10条 → "
            "4.改gold_retrieval → 5.改gold_classification → 6.改analysis → 7.human_verified=true"
        ),
        "samples": samples,
        "review_queue": {
            "must_review_sample_ids": must_review,
            "notes": "优先审这些；见各条 analysis.manual_check_hints"
        }
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nWrote {len(samples)} samples to {OUTPUT}")
    print(f"Must review ({len(must_review)}): {must_review}")

if __name__ == "__main__":
    main()
