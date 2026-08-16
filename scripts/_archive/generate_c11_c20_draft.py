# -*- coding: utf-8 -*-
"""Generate annotation draft for claims C11-C20 from claim_evidence_pairs.jsonl.
Avoids hardcoding Chinese text in Python source to prevent encoding issues."""

import json
from datetime import date

INPUT_FILE = "outputs/P001/A001/claim_evidence_pairs.jsonl"
OUTPUT_FILE = "data/annotations/P001/P001_A001_annotation_draft_C11_C20.json"
PAPER_ID = "P001"
ARTICLE_ID = "A001"
SOURCE_TYPE = "high_quality"

def load_claims(filepath, claim_ids):
    claims = {}
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            obj = json.loads(line.strip())
            cid = obj.get('claim_id', '')
            if cid in claim_ids:
                claims[cid] = obj
    return claims

def build_system_retrieval(claim):
    classify = []
    for e in claim.get('classify_evidences', [])[:5]:
        classify.append({"rank": e["rank"], "sentence_id": e["sentence_id"], "text": e["text"]})
    review = []
    for e in claim.get('review_evidences', [])[:10]:
        review.append({"rank": e["rank"], "sentence_id": e["sentence_id"], "text": e["text"]})
    return {"classify_evidences": classify, "review_evidences": review}

# ─── Analysis definitions keyed by claim_id ───
# Each entry: (gold_retrieval, gold_classification, analysis)
# claim_zh is taken directly from the input JSONL

ANALYSES = {}

# ─── C11 ───
ANALYSES['C11'] = (
    {  # gold_retrieval
        "evidences": [
            {"sentence_id": 59, "text": (
                "Based on these observations, we inferred three necessary conditions for the "
                "formation of the inferior ovary in cucumber: (1) enlargement of the floral "
                "meristem to change the relative position of the floral whorls, (2) subsequent "
                "rapid growth of the receptacle and (3) fusion of the receptacle and the carpel."
            )},
            {"sentence_id": 54, "text": (
                "After initiation, the cucumber floral meristem enlarges from the perimeter "
                "and becomes concave (Fig."
            )},
            {"sentence_id": 55, "text": (
                "1b), then sepal, petal, stamen and carpel primordia are initiated sequentially "
                "on the inner regions of this 'enlarged floral meristem' (EFM) (Fig."
            )}
        ],
        "sentence_ids": [59, 54, 55],
        "is_answerable": True
    },
    {  # gold_classification
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none"
    },
    {  # analysis
        "evidence_judgement": (
            "s59 是该 claim 的精确原文来源：'we inferred three necessary conditions for the\n"
            "formation of the inferior ovary in cucumber: (1) enlargement of the floral meristem to\n"
            "change the relative position of the floral whorls'。claim 准确转述了条件(1)的全部\n"
            "要素：花分生组织膨大=enlargement of the floral meristem，改变花器官轮相对位置=\n"
            "change the relative position of the floral whorls。\n"
            "s54 补充了分生组织膨大的形态描述（'enlarges from the perimeter and becomes concave'），\n"
            "s55 补充了膨大分生组织内部花器官原基的依次起始。\n"
            "注意：C11与C12、C13共同拆分自s59的三个条件，每条仅含一部分。\n"
            "top-5 中 s59(rank1)为最佳总括句，s54(rank3)和s55(审核池rank7)提供形态学细节。检索质量好。"
        ),
        "classification_reason": (
            "准确。claim 是 s59 条件(1)的中文直译：\n"
            "'enlargement of the floral meristem' -> 花分生组织膨大；\n"
            "'change the relative position of the floral whorls' -> 改变花器官轮相对位置。\n"
            "术语一一对应，无放大、无添加。"
        ),
        "key_differences": [],
        "rag_review": {
            "top5_is_best": True,
            "better_in_review_pool": [],
            "notes": "s59(rank1)即最佳匹配句，直接包含三个必要条件的完整陈述。"
        },
        "unsupported_diagnosis": {
            "verdict": "not_applicable",
            "reasoning": "claim 逐字对应 s59 条件(1)。",
            "suggested_keywords": [],
            "suggested_sentence_ranges": ""
        },
        "manual_check_hints": (
            "几乎无需核对。确认 s59 中 'enlargement of the floral meristem' "
            "与 claim 的翻译准确性即可。"
        ),
        "needs_manual_review": False,
        "review_focus": ["none"],
        "ai_confidence": "high"
    }
)

