# 接手说明：候选 E 已通过判据 → 先研判下一步 → 再出执行计划 → 再执行

> 日期：2026-08-21
> 工作目录：/home/ps/papers/QuantMind
> 分支：agent/m2-evidence-reconstruction（领先 origin **16** 个提交，**全部未 push**）
> 最新三个提交：`475404e` 候选 E 结果报告 → `63fc617` 候选 E 实现 → `3c3de18` 预注册确认与两处修订
> 当前恢复点：**`M3-520-candidate-E-results-owner-review`**（`data/yeren_research/worklog.jsonl` 尾条，共 80 行）

本文件取代 `KickoffPrompts/M3-520-preregistration-2026-08-21.md`（该文件描述的预注册与候选 E 已全部完成，可删）。

**本 session 的工作分三个阶段，严格按顺序做，不得跳过或合并：**

1. **阶段一：研判下一步动作**——不写代码、不跑任何新计算，只读、只分析、只产出一份决策备忘录。
2. **阶段二：生成执行计划**——基于阶段一的决策，写一份可执行的工作单元计划（边界、停止条件、验收标准）。
3. **阶段三：执行**——按计划做，遵守本文件第 6–8 节的全部纪律。

**三个阶段各自的产出必须落盘为独立文档，不得只在对话里做决定不写文档。** 阶段一未完成、未经审视，不得进入阶段二；阶段二的计划未写清楚边界与停止条件，不得进入阶段三。

---

## 0. 三十秒摘要

520 套利战法走完了 M3 的完整链条：第一轮参数化（证据不足）→ A 语义复核（查出六成样本不属本卡语义）→ B 复权审计（定案后复权价）→ C 可成交性审计（执行约束量级小）→ 预注册（六组口径全部写死）→ owner 确认三项（`stop_days=3`、S8 按 (c)、费用按写的走）→ **候选 E 一次性 walk-forward 已跑出结果，样本内外均通过预注册判据**（主口径净收益均值 +3.44%／+4.32%，placebo 上尾 p=0.00498／0.00498）。

**判据通过不等于任务结束。** 结果报告 `docs/research/yeren-system/m3-520-candidate-e-results-2026-08-21.md` §7 明确列了待裁决项，其中"候选 E 通过之后下一步做什么"是一个**尚未被任何人决定**的开放问题，有至少四个方向，各自的依赖和阻塞条件不同。**这正是本 session 阶段一要解决的问题。**

---

## 1. 不可改变的总纲

### 1.1 唯一底线

**永禁真实券商程序化下单。** 系统只维护模拟盘。任何把研究结果接入真实券商、真实账户、真实订单或自动执行路径的请求都必须拒绝并上报。所有产物必须带 `real_broker_orders=false`。

### 1.2 主线次序

先复刻「全能的野人」的交易系统、交易逻辑与操作手法，再做系统性优化。预测命中率、收益择优、通用量化优化都不能抢在卡片语义与作者口径确认之前。**这条对阶段一的研判有直接约束力**：如果某个「下一步」选项本质上是「让 520 一张卡更精细」而不是「往前推进复刻其他战法卡片」，需要在研判里明确权衡，不能默认精益求精优先于广度覆盖。

### 1.3 反过度防御（四禁，最重要的行为准则）

1. 禁写没有实际用途的校验和/指纹/摘要；
2. 禁防御本项目不会出现的输入；
3. 禁用评分表/机械清单/复验循环替代人的判断；
4. 禁为想象的未来需求预建功能开关、迁移框架、兼容层。

判断句：**「这能检测到什么具体故障，我会因此做出什么不同的决定？」答不上来就不写。** 这条也约束阶段一的研判——不要为了"看起来严谨"而堆砌评分表去选下一步方向，用文字讲清楚每个选项的依赖、阻塞、代价、收益即可。

### 1.4 本阶段边界（三个阶段通用）

- 卡 8（520）维持「研究候选、不可执行」，**候选 E 通过判据不改变这个状态**；
- 不接飞书，不进模拟盘，不创建模拟订单；
- 不修改 `data/marketdata_pit/` 的既有档案；
- 不因为"结果好看"回改候选 E 已冻结的任何口径（预注册文档 §8 冻结后禁止事项原样适用）；
- **不 push**（`git push` 需 owner 明示授权）；
- 若阶段二的计划涉及写生产代码（`backend/playbook/`、模拟盘对接、飞书对接、任何券商路径），**直接拒绝并在决策备忘录里说明为什么现在不能做**——候选 E 通过判据远不构成上生产的条件（P1–P4 未冻结，见 §2.2）。

