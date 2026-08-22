# arag-main 检索充分性闭环 + 人工检索提示 — 落地交接文档

**文档版本**：v0.1  
**适用仓库**：`PlantSci_Hallu`  
**目标读者**：后续接手实现 arag-main 检索改造、标注草稿生成、Benchmark 评测的开发者  
**最后更新**：2026-08-22  

---

## 1. 背景与目标

### 1.1 业务问题

Benchmark 构建中，`gold_retrieval` 召回不足，导致：

1. 人工难以从系统返回的 top-5 / top-10 中找到足够支撑句；
2. 下游 AI 标注在证据不足时误判「论文未提及」→ 错误打上失真标签；
3. 人工需要频繁到 `P001_sentences.csv` 全库搜索补金标，标注成本高。

典型失败样本：**C08**（石蜡切片）。论文句表 id=15、27 含 `paraffin-embedded sections`，但 RAG top-10 全是 spatial transcriptomics 相关句，AI 误判为方法添加。

### 1.2 本次改造目标（明确边界）

**要做：**

- 在现有 arag-main Agent 框架内，增加「检索充分性判断 → 关键词补检 → 最多 1～2 轮迭代」闭环；
- LLM 判断的是：**当前召回句是否足以判断观点句是否存在信息失真**；
- 若不足且疑似 RAG 漏检，LLM 只输出 **2～4 个英文关键词 + 可选图号/位置提示**；
- 程序自动执行 keyword 补检，**命中句必须直接进入候选池**；
- 仍不足时，把简短 `retrieval_hint` 留给人工，**不继续消耗 token**；
- 标注侧 `analysis` 保留「具体词语/表述对照」，不压缩成空结论。

**不做：**

- 不引入 BM25/RRF/多索引/复杂混合检索体系；
- 不让 LLM 再做全文检索或预测 sentence_id；
- 不扩大 Agent 开放式 ReAct 到更多轮次；
- 不在本阶段重构 Benchmark 三轨评测（可并行，非本方案前置）。

---

## 2. 现状机制（交接必读）

### 2.1 端到端数据流

```
公众号 MD
  → arag-main/batch_retrieval.py
  → CrossLingualRetrievalPipeline.retrieve(claim_zh)
  → outputs/<P>/<A>/evidences.jsonl
  → arag-main/clean_retrieval_output.py
  → claim_evidence_pairs.jsonl
       classify_evidences = top-5
       review_evidences   = top-10
  → scripts/generate_draft_from_pairs.py
  → annotation_draft.json（gold_retrieval / analysis）
  → 人工审核 → export_benchmark.json
  → scripts/evaluate_benchmark.py
```

### 2.2 arag-main 当前是否用 LLM 优化检索

**结论：用了，但未形成「充分性判断 → 定向补检 → 入池」闭环。**

| 环节 | 现状 | 关键文件 |
|------|------|----------|
| LLM 接收 claim | 是 | `retrieval_adaptor/pipeline.py` L176-178 |
| LLM 选择工具/query | 是（ReAct，最多 12 轮） | `src/arag/agent/base.py` |
| LLM 术语翻译 | 是 | `bilingual_entity_mapper.py` |
| LLM 看召回句 | 是（工具返回进 messages） | `base.py` L159-163 |
| 停止条件 | **rerank 分数**（2 条 ≥0.70 即 STOP） | `thresholds.py` L131-141 |
| LLM 控制最终 evidence 排序 | **否**（按 rerank 分数截断） | `pipeline.py` L205-217 |
| keyword 命中句保证入池 | **否**（需 Agent 再 read_chunk） | `keyword_search.py` |
| LLM 判断「能否判失真」 | **否** | — |
| 低分即判全文无证据 | **是**（可能过早停止） | `thresholds.py` L143-153 |
| 下游审核池 | 固定 top-10 | `clean_retrieval_output.py` L38-39 |

### 2.3 已知瓶颈（P001-A001 实测）

