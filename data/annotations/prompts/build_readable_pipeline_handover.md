# 标注草稿 → Readable 流水线复用交接文档

> 适用对象：后续处理 `P001_A002`、`P002_A001` 等新样本的同学  
> 目标：把「分批 draft → 翻译 → 排版 → readable（给人审）」做成可复用流程  
> 相关脚本：`scripts/translate_text_fields.py`、`scripts/print_readable_json.py`、`scripts/build_readable.py`

---

## 1. 当前实际链路（已跑通：P001/A001）

```text
分批 draft JSON
  → translate_text_fields.py     # 有道翻译 review_evidences[*].text → text_zh
  → print_readable_json.py       # --append-to / --rewrite（换行 + 对齐）
  → *_annotation_draft_readable.json   # 给人审
```

示例（P001/A001）：

```text
data/annotations/P001/
  P001_A001_annotation_draft_C11_C20.json
  P001_A001_annotation_draft_C20_C30.json
  P001_A001_annotation_draft_C31_C40.json
  P001_A001_annotation_draft_C40_C53.json
        │
        ├─ translate → *_translated.json
        └─ append    → P001_A001_annotation_draft_readable.json
```

单批手工命令：

```bash
# 在项目根目录、已配置有道 API 的环境中执行
python scripts/translate_text_fields.py \
  data/annotations/P001/P001_A001_annotation_draft_C31_C40.json \
  data/annotations/P001/P001_A001_annotation_draft_C31_C40_translated.json

python scripts/print_readable_json.py \
  data/annotations/P001/P001_A001_annotation_draft_C31_C40_translated.json \
  --append-to data/annotations/P001/P001_A001_annotation_draft_readable.json
```

排版规则（`print_readable_json.py` 已固化）：

| 字段 | 每行字数 | 对齐 |
|------|----------|------|
| `text_zh` | 45 | 续行与引号内正文首字对齐 |
| `_description` / `evidence_judgement` / `classification_reason` 等 | 100 | 同上 |

就地重排（不重新翻译）：

```bash
python scripts/print_readable_json.py \
  data/annotations/P001/P001_A001_annotation_draft_readable.json \
  --rewrite
```

---

## 2. 复用性现状（交接重点）

| 组件 | 复用性 | 说明 |
|------|--------|------|
| `translate_text_fields.py` | **高** | 任意符合结构的 draft JSON 可直接用；结果缓存 `.translation_cache.json` |
| `print_readable_json.py` | **高** | `--append-to` / `--rewrite` 已通用；路径由参数传入 |
| `build_readable.py` | **低** | 写死了 `P001`、`A001`、四个 batch 文件名 |
| 「batch → 最终 readable」整包编排 | **低** | 尚无「指定 paper/article/batches」的正式 CLI |

**结论：** 换下一篇时，翻译 + 排版脚本可直接用；编排层必须参数化，否则每换一个样本都要改 `build_readable.py`。

---

## 3. 建议的目录与命名约定（请后续统一遵守）

```text
data/annotations/
  prompts/                          # 本交接文档、生成草稿提示词等
  <PAPER_ID>/                       # 如 P001、P002
    <PAPER_ID>_sentences.csv
    <PAPER_ID>_<ARTICLE_ID>_annotation_draft.json          # 可选：合并草稿（勿手工拼接多 JSON）
    <PAPER_ID>_<ARTICLE_ID>_annotation_draft_Caa_Cbb.json  # 分批草稿（推荐）
    <PAPER_ID>_<ARTICLE_ID>_annotation_draft_Caa_Cbb_translated.json
    <PAPER_ID>_<ARTICLE_ID>_annotation_draft_readable.json # 给人审
    <PAPER_ID>_<ARTICLE_ID>_benchmark.json                 # 审核后导出
```

约定：

1. **一批一个合法 JSON**（单个顶层对象）。不要把多个 batch 粘成一个文件（P001_A001 的合并 draft 曾因此难解析）。
2. 分批文件名：`{P}_{A}_annotation_draft_C{start}_C{end}.json`（如 `C11_C20`）。
3. 翻译产物：同名加 `_translated` 后缀。
4. readable：`{P}_{A}_annotation_draft_readable.json`。
5. 首批（如 C01–C10）可先单独生成 readable 种子，再 append 后续批；或第一批用「生成 readable」而不是 append。

输入 draft 每条 sample 至少包含：

```text
sample_id
claim_zh
system_retrieval.classify_evidences[]   # rank, sentence_id, text
system_retrieval.review_evidences[]     # rank, sentence_id, text（翻译只动这里）
gold_retrieval / gold_classification / analysis / human_verified
```

---

## 4. 建议实现的通用 CLI（后续开发任务）

把 `scripts/build_readable.py` 改成参数化入口（名称可保持，或新增 `build_readable_generic.py`）。

### 4.1 推荐接口

```bash
python scripts/build_readable.py \
  --paper P001 \
  --article A002 \
  --batches C01_C10 C11_C20 \
  --seed-from-first
```

参数建议：

| 参数 | 含义 | 默认 |
|------|------|------|
| `--paper` | 如 `P001` | 必填 |
| `--article` | 如 `A001` | 必填 |
| `--ann-dir` | 标注根目录 | `data/annotations` |
| `--batches` | 分批后缀列表，如 `C11_C20 C21_C30` | 必填或自动 glob |
| `--auto-glob` | 自动收集 `*_annotation_draft_C*_C*.json`（排除 `*_translated*`） | 可选 |
| `--skip-translate` | 已有 `*_translated.json` 时跳过翻译 | false |
| `--seed-from-first` | 第一批：若不存在 readable，则新建；否则 append | true |
| `--rewrite-only` | 只对已有 readable 做 `--rewrite` | false |

