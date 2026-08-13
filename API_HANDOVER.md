标注草稿：从聊天框改为 API 调用（交接）
日期：2026-08-13
背景：原流程把 draft_from_pairs.md 整段贴进「DeepSeek 驱动的 Claude」聊天框，让模型读 JSONL 并一次吐 10 条完整 JSON。data/annotations/P001/A001_draft_C1_C10.json 为空，属于输出过长/截断/写盘失败。
结论：正式出草稿改走脚本调 API；聊天只用于改提示词和修难句。

1. 为什么换
方式	问题
聊天一次 10 条
上下文塞满 54 行 pairs；输出要抄全部 system_retrieval；易截断
聊天读本地文件
即使用工具，也常多读未纳入 limit 的行，token 浪费
同一模型 API
可只发 N 条、去重字段、校验失败只重一批
费用差的不是「聊天 vs API」这个入口，而是每次塞多少字、失败要不要整批重来。套餐聊天现金上可能显得便宜，token 往往更高。

2. 批大小（已拍板）
--batch-size	质量	费用	用途
1
最好
系统提示词 × 条数
失败重试
3（默认）
接近逐条
提示词约省 60–70%
生产默认
5
后几条易糊
更省
不建议默认
10
差、易截断
表面最省
禁止
失败的那一批 自动降为 1 再试。
用 Claude 且开 prompt cache 时，可再改为默认 1。通义 qwen-plus 缓存不确定，故默认 3。

粗算（10 条冒烟，提示词 ~4k，每条输入 ~2.5k，不抄证据时每条输出 ~2k）：

逐条 ≈ 85k token
每批 3 ≈ 70k
若让模型抄 system_retrieval，输出翻倍，批越大越容易顶满。

3. 五条硬原则
Python 拷贝检索，模型只判。 user 仍带 classify_evidences / review_evidences 供阅读；写出的 system_retrieval 用输入原样覆盖。
模型只返回 {"samples":[...]}，不要文件头。schema_version / taxonomy_version 由脚本写。
不要把 evidences、paper_sentences 发给模型（与 review 重复）。
校验通过才写入；不合法不落盘。
按 sample_id 续跑，已有条目跳过。
标签：distortion-v0.1。权威：README_1.md v3.0 + 两份规范 md；生成时以 draft_from_pairs.md 提示词为准。

4. 脚本（已入库）
路径：scripts/generate_draft_from_pairs.py
提示词：data/annotations/prompts/draft_from_pairs_api.md（紧凑输出；user 只发 review 10 条 + classify ids，不重复 top-5 正文）
客户端：hallu.config.ensure_env + api_client.QwenClient / build_messages（arag-main/.env 的 QWEN_*）。

python scripts/generate_draft_from_pairs.py `
  --pairs outputs/P001/A001/claim_evidence_pairs.jsonl `
  --output data/annotations/P001/P001_A001_annotation_draft_C01_C10.json `
  --paper P001 --article A001 --source-type high_quality `
  --limit 10 --batch-size 3
参数	含义
--pairs
claim_evidence_pairs.jsonl
--output
草稿 JSON
--paper / --article / --source-type
写入每条 sample
--limit
正整数 N / all
--after C10
从该 id 之后续跑
--batch-size
默认 3
--model
默认 QWEN_MODEL（qwen-plus）
--max-retries
默认 2
--dry-run
只打印将发送的批，不调 API
不要用：scripts/generate_annotation_draft.py（旧 9 类、非 API）、hallu/classifier.py（无 gold 检索 + analysis）。

5. 提示词与报文
system = draft_from_pairs.md 围栏内正文，需改三处（实现时改副本或脚本内拼接，可暂不改 md 里给人类看的「读文件」说法）：

数据在 user，不要去读磁盘 JSONL。
只输出 {"samples":[...]}，不要 system_retrieval（脚本覆盖）。
保留：三种形态、slug 表、决策树、analysis 写法。
user（每批）：

{
  "paper_id": "P001",
  "article_id": "A001",
  "article_source_type": "high_quality",
  "items": [
    {
      "claim_id": "C01",
      "claim_zh": "...",
      "classify_evidences": [{"rank": 1, "sentence_id": 3, "text": "..."}],
      "review_evidences": [{"rank": 1, "sentence_id": 3, "text": "..."}]
    }
  ]
}
temperature=0，chat_json。max_tokens：3 条一批建议 ≥ 8000。

6. 程序流程
读 JSONL → limit / after 切片
→ 若 output 已存在，收集 sample_id，跳过
→ 每批 3 条：
     调 API
     校验（§7）
     失败 → 该批拆成逐条再试；仍失败记 errors，continue
     成功 → 填 system_retrieval、sample_id、human_verified=false
     gold_retrieval.evidences[].text 用输入原文按 sentence_id 回填
→ 写顶层：schema_version=1.2，taxonomy_version=distortion-v0.1
→ review_queue.must_review_sample_ids = needs_manual_review 的 id
7. 校验（不通过则重试）
JSON 可解析；samples 条数 = 本批；claim_id 对齐。
primary_label.level2 ∈ 8 类 ∪ {no_distortion}，或 Weak/No 时 primary_label=null。
形态：
A With + 无失真：ids 非空，is_answerable=true，has_distortion=false，no_distortion
B With + 有失真：ids 非空，is_answerable=true，has_distortion=true，8 类之一
C Weak/No：is_answerable=false，has_distortion=null，primary_label=null；No 的 ids 必须空
拒绝旧标签：accurate、fact_addition、certainty_amplification、scope_generalization、mechanism_simplification、context_stripping、numerical_distortion、causality_distortion、semantic_contradiction（作为 primary_type / level2）。
8. 冒烟验收
--limit 3 --batch-size 3：一次调用是否得到 3 条合法 sample。
人工看 C01：是否 A/B/C 之一、有无旧标签、system_retrieval 是否与 pairs 一致。
--limit 10 --batch-size 3 → ..._C01_C10.json。
若后两条明显更差，对失败 id 用 --batch-size 1 续跑（已成功的会跳过）。
聊天框仅用于：改 draft_from_pairs.md、修 review_queue 里的难句。聊天里每次只贴 1 条 pair，不要整份 JSONL。

9. 与上下游
上游	outputs/<P>/<A>/claim_evidence_pairs.jsonl（arag 已跑通则不必重检）
本步
API 出 *_annotation_draft_Cxx_Cyy.json
下游
build_readable.py 翻译+排版 → 人工审 → export_benchmark.py
系统预测
run.py --from-step classify（与金标草稿独立）
评测解耦（--track / oracle-evidence）未实现，不挡出草稿；正式论文数字等金标审核后再评。

10. 实现清单（给写代码的同学）
新增 scripts/generate_draft_from_pairs.py（CLI + 批处理 + 校验 + 续跑）
从 md 抽 system 或脚本内维护 API 专用 system（禁止读盘、禁止抄检索）
用 qwen-plus 跑 P001 A001 --limit 3 再 --limit 10
README 或 draft_from_pairs.md 头上加「生产请用脚本」一句
可选：--provider deepseek（先不要作为默认）
不要做： 改 arag-main 检索 Agent 提示词；自动 remap 已有 P001 旧 9 类金标；一次 API 10 条。

一句话： 默认每批 3 条调 Qwen；模型只打标签和写 analysis；检索字段脚本拷贝；失败降到逐条。聊天不再承担生产出数。