- 12/51：`top5_is_best=false`
- 3/51：金标句不在 review 10 池内
- 31/51：`needs_manual_review=true`
- C08：人工 note「无失真，RAG 差」，人工召回 id=15、29（金标内部还有 evidences/sentence_ids 不一致问题）

---

## 3. 方案总览

### 3.1 核心思路

在**不替换现有 semantic retrieval** 的前提下，增加一个轻量「检索质检层」：

```
[现有] 术语预热 + semantic_search → 初次候选 top-N
         ↓
[新增] LLM 充分性检查（只看 claim + 当前候选句）
         ├─ sufficient=true  → 输出
         └─ sufficient=false → 输出 missing_point + keywords (+ anchors)
                ↓
[新增] 程序自动 keyword_search（sentence 级命中入池）
         ↓
[新增] LLM 第二次充分性检查（最多 1 次）
         ├─ sufficient=true  → 输出
         └─ sufficient=false → 写 retrieval_hint，停止
```

### 3.2 与现有 Agent 的关系

**推荐实现方式（最小侵入）：**

- **方案 A（推荐）**：在 `CrossLingualRetrievalPipeline.retrieve()` 内，`agent.run()` 结束后、`_finalize()` 前，插入「充分性检查 + 程序化补检」；Agent 仍负责初次 semantic 召回。
- **方案 B**：改 `prompt.py` + `thresholds.py`，让 Agent 自行做充分性判断和 keyword 补检；需同步改 keyword 入池逻辑，否则仍可能漏句。

优先 **方案 A**：行为可控、token 可预算、不依赖 Agent 是否记得 read_chunk。

---

## 4. 详细设计

### 4.1 新增：检索充分性检查（Sufficiency Check）

#### 输入

```json
{
  "claim_zh": "为探究葫芦科植物子房下位发育进程，对黄瓜花发育过程进行了石蜡切片观察（图1b）...",
  "candidates": [
    {"sentence_id": 2, "text": "Comparative spatial transcriptome mapping..."},
    {"sentence_id": 35, "text": "Comparative analyses of floral cell types..."}
  ]
}
```

- 只传 **top-5**；若第一次判断不足，第二次可带 **top-10**；
- **不传** text_zh、长上下文、taxonomy、分类标签；
- 每条 candidate 只保留 `sentence_id` + `text`（英文原句）。

#### 输出（严格 JSON，短字段）

```json
{
  "sufficient": false,
  "missing_point": "未找到石蜡切片/组织学观察的直接证据",
  "keywords": ["paraffin", "paraffin-embedded section", "histology"],
  "anchors": ["Fig. 1b", "Fig. 1d"]
}
```

#### 字段约束

| 字段 | 约束 |
|------|------|
| `sufficient` | bool |
| `missing_point` | ≤ 40 中文字符；只写**一个**最关键缺失点 |
| `keywords` | 2～4 个；英文；用于 keyword_search |
| `anchors` | 0～2 个；图号/基因名/物种名 |

#### 判断标准（写入 system prompt）

LLM 必须按「能否判断信息失真」来判，不是按「主题相关」：

- 观点句中的**方法、对象、对照、条件、结果、程度词（首次/显著）、数字/图号**是否都有可对照论文句；
- 若 claim 含「石蜡切片」，必须有含 paraffin/histology/section 等表述的句，或能明确否定其存在；
- **不能**因为已有「spatial transcriptomics + cucumber/tomato」就判 sufficient；
- **不能**因 top-5 无命中就判「论文不存在」——只能判「当前检索不足」。

#### Token 预算

- 单次充分性检查：输入约 800～1500 tokens；输出约 50～120 tokens；
- 每条 claim 最多 **2 次**充分性检查 + **2 次**keyword 补检。

---

### 4.2 新增：程序化关键词补检（Keyword Retry）

#### 触发条件

`SufficiencyCheck.sufficient == false` 且 `keywords` 非空。

