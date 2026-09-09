# 审核前端交接文档（review_ui）

> 适用对象：接手初稿审核工作的同学 / 需要了解审核界面布局、交互与注意点的标注员
> 目的：说明审核前端的启动方式、两阶段两文件结构、每块内容、交互快捷键，以及审核中必须注意的要点
> 相关文件：
> - [`project_status_handover.md`](project_status_handover.md) — 项目整体现状
> - [`draft_from_pairs.md`](draft_from_pairs.md) — 标注草稿字段与审核规范
> - [`../../scripts/review_server.py`](../../scripts/review_server.py) — 后端（零依赖）
> - [`../../scripts/review_ui/recall.html`](../../scripts/review_ui/recall.html) — 召回审核前端
> - [`../../scripts/review_ui/distortion.html`](../../scripts/review_ui/distortion.html) — 失真审核前端
> - [`../../scripts/review_ui/style.css`](../../scripts/review_ui/style.css) — 两前端共享样式
> - [`../../scripts/regenerate_analysis.py`](../../scripts/regenerate_analysis.py) — 文件1 → 文件2 二次生成
> - [`../../scripts/merge_reviews.py`](../../scripts/merge_reviews.py) — 文件2 合并回文件1

---

## 1. 定位与入口

审核前端是一个**本地、离线**的标注辅助工具，用于对「中文公众号观点句 → 英文论文证据」的信息失真进行人工审核。它把一个观点句的审核拆成**两阶段、两个文件、两个界面**：

| 阶段 | 文件 | 界面 | 干什么 |
|------|------|------|--------|
| 召回审核 | `*_annotation_draft.json`（文件1） | `recall.html` | 勾选 gold 句、写召回备注、贴人工找回段落 |
| 失真审核 | `*_distortion_review.json`（文件2） | `distortion.html` | 基于确认后的证据 + AI 二次分析，勾失真类型 |

两阶段由开发机脚本串起来（审核人离线只用其一即可）：

```
文件1(召回审核) --regenerate_analysis.py--> 文件2(失真审核) --merge_reviews.py--> 文件1(合并回)
```

- 后端：`scripts/review_server.py`（纯 Python 标准库 `http.server`，**不依赖 hallu / api_client / .env / API key**）。按文件名自动路由：打开 `*_distortion_review.json` 服务 `distortion.html`，否则服务 `recall.html`。
- 前端：`recall.html` / `distortion.html`（内联 JS，共享 `style.css`，无 CDN，完全离线）。

### 启动方式

方式一（推荐）：双击仓库根目录 `start_review.bat`（内部 `chcp 65001` + `python scripts\review_server.py`），自动打开浏览器。

方式二（命令行，仓库根目录下）：

```bash
python scripts\review_server.py
# 或指定文件：
python scripts\review_server.py --draft data\annotations\P001\P001_A001_annotation_draft.json            # 召回
python scripts\review_server.py --draft data\annotations\P001\P001_A001_distortion_review.json          # 失真
```

- 服务器默认监听 `http://127.0.0.1:8765/`。
- 只扫描两类命名：`*_annotation_draft.json`（召回）与 `*_distortion_review.json`（失真）；`_translated` / `_draft_2` / `_readable` 等中间产物一律不扫。
- 评测文件必须是**严格合法 JSON**（`json.load` 能直接读）。

---

## 2. 两阶段说明

### 2.1 阶段一 · 召回审核（recall.html）

页面从上到下：

```
顶栏（审核人 / 进度 / 导航）
① 他人审核参考（黄卡）
② 观点句（蓝卡，sticky 固定在顶部）
③ 召回证据（英文原句 + 翻译 + 上下文）
④ 召回审核操作（蓝边框区块）：gold 输入 + 召回备注 + 人工找回段落 + 「召回审核已完成」勾选
```

- **召回证据**：`review_evidences`（通常 10 条），每条含 rank（前 5 条 top-5 高亮）、英文原句 + 翻译、**gold 勾选框**、**「上下文 ▾」**（展开论文前 3 句 / 本句 / 后 3 句，实时读句表 CSV）、句末 `id=N`（AI 金标句带 ★）。
- **召回审核操作**：勾选证据句会自动填充 gold 输入框（也可手填 `sentence_id`）；「召回审核备注」记 `recall_note`；「人工找回原文段落」每行一段英文原文（`text`），中文翻译由脚本补；勾选「召回审核已完成」置 `recall_reviewed=true`（脚本据此判断是否生成）。

