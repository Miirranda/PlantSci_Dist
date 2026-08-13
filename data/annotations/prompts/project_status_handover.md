# PlantSci_Hallu 项目现状交接文档（截至 2026-08-01）

> 适用对象：继续推进本项目的同学 / 协助标注或实验的同学  
> 目标：说明项目定位、当前进度、关键资产、已跑通命令、已知问题与下一步优先级  
> 相关文档：  
> - [`draft_from_pairs.md`](draft_from_pairs.md) — 标注草稿字段与审核规范  
> - [`build_readable_pipeline_handover.md`](build_readable_pipeline_handover.md) — readable 流水线细节  
> - 仓库根目录 [`README.md`](../../../README.md) — 总览与模块边界  

---

## 1. 项目定位（一句话）

面向**中文植物科学科普文本（公众号）**与**英文原始论文**之间的**信息转述失真**，构建「跨语言证据检索 + 细粒度失真分类」的评测基准与检测流水线。评的不是生成式幻觉，而是论文→公众号是否改变了科学含义。

标签权威（2026-08-13 起）：仓库根目录《植物科学科普文本信息失真标注规范 v0.1.md》《信息失真标签优先级和冲突决策树.md》，索引见 [`README_1.md`](../../../README_1.md) v3.0。已有金标仍为旧 9 类，暂不改写。

**建议贡献锚点（待导师确认）：**

| 层级 | 内容 |
|------|------|
| 主贡献 | 资源：跨语言、句级证据、细粒度失真类型的 benchmark + 标注协议 |
| 支撑贡献 | 诊断性实验：检索失败模式、分类过判、质量分层对比、低成本消融 |
| 工程基线 | 抽取–检索–分类端到端系统（评测载体，不宜单独当唯一卖点） |
| 暂缓 | 模型微调、多 LLM 投票标注（三周内投入产出比偏低） |

---

## 2. 任务与系统骨架

### 2.1 任务

对每条中文观点句（claim）：

1. **跨语言检索**：从对应英文论文句库召回证据  
   - 分类用 `classify_evidences`（top-5）  
   - 人工审核池 `review_evidences`（固定 10 条，含 top-5 + 后续）  
2. **细粒度信息失真分类**：证据级别 + 两级标签（Omission/Addition/Substitution × 8 细类）+ No distortion；Weak/No evidence 不进入细类（不可核实 ≠ 已判定失真类型）

### 2.2 端到端链路（已跑通）

```text
中文公众号 md + 英文论文 PDF
  → scripts/run.py
      [arag] 观点句抽取 + RAG/Agent 检索
      [hallu] 信息失真细分类（只用 top-5）
  → outputs/<P>/<A>/
      claims.json(l), claim_evidence_pairs.jsonl,
      classification.json, result.json, report.md
  → 标注草稿（LLM + 人工）
  → *_annotation_draft_readable.json   # 给人审
  → scripts/export_benchmark.py        # → *_benchmark.json
  → scripts/evaluate_benchmark.py      # vs classification.json
```

### 2.3 文章质量分层

| 类型 | 示例 | 用途 |
|------|------|------|
| 高质量人工科普 | P001_A001 | 负对照 / 检索上限参考 |
| AI 生成科普 | P001_A002 | 失真更密、claim 更粗，诊断难点 |
| 一般质量（计划） | 待选 | 补正样本、平衡分布 |

---

## 3. 当前进度快照

### 3.1 已完成

- [x] P001 上端到端推理跑通（A001、A002）  
- [x] A001 readable 人工审核推进较充分（约 53 条）  
- [x] A002 readable 5 条已有较完整 gold / analysis（多数仍 `needs_manual_review`）  
- [x] 导出脚本支持 readable 脏 JSON（真实换行、`//` 注释、尾逗号、中文逗号）  
- [x] 评测脚本 `evaluate_benchmark.py` 已落地并跑通  
- [x] 首次量化评测（试跑，含 `--include-unverified`）  

### 3.2 未完成 / 缺口

- [ ] 标注指南边界细则（主/次类型、severity、科普可接受转述）定稿并同步分类 prompt  
- [ ] 正式金标：全部 `human_verified=true` 后再导出（当前试导出用了 `--include-unverified`）  
- [ ] 双人标注一致性（Cohen’s κ）  
- [ ] 扩到约 6–8 篇配对；调整质量配比（高质量不宜过多）  
- [ ] 检索消融（清洗 / 混合检索 / rerank / 译英对照）  
- [ ] 评测口径修正（见 §5）  
- [ ] 论文初稿  

### 3.3 数据与评测数字（2026-08-01 试跑）

