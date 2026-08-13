# 标注草稿生成提示词

**生产出草稿请用脚本**（不要把本文件 + 整份 JSONL 贴进聊天框）：

```bash
python scripts/generate_draft_from_pairs.py --paper P001 --article A001 --source-type high_quality --limit 10 --batch-size 3
```

API 专用系统提示词：`draft_from_pairs_api.md`。本文件给人类改规则；聊天仅用于改提示词、修 `review_queue` 难句（每次只贴 1 条 pair）。

把下面「提示词」整段发给 AI，只改开头参数。

产出 = 供人审的草稿：`system_retrieval`（原样对照）+ `gold_retrieval` / `gold_classification`（评测）+ `analysis`。  
`human_verified` 一律 false。审核后 `export_benchmark.py` 只导出已确认样本。

同一次检索两套用途：`classify_evidences` = 分类 top-5；`review_evidences` = 审核池 10 条（含 top-5）。  
先 `limit=10` 冒烟，格式满意后再 `all` 或 `after:C10`。

标签权威（生成时以本提示词为准，不必另附全文）：`README_1.md` v3.0、仓库根目录两份失真规范 md。  
taxonomy：`distortion-v0.1`。禁止旧 9 类（accurate / fact_addition / certainty_amplification 等）。

---

## 提示词

