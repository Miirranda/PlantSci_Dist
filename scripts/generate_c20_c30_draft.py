"""
Generate P001_A001_annotation_draft_smoke10.json for claims C20-C30.
Reads input from JSONL lines 20-30, processes, writes output.
"""
import json
import re

INPUT_PATH = "outputs/P001/A001/claim_evidence_pairs.jsonl"
OUTPUT_PATH = "data/annotations/P001/P001_A001_annotation_draft_smoke10.json"

# Read input JSONL
with open(INPUT_PATH, "r", encoding="utf-8") as f:
    lines = f.readlines()

raw_claims = []
for i in range(19, min(30, len(lines))):
    raw_claims.append(json.loads(lines[i]))

print(f"Loaded {len(raw_claims)} claims: {[c['claim_id'] for c in raw_claims]}")

# ============================================================
# Per-claim analysis definitions
# ============================================================

CLAIM_ANALYSES = {
    "C20": {
        "gold_retrieval": {
            "evidences": [
                {"sentence_id": 125, "text": "3a). We determined that after S7, the FIM and receptacle primordia generate cells that are actively undergoing the cell cycle, while G1/S phase cells transition to receptacle cells, suggesting a mechanism to maintain cell proliferation and support rapid receptacle growth (Fig."}
            ],
            "sentence_ids": [125],
            "is_answerable": True
        },
        "gold_classification": {
            "evidence_level": "With_Evidence",
            "primary_type": "accurate",
            "secondary_types": [],
            "is_accurate": True,
            "severity": "none"
        },
        "analysis": {
            "evidence_judgement": (
                "【可核查分句1】「S7后FIM与花托原基活跃进行细胞分裂」— "
                "由 sentence_id=125 强支撑：「after S7, the FIM and receptacle primordia "
                "generate cells that are actively undergoing the cell cycle」。\n"
                "【可核查分句2】「可能是花托快速生长从而导致与心皮融合的原因」— "
                "sentence_id=125 支撑前半「suggesting a mechanism to maintain cell proliferation "
                "and support rapid receptacle growth」；「与心皮融合」在125中未直接出现，"
                "但论文其他部分（如sent_id=265）提及 receptacle 包裹心皮形成下位子房，"
                "属于合理延伸。\n"
                "Classify top-5 质量：rank1(164)为图注噪声夹杂，rank2(125)命中关键句，"
                "rank3(37)、rank4(165)、rank5(126)均为图注碎片或方法句，噪声大。"
            ),
            "classification_reason": (
                "观点句使用「可能是」与论文「suggesting」语气匹配，核心发现（S7后FIM/花托原基活跃分裂"
                "支撑花托快速生长）准确传达。心皮融合部分虽非 sent 125 直接覆盖，但属论文后续论述"
                "的合理概括，且观点句以「可能」限定，不算事实添加。判定为 accurate。"
            ),
            "key_differences": [],
            "rag_review": {
                "top5_is_best": False,
                "better_in_review_pool": [],
                "notes": "top5 仅 rank2(sent_id=125) 命中；rank1/3/4/5 均为图注碎片或噪声，top-5 质量差。"
            },
            "unsupported_diagnosis": {
                "verdict": "not_applicable",
                "reasoning": "核心断言已被 sent_id=125 充分支撑。",
                "suggested_keywords": [],
                "suggested_sentence_ranges": ""
            },
            "manual_check_hints": (
                "1. 核对 gold sentence_id=125 是否确实覆盖 claim 全部断言；\n"
                "2. 确认「与心皮融合」是否需要补充 sent_id=265 等其他句；\n"
                "3. 留意 top-5 噪声极大，classify 若只用 top-5 可能误判。"
            ),
            "needs_manual_review": True,
            "review_focus": ["gold_sentence_ids", "rag_top5"],
            "ai_confidence": "medium"
        }
    },
    "C21": {
        "gold_retrieval": {
            "evidences": [
                {"sentence_id": 127, "text": "along with the pseudotime curve, demonstrated the cell lineage from FIM to receptacle and then to conjunctive tissue (Fig."},
                {"sentence_id": 128, "text": "3c,d). These insights and the organization information of the cell clusters, as shown in the schematic diagrams (Extended Data Fig. 4b,c), highlight the spatiotemporal structure of these functional hubs in the transect section (bottom) and longitudinal section (top) at various developmental stages of cucumber floral buds (Fig."}
            ],
            "sentence_ids": [127, 128],
            "is_answerable": True
        },
        "gold_classification": {
            "evidence_level": "With_Evidence",
            "primary_type": "accurate",
            "secondary_types": [],
            "is_accurate": True,
            "severity": "none"
        },
        "analysis": {
            "evidence_judgement": (
                "【可核查分句】「发育顺序为 FIM→花托→连接组织」— "
                "由 sentence_id=127 精准支撑：「demonstrated the cell lineage from FIM "
                "to receptacle and then to conjunctive tissue」。\n"
                "【图注】「图3c-d」由 sentence_id=128 确认。\n"
                "观点句为论文原文的忠实翻译，无额外渲染或推断。\n"
                "Classify top-5：rank1(127)直接命中，rank2(128)补充图号。"
            ),
            "classification_reason": (
                "观点句逐字对应论文句子「from FIM to receptacle and then to conjunctive tissue」，"
                "未添加任何论文未有的断言。判定 accurate。"
            ),
            "key_differences": [],
            "rag_review": {
                "top5_is_best": True,
                "better_in_review_pool": [],
                "notes": "top-5 质量好：rank1(127)直接命中核心句，rank2(128)补充上下文。"
            },
            "unsupported_diagnosis": {
                "verdict": "not_applicable",
                "reasoning": "断言完全被支撑。",
                "suggested_keywords": [],
                "suggested_sentence_ranges": ""
            },
            "manual_check_hints": "快速确认 sent_id=127 措辞即可；此条置信度高，审核优先级低。",
            "needs_manual_review": False,
            "review_focus": ["none"],
            "ai_confidence": "high"
        }
    },
    "C22": {
        "gold_retrieval": {
            "evidences": [
                {"sentence_id": 130, "text": "We infer that in cucumber, the fruit flesh outside the carpel is mostly derived from the development of receptacle."},
                {"sentence_id": 131, "text": "Accordingly, the receptacle-derived tissue is important for the genetic improvement."}
            ],
            "sentence_ids": [130, 131],
            "is_answerable": True
        },
        "gold_classification": {
            "evidence_level": "With_Evidence",
            "primary_type": "accurate",
            "secondary_types": [],
            "is_accurate": True,
            "severity": "none"
        },
        "analysis": {
            "evidence_judgement": (
                "【可核查分句1】「黄瓜心皮外的果肉是由花托发育而来」— "
                "由 sentence_id=130 逐词对应支撑：「the fruit flesh outside the carpel "
                "is mostly derived from the development of receptacle」。\n"
                "【可核查分句2】「花托及其相关组织的研究对遗传改良意义重大」— "
                "由 sentence_id=131 逐词对应支撑：「the receptacle-derived tissue is "
                "important for the genetic improvement」。\n"
                "观点句以「推测」对应论文「We infer」，语气匹配精准。"
            ),
            "classification_reason": (
                "观点句为论文 sent 130+131 的中文翻译，连「推测」/「We infer」的审慎措辞"
                "都原样保留。无任何失真。判定 accurate。"
            ),
            "key_differences": [],
            "rag_review": {
                "top5_is_best": True,
                "better_in_review_pool": [],
                "notes": "top-5 质量优秀：rank1(130)和rank2(131)直接命中两个分句。"
            },
            "unsupported_diagnosis": {
                "verdict": "not_applicable",
                "reasoning": "全部断言被精准支撑。",
                "suggested_keywords": [],
                "suggested_sentence_ranges": ""
            },
            "manual_check_hints": "核对 sent_id=130 与 131 即可；置信度极高。",
            "needs_manual_review": False,
            "review_focus": ["none"],
            "ai_confidence": "high"
        }
    },
    "C23": {
        "gold_retrieval": {
            "evidences": [
                {"sentence_id": 132, "text": "To understand the gene expression dynamics and cytological basis of the EFM in inferior ovary, we selected sections of floral buds from S1 to S4 stages and conducted unsupervised clustering analysis with near single-cell resolution (average cell diameter from S1 to S4 of 12.96 μm and bin30 diameter of 15 μm) (Fig."},
                {"sentence_id": 134, "text": "This revealed seven clusters with distinct locations and expressed marker genes during early floral organ development (Fig."},
                {"sentence_id": 139, "text": "The distribution of cluster 3 (RP) and cluster 4 (EFM) indicates that their initiation and development determine the morphogenesis of the enlarged and concave floral meristem."}
            ],
            "sentence_ids": [132, 134, 139],
            "is_answerable": True
        },
        "gold_classification": {
            "evidence_level": "With_Evidence",
            "primary_type": "accurate",
            "secondary_types": [],
            "is_accurate": True,
            "severity": "none"
        },
        "analysis": {
            "evidence_judgement": (
                "【可核查分句1】「S1-S4近单细胞分辨率无监督聚类→7个cluster」— "
                "sentence_id=132 支撑方法：「unsupervised clustering analysis with near "
                "single-cell resolution」；sentence_id=134 支撑结果：「seven clusters」。\n"
                "【可核查分句2】「RP和EFM的起始和发育决定着凹形花分生组织的形态发生」— "
                "sentence_id=139 精准对应：「initiation and development determine the "
                "morphogenesis of the enlarged and concave floral meristem」。\n"
                "注意：观点句说「花托起始形成」而论文132句说「EFM in inferior ovary」，"
                "语义有细微偏移（花托 vs EFM），但整体准确。\n"
                "Classify top-5：rank1(139)命中分句2，rank2(160)不直接相关，"
                "rank3(132)命中分句1——top-5覆盖关键断言但分布分散。"
            ),
            "classification_reason": (
                "观点句准确转述论文实验设计（132+134）和核心发现（139）。"
                "「花托起始形成过程」与原文「EFM in inferior ovary」有轻微语境偏移，"
                "但核心信息不失真。判定 accurate。"
            ),
            "key_differences": [
                {
                    "type": "context_stripping",
                    "paper_expression": "gene expression dynamics and cytological basis of the EFM in inferior ovary",
                    "article_expression": "花托起始形成过程中的基因动态表达和细胞基础",
                    "description": "论文强调 EFM（扩展花分生组织）在下位子房中的角色，观点句转为「花托起始」，语境微调但不影响核心信息"
                }
            ],
            "rag_review": {
                "top5_is_best": True,
                "better_in_review_pool": [],
                "notes": "top-5 覆盖良好：rank1(139)命中核心，rank3(132)提供方法细节，rank4/5噪声。rank2(160)有部分相关但与关键断言不直接匹配。"
            },
            "unsupported_diagnosis": {
                "verdict": "not_applicable",
                "reasoning": "所有断言均有明确支撑。",
                "suggested_keywords": [],
                "suggested_sentence_ranges": ""
            },
            "manual_check_hints": (
                "1. 核对「7个cluster」→ sent_id=134 确认；\n"
                "2. 核对「RP/EFM决定凹形花分生组织形态」→ sent_id=139 确认；\n"
                "3. 确认是否需要补充 sent_id=135（cluster列表详情）。"
            ),
            "needs_manual_review": False,
            "review_focus": ["none"],
            "ai_confidence": "high"
        }
    },
    "C24": {
        "gold_retrieval": {
            "evidences": [
                {"sentence_id": 143, "text": "One involved the differentiation of the epidermis."},
                {"sentence_id": 144, "text": "The other two originated from the FIM and passed through the EFM, with one leading to the formation of the floral primordia, then generating floral whorls, while the other gave rise to the receptacle primordium from the EFM (Fig."},
                {"sentence_id": 145, "text": "4c). A pseudotime analysis using Monocle2 confirmed the resulting trajectories (Fig."}
            ],
            "sentence_ids": [143, 144, 145],
            "is_answerable": True
        },
        "gold_classification": {
            "evidence_level": "With_Evidence",
            "primary_type": "accurate",
            "secondary_types": [],
            "is_accurate": True,
            "severity": "none"
        },
        "analysis": {
            "evidence_judgement": (
                "【可核查分句1】「起源于FIM，分为3条发育路径」— "
                "sentence_id=143 支撑表皮路径（「One involved the differentiation of the epidermis」），"
                "sentence_id=144 支撑另两条经EFM到FP和RP的路径（「The other two originated from the FIM "
                "and passed through the EFM」）。三句合起来完整覆盖。\n"
                "【可核查分句2】「RNA速率+拟时序分析」— sentence_id=145确认Monocle2方法。\n"
                "⚠ 关键问题：sentence_id=143（表皮路径）在 review pool 中排 rank 6，"
                "不在 classify top-5 内！top-5（459/144/460/458/190）中仅有144命中核心路径，"
                "其余为方法细节或噪声。这意味着如果只送 top-5 给分类模型，"
                "可能因缺少「表皮」分句而无法完整验证该 claim。"
            ),
            "classification_reason": (
                "观点句准确概括了论文的发育轨迹发现。3条路径、FIM起源、经EFM到FP/RP的路线"
                "均与 sent 143+144 一致。判定 accurate，但 rag top-5 覆盖不完整。"
            ),
            "key_differences": [],
            "rag_review": {
                "top5_is_best": False,
                "better_in_review_pool": [143],
                "notes": "sent_id=143（表皮路径）仅在 review rank6，未进 top-5。classify top-5 缺失了三条路径之一的支撑句，可能影响分类准确性。"
            },
            "unsupported_diagnosis": {
                "verdict": "not_applicable",
                "reasoning": "全部断言有支撑，但 gold 需要 review pool 中的句。",
                "suggested_keywords": [],
                "suggested_sentence_ranges": ""
            },
            "manual_check_hints": (
                "⚠ 重点核对：sent_id=143（review rank6）是否确实是「一条分化为表皮」的支撑；\n"
                "若确认，gold 应包含 [143,144,145]；rag_review 标明 top5_is_best=false。\n"
                "另外检查 sent_id=144 是否包含完整的「两条经EFM到FP和RP」信息。"
            ),
            "needs_manual_review": True,
            "review_focus": ["gold_sentence_ids", "rag_top5"],
            "ai_confidence": "medium"
        }
    },
    "C25": {
        "gold_retrieval": {
            "evidences": [
                {"sentence_id": 158, "text": "Next, the expression patterns of selected key marker genes, such as STM, STM-like, KNAT2-like1, ETTIN, AP1 and LEAFY, were verified by RNA in situ hybridization (Fig."},
                {"sentence_id": 195, "text": "e, A heat map of the dynamic changes in expression levels of pseudotime-dependent genes."}
            ],
            "sentence_ids": [158, 195],
            "is_answerable": True
        },
        "gold_classification": {
            "evidence_level": "With_Evidence",
            "primary_type": "accurate",
            "secondary_types": [],
            "is_accurate": True,
            "severity": "none"
        },
        "analysis": {
            "evidence_judgement": (
                "【可核查分句1】「筛选出一些关键基因，表达热图及空间定位见图4e-f」— "
                "sentence_id=195 支撑热图（「A heat map of the dynamic changes in expression "
                "levels of pseudotime-dependent genes」）。\n"
                "【可核查分句2】「对其中一些基因进行了RNA原位杂交验证」— "
                "sentence_id=158 精准支撑：「expression patterns of selected key marker "
                "genes... were verified by RNA in situ hybridization」。\n"
                "观点句为纯描述性转述，无主观渲染。\n"
                "Classify top-5：rank1(158)命中分句2，rank2(195)命中分句1，覆盖良好。"
            ),
            "classification_reason": (
                "观点句忠实转述论文方法性内容：拟时序筛选基因→热图→原位杂交验证。"
                "所有断言均被论文直接支撑，无额外推断。判定 accurate。"
            ),
            "key_differences": [],
            "rag_review": {
                "top5_is_best": True,
                "better_in_review_pool": [],
                "notes": "top-5 覆盖良好：rank1(158)和rank2(195)分别支撑两个核心分句。"
            },
            "unsupported_diagnosis": {
                "verdict": "not_applicable",
                "reasoning": "描述性内容完全被支撑。",
                "suggested_keywords": [],
                "suggested_sentence_ranges": ""
            },
            "manual_check_hints": "快审：确认 sent_id=158 列出的基因是否与观点句意图一致。",
            "needs_manual_review": False,
            "review_focus": ["none"],
            "ai_confidence": "high"
        }
    },
    "C26": {
        "gold_retrieval": {
            "evidences": [
                {"sentence_id": 160, "text": "Based on the clusters, we classified the cells of floral buds into the inner part (cluster floral primordia) and peripheral part (clusters E, RP and EFM) and measured their cell area and cell numbers (Fig."}
            ],
            "sentence_ids": [160],
            "is_answerable": True
        },
        "gold_classification": {
            "evidence_level": "With_Evidence",
            "primary_type": "accurate",
            "secondary_types": [],
            "is_accurate": True,
            "severity": "none"
        },
        "analysis": {
            "evidence_judgement": (
                "【可核查分句】「分为内部区域（FP）和外部区域（E、RP和EFM）」— "
                "由 sentence_id=160 逐词对应：「classified the cells... into the inner part "
                "(cluster floral primordia) and peripheral part (clusters E, RP and EFM)」。\n"
                "观点句为论文原句的中文翻译，无任何改写或添加。"
            ),
            "classification_reason": (
                "观点句与 sent 160 一一对应，措辞精准（「内部区域」=「inner part」, "
                "「外部区域」=「peripheral part」）。判定 accurate。"
            ),
            "key_differences": [],
            "rag_review": {
                "top5_is_best": True,
                "better_in_review_pool": [],
                "notes": "top-5 rank1(160)直接命中核心句，质量好。"
            },
            "unsupported_diagnosis": {
                "verdict": "not_applicable",
                "reasoning": "断言完全被支撑。",
                "suggested_keywords": [],
                "suggested_sentence_ranges": ""
            },
            "manual_check_hints": "确认 sent_id=160 即可；置信度极高。",
            "needs_manual_review": False,
            "review_focus": ["none"],
            "ai_confidence": "high"
        }
    },
    "C27": {
        "gold_retrieval": {
            "evidences": [
                {"sentence_id": 161, "text": "4h). We determined that the initial morphological changes in the EFM reflect rapid cell proliferation in the peripheral region (Fig."},
                {"sentence_id": 162, "text": "4i) and that cell expansion in this region from S2 to S5 contributed to subsequent meristem enlargement (Fig."}
            ],
            "sentence_ids": [161, 162],
            "is_answerable": True
        },
        "gold_classification": {
            "evidence_level": "With_Evidence",
            "primary_type": "accurate",
            "secondary_types": [],
            "is_accurate": True,
            "severity": "none"
        },
        "analysis": {
            "evidence_judgement": (
                "【可核查分句1】「EFM初始形态变化是由于外部区域细胞快速增殖」— "
                "由 sentence_id=161 精准支撑：「initial morphological changes in the EFM "
                "reflect rapid cell proliferation in the peripheral region」。\n"
                "【可核查分句2】「从而促进外侧分生组织后续扩大」— "
                "由 sentence_id=162 精准支撑：「cell expansion in this region from S2 to S5 "
                "contributed to subsequent meristem enlargement」。\n"
                "黄金句161+162完整覆盖观点句的两个分句，匹配精准。"
            ),
            "classification_reason": (
                "观点句为论文 sent 161+162 的中文转述，因果链条（增殖→扩大）、"
                "部位描述（外部区域=peripheral region）、时序（初始=S2-S5→后续）"
                "全部准确对应。判定 accurate。"
            ),
            "key_differences": [],
            "rag_review": {
                "top5_is_best": True,
                "better_in_review_pool": [],
                "notes": "top-5 覆盖好：rank1(161)和rank3(162)分别命中两个核心分句，rank2(160)提供分类背景。"
            },
            "unsupported_diagnosis": {
                "verdict": "not_applicable",
                "reasoning": "全部断言精准支撑。",
                "suggested_keywords": [],
                "suggested_sentence_ranges": ""
            },
            "manual_check_hints": "确认 sent_id=161+162 即可；置信度极高。",
            "needs_manual_review": False,
            "review_focus": ["none"],
            "ai_confidence": "high"
        }
    },
    "C28": {
        "gold_retrieval": {
            "evidences": [
                {"sentence_id": 164, "text": "After S5, we observed the largest increase in the bin numbers of receptacle-related clusters (receptacle-early, cluster 9), indicating that 865 a b Basal sepal–abaxial Sepal epidermis Sepal mesophyll Sepal Unknown S1 Sepal mesophyll Unknown G0/G1 phase cells Epidermis Epidermis Trichome-base floral Trichome meristem Thicked epidermis Floral intercalary meristem FIM Floral intercalary meristem-2 Floral intercalary meristem-3 G2/M phase cells S1 G1/S phase cells Receptacle Receptacle primordia Receptacle-early Receptacle-late Provascular Protoxylem Conjunctive Conjunctive primordia Tissue Conjunctive tissue-early Conjunctive tissue-late Petal primordia Petal-early Petal S1 Petal-late Basal sepal-adaxial 1.00 Edge weight Floral primordia carpel 0.75 Placenta FP 0.50 Ovlue (ovary wall) 0.25 Stigma/transmitting tract S1 S2 S3 S4/S5 S6/S7 S8-1/2 S8-3/4 stage c Receptacle primordia Receptacle-early Receptacle-late Conjunctive primordia Conjunctive tissue-early UMAP2 UMAP2 Conjunctive tissue-late Floral intercalary meristem Floral intercalary meristem-2 Floral intercalary meristem-3 UMAP1 UMAP1 d 1.5 Bin50 density 1.0 0.5 0 0 2 4 6 Pesudotime a, A directed acyclic graph showing inferred relationships between cell states across cucumber female floral bud development."},
                {"sentence_id": 169, "text": "the rapid growth of the receptacle after this stage is driven by another phase of cell proliferation (Fig."}
            ],
            "sentence_ids": [164, 169],
            "is_answerable": True
        },
        "gold_classification": {
            "evidence_level": "With_Evidence",
            "primary_type": "accurate",
            "secondary_types": [],
            "is_accurate": True,
            "severity": "none"
        },
        "analysis": {
            "evidence_judgement": (
                "【可核查分句1】「S5后花托相关bin数量急剧上升」— "
                "由 sentence_id=164 支撑：「After S5, we observed the largest increase "
                "in the bin numbers of receptacle-related clusters」。"
                "「急剧上升」=「largest increase」，措辞匹配。\n"
                "【可核查分句2】「说明此阶段后花托快速生长」— "
                "由 sentence_id=169 支撑：「the rapid growth of the receptacle after this stage」。\n"
                "⚠ 注意：sent_id=164 文本末尾被大量图注标签污染，人工核对时请只看前半句。"
            ),
            "classification_reason": (
                "观点句准确转述论文定量发现（bin数上升）和定性结论（快速生长）。"
                "「急剧」对应「largest」无夸大。判定 accurate。"
            ),
            "key_differences": [],
            "rag_review": {
                "top5_is_best": False,
                "better_in_review_pool": [169],
                "notes": "top-5 rank1(164)命中分句1但被图注严重污染；rank5(244)补充信息但未进核心。sent_id=169(快速生长)仅在review rank8，建议 gold 中加入。"
            },
            "unsupported_diagnosis": {
                "verdict": "not_applicable",
                "reasoning": "两个分句均有支撑。",
                "suggested_keywords": [],
                "suggested_sentence_ranges": ""
            },
            "manual_check_hints": (
                "1. sent_id=164 图注污染严重，人工确认前半句可用；\n"
                "2. 确认是否需补充 sent_id=169 来支撑「快速生长」结论；\n"
                "3. sent_id=244（review rank5）也可作为补充证据。"
            ),
            "needs_manual_review": True,
            "review_focus": ["gold_sentence_ids", "noisy_retrieval"],
            "ai_confidence": "medium"
        }
    },
    "C29": {
        "gold_retrieval": {
            "evidences": [
                {"sentence_id": 173, "text": "A UMAP three-dimensional (3D) scatterplot revealed six reclusters, four of which followed a cyclic pattern (S phase, G2 phase, G0/G1 phase and M phase) according to the assigned cell-cycle marker genes20, and the remaining two were annotated as receptacle primordium and FIM (Fig."}
            ],
            "sentence_ids": [173],
            "is_answerable": True
        },
        "gold_classification": {
            "evidence_level": "With_Evidence",
            "primary_type": "accurate",
            "secondary_types": [],
            "is_accurate": True,
            "severity": "none"
        },
        "analysis": {
            "evidence_judgement": (
                "【可核查分句1】「3D UMAP散点图→6个cluster」— "
                "sentence_id=173：「A UMAP three-dimensional (3D) scatterplot revealed six reclusters」。\n"
                "【可核查分句2】「4个cluster与细胞周期相关(G0/G1, G2, S, M)」— "
                "sentence_id=173：「four of which followed a cyclic pattern (S phase, G2 phase, "
                "G0/G1 phase and M phase) according to the assigned cell-cycle marker genes」。\n"
                "【可核查分句3】「另外2个为RP和FIM」— "
                "sentence_id=173：「the remaining two were annotated as receptacle primordium and FIM」。\n"
                "观点句为论文 sent 173 的完整中文翻译，逐句对应，信息密度一致。"
            ),
            "classification_reason": (
                "观点句忠实翻译论文句子，无任何信息增删或扭曲。"
                "6 clusters、4 cell-cycle + 2 tissue 的数字和类别全部正确。判定 accurate。"
            ),
            "key_differences": [],
            "rag_review": {
                "top5_is_best": True,
                "better_in_review_pool": [],
                "notes": "top-5 rank1(173)精准覆盖全部断言，质量极好。"
            },
            "unsupported_diagnosis": {
                "verdict": "not_applicable",
                "reasoning": "全部断言被一条句子完整覆盖。",
                "suggested_keywords": [],
                "suggested_sentence_ranges": ""
            },
            "manual_check_hints": "确认 sent_id=173 即可；此条为论文原文直译，置信度极高。",
            "needs_manual_review": False,
            "review_focus": ["none"],
            "ai_confidence": "high"
        }
    },
    "C30": {
        "gold_retrieval": {
            "evidences": [
                {"sentence_id": 183, "text": "These findings support the notion that cell proliferation in the receptacle is regulated by the activation of meristematic activities in S phase cells."},
                {"sentence_id": 182, "text": "5d). The target of rapamycin (TOR) kinase gene, which promotes the activation of S phase genes and meristems42, was found to predominantly expressed in S phase cells (Extended Data Fig. 6b)."}
            ],
            "sentence_ids": [182, 183],
            "is_answerable": True
        },
        "gold_classification": {
            "evidence_level": "With_Evidence",
            "primary_type": "accurate",
            "secondary_types": [],
            "is_accurate": True,
            "severity": "none"
        },
        "analysis": {
            "evidence_judgement": (
                "【可核查分句】「花托细胞增殖主要由激活S期细胞活动调控」— "
                "由 sentence_id=183 支撑：「cell proliferation in the receptacle is "
                "regulated by the activation of meristematic activities in S phase cells」。\n"
                "sentence_id=182 补充了 TOR 激酶作为 S 期激活的上游机制。\n"
                "观点句省略了「meristematic」（分生组织）一词，简化为「S期细胞活动」，"
                "但核心因果方向（S期→花托增殖）保留准确。"
            ),
            "classification_reason": (
                "观点句将[activation of meristematic activities in S phase cells]"
                "简化为[激活S期细胞活动]，核心信息保留，提到[主要]对应论文"
                "[the notion that... is regulated by]的审慎表述。无实质性失真。判定 accurate。"
            ),
            "key_differences": [
                {
                    "type": "mechanism_simplification",
                    "paper_expression": "activation of meristematic activities in S phase cells",
                    "article_expression": "激活S期细胞活动",
                    "description": "观点句省略了「meristematic」（分生组织），但核心因果逻辑不变，属轻度简化"
                }
            ],
            "rag_review": {
                "top5_is_best": True,
                "better_in_review_pool": [],
                "notes": "top-5 rank1(183)精准命中核心句，rank3(182)补充TOR机制细节。"
            },
            "unsupported_diagnosis": {
                "verdict": "not_applicable",
                "reasoning": "核心断言已有直接支撑。",
                "suggested_keywords": [],
                "suggested_sentence_ranges": ""
            },
            "manual_check_hints": (
                "1. 核对 sent_id=183 是否充分支撑「主要由S期调控」；\n"
                "2. 「meristematic activities」省略是否影响准确性——人工判断。"
            ),
            "needs_manual_review": True,
            "review_focus": ["mechanism_simplification"],
            "ai_confidence": "medium"
        }
    }
}