#### 执行逻辑

1. 对 `keywords + anchors` 调用 keyword_search（见 4.3 改造）；
2. 将命中 **sentence_id** 直接写入 `EvidenceBoard`（不依赖 Agent read_chunk）；
3. 给补检句赋分：`rerank_score = max(现有候选 top_score * 0.9, 0.35)` 或单独调用一次轻量 rerank（可选，第二阶段再做）；
4. 合并去重后重新排序，截断到 `max_evidences`。

#### 迭代上限

| 轮次 | 动作 |
|------|------|
| 0 | 现有 Agent semantic 初次召回 |
| 1 | 充分性检查 #1 → keyword 补检 #1 |
| 2 | 充分性检查 #2 → keyword 补检 #2（可选） |
| 结束 | 仍 insufficient → 写 `retrieval_hint`，不再调用 LLM |

**总 LLM 新增调用：最多 2 次**（不含原有 Agent 内部调用）。

---

### 4.3 改造：keyword_search 句级入池

#### 现状问题

`keyword_search.py` 在 **chunk 级**匹配，返回 chunk 片段；最终 evidence 来自 `EvidenceBoard` 的 semantic 候选，keyword 命中不一定进入 top-10。

#### 必改行为

新增或改造为 **SentenceKeywordSearch**（可仍复用 chunk 文本，但输出 sentence_id）：

```
输入: keywords=["paraffin", "paraffin-embedded"]
输出: [
  {"sentence_id": 15, "text": "b, The paraffin-embedded sections show...", "matched_keywords": ["paraffin-embedded"]},
  {"sentence_id": 27, "text": "d, The paraffin-embedded sections showing...", "matched_keywords": ["paraffin-embedded"]}
]
```

**实现建议：**

- 数据源：`IndexStore.sentences` + `sentence_to_chunk`（已有句级索引）；
- 匹配：case-insensitive substring；
- 多个 keyword 命中同句：合并计分；
- 命中后直接 `board.add_candidates(...)`，`source="keyword_retry"`。

#### 验收

C08 补检后，review 池必须包含 id=15 和 id=27（或至少 id=15 + 番茄对应句）。

---

### 4.4 改造：停止条件（thresholds + prompt）

#### 现状

- `strong_hits >= 2` @ rerank ≥ 0.70 → STOP；
- `top_score < 0.30` → STOP + NO_EVIDENCE。

#### 改为

**硬停止条件（程序层）：**

1. SufficiencyCheck 返回 `sufficient=true`；
2. 已达 keyword 补检轮次上限（2 轮）；
3. Agent 达到 max_loops（保留兜底）。

**软停止不再单独触发 NO_EVIDENCE：**

- 低分只表示「semantic 未命中」，不表示「论文无此内容」；
- 低分后必须先走 keyword 补检，再判 insufficient。

#### prompt 同步修改

`src/arag/agent/prompt.py` 中删除或弱化：

> 「全部低于 low 阈值 → 判定无支撑证据，Do NOT keep trying」

改为：

> 「低分仅表示当前 query 未命中；必须针对缺失的具体表述做 keyword 补检后再判断。」

---

### 4.5 新增：RetrievalOutput 字段

在 `retrieval_adaptor/schemas.py` 的 `stats` 或顶层增加：

```json
{
  "retrieval_sufficiency": {
    "sufficient": false,
    "checks": [
      {
        "round": 1,
        "missing_point": "未找到石蜡切片证据",
        "keywords": ["paraffin", "paraffin-embedded section"],
        "anchors": ["Fig. 1b"],
        "added_sentence_ids": [15, 27]
      }
    ],
    "retrieval_hint": "缺少石蜡切片直接证据；建议搜索 paraffin、paraffin-embedded section，优先 Fig.1b/1d 图注。"
  }
}
```

`clean_retrieval_output.py` 需把 `retrieval_hint` 透传到 `claim_evidence_pairs.jsonl`，供标注脚本读取。