# ─── C12 ───
ANALYSES['C12'] = (
    {
        "evidences": [
            {"sentence_id": 59, "text": (
                "Based on these observations, we inferred three necessary conditions for the "
                "formation of the inferior ovary in cucumber: (1) enlargement of the floral "
                "meristem to change the relative position of the floral whorls, (2) subsequent "
                "rapid growth of the receptacle and (3) fusion of the receptacle and the carpel."
            )},
            {"sentence_id": 330, "text": (
                "Our findings revealed that the prolonged activity of the FIM at the base of "
                "the floral organs is responsible for the initiation and rapid growth of the "
                "receptacle, leading to female flower determination and the formation of an "
                "inferior ovary."
            )},
            {"sentence_id": 43, "text": (
                "Our cell lineage analysis revealed that the prolonged activity of the floral "
                "intercalary meristem (FIM) at the base of the floral organs in cucumber, leads "
                "to rapid growth of the receptacle, which results in inferior ovary formation."
            )}
        ],
        "sentence_ids": [59, 330, 43],
        "is_answerable": True
    },
    {
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none"
    },
    {
        "evidence_judgement": (
            "s59 是该 claim 的精确原文来源：'(2) subsequent rapid growth of the receptacle'。\n"
            "s330 提供了FIM活跃->花托快速生长的因果链条：'prolonged activity of the FIM...\n"
            "is responsible for the initiation and rapid growth of the receptacle'。\n"
            "s43 从细胞谱系角度独立确认了同一结论：'leads to rapid growth of the receptacle,\n"
            "which results in inferior ovary formation'。\n"
            "s56(rank1)描述了花托快速生长的后果（器官原基抬升->下位子房形成），但未直接陈述\n"
            "'rapid growth of the receptacle' 作为独立条件 -- s59 的条件(2)陈述更精确对应。\n"
            "多条证据交叉验证，支撑牢固。"
        ),
        "classification_reason": (
            "准确。与 C11 同理，为 s59 条件(2)的中文直译：\n"
            "'subsequent rapid growth of the receptacle' -> 花托迅速生长。\n"
            "无任何失真。"
        ),
        "key_differences": [],
        "rag_review": {
            "top5_is_best": True,
            "better_in_review_pool": [],
            "notes": (
                "C12的 classify top-5 不含 s59（s59 在 C11 中为 rank1），"
                "但 s330(rank2)和 s43(rank3)同样精确覆盖「花托迅速生长」。检索质量可接受。"
            )
        },
        "unsupported_diagnosis": {
            "verdict": "not_applicable",
            "reasoning": "claim 逐字对应 s59 条件(2)，且有多条独立证据交叉支撑。",
            "suggested_keywords": [],
            "suggested_sentence_ranges": ""
        },
        "manual_check_hints": (
            "与C11/C13共享s59作为总括句。人工确认s330/s43对「花托迅速生长」的支撑是否足够，"
            "或是否应将s59加入gold（尽管其不在C12的classify top-5中）。"
        ),
        "needs_manual_review": False,
        "review_focus": ["none"],
        "ai_confidence": "high"
    }
)

# ─── C13 ───
ANALYSES['C13'] = (
    {
        "evidences": [
            {"sentence_id": 59, "text": (
                "Based on these observations, we inferred three necessary conditions for the "
                "formation of the inferior ovary in cucumber: (1) enlargement of the floral "
                "meristem to change the relative position of the floral whorls, (2) subsequent "
                "rapid growth of the receptacle and (3) fusion of the receptacle and the carpel."
            )},
            {"sentence_id": 265, "text": (
                "By contrast, in normal cucumber female floral buds acquiring KNAT2-like1 "
                "activity, the receptacle is fused with the adaxial side of the carpel, and the "
                "rapidly growing receptacle then wraps around the carpel, resulting in a change "
                "in lateral and medial patterning and forming an inferior ovary (Fig."
            )},
            {"sentence_id": 248, "text": (
                "6c,d and Extended Data Fig. 7e), as the floral receptacle of the 'superior "
                "ovary flower' did not fuse with the carpel and elongate, while the carpel "
                "continued to grow, resulting in upward growth of the carpels."
            )}
        ],
        "sentence_ids": [59, 265, 248],
        "is_answerable": True
    },
    {
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none"
    },
    {
        "evidence_judgement": (
            "s59 是该 claim 的精确原文来源：'(3) fusion of the receptacle and the carpel'。\n"
            "s265 提供了花托-心皮融合的详细细胞学描述：'receptacle is fused with the adaxial\n"
            "side of the carpel, and the rapidly growing receptacle then wraps around the carpel'\n"
            "-- 融合+包裹的完整过程。\n"
            "s248 从反面（突变体表型）确认：上位子房突变体中花托 'did not fuse with the carpel\n"
            "and elongate'，反证融合是下位子房形成的必要条件。\n"
            "正反证据齐全，支撑牢固。"
        ),
        "classification_reason": (
            "准确。为 s59 条件(3)的中文直译：\n"
            "'fusion of the receptacle and the carpel' -> 花托和心皮融合。\n"
            "无任何失真。s265/s248 从正反两面提供了机制细节。"
        ),
        "key_differences": [],
        "rag_review": {
            "top5_is_best": True,
            "better_in_review_pool": [248],
            "notes": (
                "s265(rank1)为最佳机制描述句。s59在C13的top-5中为rank2，覆盖总括条件。"
                "s248(rank6,审核池)提供反面证据（突变体中不融合），是强补充但非必需。top-5覆盖充分。"
            )
        },
        "unsupported_diagnosis": {
            "verdict": "not_applicable",
            "reasoning": "claim 逐字对应 s59 条件(3)，且有正反证据支撑。",
            "suggested_keywords": [],
            "suggested_sentence_ranges": ""
        },
        "manual_check_hints": "轻量核对。s248为反面证据（突变体），可选择性加入gold。",
        "needs_manual_review": False,
        "review_focus": ["none"],
        "ai_confidence": "high"
    }
)