---

## 2. 已定案事实（阶段一直接引用，不要重新论证）

### 2.1 候选 E 结果（`docs/research/yeren-system/m3-520-candidate-e-results-2026-08-21.md`）

| 窗口 | 主口径 (c) 笔数 | 净收益均值 | 净胜率 | placebo 上尾 p | 判据 |
|---|---:|---:|---:|---:|---|
| 样本内 20150105–20221230 | 66,925 | +3.44% | 60.82% | 0.00498 | **通过** |
| 样本外 20230103–20260819 | 45,942 | +4.32% | 65.18% | 0.00498 | **通过** |

披露对照 (a) 两窗口均为负（−2.79%／−2.74%），证实第一轮"证据不足"是样本混入六成非本卡语义交易，不是信号本身无效。**判据通过只证明「交易级时点信号存在，值得继续研究」，不构成组合级盈利证明，不触发晋升 `stable_core`，不触发进入 `backend/playbook/`。**

### 2.2 仍未解决的缺口（决定了选项 (a) 是否可行）

- **P1–P4 全部未冻结**：证券池细则、仓位分母、并发上限、组合回撤定义。跨卡候选证据（总仓位 10%、套利不能重仓）**只登记未采信**——这是待 owner 裁决项之一（§5 第 2 条），不是研究者可以自行决定采信的。
- **S3（SMA/EMA）、S4（"即将上穿"精确判定）、S7（8–10 个点收益区间性质）仍未解决**，全部是作者未提及或无可观察定义的悬空项。
- **X1–X4 四类日线 PIT 不可识别项**（集合竞价、盘中停牌、盘中精确判定、部分成交/整手/容量）原样存在，候选 E 的执行口径是保守近似，不是对作者口径的验证。

### 2.3 待 owner 裁决的积压项（候选 E 结果报告 §7，原样照抄，不得自行决定）

1. 是否 push——分支领先 origin 现已 16 个本地提交，全部未 push。
2. 是否跨卡援引套利仓分母（总仓位 10%、套利不能重仓）到 520 的仓位分母 P1–P3。
3. 卡 8 是否还需改动——A 单元后已改第 4/5/6/8/9/10 条，owner 尚未逐条过目。
4. **候选 E 通过判据后，下一步做什么**——见 §3.1，本 session 阶段一要给出研判。
5. 是否采用实际券商费率重新预注册——研究者只能给市场惯例假设，owner 若有实际费率需重新预注册，不能事后静默替换候选 E 已跑出的结果。

**第 1/2/3/5 项明确是 owner-only 决策，本 session 不得自行拍板**，只能在决策备忘录里把选项和影响写清楚，供 owner 后续裁决。**第 4 项是研究方向选择，属于本 session 权限范围内**（比照本 session 此前 A/B/C/候选 E 阶段"owner 已概括授权『基于核心目标做出最正确选择、继续推进』"的先例），阶段一要对它给出有推理支撑的研判和推荐，然后在阶段二/三据此推进——但仍要在最终交付里明确标注这是研究方向选择而非 owner 已拍板的决定，owner 随时可以推翻。

---

## 3. 阶段一：研判下一步动作

### 3.1 核心问题与四个候选方向（原始列举，不预设权重）

结果报告 §7 第 4 条列了四个方向，逐一列出已知的依赖和阻塞条件（供研判引用，不是结论）：

**(a) 补齐 P1–P3 仓位框架后做组合级前向验证**
- 依赖：跨卡 10% 分母是否可用（§2.3 第 2 条，owner-only，未裁决）；即便可用，520 专属的证券池范围、并发上限全语料仍是空白（§2.2）。
- 代价：这是一次新的证据挖掘 + 框架设计工作，规模不小于 M3 已完成的 A/B/C/E 任一单元。
- 收益：候选 E 只证明了交易级时点信号，若这一步成立，是唯一能真正回答"能不能实盘用"的路径。
- **当前状态：至少部分阻塞于 owner 未裁决的第 2 条积压项。**