| 集 | benchmark 条数 | Hit@5 | Recall@5 | P@5 | evidence_level | primary_type |
|----|----------------|-------|----------|-----|----------------|--------------|
| A001 | 53 | 0.96 | 0.80 | 0.39 | 0.81 | **0.70** |
| A002 | 5 | 1.00 | 0.42 | 0.43 | 1.00 | **0.00** |

金标分布（重要）：

- A001：`accurate` 约 52/53 → 多数类基线 primary ≈ **0.98**（系统 0.70 **低于基线**）  
- A002：`accurate` 4/5 → 多数类基线 ≈ **0.80**（系统 0.00）  

结论一句话：**检索在高质量文章上基本可用；细分类系统性过判；AI 短文主要卡在 claim 粒度过粗。**

---

## 4. 关键路径与资产

### 4.1 代码与脚本

| 路径 | 作用 |
|------|------|
| `scripts/run.py` | 端到端编排 |
| `scripts/export_benchmark.py` | draft/readable → `*_benchmark.json` |
| `scripts/evaluate_benchmark.py` | benchmark vs `classification.json` |
| `scripts/build_readable.py` | 翻译+readable 编排（已参数化） |
| `scripts/translate_text_fields.py` | review 英→中（有道） |
| `scripts/print_readable_json.py` | 换行排版 / unwrap |
| `hallu/classifier.py` | 信息失真分类 prompt 与调用 |
| `hallu/config.py` | 标签体系、模型与路径 |
| `arag-main/` | 检索与 API 客户端 |

### 4.2 标注与金标

```text
data/annotations/P001/
  P001_sentences.csv
  P001_A001_annotation_draft_readable.json   # 人工主战场（约 53 条）
  P001_A001_benchmark.json                   # 2026-08-01 试导出，53 条
  P001_A002_annotation_draft_readable.json   # 5 条
  P001_A002_annotation_draft_smoke*.json     # 严格 JSON 烟雾稿（gold 可能旧于 readable）
  P001_A002_benchmark.json                   # 2026-08-01 试导出，5 条
  prompts/
    draft_from_pairs.md
    build_readable_pipeline_handover.md
    project_status_handover.md               # 本文档
```

### 4.3 系统预测与评测报告

```text
outputs/P001/A001/
  classification.json      # 系统预测（53 条）
  eval_report.json         # 评测报告
  claim_evidence_pairs.jsonl, report.md, ...

outputs/P001/A002/
  classification.json      # 5 条
  eval_report.json
```

**注意：** 导出/评测应以 **readable 中的人工修改** 为准；`smoke.json` 未必同步了最新 gold。

---

## 5. 已知问题与坑（必读）

### 5.1 分类过判

系统常把金标 `accurate`（或仅 secondary 轻度失真）升格为 `scope_generalization` / `certainty_amplification` 等。  
根因：主类型 vs 次类型、severity、科普可接受措辞的边界未写死；prompt 与人工标准未对齐。

### 5.2 评测口径陷阱

- 新分类器输出 `has_distortion` / `primary_label` / `severity`；旧金标仍是 `primary_type` / `is_accurate`。混用时 level2 字符串对不上（如 `accurate` vs `no_distortion`），属 taxonomy 版本差，不是系统全错。  
- Weak/No evidence 的 `has_distortion` 为 null，**不再**计为「幻觉=True」。  
- `severity` 在证据不足时为空；与金标 `none` 对比可能拉低准确率。  
- 正式报告必须带 **多数类基线**，否则会误判系统表现。

### 5.3 A002 检索 Recall 偏低的主因

并非单纯「句子抽象」，而是 **复合 claim 粒度过粗**：一条 claim 对应多个断言、金标常 4–5 句，top-5 覆盖上限被锁死。  
标注里 `evidence_judgement` 多数已做「分句拆解」，但 benchmark schema 尚未结构化为 `sub_claims`。

### 5.4 Readable JSON 不严格

人工批注常见：`true//注释`、尾逗号、中文逗号 `，`、字符串内真实换行。  
`export_benchmark.py` 的 `_sanitize_jsonish` / `strict=False` 已兼容；其他脚本若直接 `json.loads` 可能失败。

### 5.5 Pooling / hole 风险

金标若主要从本系统 top-10 选取，会系统性高估自家检索 Recall。  
标注协议要求：池外仍可从句表补金标，并建议统计「池外 gold」比例。  
A002-C01 已出现池外补句情况。

### 5.6 旧文件注意

- `P001_A001_annotation_draft.json`（合并稿）历史上曾因拼接损坏，**不要当权威源**。  
- 旧版 `P001_A001_benchmark.json`（曾 54 条 / schema 1.0）已被 2026-08-01 从 readable 试导出的 53 条 / 1.1 **覆盖**。

### 5.7 Windows

PowerShell 不支持 `&&`，请用 `;` 串联命令。编码问题可设 `PYTHONIOENCODING=utf-8`。

