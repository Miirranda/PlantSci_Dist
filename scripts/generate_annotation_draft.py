#!/usr/bin/env python3
"""Generate annotation draft JSON from claim-evidence pairs JSONL.
Reads the JSONL, applies hallucination analysis rules, outputs the draft JSON.
"""

import json
from datetime import date

INPUT_FILE = "outputs/P001/A001/claim_evidence_pairs.jsonl"
OUTPUT_FILE = "data/annotations/P001/P001_A001_annotation_draft.json"

PAPER_ID = "P001"
ARTICLE_ID = "A001"
SOURCE_TYPE = "high_quality"
GEN_DATE = "2026-07-30"

# ── Analysis embedded per claim ────────────────────────────────────────────
# Each entry: (gold_sentence_ids, is_answerable, evidence_level,
#   primary_type, secondary_types, is_accurate, severity,
#   evidence_judgement, classification_reason, key_differences,
#   unsupported_verdict, unsupported_reasoning, unsupported_keywords,
#   unsupported_ranges, manual_check_hints, needs_manual_review,
#   review_focus, ai_confidence, secondary_types_raw)

ANALYSES = {
    "C01": {
        "sentence_ids": [3, 4, 43, 277],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "certainty_amplification",
        "secondary_types": ["fact_addition", "scope_generalization", "mechanism_simplification"],
        "is_accurate": False,
        "severity": "moderate",
        "evidence_judgement": (
            "含4个可核查分句：(1)「通过空间转录组技术和细胞谱系追踪」— sentence_id=3/115/329 支撑了空间转录组+细胞谱系方法，但论文用的是 comparative spatial transcriptome mapping 和 cell lineage reconstructions，并未声称用了'细胞谱系追踪'（lineage tracing 通常指遗传标记谱系示踪），此处有轻微术语失真；"
            "(2)「首次揭示了黄瓜下位子房的发育机制」— sentence_id=2 明确说 'developmental mechanisms...remain largely unknown'，论文自己都说机制未知，'首次揭示'是典型的 fact_addition；sentence_id=3 说明 inferior ovaries develop from accelerated receptacle growth，这是描述 phenomechanism 而非完整 molecular mechanism；论文 abstract 说 'provide developmental and mechanistic insights into'，是审慎措辞，不是'揭示'；"
            "(3)「阐明了KNOX1转录因子在葫芦科植物花器官发育和性别决定中的核心作用」— sentence_id=4/44 支持 KNAT2-like1 在 receptacle growth 和 ovary positioning 中的关键作用，但论文说的是 'a key role'、'pivotal role'，不是'核心作用'，且限定于 cucumber 而非'葫芦科植物'整体范围；sentence_id=277 提到 KNAT2-like1 同时导致 bisexual flowers 和 superior ovaries，但论文未声称阐明了整个葫芦科的机制；"
            "(4)整体来看 claim 把论文限定在 cucumber 的发现推广到'葫芦科植物'（Cucurbitaceae），这是 scope_generalization。"
        ),
        "classification_reason": (
            "primary_type=certainty_amplification：论文用 'provide developmental and mechanistic insights into'（提供发育和机制层面的见解），claim 改成'首次揭示了…并阐明了…核心作用'，从 suggest/insight 级别提升到揭示/阐明/核心的定论级别。"
            "secondary_types：fact_addition（'首次'论文未声称，且论文说机制 largely unknown）、scope_generalization（cucumber→葫芦科植物）、mechanism_simplification（复杂的 FIM-receptacle-carpel 多层调控被简化为 KNOX1 单因子核心作用）。"
            "区分 certainty_amplification vs fact_addition 的主因：'首次'本身是添加的事实，但 claim 的主要失真模式是将论文的审慎表述（insights into）整体抬升为确定结论（揭示了、阐明了），故 primary 取确定性放大。"
        ),
        "key_differences": [
            {"type": "certainty_amplification", "paper_expression": "provide developmental and mechanistic insights into", "article_expression": "首次揭示了…阐明了核心作用", "description": "论文措辞审慎（insights），公众号强化为定论（揭示/阐明）"},
            {"type": "fact_addition", "paper_expression": "(论文未声称 first/首次)", "article_expression": "首次揭示了", "description": "论文未声称首次发现，且明确说机制 largely unknown"},
            {"type": "scope_generalization", "paper_expression": "cucumber", "article_expression": "葫芦科植物", "description": "论文研究仅限于黄瓜，claim 推广至整个葫芦科"}
        ],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "核对论文 abstract/discussion 是否在别处有更确定的措辞；确认论文是否有 claim '首次' 的表述（预期没有）；确认 KNOX1 作用范围是否仅限于 cucumber 还是确实讨论了 cucurbits 整体。",
        "needs_manual_review": True,
        "review_focus": ["primary_type", "gold_sentence_ids"],
        "ai_confidence": "high"
    },
    "C02": {
        "sentence_ids": [8, 9],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 仅陈述一个通用植物学事实：子房相对于其他花器官的位置是重要分类学特征。"
            "sentence_id=8 'The spatial arrangement of the outer floral organs relative to the ovary determines whether the ovary is classified as superior or inferior' 直接支撑；"
            "sentence_id=9 进一步说明上下位子房的定义。claim 是基础背景知识，与论文引言中的描述一致，无失真。"
        ),
        "classification_reason": "claim 准确传达了论文引言中的背景知识，措辞中性，无添加、无放大。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C03": {
        "sentence_ids": [8, 9],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 说明子房按与萼片、花瓣、雄蕊的相对位置分为上位和下位子房。"
            "sentence_id=8 直接对应分类依据（outer floral organs relative to ovary determines superior/inferior classification）；"
            "sentence_id=9 列出了具体花器官（sepals, petals, stamens）并给出上/下位的定义。claim 准确转述。"
        ),
        "classification_reason": "纯粹背景知识陈述，与论文表述完全一致。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C04": {
        "sentence_ids": [12, 10, 49],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含3个分句：(1)「多次独立进化出的关键创新性状」— sentence_id=10 说 'independently evolved from superior ovaries multiple times'，sentence_id=49 补充 'originated independently multiple times'，完全支撑；"
            "(2)「更好地保护雌蕊」— sentence_id=12 'providing increased protection to developing gynoecia' 直接对应；"
            "(3)「为胚珠发育分配更多能量」— sentence_id=12 'allocating more energy to developing ovules' 直接对应。claim 准确转述。"
        ),
        "classification_reason": "所有分句均有直接证据支撑，措辞准确对应论文原文。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C05": {
        "sentence_ids": [30, 31, 2, 332, 277],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": ["certainty_amplification"],
        "is_accurate": True,
        "severity": "mild",
        "evidence_judgement": (
            "含4个分句：(1)「葫芦科植物果实均来源于下位子房」— sentence_id=31 'their fleshy fruits develop from inferior ovaries' 支撑，但 note 提到 melon fruit may develop from bisexual flowers，所以'均'可能不够精确；"
            "(2)「多为单性花」— sentence_id=30 'has numerous species with predominantly unisexual flowers' 支撑，predominantly='多为'对应准确；"
            "(3)「下位子房形成机制…还尚未明确」— sentence_id=2 'developmental mechanisms...remain largely unknown' 直接支撑；"
            "(4)「及其与性别决定机制的关系还尚未明确」— sentence_id=332 'both traits appear to have coevolved' 和 sentence_id=277 表明论文已提出二者的关联机制，'尚未明确'与论文表述有轻微出入——论文在讨论中已提出直接联系模型。"
            "整体来看 claim 的主要信息（黄瓜西瓜甜瓜等、下位子房、单性花、机制不太清楚）准确，仅在最后一点有轻微 certainty 问题。"
        ),
        "classification_reason": (
            "主要分句均准确。最后一项'与性别决定机制的关系还尚未明确'有轻微确定性放大——论文已在讨论中提出了 receptacle evolution 中 unisexual flowers 和 inferior ovaries 的直接联系（sentence_id=277），并非完全'尚未明确'。"
            "severity=mild 因为整体准确度高，仅一处轻微出入。"
        ),
        "key_differences": [
            {"type": "certainty_amplification", "paper_expression": "a direct link was established between the development of the unisexual flowers and inferior ovaries", "article_expression": "与性别决定机制的关系还尚未明确", "description": "论文已提出直接关联模型，公众号说'尚未明确'反而低估了论文的结论确定性"}
        ],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "确认 melon fruit 的例外是否足以推翻'均'字；确认论文 discussion 部分对性别决定-下位子房关联的表述程度。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "medium"
    },
    "C06": {
        "sentence_ids": [52, 3, 4],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含3个分句，均有直接证据：(1)「黄瓜和番茄具有相似花器官结构」— sentence_id=52 'Cucumber and tomato have a similar floral structure' 直接支撑；"
            "(2)「前者为下位子房和单性花，后者为上位子房和两性花」— sentence_id=3 'cucumber and tomato, which have inferior and superior ovaries, respectively' + sentence_id=4 提到 'bisexual flowers with superior ovaries similar to those of tomato' 支撑；"
            "(3)「是研究子房位置和性别决定机制的理想模式植物」— sentence_id=52 'make them excellent subjects for comparison' 准确对应。"
        ),
        "classification_reason": "所有分句均有精准的英文原文对应，措辞转换忠实。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C07": {
        "sentence_ids": [49, 332, 47, 48],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "certainty_amplification",
        "secondary_types": [],
        "is_accurate": False,
        "severity": "mild",
        "evidence_judgement": (
            "含4个分句：(1)「构建了502个被子植物物种进化树」— sentence_id=47 'We annotated the phylogenetic tree of angiosperms, encompassing 502 species across 61 orders' 直接支撑；"
            "(2)「包含花性别和子房位置信息」— sentence_id=47 'with sex information and ovary positions' 直接支撑；"
            "(3)「单性花和子房下位性状在进化过程中独立出现多次」— sentence_id=49 'Unisexual flowers and inferior ovaries originated independently multiple times' 直接支撑；"
            "(4)「在葫芦科植物及其近缘种中连锁出现，说明这两个性状可能来自于同一进化事件」— sentence_id=332 'in the Cucurbitaceae, both traits appear to have coevolved' + sentence_id=51 'may have originated from a single evolutionary event in the Cucurbitaceae' 支撑。"
            "但论文用的是 'appear to have coevolved' 和 'may have originated from a single evolutionary event'，claim 说'说明…可能来自于'，'说明'比 'may' 确定性略高，但已用'可能'缓和。severity=mild。"
        ),
        "classification_reason": (
            "整体准确度较高。论文用 'may have originated'（推测），claim 用'说明…可能来自于'，'说明'一词暗示结论性略强于论文的推测用语。"
            "但差异微小，且 claim 保留了'可能'的限定词。severity=mild。"
        ),
        "key_differences": [
            {"type": "certainty_amplification", "paper_expression": "may have originated from a single evolutionary event", "article_expression": "说明这两个性状可能来自于同一进化事件", "description": "'说明'比 'may' 的推测语气更确定"}
        ],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "判断'说明'是否过度强化了论文的推测语气。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "medium"
    },
    "C08": {
        "sentence_ids": [53, 54, 55, 57, 58],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "context_stripping",
        "secondary_types": ["fact_addition"],
        "is_accurate": False,
        "severity": "mild",
        "evidence_judgement": (
            "含2个分句：(1)「对黄瓜花发育过程进行了石蜡切片观察（图1b）」— sentence_id=53 'examined the morphology of female floral buds...' 支撑了形态学观察方法，sentence_id=54/55/57 描述了具体观察结果。但论文未明确提及'石蜡切片'（paraffin-embedded sections）用于黄瓜——实际上 sentence_id=38 提到了 tomato 的 paraffin-embedded sections，而 cucumber 可能用的是其他方法。需要核对方法部分是否有 cucumber 石蜡切片的具体描述。"
            "(2)「同时以子房上位的番茄作为对比进行观察（图1d）」— sentence_id=58 'By contrast, the floral meristem of tomato is convex...' 支撑了番茄作为对比。"
            "候选证据中未检索到关于石蜡切片的明确方法描述句子，'石蜡切片'可能是公众号添加的方法细节。"
        ),
        "classification_reason": (
            "primary_type=context_stripping：claim 提到'石蜡切片观察'，但候选证据中未检索到 cucumber 使用石蜡切片的方法学描述（只有 tomato 的 paraffin-embedded 被检索到）。可能剥离了论文中实际使用的方法条件。"
            "如果论文确实用了石蜡切片但未被检索到，则本条可能是检索遗漏导致。"
        ),
        "key_differences": [
            {"type": "context_stripping", "paper_expression": "(cucumber 形态学观察方法待确认)", "article_expression": "石蜡切片观察", "description": "公众号添加了具体方法细节，可能不准确或检索遗漏"}
        ],
        "unsupported_verdict": "likely_retrieval_miss",
        "unsupported_reasoning": "候选证据中只有 tomato 的 paraffin-embedded sections（sentence_id=38），没有 cucumber 石蜡切片的明确描述。论文方法部分很可能有相关描述但未被检索到。",
        "unsupported_keywords": ["paraffin", "section", "microscopy", "histology", "cucumber"],
        "unsupported_ranges": "20-55（方法部分）",
        "manual_check_hints": "到论文方法部分搜索 'paraffin'、'microtome'、'sectioning' 确认黄瓜是否使用了石蜡切片法。如果确实用了，则本条可降为 accurate。如果没有明确提及，则 context_stripping 成立。",
        "needs_manual_review": True,
        "review_focus": ["primary_type", "gold_sentence_ids"],
        "ai_confidence": "low"
    },
    "C09": {
        "sentence_ids": [54, 55],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含2个分句：(1)「分生组织边缘膨大、中间凹陷」— sentence_id=54 'the cucumber floral meristem enlarges from the perimeter and becomes concave' 直接支撑；"
            "(2)「在膨大分生组织内部分化出萼片、花瓣、雄蕊和心皮原基」— sentence_id=55 'sepal, petal, stamen and carpel primordia are initiated sequentially on the inner regions of this enlarged floral meristem' 直接支撑。准确传达。"
        ),
        "classification_reason": "两个分句均与论文原文措辞直接对应，无失真。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C10": {
        "sentence_ids": [58],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含2个分句：(1)「番茄花分生组织中间凸起」— sentence_id=58 'the floral meristem of tomato is convex' 直接支撑；"
            "(2)「在其侧面形成各花器官原基（图1e）」— sentence_id=58 'the floral primordia sequentially initiate on its flank' 直接支撑。准确传达。"
        ),
        "classification_reason": "与论文原文完全对应，无失真。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C11": {
        "sentence_ids": [59],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 是三个必要条件的第一个：花分生组织膨大改变花器官轮相对位置。"
            "sentence_id=59 'inferred three necessary conditions... (1) enlargement of the floral meristem to change the relative position of the floral whorls' 逐字对应。准确转述。"
        ),
        "classification_reason": "与论文原文逐字对应，无失真。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C12": {
        "sentence_ids": [59, 43, 56],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 是三个必要条件的第二个：花托迅速生长。"
            "sentence_id=59 'three necessary conditions... (2) subsequent rapid growth of the receptacle' 直接支撑；"
            "sentence_id=43/56 提供了花托快速生长的额外描述。准确转述。"
        ),
        "classification_reason": "与论文原文完全对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C13": {
        "sentence_ids": [59, 265],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 是三个必要条件的第三个：花托和心皮融合。"
            "sentence_id=59 'three necessary conditions... (3) fusion of the receptacle and the carpel' 直接支撑；"
            "sentence_id=265 'receptacle is fused with the adaxial side of the carpel' 进一步支撑。准确转述。"
        ),
        "classification_reason": "与论文原文完全对应，无失真。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C14": {
        "sentence_ids": [60, 61],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含2个分句：(1)「对黄瓜花芽分化S1-S8时期的纵截面和S8-4时期、花后0天子房的横截面进行了Stereo-seq空间转录组测序」— sentence_id=60 'sampled floral buds at various stages, including S1–S8, and ovaries at 0 days post anthesis (DPA) for spatial transcriptomic analysis' 直接支撑；"
            "(2) sentence_id=61 'Longitudinal sections were analysed, as well as a transverse section of S8-4 and two 0 DPA samples' 进一步确认了截面方向。claim 准确转述了实验设计。"
            "需要注意的是 claim 提到了'Stereo-seq'这一具体技术名称，论文 method 部分应该提到，但候选证据中未直接出现 Stereo-seq 一词。不过 sentence_id=60 的 'spatial transcriptomic analysis' 已涵盖技术类型。"
        ),
        "classification_reason": "实验设计描述准确，与论文方法描述一致。'Stereo-seq' 技术名称可能在论文其他位置有提及，不是失真。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "确认论文方法部分确实使用了 Stereo-seq 平台。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C15": {
        "sentence_ids": [76, 87, 88, 89, 91, 103],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "numerical_distortion",
        "secondary_types": [],
        "is_accurate": False,
        "severity": "mild",
        "evidence_judgement": (
            "含3个分句：(1)「经数据降维后，所有样品的细胞共被分为41个聚类」— 候选证据中 sentence_id=103 列出了 0-40 共41个 cluster 编号，支撑了'41个聚类'的说法；"
            "(2)「通过特异性标记基因表达模式（图2b）进一步验证了这些聚类的组织特异性」— sentence_id=105 'The corresponding expression patterns of marker genes are shown' 部分支撑；"
            "(3)「及其空间分布（图2c）」— sentence_id=104 'spatial distribution of clusters on the sections of S8-2' 部分支撑。"
            "但需注意：候选证据中大量是图注残片和元信息。41这个数字需要在论文正文中确认。sentence_id=103 确实列出了41个cluster（0-40）。整体准确度较高。"
        ),
        "classification_reason": (
            "主体信息准确。但'41个聚类'的具体数字需要与论文正文核对（因为候选证据中的 cluster 列表包含图注残片）。severity=mild 因为数字可能准确但证据不够确定。"
        ),
        "key_differences": [],
        "unsupported_verdict": "likely_retrieval_miss",
        "unsupported_reasoning": "候选证据中 cluster 编号列表（sentence_id=103）列出了0-40共41个cluster，但该句可能是图注部分。论文正文应有更明确的 cluster 数量描述。",
        "unsupported_keywords": ["41 clusters", "total", "identified"],
        "unsupported_ranges": "65-110（结果部分 cluster 描述）",
        "manual_check_hints": "到论文结果部分正文搜索 '41' 或 'total cluster' 确认确切数量，并选取正文中更权威的描述句作为 gold sentence。",
        "needs_manual_review": True,
        "review_focus": ["gold_sentence_ids"],
        "ai_confidence": "medium"
    },
    "C16": {
        "sentence_ids": [91],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 描述了 cluster 26、27、28 的命名：'连接原基'（conjunctive primordia）、'连接组织早期'（conjunctive tissue-early）、'连接组织晚期'（conjunctive tissue-late）。"
            "sentence_id=91 'three clusters (26, 27 and 28)...which we named conjunctive primordia, conjunctive tissue-early and conjunctive tissue-late, respectively' 逐字对应。准确转述。"
        ),
        "classification_reason": "与论文原文完全对应，术语翻译准确。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C17": {
        "sentence_ids": [91, 92, 93],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "certainty_amplification",
        "secondary_types": [],
        "is_accurate": False,
        "severity": "mild",
        "evidence_judgement": (
            "含4个分句：(1)「S6-S8时期与心皮、花托以及连接组织相关的细胞聚类如图2d」— 证据中图注提及但不完整，大致支撑；"
            "(2)「连接组织不仅表达心皮和花托的标记基因（AG1、CRC）」— sentence_id=92 'marker genes in both carpel (CsAG1) and receptacle (CsCRC)' 直接支撑；"
            "(3)「也特异性表达两性花发育基因ACS2（图2e）」— sentence_id=93 'CsACS2, a gene for bisexual flower development, was specifically expressed in these clusters' 直接支撑；"
            "(4)「说明连接组织在子房发育和性别决定中可能同时起作用」— sentence_id=93 'suggesting that the conjunctive tissue plays a role in ovary development and sex determination' 支撑，但论文用 'suggesting'，claim 用'说明'，确定性略有提升。"
            "severity=mild 因为主体信息准确，仅有轻微确定性措辞差异。"
        ),
        "classification_reason": (
            "论文用 'suggesting that...plays a role in'（暗示起作用），公众号用'说明…可能同时起作用'。'说明'比 'suggesting' 略强，但保留了'可能'的限定词。差异微小。"
        ),
        "key_differences": [
            {"type": "certainty_amplification", "paper_expression": "suggesting that the conjunctive tissue plays a role in", "article_expression": "说明连接组织在…中可能同时起作用", "description": "'说明'比 'suggesting' 确定性略强"}
        ],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "确认'说明'与'suggesting'的确定性差异是否构成有意义的失真。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "medium"
    },
    "C18": {
        "sentence_ids": [115, 116],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含2个分句：(1)「利用空间转录组数据绘制了花器官发生轨迹」— sentence_id=115 'used the spatial transcriptomic data...to generate a trajectory of floral organogenesis (TOFO)' 直接支撑；"
            "(2)「根据cluster之间的关系重构细胞谱系」— sentence_id=116 'reconstructed cell lineage reveals inferred relationships between clusters' 直接支撑。准确转述。"
        ),
        "classification_reason": "与论文原文完全对应，无失真。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C19": {
        "sentence_ids": [119],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 陈述：各细胞谱系的空间分布与其发育轨迹相同。"
            "sentence_id=119 'We also examined their spatial distribution across sections, which revealed the spatiotemporal cell lineages for each supercluster' 支撑了这一说法。准确转述。"
        ),
        "classification_reason": "与论文原文对应，无失真。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C20": {
        "sentence_ids": [125, 164, 169, 170],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "certainty_amplification",
        "secondary_types": ["causality_distortion"],
        "is_accurate": False,
        "severity": "moderate",
        "evidence_judgement": (
            "含3个分句：(1)「S7后FIM与花托原基活跃地进行细胞分裂（图3a）」— sentence_id=125 'after S7, the FIM and receptacle primordia generate cells that are actively undergoing the cell cycle' 直接支撑；"
            "(2)「可能是花托快速生长从而导致与心皮融合的原因」— 论文用 'suggesting a mechanism to maintain cell proliferation and support rapid receptacle growth'（suggesting 一种机制），claim 用'可能是…的原因'，增加了因果推断且指向了与心皮融合（论文此句未提融合）。"
            "claim 将 FIM+RP 的细胞周期活动与'花托快速生长→与心皮融合'之间建立了更直接的因果关系，而论文在此处的措辞是 'suggesting a mechanism to maintain cell proliferation and support rapid receptacle growth'，并未直接延伸到融合。severity=moderate。"
        ),
        "classification_reason": (
            "primary_type=certainty_amplification：论文用 'suggesting a mechanism'（暗示一种机制），claim 改为'可能是…的原因'，且添加了论文此处未提到的'与心皮融合'作为结果链的终点。"
            "secondary=causality_distortion：claim 将论文中多步骤的、需要整合多条证据的机制链条（FIM→细胞分裂→花托生长→与心皮融合→下位子房）简化为直接的因果关系线。"
        ),
        "key_differences": [
            {"type": "certainty_amplification", "paper_expression": "suggesting a mechanism to maintain cell proliferation and support rapid receptacle growth", "article_expression": "可能是花托快速生长从而导致与心皮融合的原因", "description": "论文暗示机制，公众号建立因果链并扩展至融合"}
        ],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "确认论文在 sentence_id=125 处是否提到了与心皮融合的因果关联；如果论文在其他地方（如 sentence_id=59/265）讨论了三条件的完整因果链，评估公众号合并表述是否合理。",
        "needs_manual_review": True,
        "review_focus": ["primary_type", "causality_distortion"],
        "ai_confidence": "medium"
    },
    "C21": {
        "sentence_ids": [127],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 陈述：拟时序分析发现发育顺序为 FIM→花托→连接组织。"
            "sentence_id=127 'demonstrated the cell lineage from FIM to receptacle and then to conjunctive tissue' 逐字对应。准确转述。"
        ),
        "classification_reason": "与论文原文完全对应，细胞谱系方向准确。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C22": {
        "sentence_ids": [130, 131],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含2个分句：(1)「黄瓜心皮外的果肉是由花托发育而来」— sentence_id=130 'fruit flesh outside the carpel is mostly derived from the development of receptacle' 支撑，但论文有 'mostly'（大部分），claim 去掉了'大部分'的限定；"
            "(2)「花托及其相关组织的研究对遗传改良意义重大」— sentence_id=131 'the receptacle-derived tissue is important for the genetic improvement' 直接支撑。"
            "整体准确，但'mostly'限定词被剥离。"
        ),
        "classification_reason": (
            "主体信息准确。但论文 'We infer that...fruit flesh outside the carpel is mostly derived from the development of receptacle' 中有 'infer' 和 'mostly' 两个审慎限定，claim 省略了 'mostly'。severity=mild。"
        ),
        "key_differences": [
            {"type": "context_stripping", "paper_expression": "mostly derived from", "article_expression": "是由花托发育而来", "description": "论文保留了 mostly 的限定，公众号为更确定的断言"}
        ],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "确认 'mostly' 的省略是否显著改变含义。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "medium"
    },
    "C23": {
        "sentence_ids": [132, 134, 135, 139],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含3个分句：(1)「对S1-S4时期花芽进行近单细胞分辨率的无监督聚类分析，共分为7个cluster」— sentence_id=132 'unsupervised clustering analysis with near single-cell resolution...from S1 to S4 stages' + sentence_id=134 'This revealed seven clusters' 直接支撑；"
            "(2)「花托原基（RP）和扩展花分生组织（EFM）的起始和发育决定着凹形花分生组织的形态发生」— sentence_id=139 'The distribution of cluster 3 (RP) and cluster 4 (EFM) indicates that their initiation and development determine the morphogenesis of the enlarged and concave floral meristem' 直接支撑。"
            "准确转述。"
        ),
        "classification_reason": "所有分句均有直接证据支撑，术语和细节准确。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C24": {
        "sentence_ids": [143, 144, 145],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含3个分句：(1)「RNA速率分析和拟时序分析揭示了cluster的发育轨迹：起源于FIM」— sentence_id=143/144/145 支撑了起源和轨迹；"
            "(2)「分为3条发育路径」— sentence_id=143 'One involved the differentiation of the epidermis' + sentence_id=144 'The other two originated from the FIM...' 合计3条路径，支撑；"
            "(3)「一条分化为表皮（E），另外两条经过EFM分别分化为花原基（FP）和花托原基（RP）」— sentence_id=144 'the other two originated from the FIM and passed through the EFM, with one leading to the formation of the floral primordia...while the other gave rise to the receptacle primordium' 直接支撑。准确转述。"
        ),
        "classification_reason": "所有分句均有精确的原文对应，发育路径描述完整准确。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C25": {
        "sentence_ids": [158, 195, 157, 159],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含3个分句：(1)「筛选出一些关键基因，其表达热图及空间定位见图4e-f」— sentence_id=195 'heat map of the dynamic changes in expression levels of pseudotime-dependent genes' + sentence_id=157 '4f and Extended Data Fig. 5b' 支撑；"
            "(2)「对其中一些基因进行了RNA原位杂交验证（图4g）」— sentence_id=158 'selected key marker genes...were verified by RNA in situ hybridization (Fig.' + sentence_id=159 '4g and Extended Data Fig. 5c' 直接支撑。"
            "准确转述。"
        ),
        "classification_reason": "实验验证描述准确，与论文方法对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C26": {
        "sentence_ids": [160],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 陈述 cluster 按位置分为内部区域（FP）和外部区域（E、RP和EFM）（图4h）。"
            "sentence_id=160 'we classified the cells of floral buds into the inner part (cluster floral primordia) and peripheral part (clusters E, RP and EFM)' 直接支撑。准确转述。"
        ),
        "classification_reason": "与论文原文完全对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C27": {
        "sentence_ids": [161, 162, 163],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含2个分句：(1)「EFM初始形态变化是由于外部区域细胞快速增殖（图4i-j）」— sentence_id=161 'initial morphological changes in the EFM reflect rapid cell proliferation in the peripheral region (Fig.' + sentence_id=162 '4i)' 直接支撑；"
            "(2)「从而促进外侧分生组织后续扩大」— sentence_id=162 'cell expansion in this region from S2 to S5 contributed to subsequent meristem enlargement (Fig.' + sentence_id=163 '4j)' 直接支撑。准确转述。"
        ),
        "classification_reason": "所有分句与论文原文精确对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C28": {
        "sentence_ids": [164, 169, 170],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 陈述 S5后花托相关bin数量急剧上升说明花托快速生长。"
            "sentence_id=164 'After S5, we observed the largest increase in the bin numbers of receptacle-related clusters' + sentence_id=169/170 'the rapid growth of the receptacle after this stage is driven by another phase of cell proliferation' 直接支撑。准确转述。"
        ),
        "classification_reason": "与论文数据趋势描述一致。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C29": {
        "sentence_ids": [173],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含3个分句：(1)「3D UMAP散点图显示细胞被重新聚类为6个cluster」— sentence_id=173 'A UMAP three-dimensional (3D) scatterplot revealed six reclusters' 直接支撑；"
            "(2)「4个cluster与细胞周期相关（G0/G1、G2、S、M）」— sentence_id=173 'four of which followed a cyclic pattern (S phase, G2 phase, G0/G1 phase and M phase) according to the assigned cell-cycle marker genes' 直接支撑；"
            "(3)「另外2个分别为花托原基（RP）和花基部分生组织（FIM）」— sentence_id=173 'the remaining two were annotated as receptacle primordium and FIM' 直接支撑。全部准确。"
        ),
        "classification_reason": "所有分句与论文原文完全对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C30": {
        "sentence_ids": [183, 182],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 陈述：花托细胞增殖主要由激活S期细胞活动调控。"
            "sentence_id=183 'cell proliferation in the receptacle is regulated by the activation of meristematic activities in S phase cells' 直接支撑；"
            "sentence_id=182 'TOR kinase gene...was found to predominantly expressed in S phase cells' 提供了机制层面的额外支撑。"
            "准确转述。"
        ),
        "classification_reason": "与论文发现完全对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C31": {
        "sentence_ids": [89, 215, 216],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 陈述：调控心皮器官决定和单性花表型的转录因子CRC在S4/5后的花托中高表达。"
            "sentence_id=89 'Clusters 9 and 10 were characterized as receptacle-early and receptacle-late, based on the specific expression of CsCRABS CLAW (CsCRC), a member of the YABBY TF that is required for female development in cucumber' 支撑了 CRC 作为花托标记基因和其在雌花发育中的作用；"
            "sentence_id=215 'after S4/5. CRC also serves as a marker gene for the receptacle' + sentence_id=216 'we confirmed its expression in the receptacle during stages S5–S8-4' 直接支撑了时空表达模式。准确转述。"
        ),
        "classification_reason": "所有分句与论文原文精确对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C32": {
        "sentence_ids": [217, 218, 219, 224],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含3个分句：(1)「通过空间共表达模块分析筛选与CRC共表达的基因，共聚类到17个模块中（M1-M17）」— sentence_id=217 'spatial coexpression module analysis using Giotto' + 参数 k=17 (sentence_id=468)；sentence_id=224 显示了 M1-17 模块热图；"
            "(2)「M14分布于花托（图5h）」— sentence_id=218 'module 14 was distributed in the receptacle' 直接支撑。"
            "准确转述。"
        ),
        "classification_reason": "所有分句与论文原文对应，分析方法和结果描述准确。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C33": {
        "sentence_ids": [219, 230],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含2个分句：(1)「M14中与CRC高度相关的基因包括KNAT2-like1、ER、DIN10、CA1P等」— sentence_id=219 'This module included CRC, KNAT2-like1, ERECTA (ER), DARK INDUCIBLE 10 (DIN10), CA1P phosphatase and a putative small peptide gene' 直接支撑；"
            "(2)「KNAT2-like1和ER在细胞分裂S期富集表达，DIN10和CA1P影响碳水积累并加强吸收」— sentence_id=230 'the expression of KNAT2-like1 and ER was also predominantly enriched in S phase cells, while DIN10...' 部分支撑（后半句不完整）。"
            "整体准确，但关于 DIN10 和 CA1P 的功能描述在 sentence_id=230 中被截断，证据不够完整。"
        ),
        "classification_reason": "主体信息准确，gene list 和 S phase enrichment 均有直接证据。关于 DIN10/CA1P 的功能描述，sentence_id=231 补充 'CA1P phosphatase affect carbohydrate accumulation and enhance sink strength' 可进一步支撑。severity=mild 仅因证据不完整。",
        "key_differences": [],
        "unsupported_verdict": "likely_retrieval_miss",
        "unsupported_reasoning": "DIN10 和 CA1P 的功能描述在 sentence_id=230 中被截断，完整的句子应该包含更多信息。",
        "unsupported_keywords": ["DIN10", "CA1P", "carbohydrate", "sink strength"],
        "unsupported_ranges": "228-233",
        "manual_check_hints": "确认 sentence_id=231 是否包含了 DIN10/CA1P 功能的完整描述。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "medium"
    },
    "C34": {
        "sentence_ids": [233],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 陈述：花托的快速生长以库容量信号为核心，整合KNAT2-like1、CRC和ER来调节细胞增殖。"
            "sentence_id=233 'Based on these findings, we proposed that, in cucumber, the rapid growth of the receptacle is centred around sink capacity signalling and integration of KNAT2-like1, CRC and ER to regulate cell proliferation, ultimately promoting receptacle development' 逐字对应。"
            "但 claim 略去了 'we proposed that'（我们提出）和 'in cucumber'（在黄瓜中）两个限定。轻微 context_stripping。"
        ),
        "classification_reason": (
            "主体信息与论文完全一致。论文用 'we proposed' 表明这是提出的模型/假说，claim 陈述为事实性结论——轻微 certainty_amplification。同时省略了 'in cucumber'（物种限定）。"
            "severity=mild 因为核心内容未变。"
        ),
        "key_differences": [
            {"type": "certainty_amplification", "paper_expression": "we proposed that, in cucumber, the rapid growth...", "article_expression": "花托的快速生长以库容量信号为核心…", "description": "论文明示为 proposed model 且限定于 cucumber，公众号陈述为事实"}
        ],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "评估 'we proposed' → 事实性陈述的转换是否构成有意义的失真。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "medium"
    },
    "C35": {
        "sentence_ids": [234, 235],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 陈述：构建了黄瓜KNAT2-like1敲除突变体k-1和k-2。"
            "sentence_id=234 'CRISPR–Cas9 was used to generate two homozygous transgene-free loss-of-function mutant lines, k-1 (2 bp deletion) and k-2 (1 bp insertion)' 直接支撑。准确转述。"
        ),
        "classification_reason": "与论文方法描述完全对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C36": {
        "sentence_ids": [238, 239, 254],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "context_stripping",
        "secondary_types": [],
        "is_accurate": False,
        "severity": "mild",
        "evidence_judgement": (
            "含3个分句：(1)「k-1和k-2出现子房上位和双性花表型」— sentence_id=238 'superior ovary flowers appeared, exhibiting the phenotype of bisexual flowers with superior ovaries' 直接支撑；"
            "(2)「回补株系（C-1、C-2）表型与野生型类似」— 候选证据中未检索到关于 complementation lines (C-1, C-2) 的描述！这很可能是检索遗漏，因为论文中的 mutant 验证通常会包含 complementation test。"
            "sentence_id=239 '60% of normal female flowers transformed into superior ovary flowers in the mutants' 支撑了突变体表型比例。"
            "对 C-1/C-2 回补株系的证据缺失需要查明。"
        ),
        "classification_reason": (
            "primary_type=context_stripping：回补实验（complementation）是验证突变体表型的关键对照。公众号提到了回补株系 C-1/C-2，但候选证据中完全缺失相关句子。如果论文确实有这项实验但未被检索到，则本条应修正为 accurate；如果论文没有回补实验，则'回补株系'属于 fact_addition。"
        ),
        "key_differences": [],
        "unsupported_verdict": "likely_retrieval_miss",
        "unsupported_reasoning": "论文中很可能描述了 complementation test（回补实验），但候选证据中未检索到 C-1/C-2 或 complementation 相关的句子。这是典型的检索覆盖面不足。",
        "unsupported_keywords": ["complementation", "C-1", "C-2", "complementary", "rescue"],
        "unsupported_ranges": "234-260",
        "manual_check_hints": "到论文中搜索 'complementation'、'C-1'、'C-2'、'complementary line' 等关键词，确认回补实验是否确实存在。如果存在，追加对应的 sentence_id 做 gold retrieval 并将本条改为 accurate。",
        "needs_manual_review": True,
        "review_focus": ["gold_sentence_ids", "evidence_level"],
        "ai_confidence": "low"
    },
    "C37": {
        "sentence_ids": [248, 264, 4],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含2个分句：(1)「S5之前雄花、雌花和k-2之间没有区别，S5之后k-2花托没有快速生长并与心皮融合」— sentence_id=248 'the floral receptacle of the superior ovary flower did not fuse with the carpel and elongate, while the carpel continued to grow' 支撑了 S5 后的差异；sentence_id=264 'loss of KNAT2-like1 expression causes an arrest in receptacle development...' 支撑了机制。"
            "关于 S5 之前无区别的说法：sentence_id=243 'The development of male flowers in the k-2 mutants was identical to WT' 部分支撑了突变体和 WT 在某些发育阶段的相似性。"
            "整体准确。"
        ),
        "classification_reason": "与论文表型描述一致。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "确认 S5 之前雌花（而非雄花）突变体与 WT 是否有差异——sentence_id=243 只提到 male flowers 相同。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "medium"
    },
    "C38": {
        "sentence_ids": [253, 264, 283],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含2个分句：(1)「利用micro-CT进行3D重建」— sentence_id=253 '3D reconstruction using X-ray microcomputed tomography (micro-CT)' 直接支撑；"
            "(2)「花托在S5后的停止扩展和心皮持续向上生长导致k-2黄瓜出现类似番茄的子房上位性状」— sentence_id=253 'the arrest of receptacle expansion and continuous upward growth of the carpel resulted in the formation of superior ovaries' + sentence_id=264 直接支撑。"
            "准确转述。"
        ),
        "classification_reason": "所有分句与论文原文精确对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C39": {
        "sentence_ids": [255],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 陈述：突变体3个心皮柱头未完全融合，影响授粉和种子产生。"
            "sentence_id=255 'three carpels did not fuse to form a normal stigma, thus preventing pollination and seeds production' 直接支撑。准确转述。"
        ),
        "classification_reason": "与论文原文完全对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C40": {
        "sentence_ids": [260, 265, 266],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含3个分句：(1)「k-2果实胎座结构与黄瓜典型结构相反（图6g）」— sentence_id=260 'In WT cucumber, the inferior ovary has a typical placenta, where the medial region is the abaxial side, and the lateral region is adaxial' 描述了 WT 典型结构，sentence_id=261/263 暗示突变体结构改变，支撑了结构与典型相反的说法；"
            "(2)「野生型花托心皮近轴面融合，使内外侧模式发生变化」— sentence_id=265 'receptacle is fused with the adaxial side of the carpel...resulting in a change in lateral and medial patterning' 直接支撑；"
            "(3)「从而导致子房下位花性状（图6i）」— sentence_id=265/266 支撑。"
            "准确转述。"
        ),
        "classification_reason": "与论文提出的模型一致，准确描述了花托-心皮融合导致下位子房的机制。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C41": {
        "sentence_ids": [257, 259],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 陈述：KNAT2-like1在雌花扩展花托中特异性表达。"
            "sentence_id=257 'the specific expression of KNAT2-like1 in the expanding receptacle of female flower buds' 直接支撑；"
            "sentence_id=259 'the expression activity of KNAT2-like1 is associated with receptacle development' 进一步支撑。准确转述。"
        ),
        "classification_reason": "与论文原位杂交结果描述完全对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C42": {
        "sentence_ids": [268, 271, 272],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含2个分句：(1)「构建了ER缺失突变体er和CRC缺失突变体crc」— sentence_id=268 'generating two ER-knockout mutants, erCR-1...and erCR-2' 支撑了 ER 突变体；sentence_id=272 'all plants lacking CRC function were androecious' 暗示了 CRC 突变体的存在；"
            "(2)「以及以上3个基因的双突、三突突变体」— sentence_id=271 'we also generated double and triple mutants' 直接支撑。准确转述。"
        ),
        "classification_reason": "与论文遗传学实验描述一致。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "确认 CRC 单突的具体构建方法和命名。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C43": {
        "sentence_ids": [273],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 陈述：CRC较其他两个基因呈显性上位，ER较KNAT2-like1呈显性上位。"
            "sentence_id=273 'CRC is epistatic to ER and KNAT2-like1, and ER is epistatic to KNAT2-like1' 逐字对应。"
            "术语翻译 'epistatic' → '显性上位' 是准确的遗传学术语。"
        ),
        "classification_reason": "与论文遗传学结论完全对应，术语翻译准确。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C44": {
        "sentence_ids": [276, 266],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "claim 陈述：KNAT2-like1在花托生长、子房下位发育中起关键性作用，CRC、ER也可能参与其相互调控机制。"
            "sentence_id=276 'these findings indicate a key role for KNAT2-like1 in floral receptacle development and inferior ovary formation and suggest a regulatory mechanism involving genetic interaction with ER and CRC' 直接支撑。"
            "论文用 'suggest'（暗示），claim 用'可能参与'，保留了不确定性。准确转述。"
        ),
        "classification_reason": "与论文总结段落完全对应，不确定性措辞保留得当。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C45": {
        "sentence_ids": [282],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含2个分句：(1)「番茄花芽无花基部分生组织（FIM）结构」— sentence_id=282 'Unlike cucumber, tomato lacks a specific FIM' 直接支撑；"
            "(2)「花托位于心皮下方」— sentence_id=282 'the receptacle is located below the carpel rather than in the surrounding position for cucumber' 直接支撑。"
            "准确转述。"
        ),
        "classification_reason": "与论文比较分析结果完全对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C46": {
        "sentence_ids": [283, 264],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含2个分句：(1)「knat2-like1突变体虽然有FIM，但花托停止扩张」— sentence_id=283 'Although the superior ovary mutant still has an FIM, the arrested receptacle...were observed' 直接支撑；"
            "(2)「心皮也向上生长（图7a-b）」— sentence_id=283 'upward carpel growth were observed' + sentence_id=264 'continuous growth of the carpel' 直接支撑。"
            "准确转述。"
        ),
        "classification_reason": "与论文突变体表型描述完全对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "无需特别审核。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C47": {
        "sentence_ids": [348, 306, 308, 314, 316],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "certainty_amplification",
        "secondary_types": [],
        "is_accurate": False,
        "severity": "mild",
        "evidence_judgement": (
            "含2个分句：(1)「子房下位黄瓜FIM或花托中特异性表达的基因（如STM-like、KNAT2-like1、CRC等）在番茄中表达缺失、表达量少或表达异位」— sentence_id=348 'novel genes, STM-like and KNAT2-like1, and new expression patterns that have emerged in the FIM and receptacle, contributing to the evolution of inferior ovaries in cucumber' 支撑了这些基因在黄瓜中的特异性表达以及在进化中的作用；sentence_id=306 'SlKN4 showed little expression in the floral receptacle' 支撑番茄中表达量少；sentence_id=308 'lack of expression in the superior ovary mutant' + sentence_id=314 'a distinct expression pattern has evolved in cucumber' 支撑了表达差异。"
            "(2)整体来看 claim 将论文的发现总结为该对比结果，措辞较为准确。"
            "severity=mild：claim 中'表达缺失、表达量少或表达异位'是合理的概括，但论文原文并未精确使用这三种分类。"
        ),
        "classification_reason": (
            "主体信息准确，但 claim 对基因表达差异的三分类（缺失/量少/异位）可能过度系统化了论文中较分散的描述。论文用 'little expression'、'lack of expression'、'new expression patterns' 等措辞，而非严格的分类框架。mild certainty_amplification。"
        ),
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "确认论文是否对这三种表达差异模式进行了明确分类。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "medium"
    },
    "C48": {
        "sentence_ids": [296, 295, 316, 326],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none",
        "evidence_judgement": (
            "含3个分句：(1)「通过黄瓜与其他几个代表性被子植物KNOX序列构建系统进化树，从而鉴定黄瓜KNOX转录因子」— sentence_id=295 'phylogenetic tree' + sentence_id=326 'A more comprehensive phylogenetic tree including additional species is provided in Extended Data Fig. 10' 支撑了进化树构建；"
            "(2)「CsSTM-like在番茄中不存在直系同源基因」— sentence_id=296 'CsSTM-like, which does not have an orthologue in tomato' 直接支撑。"
            "准确转述。"
        ),
        "classification_reason": "与论文比较基因组学发现完全对应。",
        "key_differences": [],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "确认 KNOX 进化树的物种覆盖范围与 claim 中'几个代表性被子植物'的表述是否匹配。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "medium"
    },
    "C49": {
        "sentence_ids": [348, 327],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "certainty_amplification",
        "secondary_types": [],
        "is_accurate": False,
        "severity": "mild",
        "evidence_judgement": (
            "claim 陈述：FIM中新基因的出现和一些基因的新表达模式是黄瓜进化出下位子房的原因。"
            "sentence_id=348 'novel genes, STM-like and KNAT2-like1, and new expression patterns that have emerged in the FIM and receptacle, contributing to the evolution of inferior ovaries in cucumber' 支撑，但论文用 'contributing to'（贡献于），claim 改为'是…的原因'（是原因），从 contributing factor 变为 causal explanation。"
            "sentence_id=327 'in the FIM and receptacle, leading to the evolutionary innovation of an inferior ovary in the cucurbits' 使用了 'leading to'，比 'contributing to' 更强，但仍不如 '是…的原因' 绝对。"
            "整体轻微确定性放大。"
        ),
        "classification_reason": (
            "论文用 'contributing to'（贡献于）表明这些基因和表达模式是进化过程的一部分因素，而非唯一/主要原因。claim 的'是…的原因'暗示了更强的因果关系。severity=mild。"
        ),
        "key_differences": [
            {"type": "certainty_amplification", "paper_expression": "contributing to the evolution of inferior ovaries", "article_expression": "是黄瓜进化出子房下位这一性状的原因", "description": "contributing factor → 原因（因果强度提升）"}
        ],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "确认论文讨论部分是否使用了比 'contributing to' 更强的措辞来描述这些基因的因果作用。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "medium"
    },
    "C50": {
        "sentence_ids": [3, 43, 59],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "certainty_amplification",
        "secondary_types": ["mechanism_simplification"],
        "is_accurate": False,
        "severity": "mild",
        "evidence_judgement": (
            "含2个分句：(1)「通过空间转录组分析和细胞谱系重建，揭示了黄瓜下位子房的形成依赖于花托快速生长」— sentence_id=3 'Comparative spatial transcriptome mapping and cell lineage reconstructions...revealed that inferior ovaries develop from accelerated receptacle growth' 支撑，但论文说 'develop from'（由…发育而来），claim 说'依赖于'（depends on），措辞略不同；"
            "(2)「这一过程由花基部分生组织（FIM）驱动」— sentence_id=43 'prolonged activity of the floral intercalary meristem (FIM)...leads to rapid growth of the receptacle, which results in inferior ovary formation' 支撑了 FIM→花托生长→下位子房的链条。"
            "论文还提出了三个必要条件（sentence_id=59），claim 简化了机制链条（未提 floral meristem enlargement 和 carpel fusion）。"
            "severity=mild 因为主体方向正确，但略去了多步骤机制中的部分环节。"
        ),
        "classification_reason": (
            "primary_type=certainty_amplification：论文用 'develop from' 描述发育来源（描述性），claim 用'依赖于'（暗示必要条件），确定性略强。"
            "secondary=mechanism_simplification：论文提出了三个必要条件（enlargement→growth→fusion），claim 只强调了 growth+FIM，省略了 enlargement 和 fusion。"
        ),
        "key_differences": [
            {"type": "certainty_amplification", "paper_expression": "develop from accelerated receptacle growth", "article_expression": "依赖于花托快速生长", "description": "'develop from'（发育来源）→ '依赖于'（必要条件）"},
            {"type": "mechanism_simplification", "paper_expression": "three necessary conditions: enlargement, rapid growth, fusion", "article_expression": "花托快速生长…由FIM驱动", "description": "三条件简化为单一的 FIM→花托生长 轴"}
        ],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "确认 claim 的简化是否在合理概括范围内（论文摘要本身也强调了 FIM→receptacle growth 这一主线）。",
        "needs_manual_review": True,
        "review_focus": ["primary_type", "secondary_types"],
        "ai_confidence": "medium"
    },
    "C51": {
        "sentence_ids": [266, 274, 342, 259, 4],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "certainty_amplification",
        "secondary_types": [],
        "is_accurate": False,
        "severity": "mild",
        "evidence_judgement": (
            "含2个分句：(1)「黄瓜中一个花托特异表达的KNOX1转录因子（KNAT2-like1）」— sentence_id=274 'KNAT2-like1 was specifically expressed in the receptacle' + sentence_id=4 'a receptacle-specific KNOX1 transcription factor' 直接支撑；"
            "(2)「在调控花托生长和子房位置中起关键作用」— sentence_id=266 'pivotal role of KNAT2-like1 in receptacle development and ovary positioning' + sentence_id=342 'KNAT2-like1 is critical for receptacle development and inferior ovary formation' 支撑。"
            "论文用 'pivotal role' 和 'critical for' 已经是较强措辞，claim 的'关键作用'准确对应。但论文保留了 'in cucumber' 限定，claim 未提物种。"
            "severity=mild。"
        ),
        "classification_reason": (
            "主体信息准确。论文用 'pivotal/critical' 等较强措辞，与 claim 的'关键作用'一致。省略 'in cucumber' 限定为轻微 context_stripping。"
        ),
        "key_differences": [
            {"type": "context_stripping", "paper_expression": "pivotal role of KNAT2-like1 in receptacle development and ovary positioning (in cucumber)", "article_expression": "在调控花托生长和子房位置中起关键作用", "description": "省略了物种限定"}
        ],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "确认省略 'in cucumber' 是否可视作上下文中隐含（因为整篇都在讲黄瓜）。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "high"
    },
    "C52": {
        "sentence_ids": [4, 277, 333, 264],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": ["certainty_amplification"],
        "is_accurate": True,
        "severity": "mild",
        "evidence_judgement": (
            "含3个分句：(1)「敲除KNAT2-like1，黄瓜花托生长受阻，导致下位子房转变为类似番茄的上位子房」— sentence_id=4 'Genetic knockout...caused arrest in receptacle growth and yielded bisexual flowers with superior ovaries similar to those of tomato' + sentence_id=264/333 直接支撑；"
            "(2)「部分雌花转变为两性花」— sentence_id=277 'mutation of just KNAT2-like1...simultaneously leads to the formation of bisexual flowers and superior ovaries' 直接支撑；"
            "(3)「表明KNAT2-like1不仅调控子房位置，还参与性别决定」— sentence_id=277 'a direct link was established between the development of the unisexual flowers and inferior ovaries' 支撑了关联。"
            "论文说 'suggesting'（暗示建立了直接联系），而 claim 说'表明…不仅…还'，措辞略强但整体准确。'不仅…还'这个归纳是合理的。"
        ),
        "classification_reason": (
            "主体信息准确。论文用 'suggesting that...a direct link was established'（暗示建立了直接联系），claim 用'表明…不仅…还参与'，将 suggesting 转为确定性结论。轻微 certainty_amplification 作为 secondary。"
        ),
        "key_differences": [
            {"type": "certainty_amplification", "paper_expression": "suggesting that during receptacle evolution a direct link was established between the development of the unisexual flowers and inferior ovaries", "article_expression": "表明KNAT2-like1不仅调控子房位置，还参与性别决定", "description": "suggesting → 表明，论文的推测变为公众号的结论"}
        ],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "确认论文对 KNAT2-like1 在性别决定中角色的措辞强度。",
        "needs_manual_review": False,
        "review_focus": [],
        "ai_confidence": "medium"
    },
    "C53": {
        "sentence_ids": [233, 276, 277, 33],
        "is_answerable": True,
        "evidence_level": "With_Evidence",
        "primary_type": "certainty_amplification",
        "secondary_types": ["fact_addition", "mechanism_simplification"],
        "is_accurate": False,
        "severity": "moderate",
        "evidence_judgement": (
            "含3个分句：(1)「揭示了KNAT2-like1与CRC和ER共表达调控花托快速生长」— sentence_id=233/267/276 支撑了共表达调控模块的存在，但论文用 'we proposed that...is centred around sink capacity signalling and integration of...' 是提出的模型，不是被揭示的确定事实；"
            "(2)「从而导致子房下位及单性花的机制」— sentence_id=277 'mutation of just KNAT2-like1...leads to...bisexual flowers and superior ovaries, suggesting...a direct link' + sentence_id=341 'developmental innovation of the receptacle...contributes to the evolution of unisexual flowers' 支撑了关联；"
            "(3)「为作物育种提供了新靶点」— sentence_id=33 'provide valuable insights for future breeding programmes' 支撑了育种价值的说法，但论文用 'provide valuable insights for'（提供有价值的见解），claim 改为'提供了新靶点'（提供了新靶点），从 insights 变为 concretized 的靶点。"
            "severity=moderate：论文提出了一个模型，claim 将其表述为已被揭示的确定机制；'新靶点'比 'insights' 具体化了育种应用。"
        ),
        "classification_reason": (
            "primary_type=certainty_amplification：论文用 'we proposed'（我们提出）、'suggesting'（暗示）表示模型和推测，claim 用'揭示了…的机制'表述为确定事实。"
            "secondary_types：fact_addition（'新靶点'是比 'insights' 更具体、更强的声称）、mechanism_simplification（sink capacity signalling 这个关键概念在 claim 中被省略，简化为单纯的共表达调控）。"
        ),
        "key_differences": [
            {"type": "certainty_amplification", "paper_expression": "we proposed that...is centred around sink capacity signalling and integration of KNAT2-like1, CRC and ER", "article_expression": "揭示了KNAT2-like1与CRC和ER共表达调控…的机制", "description": "proposed model → 揭示的机制"},
            {"type": "fact_addition", "paper_expression": "provide valuable insights for future breeding programmes", "article_expression": "为作物育种提供了新靶点", "description": "insights（见解）→ 新靶点（concrete targets）"}
        ],
        "unsupported_verdict": "not_applicable",
        "unsupported_reasoning": "",
        "unsupported_keywords": [],
        "unsupported_ranges": "",
        "manual_check_hints": "核对论文 discussion 是否将 KNAT2-like1/CRC/ER 明确称为 'breeding targets'；确认 '揭示了…机制' 是否过度陈述了论文的模型性质。",
        "needs_manual_review": True,
        "review_focus": ["primary_type", "secondary_types"],
        "ai_confidence": "medium"
    },
}


