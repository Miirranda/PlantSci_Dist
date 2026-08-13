# 植物科学公众号科普文章信息失真检测

输入一篇中文公众号文章 + 一篇英文学术论文，自动完成：

1. **LLM 观点句筛选**（arag）— 约 15–30 条事实性科学断言
2. **跨语言 RAG+Agent 检索**（arag）
3. **信息失真细分类**（hallu）— 论文→公众号转述失真，不是生成式幻觉
4. **证据链输出**（hallu）

标签权威：[`README_1.md`](README_1.md)（冻结口径）+ 仓库根目录两份规范 md。

## 目录结构

```
PlantSci_Hallu/
├── arag-main/             # 模块 A：LLM 抽句 + RAG+Agent 检索
│   ├── retrieval_adaptor/
│   │   ├── claim_extractor.py   # 规则分句 + LLM 核验观点句
│   │   └── pipeline.py          # 检索流水线
│   ├── batch_retrieval.py
│   └── api_client/
├── hallu/                 # 模块 B：信息失真分类 + 证据链
│   ├── classifier.py
│   ├── evidence_chain.py
│   ├── arag_bridge.py     # 调用 arag 子进程
│   └── adapters/          # arag ↔ hallu 数据契约
├── scripts/run.py         # 唯一编排入口
├── data/                  # 文章 / 论文 / 标注
└── outputs/<P>/<A>/       # 按样本分目录的运行产物
```

## 快速开始

```bash
# 依赖：Python 3.9+，arag-main/.env 中配置 Qwen / SiliconFlow 密钥

python scripts/run.py \
  --article data/articles/high_quality/P001_A001_黄瓜下位子房的发育机制.md \
  --paper   data/papers/P001_2025_NatPlants_cucurbits-KNOX1-ovary.pdf \
  --output-dir outputs/P001/A001
```

### 常用参数

| 参数 | 说明 |
|------|------|
| `--skip-extract` | 复用已有 `claims.json(l)`（跳过 LLM 抽句） |
| `--skip-retrieval` | 复用已有 `evidences.jsonl` / pairs |
| `--from-step classify` | 从分类阶段续跑 |
| `--limit N` | 只跑前 N 条（调试） |
| `--workers 1` | arag 并发（默认 1） |

### 仅跑 arag

```bash
cd arag-main
python batch_retrieval.py \
  --article ../data/articles/high_quality/P001_A001_黄瓜下位子房的发育机制.md \
  --claims-out ../outputs/P001/A001/claims.jsonl \
  --output ../outputs/P001/A001/_arag_run \
  --workers 1
```

旧规则切句仅调试：`--wechat data/wechat --legacy-split`（生产请用 `--article`）。

### 输出文件

```
outputs/P001/A001/
├── claims.json / claims.jsonl      # 观点句
├── evidences.jsonl                 # 完整检索（含 sentence_id）
├── claim_evidence_pairs.jsonl      # 精简：classify top-5 + review 池 10
├── classification.json             # 信息失真分类（只用 top-5）
├── result.json
└── report.md
```

同一次检索，两套用途：`classify_evidences`（top-5，送分类）与
`review_evidences`（固定 10 条，供人工判断 top-5 是否真相关）。

### 标注与评测（人工）

```
data/annotations/
├── prompts/draft_from_pairs.md            # 生成标注初稿的提示词
└── P001/
    ├── P001_sentences.csv                 # 句表（export_sentence_table）
    ├── P001_A001_annotation_draft.json    # 初稿：评测字段 + system_retrieval + analysis
    └── P001_A001_benchmark.json           # 审核后导出的评测终稿
```

草稿 = `gold_retrieval` / `gold_classification`（评测）
+ `system_retrieval`（分类 top-5 与审核池 10，对照用）
+ `analysis` + `human_verified`。  
金标证据须同时带 `sentence_id` 与论文原文。导出 benchmark 时去掉
`system_retrieval` / `analysis`，只留已确认样本。

新标注使用 `distortion-v0.1`（`primary_label.level1/level2`）。
已有 P001 金标仍为旧 9 类扁平标签，评测脚本可读取，但**不要自动改写**。

```bash
# 导出句表
cd arag-main && python scripts/export_sentence_table.py --paper-id P001 \
  -o ../data/annotations/P001/P001_sentences.csv

# 用 prompts/draft_from_pairs.md 让 LLM 从 claim_evidence_pairs.jsonl 生成初稿

# 审核后导出 benchmark（默认只要 human_verified=true）
python scripts/export_benchmark.py
```

## 模块边界

- **arag-main**：中文文章 → 规则分句 + LLM 核验观点句 → 英文论文证据
- **hallu**：claims + 证据 → 信息失真细分类 → 证据链
- **scripts/run.py**：薄编排，不写业务逻辑

> 注：观点句默认路径为「按。！？；换行切句 → 规则粗滤 → LLM 批核验 keep/drop」。  
> 可用环境变量 `CLAIM_VERIFY_BATCH_SIZE`（默认 25）控制核验批大小。  
> 新抽句结果与旧标注 `P001_claims.json`（54 条规则切句）条数可能不同，不可按 id 直接对齐。