---

## 5. 标注侧改造（generate_draft / analysis）

### 5.1 analysis 写法规范（替换现有多段重复）

**保留一个主字段**（可仍叫 `analysis.verdict_paragraph` 或合并进现有结构），必须包含四要素：

1. **判定**（有无失真 / 暂不可判）；
2. **观点句争议表述**（引号标出具体词语）；
3. **论文对照**（id=  + 对应英文/中文关键表述）；
4. **语义变化说明**（添加/遗漏/替换/程度变化）+ 标签理由。

**示例（有失真）：**

> 判定为 significance_addition。观点句使用「**首次揭示**」，论文 id=2 仅表述「revealed that...」，未出现 first/novel/breakthrough 等优先性表述；观点句额外增加了研究创新程度，故属意义添加，而非对实验结果的正常概括。

**示例（无失真，RAG 曾漏检）：**

> 判定为无失真。观点句「**石蜡切片观察**」分别对应 id=15「paraffin-embedded sections...cucumber」与 id=27「...tomato」；此前误判为方法添加系 RAG 未召回图注句，非观点句与论文差异。

**示例（暂不可判）：**

> 暂不可判。观点句关键表述「CRC 在番茄心皮中表达」，当前仅有 id=219 关于黄瓜 CRC，未覆盖「番茄」与「心皮定位」，无法判断该表述是否失真。

#### 应删除/合并的冗余

- `evidence_judgement` + `classification_reason` + `key_differences.description` 三段重复 → 合并为一段 + 结构化 `key_spans`（见下）。

#### 可选结构化字段（便于 UI 高亮）

```json
"key_spans": [
  {
    "claim_span": "首次揭示",
    "paper_span": "revealed that",
    "sentence_id": 2,
    "change_type": "significance_addition"
  }
]
```

### 5.2 retrieval_hint（低成本人工辅助）

**仅当** `retrieval_sufficiency.sufficient=false` 时写入；单行字符串，≤ 100 中文字符：

```
"retrieval_hint": "缺少石蜡切片证据；建议搜索 paraffin、paraffin-embedded section，优先 Fig.1b/1d 图注。"
```

**生成方式：**

- 优先从 `RetrievalOutput.retrieval_sufficiency` 直接拷贝（程序生成，**不再调 LLM**）；
- 标注草稿生成阶段仅回填，不让 Qwen 重写一遍。

### 5.3 修改 `draft_from_pairs_api.md` 要点

1. 明确：**不得**因 top-5 无命中就判 addition / 论文未提及；
2. `unsupported_diagnosis.verdict=likely_retrieval_miss` 时，`reason` 必须指向**具体缺失表述**，不是泛泛「检索差」；
3. 图注完整句（如 paraffin-embedded sections）**可以进 gold**，仅排除 OCR 残片；
4. 若 pairs 已带 `retrieval_hint`，`analysis` 中引用即可，勿重复生成关键词列表。

---

## 6. 文件级改动清单

| 优先级 | 文件 | 改动内容 |
|--------|------|----------|
| P0 | `arag-main/retrieval_adaptor/pipeline.py` | 在 `_finalize` 前插入 sufficiency check + keyword retry 循环 |
| P0 | 新建 `arag-main/retrieval_adaptor/sufficiency_check.py` | LLM 充分性判断 + JSON 解析 + 校验 |
| P0 | `arag-main/src/arag/tools/keyword_search.py` 或新建 `sentence_keyword_search.py` | 句级匹配 + 返回 sentence_id + 入 EvidenceBoard |
| P0 | `arag-main/retrieval_adaptor/evidence_board.py` | 支持 `source=keyword_retry` 标记；合并排序 |
| P0 | `arag-main/retrieval_adaptor/schemas.py` | 增加 `retrieval_sufficiency` 字段 |
| P1 | `arag-main/retrieval_adaptor/thresholds.py` | 低分不直接 NO_EVIDENCE；与 sufficiency 联动 |
| P1 | `arag-main/src/arag/agent/prompt.py` | 调整 STOP/CONTINUE 文案 |
| P1 | `arag-main/clean_retrieval_output.py` | 透传 `retrieval_hint` 到 pairs |
| P1 | `data/annotations/prompts/draft_from_pairs_api.md` | analysis 规范 + 禁止早判 addition |
| P2 | `scripts/generate_draft_from_pairs.py` | 读取 pairs.retrieval_hint；合并 analysis 字段校验 |
| P2 | `scripts/evaluate_benchmark.py` | 金标 `evidences`/`sentence_ids` 一致性校验（独立任务） |
| P2 | `arag-main/tests/test_sufficiency_retry.py` | C08 回归测试 |

