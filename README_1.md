# 植物科学科普文本信息失真：项目内索引与冻结口径

> 版本: 3.0 | 2026-08-13  
> 任务定位：**论文 → 公众号转述中的信息失真**（不是生成式「幻觉」）

本文档**不再是标签定义的权威来源**。权威文档：

| 文档 | 职责 |
|------|------|
| [`植物科学科普文本信息失真标注规范 v0.1.md`](植物科学科普文本信息失真标注规范%20v0.1.md) | 8 个细类定义、正反例、与相邻类区别 |
| [`信息失真标签优先级和冲突决策树.md`](信息失真标签优先级和冲突决策树.md) | Primary/Secondary、Level-1 优先级、No distortion 排除区 |

下游实现（`hallu/config.py`、`hallu/classifier.py`、`draft_from_pairs.md`、评测脚本）与上述两份文档及**下文冻结口径**保持一致。

旧版 `README_1.md` v2.1（幻觉 + 9 类扁平标签）已 superseded。已标注数据仍使用旧标签，**暂不自动改写**（见 §6）。

---

## 1. 任务定义

对每条**中文科普观点句 (claim)** 与其对应的**英文学术论文证据句**，做两件独立的事：

1. **证据维度** `evidence_level`：以本篇论文为唯一依据，证据能否支撑细粒度比对？  
2. **失真维度**（仅 `With_Evidence`）：转述是否改变了科学含义？若改变，属于哪类操作（删减 / 添加 / 替换）？

评的不是「这句话在现实世界对不对」，而是「相对这篇论文，转述有没有改变科学含义」。

---

## 2. 冻结口径（代码与新标注必须遵守）

### 2.1 英文 slug（level2）

| level1 | level2 slug | 中文 |
|--------|-------------|------|
| omission | `context_omission` | 背景限定删减 |
| omission | `evidence_uncertainty_omission` | 证据与不确定性删减 |
| omission | `mechanism_omission` | 机制删减 |
| addition | `function_application_addition` | 功能/应用添加 |
| addition | `significance_addition` | 意义/重要性添加 |
| substitution | `relation_substitution` | 关系替换 |
| substitution | `magnitude_substitution` | 作用程度替换 |
| substitution | `mechanism_substitution` | 机制替换 |
| — | `no_distortion` | 无失真（N0–N5） |

`taxonomy_version`: **`distortion-v0.1`**。  
Level-1 冲突优先级：**substitution > addition > omission**。  
不要再增加第 9 个细类（决策树已冻结类别集合）。

### 2.2 证据维度独立于失真类型

| `evidence_level` | 含义 | 是否进入 8 类判定 |
|------------------|------|:----------------:|
| `With_Evidence` | 至少一句直接对应核心断言，可细比对 | 是 |
| `Weak_Evidence` | 主题相关，但核心断言无法被证据充分验证或证伪 | **否** |
| `No_Evidence` | 核心断言完全找不到对应 | **否** |

**不可核实 ≠ 已判定失真类型。**  
`Weak_Evidence` / `No_Evidence` 时：`has_distortion = null`，`primary_label` 为空，不要标 8 类之一，也不要再写成「这就是幻觉」。

### 2.3 明确不扩类的现象

下列现象**不新增标签**。若无法归入 8 类，标 `needs_manual_review=true`，并填 `uncovered_phenomenon`：

| 值 | 何时用 |
|----|--------|
| `numerical_change` | 精确数值被方向性改动，且不是程度词替换（contributes→determines） |
| `semantic_contradiction` | 科学含义正负相反，且不是关系/机制替换 |
| `other` | 其余无法覆盖的独立变化 |

若数值变化其实是「贡献→决定」这类程度词，用 `magnitude_substitution`，不要用 uncovered。

### 2.4 Schema（新标注 / 系统输出）