# ─── C14 ───
ANALYSES['C14'] = (
    {
        "evidences": [
            {"sentence_id": 60, "text": (
                "To dissect the underlying gene expression network driving the development of "
                "the inferior ovary of cucumber, we sampled floral buds at various stages, "
                "including S1-S8, and ovaries at 0 days post anthesis (DPA) for spatial "
                "transcriptomic analysis."
            )},
            {"sentence_id": 61, "text": (
                "Longitudinal sections were analysed, as well as a transverse section of S8-4 "
                "and two 0 DPA samples (Extended Data Fig. 2a), using a standard analytical "
                "procedure8."
            )},
            {"sentence_id": 112, "text": (
                "To confirm the accuracy of our annotation, we assessed the spatial distribution "
                "of several selected marker genes by RNA in situ hybridization, all of which "
                "confirmed the spatiotemporal expression patterns indicated by the Stereo-seq "
                "signals (Extended Data Fig. 2p)."
            )}
        ],
        "sentence_ids": [60, 61, 112],
        "is_answerable": True
    },
    {
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none"
    },
    {
        "evidence_judgement": (
            "拆为4个可核查分句，均有直接对应：\n"
            "(1)「解析黄瓜子房下位基因调控网络」-> s60 'To dissect the underlying gene expression\n"
            "network driving the development of the inferior ovary of cucumber'，research aim逐词对应；\n"
            "(2)「S1-S8时期的纵截面」-> s61 'Longitudinal sections were analysed'，\n"
            "s60 补充 'floral buds at various stages, including S1-S8'；\n"
            "(3)「S8-4时期、花后0天（0 DAP）子房的横截面」-> s61 'a transverse section of S8-4\n"
            "and two 0 DPA samples'，横截面=transverse section，0 DAP=0 DPA（论文用DPA=days\n"
            "post anthesis，公众号用DAP=days after pollination的变体，术语略有不同但指代相同）；\n"
            "(4)「Stereo-seq空间转录组测序」-> s60 说 'spatial transcriptomic analysis'，\n"
            "s112 明确提及 'Stereo-seq signals'，证实论文使用Stereo-seq平台 -- 术语准确。\n"
            "注意：s60未提 Stereo-seq 品牌名，但s112确认了平台名称，所以claim在技术命名上有论文内部支撑。"
        ),
        "classification_reason": (
            "准确。研究方法描述的四个维度（研究目的、样本范围、切片方向、技术平台）均在\n"
            "s60/s61/s112中找到精确对应。\n"
            "「0 DAP」vs「0 DPA」为同一概念的不同缩写变体（DPA=days post anthesis,\n"
            "DAP=days after pollination），非误差。\n"
            "Stereo-seq 品牌名在 s112 中有支撑（'Stereo-seq signals'）。"
        ),
        "key_differences": [],
        "rag_review": {
            "top5_is_best": True,
            "better_in_review_pool": [],
            "notes": (
                "s60(rank2)+s61(rank1)完美覆盖研究目的和实验设计。s112确认了Stereo-seq平台名称。"
                "top-5中s432(rank3)为方法细节（Seurat整合），s62(rank4)为bin50技术细节，"
                "与claim核心信息相关但非必需。检索质量好。"
            )
        },
        "unsupported_diagnosis": {
            "verdict": "not_applicable",
            "reasoning": "所有方法学细节均有论文原文支撑。",
            "suggested_keywords": [],
            "suggested_sentence_ranges": ""
        },
        "manual_check_hints": (
            "核对：(1)s112是否明确提及Stereo-seq（已在gold中确认）；"
            "(2)DAP vs DPA的术语差异是否需要标注。"
        ),
        "needs_manual_review": False,
        "review_focus": ["none"],
        "ai_confidence": "high"
    }
)

