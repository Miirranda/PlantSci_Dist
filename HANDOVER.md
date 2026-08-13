还需要根据前面的讨论明确后续的Benchmark annotation评测的解耦方案

结合前面「两轨评测」和「Weak 灰区」的讨论，把后续 Benchmark 标注与打分的解耦方案写清楚。

后续 Benchmark 不要拆成两套金标文件。解耦做在三处：标注字段各司其职、评测入口分轨读、分类实验用不同证据输入。

0. 目标一句话
同一条 sample、同一份 *_benchmark.json，要能分别回答三个问题，互不绑架：

问题	看什么	不看什么
检索有没有把该找的句找上来？
gold_retrieval vs 系统 top-k
失真细类对不对
以论文为据，转述有没有改变科学含义？
gold_classification（在金标证据上）
系统检索是否漏检
整条流水线好不好？
检索 top-5 → 分类 的端到端分
不要当成「纯分类能力」
当前代码是：字段已分桶，评测脚本仍绑在一起跑，分类输入绑死系统 top-5。下面方案就是把后两层拆开。

1. 标注解耦（金标怎么填）
审核顺序保持：先检索金标，再失真金标。两步用不同问题，禁止用分类结果去「修正」检索金标，也禁止因检索差就改细类。

1.1 RAG 轨 — gold_retrieval
只回答：若人工能从论文里找到对照句，是哪些 sentence_id？

字段	规则
sentence_ids
真正用于对照的句；Weak 也可保留「主题相关句」（证明不是检索全空）
is_answerable
能否充分核实核心断言。With_Evidence → true；Weak / No → false
池外更优句可以进金标（在 analysis.rag_review 注明），但评测只认 ID 列表。

不要：因为「细类是 omission」就把某句从检索金标里删掉；检索金标是「对照所需的论文句」，不是「分类标签的附件」。

1.2 证据维度 — evidence_level（桥，但独立打分）
只回答：以本篇论文为唯一依据，能不能做细粒度比对？

取值	操作定义	检索金标	失真细类
With_Evidence
至少一句覆盖核心断言的对象+关系
非空，is_answerable=true
进入 8 类 / no_distortion
Weak_Evidence
有相关句，但既不能打包票说对、也不能打包票说错
可非空，is_answerable=false
不标 8 类，has_distortion=null
No_Evidence
连主题相关句都没有
必须空，is_answerable=false
同上
这是前面讨论的灰区正式落点：Weak 属于证据维度，不是失真细类，也不是「幻觉」。

导出/审核时应做一致性检查（尚未写进脚本，后续要加）：

No ⇒ ids 空 ∧ answerable false ∧ primary_label null
Weak ⇒ answerable false ∧ primary_label null（ids 允许非空）
With ⇒ ids 非空 ∧ answerable true ∧ 必有 primary_label（8 类之一或 no_distortion）
1.3 失真轨 — gold_classification（仅 With_Evidence）
只回答：在已经能比对的前提下，转述如何改变科学含义？

用 distortion-v0.1：has_distortion + primary_label + 可选一条 secondary_label。
N0–N5 → no_distortion。数值/纯反义无法归入 8 类 → uncovered_phenomenon，不要硬塞。

不要：因为系统 top-5 没找到句，就把本应是 relation_substitution 的样本改成 Weak。Weak 只描述「论文本身核不实」，不描述「检索没找着」。检索没找着应写在 analysis.unsupported_diagnosis=likely_retrieval_miss，金标证据用人工从句表补的 ids，分类仍按金标句来判。

1.4 一条样本的三种合法形态
A. 可核且无失真
   retrieval: ids 非空, answerable=true
   level: With_Evidence
   class: has_distortion=false, no_distortion
B. 可核且有失真
   retrieval: ids 非空, answerable=true
   level: With_Evidence
   class: has_distortion=true, 8 类之一
C. 不可充分核实（灰区 / 无据）
   retrieval: Weak 可有相关 ids；No 必须空; answerable=false
   level: Weak_Evidence | No_Evidence
   class: has_distortion=null, primary_label=null
复合句：先拆分句写在 evidence_judgement；整句 evidence_level 取最弱可核等级（任一核心断言无法核实 → 整句至少 Weak）。只有整句 With 才给细类。

2. 评测解耦（分怎么打）
继续一份 *_benchmark.json。评测脚本做三种读法，而不是两套文件。

2.1 三种实验设置
设置	检索预测	分类输入证据	打哪些分	回答的问题
E2E（现状）
系统 top-k
系统 top-5
两轨都打，但分类分含级联
整条流水线
Retrieval-only
系统 top-k
不跑分类
只 Hit/Recall/P@k
纯检索
Oracle-class
不评检索
gold_retrieval.sentence_ids 对应原文
只 evidence_level + 失真标签
纯分类
这是解耦的关键一刀：E2E 分类差，可能是检索差；Oracle 分类差，才是分类器/标签问题。

2.2 CLI 建议（尚未实现）
# 只评检索
python scripts/evaluate_benchmark.py --track retrieval \
  --benchmark ... --predictions outputs/.../classification.json