### 2.2 阶段二 · 失真审核（distortion.html）

页面从左到右、从上到下：

```
顶栏（审核人 / 进度 / 导航）
左栏主区：① 观点句（sticky）→ ② 证据句（勾选 gold + 人工找回，带翻译）→ ③ AI 辅助分析（紫卡）→ ④ 人工审核操作（失真类别）
右栏侧栏（sticky）：AI 结论 + 失真判断指南
```

- **证据句**：直接读文件2 里冻结的 `gold_evidences`（勾选句 text+text_zh）+ `manual_retrieved_paragraphs`（人工段落，含翻译），**不再查文件1 的召回池**。
- **AI 辅助分析**：渲染 `generated_analysis`（二次生成结果）。文件2 里没有 `generated_analysis` 时显示「未二次生成，请先运行 regenerate_analysis.py」。
- **人工审核操作**：证据级别 / 失真类型（1-9）/ 次要失真 / 严重度 / 未覆盖现象 / 备注 / 「标记已完成审核」。

---

## 3. 各块内容说明

### 3.1 顶栏（两界面共用）

| 元素 | 作用 |
|------|------|
| 标题 + 副标题 | 「召回审核（文件1）」或「失真审核（文件2）」；副标题显示当前文件与样本条数 |
| 审核人输入框 | **必须先填姓名**，结果按姓名分列存储，存 localStorage 自动记住 |
| 进度 | 位置 `i/N` + 进度条 + 「我的进度 N」（召回界面按 `recall_reviewed` 计数，失真界面按 `human_verified` 计数） |
| 导航按钮 | 「← 上一条」「下一条 →」「跳转」「下一条未审核」 |

### 3.2 观点句（蓝卡，固定）

大字显示 `claim_zh`（公众号观点句）；meta 行显示 `sample_id`。召回界面另显示 AI 金标句、基线人工备注。

### 3.3 他人审核参考（黄卡，仅召回界面）

显示**其他人**对当前样本的召回记录（gold 句、是否已召回审核、召回备注）。自己是第一位审核人时显示「暂无他人审核」。

### 3.4 AI 辅助分析（紫卡，仅失真界面）

顶部 chip：证据级别（With/Weak/No）、失真类型、次要、严重度、置信度；明细块：`evidence_judgement`（证据判定）、`classification_reason`（分类理由）、`key_differences`（关键差异：论文 vs 公众号）。

### 3.5 失真判断指南（右栏，仅失真界面）

完整判据（内嵌自权威规范）：判断流程（Step 0–5）→ 无失真 → 8 类失真（定义 + 判断问题 + 正例 + 反例）→ 不可标注区 → 易混对照 → 8 类之外现象。点击 8 类里的某类会**同时选中**对应失真类型按钮（双向联动）。

---

## 4. 交互与快捷键

| 快捷键 | 召回界面 | 失真界面 |
|--------|----------|----------|
| `←` / `→` | 上一条 / 保存并下一条 | 上一条 / 保存并下一条 |
| `S` | 保存召回 | 保存失真 |
| `1–9` | — | 选失真类型（1=无失真，2–4=删减，5–6=添加，7–9=替换） |
| `D` | — | 切换「标记已完成审核」 |

> 焦点在输入框 / 文本域内时，快捷键不触发。换页时若当前样本有未保存修改，会弹确认「放弃修改并跳转？」。

---

## 5. 数据保存与两文件结构

- 前端 `POST /api/save` → 后端写回**当前加载文件的顶层 `human_reviews` 键**。
- 结构：`human_reviews[审核人名][sample_id] = record`，**多审核人并存、互不覆盖**；保存用**合并语义**（只更新 payload 里显式出现的字段，不清掉其他字段）。

**文件1（召回）record 字段**：`gold_sentence_ids` / `recall_note` / `manual_retrieved_paragraphs` / `recall_reviewed` / `recall_updated_at` / `updated_at`。

**文件2（失真）record 字段**（由 `regenerate_analysis.py` 从文件1 冻结 + 生成）：

| 字段 | 来源 | 说明 |
|------|------|------|
| `gold_sentence_ids` / `recall_note` / `recall_reviewed` / `recall_updated_at` | 文件1 拷贝 | 召回上下文 |
| `gold_evidences` | 脚本冻结 | 勾选句 text+text_zh |
| `manual_retrieved_paragraphs` | 脚本冻结 | 人工段落（含补好的翻译） |
| `generated_analysis` | 脚本生成 | AI 失真分析（含 `generated_at`） |
| `evidence_level` / `primary_level2` / `secondary_level2` / `severity` / `uncovered_phenomenon` / `note` / `human_verified` | 失真界面保存 | 人工失真标注 |