# ─── C15 ───
ANALYSES['C15'] = (
    {
        "evidences": [
            {"sentence_id": 280, "text": (
                "These clusters were annotated based on their distribution and the expression "
                "of marker genes (Fig."
            )},
            {"sentence_id": 112, "text": (
                "To confirm the accuracy of our annotation, we assessed the spatial distribution "
                "of several selected marker genes by RNA in situ hybridization, all of which "
                "confirmed the spatiotemporal expression patterns indicated by the Stereo-seq "
                "signals (Extended Data Fig. 2p)."
            )},
            {"sentence_id": 111, "text": (
                "Clusters 12, 16, 20, 35 and 39 had no known marker genes and were unannotated."
            )}
        ],
        "sentence_ids": [280, 112, 111],
        "is_answerable": True
    },
    {
        "evidence_level": "Weak_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none"
    },
    {
        "evidence_judgement": (
            "拆为4个可核查分句：\n"
            "(1)「经数据降维后」-> 检索证据中未直接提及降维方法（UMAP/t-SNE），\n"
            "但空间转录组分析的标准流程包含降维步骤，论文方法部分大概率描述但未被检索命中；\n"
            "(2)「所有样品细胞共被分为41个聚类（图2a）」-> 审核池中无直接陈述 '41 clusters'\n"
            "的句子。s111 提及 clusters 编号达到39且有未注释聚类，暗示总数>=39。\n"
            "s76 提及 cluster 33。论文大概率在 Results 中明确写出聚类总数，但系统检索未命中\n"
            "此句 -- 检索覆盖不足。41这个数字本身可信（基于s111的编号范围），但无法在现有\n"
            "证据中直接确认；\n"
            "(3)「通过特异性标记基因表达模式（图2b）验证组织特异性」-> s280 'annotated\n"
            "based on their distribution and the expression of marker genes' 直接支撑；\n"
            "s112 补充了RNA原位杂交的独立验证方法；\n"
            "(4)「空间分布（图2c）验证」-> s280 同样覆盖（'based on their distribution'），\n"
            "s104(rank5) 提供了 'spatial distribution of clusters on the sections'。\n"
            "判 Weak_Evidence：聚类验证的方法论（标记基因+空间分布）在现有证据中有充分支撑，\n"
            "但「41」这一精确数字无法在检索结果中确认 -- 为 likely_retrieval_miss。"
        ),
        "classification_reason": (
            "Weak_Evidence：聚类注释和验证的方法学描述准确（s280/s112），'41'的具体数字\n"
            "无法在现有检索证据中确认。论文Results部分极大概率有明确陈述（如 'In total,\n"
            "41 clusters were identified'），但未被检索命中。\n"
            "不应判为 claim 错误 -- 41个聚类是标准空间转录组分析的可信输出，且s111的聚类编号\n"
            "（最高39+未注释聚类）与此一致。人工需在论文全文中确认41这个数字。"
        ),
        "key_differences": [],
        "rag_review": {
            "top5_is_best": False,
            "better_in_review_pool": [],
            "notes": (
                "top-5中无可直接确认「41 clusters」的最佳句 -- s105(rank1)仅说'marker genes "
                "are shown'过于笼统；s112(rank2)描述验证方法但未提聚类总数；s280(rank3)描述"
                "注释依据但同样未提总数。检索遗漏了明确陈述聚类总数的句子。"
            )
        },
        "unsupported_diagnosis": {
            "verdict": "likely_retrieval_miss",
            "reasoning": (
                "论文Results中极大概率有明确陈述 '41 clusters' 的句子（标准空间转录组论文的\n"
                "常规报告格式），但未被系统检索命中。人工需在论文全文搜索 '41' 或 'forty-one'\n"
                "以及 cluster/clustering 确认。"
            ),
            "suggested_keywords": ["41", "clusters", "identified", "total"],
            "suggested_sentence_ranges": "Results 中空间转录组聚类结果段落（约 sentence 65-115）"
        },
        "manual_check_hints": (
            "关键核对：(1)论文Results是否明确写了 '41 clusters were identified'？"
            "扫句子约70-110范围，搜索 '41' 或 'forty-one'；"
            "(2)若找到确认41的句子，将其 sentence_id 加入 gold，改 evidence_level=With_Evidence；"
            "(3)若论文未提41或写了其他数字，则需改 primary_type=numerical_distortion。"
        ),
        "needs_manual_review": True,
        "review_focus": ["evidence_level", "gold_sentence_ids"],
        "ai_confidence": "medium"
    }
)