**(b) 先解决 S3/S4/S7 剩余未冻结项**
- 依赖：无外部阻塞，全语料检索即可推进（沿用 A 单元的方法）。
- 代价：中等，预计一个工作单元量级；但 S4（"即将上穿"精确判定）大概率没有可观察定义，检索可能空手而归（诚实登记"仍不可识别"也是合法产出）。
- 收益：收窄执行代理与作者口径的差距，但候选 E 已经在**当前代理**下通过判据，S3/S4/S7 解决与否短期内不改变已发布的结果。

**(c) 维持候选，让位给其他战法卡片的 M3 验证**
- 依赖：无外部阻塞。M2 已产出 18 张卡片，卡 7/9–18 均未进入 M3；第三批候选（P1 硬逻辑／P5 财报两步读法／S3 题材容量下钻／N2 直接点名利空退出／T2 ETF 表达／B4 清仓后轻仓试错重入）已在 `KickoffPrompts` 历史记录中列过队列（见 `docs/research/yeren-system/m2-owner-decision-analysis-2026-08-20.md` 与 `playbook-cards-batch2-2026-08-20.md` 的待验证清单）。
- 代价：需要重新进入 M2 收尾未完成的"第三批候选"评估，工作量与 M3-520 全链路相当或更大（因为要从头做语义复核，520 已经把这条链路走完一次，有方法论可以复用）。
- 收益：直接对齐 §1.2 主线次序（先复刻广度，再做深度优化）；520 已经是全部 18 张卡片里**最先走完 M3 全链路**的一张，继续深挖收益递减，转向广度符合"先复刻系统"的既定优先级。

**(d) 用实际券商费率重新预注册**
- 依赖：**完全阻塞于 owner 提供实际费率**（§2.3 第 5 条），研究者不能替 owner 猜测。
- 代价：若 owner 提供费率，重跑候选 E 的成本很低（改 `CostModel` 的几个参数，重新预注册后重跑，量级是几分钟）。
- **当前状态：不是本 session 能启动的方向，只能在决策备忘录里向 owner 提出请求。**

### 3.2 阶段一必须回答的问题（逐条落笔，不能只在对话里过一遍）

1. **对 (a)(b)(c)(d) 四个方向，each 用一句话总结"现在能不能启动、为什么"**——严格依据 §2.2/§2.3 的已定案事实，不要引入新假设。
2. **给出推荐方向及理由**——如果推荐 (a) 或 (d)，必须说明如何绕开或部分推进其被阻塞的依赖项（例如"先做 (a) 里不依赖跨卡分母的那部分"）；如果推荐 (c)，必须点名具体从哪张卡开始、为什么是那张（词频/证据强度/M2 遗留清单里的优先级）。
3. **是否存在"组合方案"**——例如"这个 session 内先做 (c) 的一小步（复核下一张卡的语义）+ 把 (a)(d) 的阻塞条件写成给 owner 的明确请求"，不必强制单选。
4. **本 session 自己不能拍板的四项（§2.3 第 1/2/3/5 条）分别需要向 owner 提出什么具体问题**——不是笼统"请 owner 决定"，要写成 owner 可以直接回答的具体问题（参照本次候选 E 之前 `KickoffPrompts/M3-520-preregistration-2026-08-21.md` §10 的写法，owner 回复"stop_days 选3,S8按(c),费用先按你写的走"就直接解除了三项门槛——这是可复用的好模式：把决策点收窄成 owner 一句话能回答的形式）。

### 3.3 阶段一产出

写一份决策备忘录，文件名建议 `docs/research/yeren-system/m3-post-candidate-e-next-step-decision-2026-08-21.md`（跨日执行用实际日期），结构参考本项目已有的决策备忘录（如 `m3-520-next-research-decision-2026-08-21.md`）：结论先行 → 逐方向分析（依赖/代价/收益）→ 推荐 → 给 owner 的具体问题清单。**这份文档写完、经过自我审视（读一遍，检查有没有绕开 §2.2/§2.3 的已定案事实、有没有堆砌评分表代替判断）之后，才能进入阶段二。**

---

## 4. 阶段二：生成执行计划

基于阶段一选定的方向（含"组合方案"的情况），写一份可执行的工作单元计划。**格式沿用本项目已经验证过的模式**（参考 `m3-520-next-research-decision-2026-08-21.md` §6"选定工作单元规格"一节的写法）：

