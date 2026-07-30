# 植物科学公众号科普文章幻觉检测

输入一篇中文公众号文章 + 一篇英文学术论文，自动完成：

1. **LLM 观点句筛选**（arag）— 约 15–30 条事实性科学断言
2. **跨语言 RAG+Agent 检索**（arag）
3. **幻觉细分类**（hallu）
4. **证据链输出**（hallu）

## 目录结构

```
PlantSci_Hallu/
├── arag-main/             # 模块 A：LLM 抽句 + RAG+Agent 检索
│   ├── retrieval_adaptor/
│   │   ├── claim_extractor.py   # 规则分句 + LLM 核验观点句
│   │   └── pipeline.py          # 检索流水线
│   ├── batch_retrieval.py
│   └── api_client/
├── hallu/                 # 模块 B：幻觉分类 + 证据链
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
├── claims.json / claims.jsonl      # LLM 观点句
├── evidences.jsonl                 # 完整检索（含 sentence_id）
├── claim_evidence_pairs.jsonl      # 精简 claim ↔ 证据（sentence_id + text）
├── classification.json
├── result.json                     # 运行终稿
└── report.md
```

### 标注与评测（人工）

```
data/annotations/
├── prompts/draft_from_pairs.md            # 生成标注初稿的提示词
└── P001/
    ├── P001_sentences.csv                 # 句表（export_sentence_table）
    ├── P001_A001_annotation_draft.json    # 初稿：benchmark 字段 + analysis
    └── P001_A001_benchmark.json           # 审核后导出的评测终稿
```

草稿与终稿字段同名（`gold_retrieval` / `gold_classification`），初稿额外带
`analysis`（判定理由、检索漏检 vs 观点句本身有误的诊断、人工重点核对提示）与
`human_verified`。人工只需改这三处：金标 `sentence_ids`、分类标签、`human_verified`。

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
- **hallu**：claims + 证据 → 幻觉细分类 → 证据链
- **scripts/run.py**：薄编排，不写业务逻辑

> 注：观点句默认路径为「按。！？；换行切句 → 规则粗滤 → LLM 批核验 keep/drop」。  
> 可用环境变量 `CLAIM_VERIFY_BATCH_SIZE`（默认 25）控制核验批大小。  
> 新抽句结果与旧标注 `P001_claims.json`（54 条规则切句）条数可能不同，不可按 id 直接对齐。