# ─── C16 ───
ANALYSES['C16'] = (
    {
        "evidences": [
            {"sentence_id": 91, "text": (
                "We also observed three clusters (26, 27 and 28) situated between the carpel "
                "and receptacle at various stages of floral bud development, which we named "
                "conjunctive primordia, conjunctive tissue-early and conjunctive tissue-late, "
                "respectively (Fig."
            )}
        ],
        "sentence_ids": [91],
        "is_answerable": True
    },
    {
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none"
    },
    {
        "evidence_judgement": (
            "s91 是该 claim 的逐词精确原文来源，单一句子覆盖全部信息：\n"
            "(1) cluster编号：26, 27, 28 -> 'three clusters (26, 27 and 28)'；\n"
            "(2) 位置：心皮和花托之间 -> 'situated between the carpel and receptacle'；\n"
            "(3) 命名：连接原基/连接组织早期/连接组织晚期 -> 'conjunctive primordia,\n"
            "conjunctive tissue-early and conjunctive tissue-late'。\n"
            "术语翻译准确：conjunctive->连接，primordia->原基，tissue-early->组织早期，\n"
            "tissue-late->组织晚期。\n"
            "s91(rank1)即为最佳匹配。"
        ),
        "classification_reason": (
            "准确。s91 的中文直译，术语一一对应：\n"
            "'three clusters (26, 27 and 28)' -> cluster 26、27、28；\n"
            "'situated between the carpel and receptacle' -> 位于心皮和花托之间；\n"
            "'conjunctive primordia' -> 连接原基；\n"
            "'conjunctive tissue-early' -> 连接组织早期；\n"
            "'conjunctive tissue-late' -> 连接组织晚期。\n"
            "无任何失真。"
        ),
        "key_differences": [],
        "rag_review": {
            "top5_is_best": True,
            "better_in_review_pool": [],
            "notes": "s91(rank1)即为最佳且唯一的精确匹配句。其余top-5均为图注碎片或泛化描述。检索质量好。"
        },
        "unsupported_diagnosis": {
            "verdict": "not_applicable",
            "reasoning": "claim 逐词对应 s91。",
            "suggested_keywords": [],
            "suggested_sentence_ranges": ""
        },
        "manual_check_hints": (
            "几乎不需核对。确认中文术语翻译（连接原基/连接组织早期/连接组织晚期）"
            "与英文术语的对应即可。"
        ),
        "needs_manual_review": False,
        "review_focus": ["none"],
        "ai_confidence": "high"
    }
)

# ─── C17 ───
ANALYSES['C17'] = (
    {
        "evidences": [
            {"sentence_id": 93, "text": (
                "In contrast to previous recognition of expression in carpel, we observed that "
                "Cs1-aminocyclopropane-1-carboxylate synthase 2 (CsACS2)24, a gene for bisexual "
                "flower development, was specifically expressed in these clusters, suggesting "
                "that the conjunctive tissue plays a role in ovary development and sex "
                "determination (Fig."
            )},
            {"sentence_id": 92, "text": (
                "2a-c). Besides the expression of marker genes in both carpel (CsAG1) and "
                "receptacle (CsCRC), the marker gene CsHANABA TARANU (CsHAN1) was exclusively "
                "expressed in cluster 27 (ref."
            )}
        ],
        "sentence_ids": [93, 92],
        "is_answerable": True
    },
    {
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none"
    },
    {
        "evidence_judgement": (
            "拆为4个可核查分句，均被 s93/s92 覆盖：\n"
            "(1)「S6-S8时期与心皮、花托以及连接组织相关的细胞聚类如图2d」-> s93引用Fig 2d,e；\n"
            "(2)「连接组织表达心皮和花托的标记基因（AG1、CRC）」-> s92 'expression of marker\n"
            "genes in both carpel (CsAG1) and receptacle (CsCRC)'，基因名称准确（AG1=CsAG1,\n"
            "CRC=CsCRC），且 s107（审核池）的bubble plot进一步确认了AG1/CRC在cluster 27/28\n"
            "中的表达；\n"
            "(3)「特异性表达两性花发育基因ACS2」-> s93 'CsACS2...a gene for bisexual flower\n"
            "development, was specifically expressed in these clusters'，「特异性表达」=\n"
            "specifically expressed；\n"
            "(4)「连接组织在子房发育和性别决定中可能同时起作用」-> s93 'suggesting that the\n"
            "conjunctive tissue plays a role in ovary development and sex determination'，\n"
            "claim的「可能」准确对应suggesting的审慎语气，ovary development=子房发育，\n"
            "sex determination=性别决定。\n"
            "top-5中s93(rank1)为最佳句。"
        ),
        "classification_reason": (
            "准确。所有要素均与论文原文精确对应：\n"
            "'CsACS2' -> ACS2；'bisexual flower development' -> 两性花发育；\n"
            "'specifically expressed' -> 特异性表达；\n"
            "'suggesting that the conjunctive tissue plays a role in ovary development and sex\n"
            "determination' -> 说明连接组织在子房发育和性别决定中可能同时起作用。\n"
            "claim用「可能」保留了论文suggesting的不确定性，表述审慎。\n"
            "注意：s92中出现的HAN1(CsHAN1)基因未被claim提及 -- 但claim仅陈述AG1和CRC\n"
            "作为心皮/花托标记基因，不涉及HAN1，属合理简化非遗漏。"
        ),
        "key_differences": [],
        "rag_review": {
            "top5_is_best": True,
            "better_in_review_pool": [],
            "notes": (
                "s93(rank1)即最佳句 -- 直接陈述ACS2在连接组织中的特异性表达及其双重功能。"
                "s92(rank4)补充了AG1/CRC标记基因信息。top-5覆盖充分。"
            )
        },
        "unsupported_diagnosis": {
            "verdict": "not_applicable",
            "reasoning": "所有分句均有精确对应。",
            "suggested_keywords": [],
            "suggested_sentence_ranges": ""
        },
        "manual_check_hints": (
            "轻量核对：(1)确认图2d/e的编号是否正确映射；"
            "(2)确认AG1->CsAG1、CRC->CsCRC的基因名称映射。"
        ),
        "needs_manual_review": False,
        "review_focus": ["none"],
        "ai_confidence": "high"
    }
)

