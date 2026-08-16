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
├── hallu/                 # 模块 B：信息失真分类 + 证据链
├── scripts/run.py         # 编排入口
├── data/
│   ├── papers/papers_index.json   # 唯一论文清单（扩库只改这里）
│   ├── papers/P002.pdf
│   ├── articles/.../Pxxx_Axxx_*.md
│   ├── corpus/P001/chunks.json    # 按篇中间产物
│   ├── index/P001/                # 按篇向量库（只检索这一座）
│   └── annotations/P001/          # 按篇标注与句表
└── outputs/<P>/<A>/
```

一篇论文一座索引：检索 P001 时只加载 `data/index/P001/`，不会扫 P002–P030。

## 快速开始

```bash
# 依赖：Python 3.9+，arag-main/.env 中配置 Qwen / SiliconFlow 密钥

# 1) 为该篇论文建库（已有完整索引则跳过）
python scripts/ensure_index.py --paper-id P001

# 2) 跑一篇公众号（自动只检索 P001）
python scripts/run.py \
  --article data/articles/high_quality/P001_A001_黄瓜下位子房的发育机制.md \
  --paper-id P001
```

### 常用参数

| 参数 | 说明 |
|------|------|
| `--skip-extract` | 复用已有 `claims.json(l)`（跳过 LLM 抽句） |
| `--skip-retrieval` | 复用已有 `evidences.jsonl` / pairs |
| `--from-step classify` | 从分类阶段续跑 |
| `--limit N` | 只跑前 N 条（调试） |
| `--workers 1` | arag 并发（默认 1） |

### 扩到新论文

1. 把 PDF 放到 `data/papers/P00x.pdf`（或在注册表填写 `pdf` 路径）
2. 在 `data/papers/papers_index.json` 增加 `P00x` 条目与配文章
3. `python scripts/ensure_index.py --paper-id P00x`

切句规则大改时：`python scripts/ensure_index.py --paper-id P00x --rebuild`，然后按原文重对齐该篇金标 id。

### 仅跑 arag

```bash
cd arag-main
python batch_retrieval.py \
  --paper-id P001 \
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
├── prompts/draft_from_pairs_api.md        # 生产：API 系统提示词
├── prompts/draft_from_pairs.md            # 人类改规则（勿整份贴进聊天）
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
# 导出句表（默认 data/index/P001/ → data/annotations/P001/P001_sentences.csv）
cd arag-main && python scripts/export_sentence_table.py --paper-id P001

# 生产：API 脚本从 pairs 生成初稿（聊天框不要一次贴整份 JSONL）
python scripts/generate_draft_from_pairs.py \
  --paper P001 --article A001 --source-type high_quality \
  --limit 10 --batch-size 3
# 提示词：data/annotations/prompts/draft_from_pairs_api.md

# 审核后导出 benchmark（默认只要 human_verified=true）
python scripts/export_benchmark.py
```

## 模块边界

- **arag-main**：中文文章 → 规则分句 + LLM 核验观点句 → 英文论文证据
- **hallu**：claims + 证据 → 信息失真细分类 → 证据链
- **scripts/run.py**：薄编排，不写业务逻辑

> 注：观点句默认路径为「按。！？换行切句（编号清单不按分号切）→ 规则筛元信息/残句 → LLM 角色核验 → 总结段去重」。  
> 可用环境变量 `CLAIM_VERIFY_BATCH_SIZE`（默认 25）控制核验批大小。  
> 新抽句结果与旧标注 `P001_claims.json`（54 条规则切句）条数可能不同，不可按 id 直接对齐。