def build_sample(claim_data, analysis):
    """Build one sample dict from claim data and analysis."""
    claim_id = claim_data["claim_id"]
    sample_id = f"{PAPER_ID}-{ARTICLE_ID}-{claim_id}"

    # Build gold_classification
    gold_cls = {
        "evidence_level": analysis["evidence_level"],
        "primary_type": analysis["primary_type"],
        "secondary_types": analysis.get("secondary_types", []),
        "is_accurate": analysis["is_accurate"],
        "severity": analysis["severity"]
    }

    # If No_Evidence or primary_type empty, adjust
    if analysis["evidence_level"] == "No_Evidence":
        gold_cls["primary_type"] = ""
        gold_cls["secondary_types"] = []
        gold_cls["is_accurate"] = False

    # Build analysis dict
    analysis_dict = {
        "evidence_judgement": analysis["evidence_judgement"],
        "classification_reason": analysis["classification_reason"],
        "key_differences": analysis.get("key_differences", []),
        "unsupported_diagnosis": {
            "verdict": analysis.get("unsupported_verdict", "not_applicable"),
            "reasoning": analysis.get("unsupported_reasoning", ""),
            "suggested_keywords": analysis.get("unsupported_keywords", []),
            "suggested_sentence_ranges": analysis.get("unsupported_ranges", "")
        },
        "manual_check_hints": analysis.get("manual_check_hints", ""),
        "needs_manual_review": analysis.get("needs_manual_review", False),
        "review_focus": analysis.get("review_focus", []),
        "ai_confidence": analysis.get("ai_confidence", "medium")
    }

    sample = {
        "sample_id": sample_id,
        "paper_id": PAPER_ID,
        "article_id": ARTICLE_ID,
        "article_source_type": SOURCE_TYPE,
        "claim_zh": claim_data["claim_zh"],
        "gold_retrieval": {
            "sentence_ids": analysis["sentence_ids"],
            "is_answerable": analysis["is_answerable"]
        },
        "gold_classification": gold_cls,
        "analysis": analysis_dict,
        "human_verified": False
    }

    return sample