# ─── C18 ───
ANALYSES['C18'] = (
    {
        "evidences": [
            {"sentence_id": 115, "text": (
                "To identify the major cell lineages involved in cucumber floral bud "
                "organogenesis and the mechanisms of receptacle development, we used the "
                "spatial transcriptomic data (Extended Data Fig. 3c) to generate a trajectory "
                "of floral organogenesis (TOFO), drawing on methods from the trajectory of "
                "mammal embryogenesis (TOME) analysis30."
            )},
            {"sentence_id": 116, "text": (
                "The reconstructed cell lineage reveals inferred relationships between clusters "
                "across sections from S1 to S8-4 (Fig."
            )},
            {"sentence_id": 329, "text": (
                "In this current study, we generated a comprehensive collection of spatially "
                "resolved transcriptomes and reconstructed the cellular territories and cell "
                "lineages for the continuously developing floral organs of cucumber."
            )}
        ],
        "sentence_ids": [115, 116, 329],
        "is_answerable": True
    },
    {
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none"
    },
    {
        "evidence_judgement": (
            "拆为2个可核查分句：\n"
            "(1)「利用空间转录组数据绘制了花器官发生轨迹」-> s115 'used the spatial\n"
            "transcriptomic data...to generate a trajectory of floral organogenesis (TOFO)'，\n"
            "空间转录组数据=spatial transcriptomic data，花器官发生轨迹=trajectory of floral\n"
            "organogenesis，逐词对应；\n"
            "(2)「根据cluster之间的关系重构细胞谱系（图3a）」-> s116 'The reconstructed cell\n"
            "lineage reveals inferred relationships between clusters across sections' --\n"
            "重构细胞谱系=reconstructed cell lineage，cluster之间的关系=relationships between\n"
            "clusters。s329 概括性地确认了 'reconstructed the cellular territories and cell\n"
            "lineages'。\n"
            "图3a 的引用与 s116/s115 的Fig引用一致。\n"
            "检索质量好：s115(rank1)和s116(rank2)为最精确的对应句。"
        ),
        "classification_reason": (
            "准确。s115+s116覆盖了claim的全部内容：\n"
            "'spatial transcriptomic data' -> 空间转录组数据；\n"
            "'trajectory of floral organogenesis (TOFO)' -> 花器官发生轨迹；\n"
            "'reconstructed cell lineage' -> 重构细胞谱系；\n"
            "'relationships between clusters' -> cluster之间的关系。\n"
            "翻译准确，无失真。"
        ),
        "key_differences": [],
        "rag_review": {
            "top5_is_best": True,
            "better_in_review_pool": [],
            "notes": (
                "s115(rank1)+s116(rank2)即最佳匹配组合。s329(rank3)为概括性描述，"
                "补充确认了cell lineage reconstruction的整体框架。检索质量好。"
            )
        },
        "unsupported_diagnosis": {
            "verdict": "not_applicable",
            "reasoning": "所有分句均被s115/s116精确支撑。",
            "suggested_keywords": [],
            "suggested_sentence_ranges": ""
        },
        "manual_check_hints": "几乎不需核对。确认TOFO方法来自TOME分析的引用（citation 30）即可。",
        "needs_manual_review": False,
        "review_focus": ["none"],
        "ai_confidence": "high"
    }
)