`merge_reviews.py` 把文件2 的失真字段 + `generated_analysis` + 补好翻译的 `manual_retrieved_paragraphs` 写回文件1（**不**覆盖文件1 的召回字段，也**不**写回 `gold_evidences`）。

---

## 6. 审核中必须注意的要点

1. **先填审核人姓名**。不填无法保存；姓名决定结果存到谁名下。

2. **AI 答案会被预选为默认值——这是最大的坑**。失真界面打开样本时，证据级别 / 失真类型 / 严重度都会预填 AI 判断。**如果一条都不改直接保存，等于默认接受了 AI**。此前「后半部分数据摆烂默认 AI」导致质量差，根因就在这。审核时务必逐条自己判断，拿不准再参考 AI。

3. **判断顺序先证据后失真**：先按 Step 0 定 `evidence_level`（找不到对应句 → No_Evidence，主题相关但证不充分 → Weak_Evidence，至少一句直接对应核心断言 → With_Evidence）；只有 With_Evidence 才进入 8 类失真比对。**不可核实 ≠ 已判定失真类型**。

4. **上下文按钮**（仅召回界面）：拿不准时点证据句右侧「上下文 ▾」看前后各 3 句；它**实时读句表 CSV**，而证据句英文原文（`ev-en`）是检索时拷贝的快照。

5. **证据句是快照 vs 上下文实时**：如果将来修了句表（补截断句），`ev-en` 不会自动更新，但「上下文」里的「本句」会是补全后的版本。

6. **gold 句**（召回界面）：勾选证据句或手动填 `sentence_id`，两者双向同步；这是人工认定的正确召回句。

7. **「下一条未审核」**：召回界面按「自己名下 recall_reviewed=false」跳转，失真界面按「自己名下 human_verified=false」跳转。

8. **两阶段顺序**：文件2 是文件1 的「二次生成产物」。如果召回又改了（gold 句 / 段落变了），要**重新运行 `regenerate_analysis.py`** 再进失真审核，否则失真界面看到的证据/AI 分析是旧的。

---

## 7. 修改位置速查

| 想改什么 | 改哪里 |
|----------|--------|
| 失真分类、判据（正例/反例/决策树/不可标注区/易混对照） | `scripts/review_server.py` 里的 `TAXONOMY` 和 `GUIDE` 两个 dict（前端只是渲染） |
| 召回界面布局 / 交互 | `scripts/review_ui/recall.html` |
| 失真界面布局 / 交互 | `scripts/review_ui/distortion.html` |
| 共享样式 | `scripts/review_ui/style.css` |
| 上下文窗口大小（前后各几句） | `recall.html` 顶部 `CTX_WINDOW` 常量 |
| 可审核文件命名收敛 / 两界面路由 | `review_server.py` 的 `discover_reviewables()` / `detect_mode()` |
| 文件1 → 文件2 二次生成 | `scripts/regenerate_analysis.py` |
| 文件2 → 文件1 合并 | `scripts/merge_reviews.py` |

> 注意：`TAXONOMY` 是 `hallu/config.py` 的内嵌副本，权威定义改两边要同步。

---

## 8. 已知坑（必读）

- **只认两类命名**：`*_annotation_draft.json`（召回）与 `*_distortion_review.json`（失真）；其它命名不被扫描。
- **评测文件必须严格 JSON**：给人看的排版稿（含真实换行 / 尾逗号 / 注释）会报友好错误。
- **句表有 4 句被图注截断**（P001，sid 129 / 145 / 229 / 246）：正文被 Fig.3/4/6/7 + 页码 + 图注打断，续写粘到图注句尾部。**约束：sentence_id 不可动**（benchmark 草稿基于固定 sentence_id 构建），最多只补 `text` 末尾。
- **Windows 控制台中文乱码**：用 `PYTHONIOENCODING=utf-8` 或 `start_review.bat` 里的 `chcp 65001`。
- **regenerate_analysis.py / merge_reviews.py 是开发机脚本**：前者需 API key + 有道，不进离线审核 kit；离线审核人只拿召回文件或失真文件 + 对应界面。