```
## 参数（先填）

- 输入 JSONL：outputs/P001/A001/claim_evidence_pairs.jsonl
- 输出 JSON：data/annotations/P001/P001_A001_annotation_draft_smoke10.json
- paper_id：P001
- article_id：A001
- article_source_type：high_quality
- limit：10
  （正整数 N=只处理前 N 行；all=全文；after:C10=从该 id 之后续跑，追加不覆盖已有 samples）

---

## 任务

按 limit 读 JSONL：每行 = 一条中文观点句 + 系统检索。判断该句相对**本篇论文**的转述是否失真，写出完整合法 JSON。

达到 limit 立即停止。human_verified 全为 false。

---

## 输入（每行）

{
  "claim_id": "C01",
  "claim_zh": "...",
  "classify_evidences": [{"rank": 1, "sentence_id": 3, "text": "..."}],
  "review_evidences": [{"rank": 1, "sentence_id": 3, "text": "..."}]
}

若只有 evidences：前 5=classify，前 10=review。
sentence_id 为论文句编号。金标句优先从 review 池选；池外更优须在 rag_review 注明，且 id 能在句表核对。

---

## 输出

顶层：

{
  "schema_version": "1.2",
  "taxonomy_version": "distortion-v0.1",
  "status": "draft",
  "paper_id": "{参数}",
  "article_id": "{参数}",
  "article_source_type": "{参数}",
  "generated_date": "{当天}",
  "generation_mode": "smoke|full|resume",
  "limit": "{参数原样}",
  "sample_count": 0,
  "_description": "标注草稿。审核顺序：claim → classify top-5 → review 6-10 → gold_retrieval → gold_classification → analysis → human_verified=true",
  "samples": [],
  "review_queue": {"must_review_sample_ids": [], "notes": ""}
}

generation_mode：N→smoke；all→full；after:Cxx→resume。

### sample（字段必须齐全）

{
  "sample_id": "{paper_id}-{article_id}-{claim_id}",
  "paper_id": "P001",
  "article_id": "A001",
  "article_source_type": "high_quality",
  "claim_zh": "原文，可按约80字换行",
  "system_retrieval": {
    "classify_evidences": [{"rank": 1, "sentence_id": 3, "text": "..."}],
    "review_evidences": [{"rank": 1, "sentence_id": 3, "text": "..."}]
  },
  "gold_retrieval": {
    "evidences": [{"sentence_id": 3, "text": "论文原句"}],
    "sentence_ids": [3],
    "is_answerable": true
  },
  "gold_classification": {
    "evidence_level": "With_Evidence",
    "has_distortion": true,
    "primary_label": {"level1": "addition", "level2": "significance_addition"},
    "secondary_label": {"level1": "omission", "level2": "evidence_uncertainty_omission"},
    "severity": "moderate",
    "needs_manual_review": false,
    "uncovered_phenomenon": "",
    "reason": "论文未写 first；公众号加了「首次」"
  },
  "analysis": {
    "evidence_judgement": "拆分句；各分句由哪个 sentence_id 支撑到什么程度；top-5 是否覆盖。",
    "classification_reason": "为何该 level2、为何不是相邻类；必须有中英措辞对照。",
    "key_differences": [
      {
        "type": "significance_addition",
        "paper_expression": "...",
        "article_expression": "...",
        "description": "..."
      }
    ],
    "rag_review": {
      "top5_is_best": false,
      "better_in_review_pool": [4, 115],
      "notes": "rank2/3 噪声；池内 4 更贴但未进 top-5"
    },
    "unsupported_diagnosis": {
      "verdict": "not_applicable",
      "reasoning": "",
      "suggested_keywords": [],
      "suggested_sentence_ranges": ""
    },
    "manual_check_hints": "可执行动作：先核对 id=3/4；再看 review 第7条 id=115。",
    "needs_manual_review": true,
    "review_focus": ["gold_sentence_ids", "rag_top5"],
    "ai_confidence": "medium"
  },
  "human_verified": false
}

---

## 硬性规则

### 复制检索
- system_retrieval 从输入原样复制 classify（≤5）与 review（≤10）；含 rank、sentence_id、text；不改写、不重排。

### 三轨分开填（禁止互相迁就）
1. gold_retrieval：人工对照需要哪些论文句？（不是分类标签的附件）
2. evidence_level：以本篇论文为唯一依据，能否充分比对？
3. 失真细类：仅 With_Evidence 时，转述如何改变科学含义？

检索没找着 ≠ Weak。论文里很可能有、只是池里没有 → evidence_level 仍按「论文能否核」来判：能核则 With，金标句可标池外 id，unsupported_diagnosis=likely_retrieval_miss。Weak 只表示论文本身既不能证真也不能证伪。

不要因为细类是 omission 就删掉检索金标句。

gold 按**断言覆盖**选核心句，不是检索句全集：同一断言的近义句只留 1 条；复合句每个独立断言各 1 条；目标 1–3、最多 5。方法近义、图注、作者行不得进 gold。分析里用来打标签的对照句必须出现在 `gold_sentence_ids`。API 出数不要在长字段里插排版换行（`print_readable_json.py` 再折）。

### 三种合法形态（必须落在其一）

A. 可核无失真：ids 非空，is_answerable=true，With_Evidence，has_distortion=false，primary_label.level2=no_distortion，secondary_label=null，severity=none

B. 可核有失真：ids 非空，is_answerable=true，With_Evidence，has_distortion=true，level2 为下表 8 类之一，severity=mild|moderate|severe

C. 不可充分核实：is_answerable=false，has_distortion=null，primary_label=null，secondary_label=null
   - Weak_Evidence：可保留主题相关句（ids 可非空）
   - No_Evidence：ids 与 evidences 必须空

复合句：evidence_judgement 里拆分句；整句 evidence_level 取最弱可核档（任一核心断言无法核实 → 至少 Weak）。仅整句 With 才标细类。

### 失真判定（仅 With；按序，Primary=含义变化最大者）
1. 完全支持（含合理压缩/同义/术语通俗化/非关键细节/程度弱化/一般背景）→ no_distortion
2. 改变已有科学关系（相关→因果、间接→直接、机制换成另一种）→ substitution
3. 增加论文没有的功能/应用或「首次/突破」→ addition
4. 删除重要限定（物种/条件/不确定性/关键机制）→ omission
5. 第二个独立错误最多一条 secondary；同一变化禁止双标
6. 冲突：substitution > addition > omission

slug（level1 小写；禁止自造）：

| level1 | level2 | 中文 |
|---|---|---|
| omission | context_omission | 背景限定删减 |
| omission | evidence_uncertainty_omission | 证据与不确定性删减 |
| omission | mechanism_omission | 机制删减 |
| addition | function_application_addition | 功能/应用添加 |
| addition | significance_addition | 意义/重要性添加 |
| substitution | relation_substitution | 关系替换 |
| substitution | magnitude_substitution | 作用程度替换 |
| substitution | mechanism_substitution | 机制替换 |
| — | no_distortion | 无失真 |

8 类盖不住（数值方向性改动、纯正负反义）：不要硬塞。uncovered_phenomenon=numerical_change|semantic_contradiction|other，gold_classification.needs_manual_review=true，analysis.needs_manual_review=true。
禁止输出 is_hallucination。不要用旧标签名。

脏句（图注残片、标题作者粘连）不得进 gold。

### 可读性
pretty-print，缩进 2 空格。论文句必须 {sentence_id, text}。长字段约 70–90 字换行（英文按空格、中文按标点，不拆单词）。

---

## analysis（质量关键，勿空话）

- evidence_judgement：分句 × sentence_id × 支撑程度；top-5 是否覆盖关键断言。
- classification_reason：level2 的措辞对照；相邻类为何落败。
- key_differences：有失真时必须覆盖 primary（及 secondary）的每一处措辞差。
- rag_review：top5_is_best；better_in_review_pool=更优 id 列表；notes 一句。
- unsupported_diagnosis：仅证据不足/部分支撑时填 likely_retrieval_miss | likely_claim_error | uncertain，并给 keywords/句号范围；否则 verdict=not_applicable。
- manual_check_hints：可执行动作（核对哪些 id）。
- needs_manual_review：级别摇摆、金标难选、top-5 差、两类之间、复合句、uncovered、confidence≠high → true。
- review_focus 选 1–3：evidence_level / gold_sentence_ids / rag_top5 / primary_label / secondary_label / noisy_retrieval / composite_claim / uncovered_phenomenon / none
- ai_confidence：high|medium|low。review_queue.must_review_sample_ids = 本次 needs_manual_review=true 的 sample_id。

---

## 执行

1. 读 limit，确定行范围。
2. 逐行：复制 system_retrieval → 按三轨+三种形态填 gold → 写 analysis。
3. 停；写 sample_count / generation_mode / limit / review_queue。
4. 输出必须是完整合法 JSON。
5. resume：只追加未生成的 claim，不覆盖已人工改过的条目。
```