# ─── C19 ───
ANALYSES['C19'] = (
    {
        "evidences": [
            {"sentence_id": 119, "text": (
                "3a). We also examined their spatial distribution across sections, which "
                "revealed the spatiotemporal cell lineages for each supercluster (Fig."
            )},
            {"sentence_id": 118, "text": (
                "Based on the annotation and relationship of clusters, the trajectories were "
                "divided into groups corresponding to different tissues (Fig."
            )}
        ],
        "sentence_ids": [119, 118],
        "is_answerable": True
    },
    {
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none"
    },
    {
        "evidence_judgement": (
            "这是一个简洁的观察性陈述，仅有1个可核查断言。\n"
            "s119 提供了最直接支撑：'We also examined their spatial distribution across sections,\n"
            "which revealed the spatiotemporal cell lineages for each supercluster' -- 空间分布\n"
            "揭示了各超聚类的时空细胞谱系，意味着空间分布与发育谱系具有一致性。\n"
            "s118 补充了轨迹分组与组织类型对应的信息：'trajectories were divided into groups\n"
            "corresponding to different tissues' -- 组织空间的分布与发育轨迹分组一致。\n"
            "claim说「相同」而论文说'revealed'和'corresponding to' -- '相同'是对一致性的\n"
            "合理简化，但论文的措辞更为审慎（未直接声称'identical'）。属可接受的科普转述，\n"
            "不构成实质性失真。\n"
            "注意：s119位于审核池rank1，但claim极短（仅一个简单事实），证据已足够。"
        ),
        "classification_reason": (
            "准确但措辞略强于原文。\n"
            "s119 'spatial distribution...revealed the spatiotemporal cell lineages' 和\n"
            "s118 'trajectories were divided into groups corresponding to different tissues'\n"
            "共同支撑了空间分布与发育轨迹的一致性关系。\n"
            "「相同」较 'revealed'/'corresponding to' 力度略增，但作为对这一常规质控观察的\n"
            "科普转述属于可接受范围 -- 空间转录组分析中轨迹图与空间分布图的一致性本就是预期结果。"
        ),
        "key_differences": [],
        "rag_review": {
            "top5_is_best": True,
            "better_in_review_pool": [],
            "notes": (
                "s119(rank1)为最佳句，覆盖空间分布->谱系信息的逻辑。"
                "s118(rank2)补充组织对应关系。检索质量好。"
            )
        },
        "unsupported_diagnosis": {
            "verdict": "not_applicable",
            "reasoning": "claim的单一断言被s119/s118充分支撑。",
            "suggested_keywords": [],
            "suggested_sentence_ranges": ""
        },
        "manual_check_hints": (
            "轻量核对：确认论文是否直接声称空间分布与轨迹'identical'还是仅'revealed'关联。"
            "若审稿人认为'相同'太强，可下调为mild certainty_amplification。"
        ),
        "needs_manual_review": False,
        "review_focus": ["none"],
        "ai_confidence": "high"
    }
)