**不建议本阶段改动：**

- `index_builder.py` 分句逻辑大改；
- 新增 BM25 服务；
- `max_loops` 从 12 提高到更大值。

---

## 7. 配置项（建议新增环境变量）

```bash
# 充分性检查
ARAG_SUFFICIENCY_ENABLED=1
ARAG_SUFFICIENCY_MAX_ROUNDS=2
ARAG_KEYWORD_RETRY_MAX_ROUNDS=2

# 充分性检查输入候选数
ARAG_SUFFICIENCY_TOP_N=5

# Agent 轮次（可选下调以控成本）
ARAG_AGENT_MAX_LOOPS=6
```

默认值：`SUFFICIENCY_ENABLED=1`，`MAX_ROUNDS=2`。

---

## 8. 实施顺序与验收

### Phase 0：基线冻结（0.5 天）

- [ ] 导出 P001-A001 当前 `evidences.jsonl` + `claim_evidence_pairs.jsonl` 作对比基线；
- [ ] 记录 C06/C07/C08 当前 review 池 sentence_ids；
- [ ] 确认 `P001_sentences.csv` 与 index fingerprint 一致。

### Phase 1：keyword 句级入池（1 天）

- [ ] 实现 SentenceKeywordSearch；
- [ ] 单测：`keywords=["paraffin"]` → 返回 id=15, 27；
- [ ] 手动调用 pipeline，不跑 sufficiency，仅强制 keyword → 入 evidences。

**验收：** C08 人工 keyword 补检后，evidences 含 15、27。

### Phase 2：充分性检查 + 两轮补检（1～2 天）

- [ ] 实现 `sufficiency_check.py`；
- [ ] 接入 `pipeline.retrieve()`；
- [ ] 写 `retrieval_sufficiency` + `retrieval_hint`；
- [ ] `clean_retrieval_output.py` 透传。

**验收：**

| 样本 | 期望 |
|------|------|
| C08 | review 池含 paraffin 句；`retrieval_sufficiency.sufficient=true`（补检后）或 hint 含 paraffin 关键词 |
| C09 | 不因主题相关早停；形态描述句仍在前 10 |
| 简单句 C02 类 | 不触发多余 keyword 轮次（sufficient 首轮为 true） |

### Phase 3：标注 prompt 与草稿生成（0.5～1 天）

- [ ] 更新 `draft_from_pairs_api.md`；
- [ ] `generate_draft_from_pairs.py` 回填 `retrieval_hint`；
- [ ] analysis 合并为「四要素一段 + key_spans」。

**验收：** C08 新草稿不再输出「论文完全未使用石蜡切片」；人工 note 不再需要大量补召回。

### Phase 4：回归与指标（0.5 天）

- [ ] 对比改造前后：review 池金标命中率、池外金标比例、平均 LLM 调用次数/token；
- [ ] 目标：池外金标 3/51 → ≤1/51；C08 类方法/图注句召回率明显提升。

---

## 9. 测试用例（必须回归）

### TC-01 C08 石蜡切片