- 固定输入、固定范围（只做这些）、明确输出、停止条件、不做事项——**五要素缺一不可**。
- 如果阶段一选的是 (c)（转向另一张卡），计划要点名具体是哪张卡、语义复核要检索哪些关键词/时间段、预计产出（卡面修订建议 + 语义定案表，格式参照 `m3-520-semantic-audit-2026-08-21.md`）。
- 如果阶段一选的是 (b)（S3/S4/S7），计划要点名具体检索策略（沿用 A 单元"全语料交叉检索"的方法，见该报告 §4）。
- 如果阶段一选的是"部分推进 (a)"，计划必须明确划出"不依赖跨卡分母就能做的部分"和"依赖 owner 裁决、本次不做"的部分，不能含糊。
- 计划文档写完后，检查一遍：**是否有任何一步隐含了"先斩后奏"式的生产代码改动、飞书/模拟盘接入、真实下单**——有则删除，改成"标记为待 owner 授权后的下一阶段"。

文件名建议 `docs/research/yeren-system/m3-post-candidate-e-execution-plan-2026-08-21.md`。

---

## 5. 阶段三：执行

按阶段二的计划做。**通用纪律（不因方向不同而改变）：**

- 引用原话只由 `sentences[start:end+1]` 无分隔拼接，`observation` 按 `aweme_id` 取最高版本，用 `evidence_quote.py`，不手打；
- 事实 / 解释 / 研究代理 / 待 owner 决策**分栏写**；
- 数字没有作者来源时标记 **researcher-added**；
- 不修改既有 observation/hypothesis 以迎合新结论；不修改 `data/marketdata_pit/`；
- 面向 owner 的回复用中文；代码、注释、commit message 用英文；conventional commits；
- 若涉及新代码：TDD（先写测试）；`ruff check backend/ scripts/` 必须干净；`FEISHU_INTERACTIVE_ENABLED=false pytest -q` 全过；
- **codex 门**（第 6 节展开）；
- commit 落本地；**不 push**；
- worklog 追加一条（含 inputs/outputs/findings/`real_broker_orders=false`/`resume_from`）；
- memory 更新（`MEMORY.md` 索引行 + `project-midterm-rearch-yeren-playbook-2026-08-12.md` 详情）。

---

## 6. codex 门（代码任务强制，docs-only 豁免）

如果阶段三只写文档（语义复核、决策备忘录、执行计划本身），**不需要 codex review**。若写了任何代码（例如阶段一选了 (b)/(c) 中需要新脚本做词频检索或新的规则参数化验证）：

```bash
# cycle 1：审未提交改动，必须后台跑（8–20 分钟）
setsid nohup bash -c "timeout 2400 codex review --uncommitted > <scratchpad>/out 2>&1 < /dev/null" &
disown
# 复验（prompt 含引号/中文写进文件再用 "$(cat file)" 传入）
codex exec --sandbox read-only "复验上一轮的 findings 是否已修复，只报告缺陷并按 P0/P1/P2/P3 分级"
```

**本轮（候选 E 实现）踩到的经验，直接适用：**

- **不要用 `pgrep -f <脚本名>` 做等待循环**——监控进程自己的命令行也含该字符串，会自匹配导致永不退出；改用 `while [ -d /proc/<pid> ]; do sleep 5; done`；
- **codex 的 findings 大概率是真 bug，不是噪声**。候选 E 实现阶段 codex review + 4 轮复验累计挖出 **5 个 P1 + 9 个 P2**，全部证实是真实缺陷（不是过度防御式的假警报），包括一次代码与预注册文档本身自相矛盾（§1.2 字面意思实现了已被 §1.3 否决的选项，这类"文档内部矛盾"是 codex 复验才抓出来的，人工写的时候没意识到）；
- **不要在 codex review 后台跑的过程中编辑被审查的文件**——本轮出现过一次真实的竞态（复验进程读到的是修复前的代码状态，导致复验结果对不上当时文件内容），正确做法是：改完、跑完测试、再启动下一轮 codex，中途不动代码；
- **codex 的复验不是走过场，收敛速度会越来越慢但仍可能挖到东西**——本轮从第一轮 1P1+4P2，到第四轮还挖出 1 个新 P1（约束预取的链式漏洞）+ 1 个新 P2（MAE 口径），第五轮才收敛到只剩 1 个文档一致性 P2。**不要在前两轮"看起来差不多了"就提前收工**，除非连续两轮复验都是"无剩余缺陷"。

---