# ─── C20 ───
ANALYSES['C20'] = (
    {
        "evidences": [
            {"sentence_id": 125, "text": (
                "3a). We determined that after S7, the FIM and receptacle primordia generate "
                "cells that are actively undergoing the cell cycle, while G1/S phase cells "
                "transition to receptacle cells, suggesting a mechanism to maintain cell "
                "proliferation and support rapid receptacle growth (Fig."
            )},
            {"sentence_id": 265, "text": (
                "By contrast, in normal cucumber female floral buds acquiring KNAT2-like1 "
                "activity, the receptacle is fused with the adaxial side of the carpel, and "
                "the rapidly growing receptacle then wraps around the carpel, resulting in a "
                "change in lateral and medial patterning and forming an inferior ovary (Fig."
            )},
            {"sentence_id": 330, "text": (
                "Our findings revealed that the prolonged activity of the FIM at the base of "
                "the floral organs is responsible for the initiation and rapid growth of the "
                "receptacle, leading to female flower determination and the formation of an "
                "inferior ovary."
            )}
        ],
        "sentence_ids": [125, 265, 330],
        "is_answerable": True
    },
    {
        "evidence_level": "With_Evidence",
        "primary_type": "accurate",
        "secondary_types": [],
        "is_accurate": True,
        "severity": "none"
    },
    {
        "evidence_judgement": (
            "拆为3个可核查分句：\n"
            "(1)「S7后FIM与花托原基活跃地进行细胞分裂（图3a）」-> s125 精确对应：'after S7,\n"
            "the FIM and receptacle primordia generate cells that are actively undergoing the cell\n"
            "cycle'。'actively undergoing the cell cycle'=活跃进行细胞分裂，FIM和花托原基\n"
            "的命名完全一致；\n"
            "(2)「可能是花托快速生长的原因」-> s125 'suggesting a mechanism to maintain cell\n"
            "proliferation and support rapid receptacle growth'，'suggesting'->「可能是」\n"
            "审慎对应，'support rapid receptacle growth'->花托快速生长；\n"
            "(3)「从而导致与心皮融合」-> s125 本身未提融合，但 s265 详细描述了花托快速生长后\n"
            "与心皮融合的过程：'the receptacle is fused with the adaxial side of the carpel,\n"
            "and the rapidly growing receptacle then wraps around the carpel'。\n"
            "s330 从FIM->花托快速生长->下位子房形成的完整因果链条也隐含了融合步骤。\n"
            "claim将细胞分裂->快速生长->心皮融合串联为一个因果链条，s125+s265+s330的组合\n"
            "充分支撑了这一推理。"
        ),
        "classification_reason": (
            "准确。claim的核心发现与论文一致，且用「可能」保留了s125 'suggesting' 的审慎语气：\n"
            "'after S7, the FIM and receptacle primordia generate cells that are actively\n"
            "undergoing the cell cycle' -> S7后FIM与花托原基活跃地进行细胞分裂；\n"
            "'suggesting a mechanism to maintain cell proliferation and support rapid receptacle\n"
            "growth' -> 可能是花托快速生长的原因。\n"
            "末尾的「从而导致与心皮融合」是对s265内容的前向延伸 -- 论文确实描述了快速生长后\n"
            "融合的因果顺序，属合理推理非添加。\n"
            "注意：s164(rank1)为含噪声标签的图注碎片（大段器官名称列表），非最佳证据。\n"
            "s125(rank2)才是精确对应句。"
        ),
        "key_differences": [],
        "rag_review": {
            "top5_is_best": False,
            "better_in_review_pool": [],
            "notes": (
                "s125(rank2)是精确对应句，但被排在rank2。rank1(s164)是图注噪声碎片（包含大量"
                "器官名称标签），不应作为证据。rank3(s37)同样是图注噪声。top-5排序有噪声问题，"
                "但最佳句s125在rank2仍可用。"
            )
        },
        "unsupported_diagnosis": {
            "verdict": "not_applicable",
            "reasoning": "所有核心断言均有精确对应（s125=细胞分裂+suggesting机制，s265=融合过程）。",
            "suggested_keywords": [],
            "suggested_sentence_ranges": ""
        },
        "manual_check_hints": (
            "核对：(1)s125中 'G1/S phase cells transition to receptacle cells' 的细胞周期"
            "细节是否与claim的「花托原基活跃地进行细胞分裂」完全对应；"
            "(2)s164(rank1)为图注噪声，人工确认gold中不应包含此条；"
            "(3)确认因果链条（细胞分裂->快速生长->融合）是否与论文Discussion中的模型一致。"
        ),
        "needs_manual_review": True,
        "review_focus": ["gold_sentence_ids", "rag_top5"],
        "ai_confidence": "medium"
    }
)


def main():
    claim_ids = [f'C{i:02d}' for i in range(11, 21)]
    claims = load_claims(INPUT_FILE, claim_ids)

    samples = []
    for cid in claim_ids:
        if cid not in claims:
            print(f"WARNING: {cid} not found in input")
            continue
        claim = claims[cid]
        sr = build_system_retrieval(claim)
        gold_ret, gold_cls, analysis = ANALYSES[cid]

        sample_id = f"{PAPER_ID}-{ARTICLE_ID}-{cid}"
        samples.append({
            "sample_id": sample_id,
            "paper_id": PAPER_ID,
            "article_id": ARTICLE_ID,
            "article_source_type": SOURCE_TYPE,
            "claim_zh": claim["claim_zh"],
            "system_retrieval": sr,
            "gold_retrieval": gold_ret,
            "gold_classification": gold_cls,
            "analysis": analysis,
            "human_verified": False
        })

    output = {
        "schema_version": "1.1",
        "status": "draft",
        "paper_id": PAPER_ID,
        "article_id": ARTICLE_ID,
        "article_source_type": SOURCE_TYPE,
        "generated_date": str(date.today()),
        "generation_mode": "smoke",
        "limit": "C10:C20",
        "sample_count": len(samples),
        "_description": (
            "标注草稿：C11-C20 观点句（续C01-C10之后的第二批10条）。"
            "评测字段(gold_retrieval/gold_classification) + system_retrieval 对照 + analysis；"
            "人工审核后导出终稿。\n"
            "人工审核顺序：1.读claim_zh -> 2.看classify(top-5) -> 3.扫review第6-10条 -> "
            "4.改gold_retrieval -> 5.改gold_classification -> 6.改analysis -> 7.human_verified=true"
        ),
        "samples": samples,
        "review_queue": {
            "must_review_sample_ids": [
                s["sample_id"] for s in samples if s["analysis"]["needs_manual_review"]
            ],
            "notes": "优先审这些；见各条 analysis.manual_check_hints"
        }
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Written {len(samples)} samples to {OUTPUT_FILE}")
    must_review = [s["sample_id"] for s in samples if s["analysis"]["needs_manual_review"]]
    print(f"Must review: {must_review}")

if __name__ == '__main__':
    main()