---

## 6. 常用命令

在仓库根目录 `PlantSci_Hallu` 下执行。

### 6.1 端到端推理

```powershell
python scripts/run.py `
  --article data/articles/ai_generated/P001_A002.md `
  --paper data/papers/P001_2025_NatPlants_cucurbits-KNOX1-ovary.pdf `
  --output-dir outputs/P001/A002
```

需配置 `arag-main/.env`（见 `arag-main/.env.example`）。

### 6.2 Readable 构建（示例）

```powershell
python scripts/build_readable.py --paper P001 --article A002 --batches smoke
```

细节见 `build_readable_pipeline_handover.md`。

### 6.3 导出 benchmark（试跑：含未 verified）

```powershell
python scripts/export_benchmark.py `
  --draft data/annotations/P001/P001_A001_annotation_draft_readable.json `
  --output data/annotations/P001/P001_A001_benchmark.json `
  --include-unverified

python scripts/export_benchmark.py `
  --draft data/annotations/P001/P001_A002_annotation_draft_readable.json `
  --output data/annotations/P001/P001_A002_benchmark.json `
  --include-unverified
```

正式终稿：去掉 `--include-unverified`，且样本 `human_verified=true`。

### 6.4 评测

```powershell
python scripts/evaluate_benchmark.py `
  --benchmark data/annotations/P001/P001_A001_benchmark.json `
  --predictions outputs/P001/A001/classification.json `
  --output outputs/P001/A001/eval_report.json

python scripts/evaluate_benchmark.py `
  --benchmark data/annotations/P001/P001_A002_benchmark.json `
  --predictions outputs/P001/A002/classification.json `
  --output outputs/P001/A002/eval_report.json
```

---

## 7. 标注工作要点（给人审）

人工审核顺序（见 `draft_from_pairs.md`）：

1. 读 `claim_zh`  
2. 看 classify top-5  
3. 扫 review 第 6–10 条  
4. 改 `gold_retrieval`  
5. 改 `gold_classification`  
6. 改 `analysis`  
7. `human_verified=true`  

减负手段（已落地）：

- top-5 送分类 / top-10 供人工备选（减少全文手翻）  
- `analysis` 含 AI 建议、`needs_manual_review`、`manual_check_hints`  
- `text_zh` 翻译降低中英对照成本  

粗估工时：约 **4–5 小时 / 篇**（按 ~50 观点句；跨语言 + 句级证据 + 细分类）。

**当前主线仍是标注**；标准骨架已有，需细化边界后再大批量扩标，避免把模糊边界固化进金标。

---

## 8. 建议下一步（优先级）

### P0 — 细化标准（短，半天–1 天）

1. 补标注指南：primary vs secondary、severity 阈值、科普可接受转述正反例  
2. 同步修改 `hallu/classifier.py` 的分类原则  
3. 修正评测：加多数类基线；明确 severity/is_accurate 是否纳入系统输出  

### P1 — 标注主线

1. A001/A002 收尾：`human_verified`、处理 `needs_manual_review`  
2. 选 6–8 篇配对，提高 AI/一般质量占比，增加失真正样本  
3. 至少 1 篇双标，报 κ  

### P2 — 实验支线（穿插，不压过标注）

1. PDF 分句/图注清洗  
2. dense + BM25 混合检索、rerank  
3. 中文 claim vs 译英后再检索  

### P3 — 论文

错误案例归类 + 初稿；微调 / 多模型投票 → future work。

---

## 9. 模块边界（防踩坑）

| 模块 | 负责 | 不负责 |
|------|------|--------|
| `arag-main` | 观点句、跨语言检索、句表 | 信息失真细分类金标评测 |
| `hallu` | 分类与证据链 | 检索实现 |
| `scripts/run.py` | 薄编排 | 业务逻辑 |
| `export_benchmark` / `evaluate_benchmark` | 金标导出与离线打分 | 训练/微调 |
| `arag-main/scripts/eval.py` | 上游 ARAG QA 评测 | **与本项目 `*_benchmark.json` 无关，勿混用** |

---

## 10. 交接检查清单

接手同学建议按顺序确认：

1. 能读通本文件 §1–§5  
2. 本地能打开 A001/A002 的 readable、benchmark、`eval_report.json`  
3. 复现一次 `export_benchmark` + `evaluate_benchmark`（A002 五条最快）  
4. 阅读 `draft_from_pairs.md` 标签定义与审核顺序  
5. 与导师确认：贡献定位、投稿档次、标注人力、是否暂缓微调  
6. 再开始扩标或改 prompt  

---

## 11. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-08-01 | 首版：覆盖项目定位、A001/A002 试评测、导出/评测脚本、已知坑与三周优先级 |