# 只评端到端分类（输入=系统 top-5，现状）
python scripts/evaluate_benchmark.py --track classification \
  --benchmark ... --predictions outputs/.../classification.json
# 纯分类：预测来自 oracle 跑出来的 classification_oracle.json
python scripts/evaluate_benchmark.py --track classification \
  --benchmark ... --predictions outputs/.../classification_oracle.json
# 默认 all：报告里必须分 section，禁止合成一个「总分」
python scripts/evaluate_benchmark.py --track all ...
行为约定：

--track retrieval 缺分类字段不扣分；缺 sentence_id 才算缺预测。
--track classification 缺检索字段不扣分。
现在「整条 sample 无预测 → 两轨都丢」要改掉。
severity 系统若为空，该指标标 n/a，不要当 0。
旧金标 hallu-9class 与新预测 distortion-v0.1 混评时，level2 字符串对不齐是版本差；可看 evidence_level / has_distortion，并在报告头打印两边 taxonomy_version。
2.3 指标分桶（报告结构）
summary.retrieval          # 所有 sample
  Hit@k, Recall@k, P@k
  可选分层: answerable=true / Weak / No
summary.evidence_level     # 所有 sample
  accuracy；confusion（3×3）
summary.classification_e2e       # 仅 With_Evidence 金标上算细类
summary.classification_oracle    # 同上，但预测来自 gold 证据
  level1 accuracy
  level2 accuracy
  has_distortion accuracy
  secondary micro-F1
  多数类基线、macro-F1（P001 几乎全 no_distortion，必须报基线）
细类准确率的分母：不要包含 Weak/No。否则「正确输出 null」和「细类打对」混在一起。Weak/No 只进 evidence_level 混淆矩阵。

检索空金标规则保持：金标空且预测空 → 满分；金标空预测非空 → 0（惩罚乱推）。Weak 有相关 ids 时按普通集合命中算，不要求 top-k 能「证成」claim。

2.4 运行时如何产出 Oracle 预测（尚未实现）
scripts/run.py --evidence-source gold \
  --benchmark data/annotations/P00x/P00x_A00y_benchmark.json \
  --from-step classify
→ outputs/.../classification_oracle.json
adapter 把 gold_retrieval.sentence_ids 从句表/evidences.jsonl 填成 evidence_sentences，再进 hallu.classifier。不改分类器、不改金标 schema。

3. 和「幻觉 / 失真」新体系怎么对齐
旧规则把 Weak/No 算进 is_hallucination=true，会把「检索失败 / 不可核实」算成分类错误。新规则切断这条：

检索轨：不管细类，只看 ids。
证据轨：Weak/No 是合法金标，系统输出 Weak/No 算对。
失真轨：只在双方都是 With_Evidence 时比 8 类。
因此 Benchmark 解耦 = 三维，不是两维：

RAG 质量     ↔ gold_retrieval
可核性        ↔ evidence_level
转述操作类型  ↔ primary_label（仅可核样本）
旧 9 类金标未 remap 前：E2E/Oracle 的 level2 准确率不要当正式论文数字；检索轨可以照常报。

4. 落地顺序（建议写进 HANDOVER）
P0 — 标注协议（不改已有 JSON，只约束新标）
新草稿（P002 重标、以后样本）按 §1 三种形态填。export 加一致性校验，失败则警告不阻断调试。

P1 — evaluate_benchmark.py --track
分轨累计、缺预测按轨跳过、报告分 section、Weak/No 不进细类分母、打印 taxonomy_version。
验收：同一份 A001 金标，三次命令分别只出检索 / 只出分类 / 全部。

P2 — Oracle 分类路径
--evidence-source gold → classification_oracle.json。主文表格应同时有 E2E 与 Oracle 两列分类分。

P3 — 增强（有精力再做）
检索分层（answerable vs Weak）；池内/池外金标占比；复合句 sub_claims；MRR/nDCG 作辅指标。

明确不做： 拆 *_retrieval_benchmark.json + *_classification_benchmark.json；为解耦去改 arag-main 内部 eval。

5. 读数时怎么用（避免再踩坑）
你看到	应解释为
Hit@5 高、Oracle 细类高、E2E 细类低
分类器行，检索级联差
Hit@5 低、Oracle 也低
两边都有问题，先看 Oracle 混淆矩阵
evidence_level 把 With 打成 Weak/No
检索不足或分类器过保守；对一下 Oracle（金标句在手仍打 Weak → 分类器问题）
Weak 样本 Recall 还可以
正常：相关句找回了，但论文仍核不实
旧金标 accurate vs 新预测 no_distortion
版本差，不要写进正式结果
收束： 标注上检索 / 可核性 / 失真三类字段分开填；评测上 Retrieval-only、Oracle-class、E2E 三套设置分开报。Weak 是可核性灰区，不是失真标签，也不是拆文件的理由。当前缺的是 --track 和 oracle 跑批，不是新的 benchmark 文件格式。