```json
{
  "evidence_level": "With_Evidence",
  "has_distortion": true,
  "primary_label": {
    "level1": "substitution",
    "level2": "relation_substitution"
  },
  "secondary_label": {
    "level1": "addition",
    "level2": "function_application_addition"
  },
  "severity": "moderate",
  "needs_manual_review": false,
  "uncovered_phenomenon": "",
  "reason": "Association changed into causal claim"
}
```

无失真：`has_distortion=false`，`primary_label.level2=no_distortion`，`secondary_label=null`，`severity=none`。  
证据不足：`has_distortion=null`，`primary_label=null`，`secondary_label=null`。  
`secondary_label` 最多一条独立变化；同一变化禁止 primary+secondary 重复标。  
`severity`：`none` / `mild` / `moderate` / `severe`（无失真用 `none`）。

为兼容旧评测脚本，系统输出可同时带扁平别名：`primary_type` = level2 slug，`secondary_types` = [level2]，`is_accurate` = `has_distortion is false`（仅 With_Evidence）。**不要再输出 `is_hallucination=true` 来表示 Weak/No。**

### 2.5 判定顺序（与决策树一致）

1. 定 `evidence_level`（证据不足则结束）。  
2. 是否完全支持论文？是 → `no_distortion`。  
3. 是否改变已有科学关系？→ Substitution。  
4. 是否增加论文没有的信息？→ Addition。  
5. 是否删除重要限定？→ Omission。  
6. 是否存在第二个**独立**错误？最多一条 Secondary。  
7. 过一遍 N0–N5：合理压缩、术语通俗化、同义转述、非关键实验细节、正常程度弱化、一般背景补充 → 不标。

---

## 3. 两轨评测（不变）

| 轨 | 金标 | 指标 |
|----|------|------|
| RAG | `gold_retrieval.sentence_ids` | Hit@k / Recall@k / P@k |
| 失真分类 | `gold_classification`（新：level1/level2） | evidence_level 准确率；level1/level2 准确率；has_distortion |

分类输入默认仍是检索 top-5，因此分类分含级联误差。解读时两轨分开看。

---

## 4. 旧 9 类 → 新 8 类（仅提示，禁止自动改金标）

| 旧 `primary_type` | 提示性对应 |
|-------------------|------------|
| `accurate` | `no_distortion` |
| `certainty_amplification` | `evidence_uncertainty_omission`（新增强化词则可能是 Addition） |
| `scope_generalization` / `context_stripping` | `context_omission` |
| `mechanism_simplification` | `mechanism_omission` |
| `causality_distortion` | `relation_substitution` |
| `fact_addition` | 须人工拆：`function_application_addition` 或 `significance_addition` |
| `numerical_distortion` | `uncovered_phenomenon=numerical_change` |
| `semantic_contradiction` | 能归入替换则归入，否则 `uncovered_phenomenon=semantic_contradiction` |

已有 `P001_*_benchmark.json` 等文件的 `taxonomy_version` 视为 `hallu-9class`。新草稿与新系统输出使用 `distortion-v0.1`。

---

## 5. 与其他文件的关系

| 文件 | 关系 |
|------|------|
| `hallu/config.py` — `DISTORTION_LABELS` | 标签表与 schema 规范化 |
| `hallu/classifier.py` — `CLASSIFICATION_SYSTEM` | 分类 prompt，从规范 + 决策树派生 |
| `data/annotations/prompts/draft_from_pairs.md` | 新标注草稿提示词 |
| `scripts/export_benchmark.py` / `evaluate_benchmark.py` | 兼容新旧 schema，不改写旧金标 |

**改标签先改两份规范 md，再改 `config.py`，再同步 prompt / 评测。**

---

## 6. 修订记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-08-13 | 3.0 | 任务改为信息失真；权威文档切换；冻结 slug/schema/evidence_level；旧 9 类 superseded |
| 2026-08-03 | 2.1 | （归档）幻觉 + 9 类扁平标签；Weak/No 曾被定义为幻觉判决 |
| 2026-08-03 | 2.0 / 1.0 | （归档） |