# ============================================================
# Build output
# ============================================================

samples = []
for claim in raw_claims:
    cid = claim["claim_id"]
    czh = claim["claim_zh"]
    analysis_data = CLAIM_ANALYSES[cid]

    sample = {
        "sample_id": f"P001-A001-{cid}",
        "paper_id": "P001",
        "article_id": "A001",
        "article_source_type": "high_quality",
        "claim_zh": czh,
        "system_retrieval": {
            "classify_evidences": claim.get("classify_evidences", []),
            "review_evidences": claim.get("review_evidences", [])
        },
        "gold_retrieval": analysis_data["gold_retrieval"],
        "gold_classification": analysis_data["gold_classification"],
        "analysis": analysis_data["analysis"],
        "human_verified": False
    }
    samples.append(sample)

# Build review_queue
must_review = [s["sample_id"] for s in samples if s["analysis"]["needs_manual_review"]]

output = {
    "schema_version": "1.1",
    "status": "draft",
    "paper_id": "P001",
    "article_id": "A001",
    "article_source_type": "high_quality",
    "generated_date": "2026-07-31",
    "generation_mode": "smoke",
    "limit": "C20:C30",
    "sample_count": len(samples),
    "_description": (
        "标注草稿：C20-C30 观点句（共11条）。评测字段(gold_retrieval/gold_classification) "
        "+ system_retrieval 对照 + analysis；人工审核后导出终稿。\n"
        "本批次为续跑：C01-C19已在前批次处理。\n"
        "人工审核顺序：1.读claim_zh → 2.看classify(top-5) → 3.扫review第6-10条 "
        "→ 4.改gold_retrieval → 5.改gold_classification → 6.改analysis → 7.human_verified=true"
    ),
    "samples": samples,
    "review_queue": {
        "must_review_sample_ids": must_review,
        "notes": "优先审这些；见各条 analysis.manual_check_hints"
    }
}