- **输入 claim**：「…石蜡切片观察（图1b）…番茄…（图1d）」
- **期望**：补检后 evidences 含 sentence_id 15、27；不应仅含 spatial transcriptomics 句。
- **不应出现**：`NO_EVIDENCE` 或「论文未提及 paraffin」。

### TC-02 简单支持句（无多余补检）

- 选一条 top-5 已充分覆盖的简单背景句；
- **期望**：sufficiency 首轮 true；keyword retry 0 次；token 增量 < 500/条。

### TC-03 真正池外金标（C06 id=40）

- **期望**：若 semantic+2 轮 keyword 仍找不到 id=40，`retrieval_hint` 给出 `similar floral structure` / `Cucumber and tomato` 等关键词；
- **不要求** 本阶段 100% 自动命中，但 hint 必须可指导人工在 CSV 中找到。

### TC-04 低分不早停

- 构造 semantic 全 < 0.30 但 keyword 可命中的 claim；
- **期望**：仍执行 keyword retry，不输出 NO_EVIDENCE。

---

## 10. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 充分性 LLM 仍误判 sufficient | 第二轮只检查「missing_point 是否已覆盖」；保留 retrieval_hint |
| keyword 过宽召回噪声 | 限制 2～4 词；仅并入 board，最终仍 rerank/截断 |
| token 成本上升 | 最多 2 次 sufficiency；输入仅 top-5；可用 `ARAG_SUFFICIENCY_ENABLED=0` 回滚 |
| 与现有 Agent 行为冲突 | Phase 1 先只做程序化补检，不动 Agent 主循环 |
| 金标不一致（C08 evidences vs sentence_ids） | 标注阶段单独修；评测加一致性校验 |

**回滚开关：**

```bash
ARAG_SUFFICIENCY_ENABLED=0
```

恢复为当前纯 Agent + 分数停止逻辑。

---

## 11. 与 Benchmark / 人工流程的衔接

### 11.1 人工审核顺序（不变）

> claim → classify top-5 → review 6-10 → gold_retrieval → gold_classification → analysis → human_verified

### 11.2 人工新增字段使用方式

| 字段 | 人工怎么用 |
|------|------------|
| `retrieval_hint` | 在 `P001_sentences.csv` 或 IDE 搜索框直接搜关键词 |
| `retrieval_sufficiency.checks[].added_sentence_ids` | 优先看补检新进的句 |
| `human_recalled_sentence_ids` | 仍保留；用于统计 RAG 失败率 |
| `retrieval_quality` | poor / ok；与 hint 联动 |

### 11.3 评测注意

- 本改造只提升 **系统 retrieval**；`evaluate_benchmark.py` 仍默认 pred=top-5；
- 改造生效后应单独看「review 池 Recall@10」是否提升；
- Oracle 分类评测仍按 `HANDOVER.md` 规划，与本方案独立。

---

## 12. 交付物 checklist

实现完成后应交付：

- [ ] `sufficiency_check.py` + 单测；
- [ ] 句级 keyword 补检 + 入池；
- [ ] `RetrievalOutput.retrieval_sufficiency` 字段文档；
- [ ] `claim_evidence_pairs.jsonl` 含 `retrieval_hint`；
- [ ] 更新后的 `draft_from_pairs_api.md`；
- [ ] C08/C06/C09 回归报告（改造前后 sentence_id 对比）；
- [ ] 每条 claim 平均新增 token / 调用次数统计。

---

## 13. 一句话总结

**不要重做 RAG 架构；在现有 arag-main 输出后加一层「LLM 判断证据是否够判失真 → 程序 keyword 补检 1～2 轮 → 失败则给人工关键词提示」，并保证 keyword 命中句直接进入 review 池。**  

标注侧 `analysis` 必须保留「哪个词、哪句话、为何失真」的可审核依据，不能把分析压成结论句。

---

如需我把这份文档写入仓库（例如 `docs/handover_rag_sufficiency_v0.1.md`），请切换到 Agent 模式并说明目标路径。