# 标注草稿生成提示词

把下面「提示词」整段发给 AI，开头的参数自己填。

草稿 = benchmark 的全部字段 + `analysis` + `human_verified`。
人工审核后去掉 `analysis`、只留 `human_verified=true` 的样本，就是 benchmark。

---

## 提示词

```
## 参数（请先填写）

- 输入文件（claim ↔ 证据对照 JSONL）：/outputs/P001/A001/claim_evidence_pairs.jsonl
- 输出文件（标注草稿 JSON）：C:\Users\18734\Desktop\PlantSci_Hallu\data\annotations\P001\P001_A001_annotation_draft.json
- paper_id：P001
- article_id：A001
- article_source_type：high_quality

---

## 任务

读取输入文件的每一行（一条中文观点句 + 若干条英文论文证据句），逐条判断该观点句
相对论文是否存在信息失真，并把结果写入输出文件。

产出的是**供人工审核的草稿**：每条都要给出评估结论，同时在 analysis 里把
「为什么这么判」和「人工该重点核对什么」讲清楚。`human_verified` 一律为 false。

---

## 输入格式

每行 JSONL：

{
  "claim_id": "C01",
  "claim_zh": "中文观点句",
  "evidences": [
    {"sentence_id": 3, "text": "英文论文句..."}
  ]
}

- sentence_id 是论文句级索引里的整数编号
- evidences 是 RAG 检索到的候选，**可能有遗漏，也可能含噪声**（图注残片、标题作者粘连等）
- 金标句要从中精选，不是照单全收

---

## 输出文件结构

{
  "schema_version": "1.0",
  "status": "draft",
  "paper_id": "{参数}",
  "article_id": "{参数}",
  "article_source_type": "{参数}",
  "generated_date": "{当天日期}",
  "_description": "标注草稿：字段与 benchmark 一致，额外含 analysis；人工审核后导出终稿。",
  "samples": [ /* 见下 */ ],
  "review_queue": {
    "must_review_sample_ids": [],
    "notes": "优先审这些；见各条 analysis.manual_check_hints"
  }
}

---

## 每条 sample

{
  "sample_id": "{paper_id}-{article_id}-{claim_id}",
  "paper_id": "P001",
  "article_id": "A001",
  "article_source_type": "high_quality",
  "claim_zh": "{观点句原文}",

  "gold_retrieval": {
    "sentence_ids": [3, 4],
    "is_answerable": true
  },

  "gold_classification": {
    "evidence_level": "With_Evidence",
    "primary_type": "fact_addition",
    "secondary_types": ["certainty_amplification"],
    "is_accurate": false,
    "severity": "moderate"
  },

  "analysis": {
    "evidence_judgement": "逐句说明：sentence_id=3 支持了 claim 的哪个分句，sentence_id=4 支持了哪部分；哪些分句没有证据覆盖。",
    "classification_reason": "为什么给这个 primary/secondary。要引用论文原措辞与公众号措辞逐一对照。",
    "key_differences": [
      {
        "type": "fact_addition",
        "paper_expression": "provide developmental and mechanistic insights into",
        "article_expression": "首次揭示了…阐明了核心作用",
        "description": "论文用审慎措辞，公众号加了『首次』并强化为定论"
      }
    ],
    "unsupported_diagnosis": {
      "verdict": "likely_retrieval_miss | likely_claim_error | uncertain | not_applicable",
      "reasoning": "为什么这么判",
      "suggested_keywords": ["KNAT2-like1", "receptacle"],
      "suggested_sentence_ranges": "如 200–260 附近"
    },
    "manual_check_hints": "给人的具体动作建议",
    "needs_manual_review": true,
    "review_focus": ["gold_sentence_ids", "primary_type"],
    "ai_confidence": "medium"
  },

  "human_verified": false
}

字段要求：
- gold_retrieval.sentence_ids：精选 0–5 个真正能支撑或反驳该 claim 的整数编号，脏句不要放进来
- is_answerable：论文里确实有可核查依据就 true；判定 No_Evidence 时 false
- gold_classification 各字段必须与 analysis 的结论一致
- human_verified 恒为 false

---

## 判定规范

### 证据级别

- With_Evidence：论文有明确对应，可做细粒度失真判断
- Weak_Evidence：主题相关但核心断言无法核实
- No_Evidence：找不到任何对应 → sentence_ids 为空，is_answerable=false

### 失真检查（With_Evidence 时逐项过）

1. 确定性：may / suggest / provide insights → 揭示 / 阐明 / 证实？
2. 程度：partially / a key role → 核心 / 决定性？
3. 范围：物种、组织、发育阶段等限定被省略或推广？
4. 因果：相关性说成因果？方向颠倒？
5. 数值：改动、模糊化、选择性引用？
6. 语境：实验条件、方法局限被剥离？
7. 机制：多层调控被压成单一因子？
8. 事实添加：论文没有的断言（首次、突破、唯一）？
9. 语义相反？

### 标签

| primary_type | 中文 |
|---|---|
| accurate | 准确传达 |
| certainty_amplification | 确定性放大 |
| mechanism_simplification | 机制简化 |
| scope_generalization | 范围泛化 |
| numerical_distortion | 数值失真 |
| causality_distortion | 因果扭曲 |
| context_stripping | 语境剥离 |
| fact_addition | 事实添加 |
| semantic_contradiction | 反义矛盾 |

- 无失真：primary_type=accurate，secondary_types=[]，is_accurate=true，severity=none
- No_Evidence：primary_type=""，secondary_types=[]
- 现有标签覆盖不了：primary_type 填 candidate_英文名，并设 needs_manual_review=true
- severity：none / mild / moderate / severe

---

## analysis 写作要求（重点）

这是给人看的部分，不要写空话。

**evidence_judgement**
把 claim 拆成几个可核查的分句，逐个说明由哪个 sentence_id 支撑、支撑到什么程度、
哪些分句完全没有证据。

**classification_reason**
说明为什么是这个 primary_type，以及为什么不是另一个相近的类型。
必须落到具体措辞对比上。

**unsupported_diagnosis**（证据不足或只能部分支撑时必填，其余填 not_applicable）
这是人工核验最需要的判断——区分「检索没捞到」还是「公众号说错了」：

- `likely_retrieval_miss`：论文很可能有相关内容但没被检索到。
  典型信号：claim 里的关键实体（基因名、物种、方法）在候选证据中完全没出现；
  候选里全是图注残片或元信息；检索到的句子都在同一段落，说明覆盖面窄。
  这时要在 suggested_keywords 给出该去句表搜索的英文关键词，
  在 suggested_sentence_ranges 给出建议翻看的编号范围。

- `likely_claim_error`：论文确实没有这个说法，是公众号添加或改写的。
  典型信号：相关主题的句子已被检索到且表述明确，但与 claim 的断言方向或范围不符。

- `uncertain`：两种都有可能，说明分别的可能性和判别方法。

**manual_check_hints**
写成可执行的动作，例如：
「到句表里搜 receptacle-specific / KNAT2-like1，确认 200–260 附近是否有更贴切的句子；
若确实没有，则本条应从 With_Evidence 降为 Weak_Evidence」。

**needs_manual_review / review_focus / ai_confidence**
下列情况必须 needs_manual_review=true：
证据级别摇摆、金标句难取舍、标签在两类之间、检索噪声大、复合观点句、出现新类型、置信度不高。

review_focus 从这些里选 1–3 个：
evidence_level / gold_sentence_ids / primary_type / secondary_types /
noisy_retrieval / composite_claim / new_type / none

ai_confidence：high / medium / low（medium 与 low 通常要 needs_manual_review=true）

---

## 执行方式

逐条处理：读一行 → 分析 → 写入 samples → 下一行。
中断后可从已写入的最后一条 claim_id 之后继续。
全部处理完后填 review_queue.must_review_sample_ids
（所有 needs_manual_review=true 的 sample_id），并确保输出是合法完整 JSON。

---

## 开始

确认参数已填写，从输入文件第 1 行开始。
```
