# 标注草稿生成提示词

把下面「提示词」整段发给 AI，开头的参数自己填。

草稿 = 评测字段（`gold_retrieval` / `gold_classification`）
+ 系统检索对照（`system_retrieval`）
+ `analysis` + `human_verified`。

人工审核后导出：去掉 `system_retrieval` / `analysis`，只留 `human_verified=true` 的样本，即为 benchmark。

流水线约定（同一次检索，两套用途）：
- 幻觉分类只用 top-5（`classify_evidences`）
- 人工审核池固定 10 条（`review_evidences`，且包含那 top-5）

---

## 提示词

```
## 参数（请先填写）

- 输入文件（claim ↔ 证据对照 JSONL）：【在此填写路径，如 outputs/P001/A001/claim_evidence_pairs.jsonl】
- 输出文件（标注草稿 JSON）：【在此填写路径，如 data/annotations/P001/P001_A001_annotation_draft.json】
- paper_id：【如 P001】
- article_id：【如 A001】
- article_source_type：【如 high_quality】

---

## 任务

读取输入文件的每一行（一条中文观点句 + 系统检索结果），逐条判断该观点句
相对论文是否存在信息失真，并把结果写入输出文件。

产出的是**供人工审核的草稿**：
1. 原样保留系统检索对照（分类用 top-5 + 审核池 10 条）；
2. 给出金标检索与金标分类（评测字段）；
3. 在 analysis 里写清「为什么这么判」和「人工该重点核对什么」。

`human_verified` 一律为 false。

---

## 输入格式

每行 JSONL（新格式）：

{
  "claim_id": "C01",
  "claim_zh": "中文观点句",
  "classify_evidences": [
    {"rank": 1, "sentence_id": 3, "text": "英文论文句..."}
  ],
  "review_evidences": [
    {"rank": 1, "sentence_id": 3, "text": "英文论文句..."},
    {"rank": 10, "sentence_id": 337, "text": "..."}
  ]
}

兼容旧字段：若只有 `evidences`，则前 5 条视为 classify，前 10 条视为 review。

说明：
- sentence_id 是论文句级索引里的整数编号
- classify_evidences：实际送进幻觉分类的 top-5
- review_evidences：固定 10 条审核池（包含 top-5），可能有噪声
- 金标句要从审核池中精选，也可标注「池外更优句」（见 analysis.rag_review），但 gold 编号须能在句表中核对

---

## 输出文件结构

{
  "schema_version": "1.1",
  "status": "draft",
  "paper_id": "{参数}",
  "article_id": "{参数}",
  "article_source_type": "{参数}",
  "generated_date": "{当天日期}",
  "_description": "标注草稿：评测字段 + system_retrieval 对照 + analysis；人工审核后导出终稿。",
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
  "claim_zh": "观点句原文（可按约80字符换行）",

  "system_retrieval": {
    "classify_evidences": [
      {
        "rank": 1,
        "sentence_id": 3,
        "text": "Comparative spatial transcriptome mapping and cell lineage\nreconstructions in developing floral buds of cucumber and tomato..."
      }
    ],
    "review_evidences": [
      {
        "rank": 1,
        "sentence_id": 3,
        "text": "..."
      }
    ]
  },

  "gold_retrieval": {
    "evidences": [
      {
        "sentence_id": 3,
        "text": "金标论文原句（必须带编号+原文）..."
      }
    ],
    "sentence_ids": [3],
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
    "evidence_judgement": "对照 review_evidences：哪些分句被哪条 sentence_id 支撑；top-5 是否够用。",
    "classification_reason": "为什么给这个 primary/secondary；引用论文原措辞与公众号措辞对照。",
    "key_differences": [
      {
        "type": "fact_addition",
        "paper_expression": "provide developmental and mechanistic insights into",
        "article_expression": "首次揭示了…阐明了核心作用",
        "description": "论文用审慎措辞，公众号加了『首次』并强化为定论"
      }
    ],
    "rag_review": {
      "top5_is_best": false,
      "better_in_review_pool": [4, 115],
      "notes": "rank2/3 偏噪声；审核池里的 4 更贴切但未进 top-5"
    },
    "unsupported_diagnosis": {
      "verdict": "likely_retrieval_miss | likely_claim_error | uncertain | not_applicable",
      "reasoning": "为什么这么判",
      "suggested_keywords": ["KNAT2-like1", "receptacle"],
      "suggested_sentence_ranges": "如 200–260 附近"
    },
    "manual_check_hints": "先看 classify_evidences 是否够用；不够再扫 review_evidences 第6–10条。",
    "needs_manual_review": true,
    "review_focus": ["gold_sentence_ids", "rag_top5"],
    "ai_confidence": "medium"
  },

  "human_verified": false
}

---

## 字段要求（硬性）

### system_retrieval（原样带入，供人工对照）
- 从输入复制 classify_evidences（≤5）与 review_evidences（≤10）
- 每条必须含 rank、sentence_id、text
- 不要改写原文，不要重排序

### gold_retrieval（评测金标，人工主要改这里）
- evidences：精选 0–5 个真正能支撑或反驳该 claim 的对象，每条必须同时有 sentence_id 与 text
- sentence_ids：与 evidences[].sentence_id 完全一致的整数列表
- is_answerable：有可核查依据为 true；No_Evidence 时 false，且 evidences / sentence_ids 为空
- 脏句（图注残片、标题作者粘连）不要放进金标

### gold_classification
- 必须与 analysis 结论一致
- With_Evidence 时才做细粒度失真标签

### 人工可读性
1. 输出必须是 pretty-printed JSON（缩进 2 空格），禁止整文件压成单行
2. 凡论文原句一律 {"sentence_id": int, "text": "..."}，禁止只给编号不给原文
3. 长文本换行：claim_zh、evidences[].text、analysis 长字段中，
   英文按空格、中文按标点，大约每 70–90 个字符插入 \n，
   不要在单词中间断开，保证默认编辑器宽度下无需横向滚动即可读完

### human_verified
- 恒为 false（留给人工改）

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

## analysis 写作要求

**evidence_judgement**
把 claim 拆成可核查分句，说明由哪个 sentence_id 支撑、支撑到什么程度、
哪些分句完全没有证据；同时说明 top-5 是否覆盖关键断言。

**classification_reason**
说明为什么是这个 primary_type，以及为什么不是相近的另一类；必须落到措辞对比。

**rag_review**（重点：服务后期 RAG 对比）
- top5_is_best：classify 的 top-5 是否已包含最相关句
- better_in_review_pool：审核池第 6–10 条（或池内未进 top-5）里更优的 sentence_id 列表
- notes：一句话说明 top-5 的问题（噪声 / 漏检 / 尚可）

**unsupported_diagnosis**（证据不足或只能部分支撑时必填，其余 not_applicable）
- likely_retrieval_miss：论文很可能有相关内容但没被检索到；填 suggested_keywords / suggested_sentence_ranges
- likely_claim_error：论文确实没有这个说法
- uncertain：两种都有可能

**manual_check_hints**
写成可执行动作，例如：
「先核对 classify_evidences 的 id=3/4；再看 review 第 7 条 id=115 是否更贴切；
若 115 更优，把 gold 改为 [3,115]，并在 rag_review 标明 top5_is_best=false」。

**needs_manual_review / review_focus / ai_confidence**
下列情况必须 needs_manual_review=true：
证据级别摇摆、金标句难取舍、top-5 质量可疑、标签在两类之间、检索噪声大、
复合观点句、出现新类型、置信度不高。

review_focus 从这些里选 1–3 个：
evidence_level / gold_sentence_ids / rag_top5 / primary_type / secondary_types /
noisy_retrieval / composite_claim / new_type / none

ai_confidence：high / medium / low（medium 与 low 通常要 needs_manual_review=true）

---

## 人工审核顺序（写进 _description 即可，AI 无需执行）

1. 读 claim_zh
2. 看 system_retrieval.classify_evidences（分类用的 5 句）
3. 扫 review_evidences 第 6–10 条，有没有更相关的
4. 改 gold_retrieval.evidences / sentence_ids
5. 改 gold_classification
6. 必要时改 analysis.rag_review / manual_check_hints
7. human_verified = true

---

## 执行方式

逐条处理：读一行 → 复制 system_retrieval → 判定金标与分类 → 写 analysis → 写入 samples。
中断后可从已写入的最后一条 claim_id 之后继续。
全部处理完后填 review_queue.must_review_sample_ids
（所有 needs_manual_review=true 的 sample_id），并确保输出是合法完整 JSON。

---

## 开始

确认参数已填写，从输入文件第 1 行开始。
```