def main():
    # Read input
    claims = []
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                claims.append(json.loads(line))

    print(f"Read {len(claims)} claims from {INPUT_FILE}")

    samples = []
    skipped = []
    for claim_data in claims:
        cid = claim_data["claim_id"]
        if cid in ANALYSES:
            sample = build_sample(claim_data, ANALYSES[cid])
            samples.append(sample)
        else:
            skipped.append(cid)
            print(f"WARNING: No analysis found for {cid}, skipping")

    if skipped:
        print(f"Skipped claims: {skipped}")

    # Build review queue
    must_review = [s["sample_id"] for s in samples if s["analysis"]["needs_manual_review"]]

    output = {
        "schema_version": "1.0",
        "status": "draft",
        "paper_id": PAPER_ID,
        "article_id": ARTICLE_ID,
        "article_source_type": SOURCE_TYPE,
        "generated_date": GEN_DATE,
        "_description": "标注草稿：字段与 benchmark 一致，额外含 analysis；人工审核后导出终稿。",
        "samples": samples,
        "review_queue": {
            "must_review_sample_ids": must_review,
            "notes": "优先审这些；见各条 analysis.manual_check_hints"
        }
    }

    # Write output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(samples)} samples to {OUTPUT_FILE}")
    print(f"Must review: {len(must_review)} samples")
    for sid in must_review:
        print(f"  - {sid}")


if __name__ == "__main__":
    main()