路径推导：

```text
ANN = data/annotations/{paper}/
PREFIX = {paper}_{article}_annotation_draft
BATCH_FILE = ANN / f"{PREFIX}_{batch}.json"          # batch=C11_C20
TRANS_FILE = ANN / f"{PREFIX}_{batch}_translated.json"
READABLE  = ANN / f"{PREFIX}_readable.json"
```

### 4.2 伪代码流程

```text
for each batch in batches:
  src = PREFIX_{batch}.json
  tmp = PREFIX_{batch}_translated.json
  if not skip_translate:
      translate_text_fields.py src tmp
  else:
      tmp = src if no translated else translated

  if readable not exists and seed_from_first:
      print_readable_json.py tmp  → 写出 READABLE
      # 或：print_readable_json.py tmp --append-to READABLE（脚本在目标不存在时会新建）
  else:
      print_readable_json.py tmp --append-to READABLE

可选：最后再 print_readable_json.py READABLE --rewrite 统一排版
```

### 4.3 实现时注意点（踩坑清单）

1. **append 按 `sample_id` 去重**：同 id 已存在则跳过（`print_readable_json.py` 已实现）。分批若边界重叠（如 C20 同时出现在两批），后批会自动跳过重复 id。
2. **不要合并拼接多顶层 JSON**：`--concat` 仅用于历史脏文件；新流程禁止。
3. **readable 不是严格合法 JSON**（字符串内真实换行），给人审可以；机器再处理需 `json.loads(..., strict=False)` 或 `--rewrite` 前先保证语法（忌尾逗号、`//` 注释）。
4. **翻译缓存**：项目根目录 `.translation_cache.json`；换环境可拷贝以省 API。
5. **换行空格**：`unwrap` 不得吞掉英文词间空格；`print_readable_json.wrap_chinese_text` 已修复，改动时勿回退为「按行 `strip` 再拼接」。
6. **Windows 控制台**：翻译脚本已做 UTF-8 stdout；若打印报错，设 `PYTHONIOENCODING=utf-8`。
7. **首批种子**：C01–C10 若已是「带 text_zh 的 smoke readable」，后续批只 append；不要整文件覆盖。

### 4.4 验收标准（新样本跑通即算复用成功）

- [ ] 不改脚本源码，仅改 CLI 参数即可处理 `P00x_A00y`
- [ ] 产出 `{P}_{A}_annotation_draft_readable.json`
- [ ] 所有 `review_evidences` 均有 `text_zh`
- [ ] `sample_id` 连续、无重复（或重叠批已去重）
- [ ] `--rewrite` 后英文短语不被粘连（如保持 `lineage reconstructions`）
- [ ] 续行与正文首字对齐（目测 `_description` / `text_zh` / `evidence_judgement`）

---

## 5. 新样本操作清单（在通用 CLI 完成前的临时做法）

以 `P001_A002` 为例：

1. 准备分批草稿（合法 JSON）：  
   `data/annotations/P001/P001_A002_annotation_draft_C01_C10.json` 等  
2. 若尚无 readable：对第一批  
   `translate` → `print_readable_json.py <translated.json>`  
   （会生成 `..._translated_readable.json`，请改名为或直接用 `--append-to` 指向目标 readable；目标不存在时 `--append-to` 会新建）  
3. 后续批：`translate` → `--append-to` 同一 readable  
4. 需要统一排版时：`--rewrite`  
5. 人工审核（见同目录审核说明 / `draft_from_pairs.md` 末尾「人工审核顺序」）  
6. 导出：`python scripts/export_benchmark.py --draft ... --output ...`

在通用 CLI 落地后，第 2–4 步合并为一条 `build_readable.py --paper ... --article ... --batches ...`。

---

## 6. 与上下游的边界

| 阶段 | 入口 | 产出 |
|------|------|------|
| 流水线跑文 | `scripts/run.py` | `outputs/{P}/{A}/claim_evidence_pairs.jsonl` 等 |
| 生成标注草稿 | `prompts/draft_from_pairs.md` + LLM / 各 `generate_*_draft.py` | 分批 `*_annotation_draft_C*_C*.json` |
| **本流程** | translate + print_readable / build_readable | `*_readable.json` |
| 人工审核 | 编辑器打开 readable | `human_verified=true` |
| 导出评测 | `scripts/export_benchmark.py` | `*_benchmark.json` |

本交接文档**只覆盖「草稿分批 → readable」**；不覆盖 RAG 跑批与幻觉分类训练。

---

## 7. 参考文件

| 路径 | 用途 |
|------|------|
| `scripts/translate_text_fields.py` | 翻译 `review_evidences` → `text_zh` |
| `scripts/print_readable_json.py` | 换行、对齐、append、rewrite |
| `scripts/build_readable.py` | 当前写死 P001/A001 的一键脚本（待参数化） |
| `scripts/export_benchmark.py` | 审核后导出 benchmark |
| `data/annotations/prompts/draft_from_pairs.md` | 草稿字段定义与人工审核顺序 |
| `.translation_cache.json` | 有道翻译缓存（项目根目录） |

---

## 8. 建议排期（给实现同学）

1. **P0**：参数化 `build_readable.py`（`--paper/--article/--batches`），不改翻译/排版内核。  
2. **P1**：`--auto-glob` + 按 `Cxx` 数值排序；`--skip-translate`。  
3. **P2**：README 增加「新文章标注 readable 一键命令」小节；用 `P001_A002` smoke 做回归。  
4. **P3**（可选）：从多 batch 生成「机器可读合并 draft」（严格 JSON，无真实换行），与 readable 分离，避免再手工拼接。

---

*文档版本：2026-08-01 · 基于 P001/A001 已跑通经验整理*