# ============================================================
# Write with pretty-print & text wrapping for readability
# ============================================================

def wrap_text_in_json(obj, max_len=85):
    """Recursively wrap long text fields at ~max_len chars for readability."""
    if isinstance(obj, str):
        if len(obj) <= max_len:
            return obj
        # Wrap at word boundaries, but preserve \n
        paragraphs = obj.split('\n')
        wrapped_paras = []
        for para in paragraphs:
            if len(para) <= max_len:
                wrapped_paras.append(para)
            else:
                words = para.split(' ')
                lines = []
                current = ""
                for w in words:
                    test = current + (" " if current else "") + w
                    if len(test) <= max_len:
                        current = test
                    else:
                        if current:
                            lines.append(current)
                        # Handle very long single words (e.g., URLs): force break
                        if len(w) > max_len:
                            for k in range(0, len(w), max_len):
                                lines.append(w[k:k+max_len])
                            current = ""
                        else:
                            current = w
                if current:
                    lines.append(current)
                wrapped_paras.append('\n'.join(lines))
        return '\n'.join(wrapped_paras)
    elif isinstance(obj, dict):
        return {k: wrap_text_in_json(v, max_len) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [wrap_text_in_json(item, max_len) for item in obj]
    else:
        return obj

# Write output
json_str = json.dumps(output, ensure_ascii=False, indent=2)

with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    f.write(json_str)

print(f"Written {len(samples)} samples to {OUTPUT_PATH}")
print(f"Must review: {must_review}")
print(f"Filesize: {len(json_str)} chars")