## 7. 开工检查（逐条跑，核对预期输出）

```bash
cd /home/ps/papers/QuantMind
git status -sb
git branch --show-current
git log --oneline -3
tail -1 data/yeren_research/worklog.jsonl | python3 -c \
  "import sys,json;d=json.load(sys.stdin);print(d['work_unit'],'|',d['status'],'|',d['resume_from'])"
```

预期：

```
## agent/m2-evidence-reconstruction...origin/agent/m2-evidence-reconstruction [领先 16]
agent/m2-evidence-reconstruction
475404e research: report 520 candidate-E walk-forward results
63fc617 research: implement 520 candidate-E walk-forward per frozen preregistration
3c3de18 research: record owner confirmation and two pre-run corrections to 520 preregistration
M3-520-candidate-E-walkforward | completed | M3-520-candidate-E-results-owner-review
```

工作树应干净。

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q tests/yeren_research/    # 预期 105 passed
$PY/ruff check backend/ scripts/                                        # 预期 All checks passed!
```

核对候选 E 结果关键数字（阶段一分析时会引用）：

```bash
python3 -c "
import json
d = json.load(open('data/yeren_research/inventory/m3-520-candidate-e-walkforward-2026-08-21.json'))
for name in ('in_sample', 'out_of_sample'):
    w = d['windows'][name]
    print(name, w['primary_cohort_s8_c']['trades'], w['primary_cohort_s8_c']['mean_net_return_pct'],
          w['placebo']['upper_tail_p_value'], w['judgment_criteria']['pass'])
"
```

预期：

```
in_sample 66925 3.4448573090790426 0.004975124378109453 True
out_of_sample 45942 4.32347000134882 0.004975124378109453 True
```

---

## 8. 先读的文件（按顺序）

1. `CLAUDE.md`、`AGENTS.md` —— 反过度防御四禁与主线次序
2. `docs/research/yeren-system/m3-520-candidate-e-results-2026-08-21.md` —— 候选 E 结果，§5/§7 是阶段一的直接输入
3. `docs/research/yeren-system/m3-520-preregistration-2026-08-21.md` —— 预注册全文，尤其 §9（追加确认与修订）与 §7（积压项现状）
4. `docs/research/yeren-system/m3-520-semantic-audit-2026-08-21.md`、`m3-520-adjustment-audit-2026-08-21.md`、`m3-520-executability-audit-2026-08-21.md` —— A/B/C 三单元，若阶段一选 (b) 需要复用其方法
5. `docs/research/yeren-system/playbook-cards-batch2-2026-08-20.md` —— 卡 7–12，若阶段一选 (c) 的候选起点之一
6. `docs/research/yeren-system/m2-owner-decision-analysis-2026-08-20.md` —— 若阶段一选 (c)，第三批候选（P1/P5/S3/N2/T2/B4）的证据起点在此
7. `docs/research/yeren-system/base-v3-spec-2026-08-20.md` —— 证据分类定义，任何方向都可能用到
8. `scripts/yeren_research/m3_520_candidate_e.py` —— 候选 E 实现，供理解已有方法论（六条执行约束、S8 事后归类、费用模型的写法可复用到其他卡片）

---

## 9. 交付与停止条件

本 session 至少要留下：

1. 阶段一决策备忘录（`m3-post-candidate-e-next-step-decision-2026-08-21.md` 或实际日期）；
2. 阶段二执行计划（`m3-post-candidate-e-execution-plan-2026-08-21.md` 或实际日期）；
3. 阶段三的实际产出（依计划而定：卡片语义复核报告 / 词频检索结果 / 新脚本+测试等）；
4. 一条新的 worklog JSONL 记录（含 inputs/outputs/findings/`real_broker_orders=false`/`resume_from`）；
5. 本地 commit（conventional commit，英文 message，**不 push**）；
6. 更新 memory：`MEMORY.md` 首条 hook + `project-midterm-rearch-yeren-playbook-2026-08-12.md` 详情。

**§2.3 中标注 owner-only 的四项（push／跨卡 10% 分母／卡 8 过目／实际券商费率）本 session 不得自行决定，必须在阶段一决策备忘录里转化为 owner 可以直接回答的具体问题。**

无论阶段一/二/三选了哪个方向，都不要进入 `backend/playbook/` 生产路径、模拟盘、飞书或真实券商路径。卡 8 维持「研究候选、不可执行」。
