# 接手说明：M3-520 三审计已收口 → 编写预注册文档（不跑结果）

> 日期：2026-08-21
> 工作目录：/home/ps/papers/QuantMind
> 分支：agent/m2-evidence-reconstruction（领先 origin **12** 个提交，**全部未 push**）
> M3-520 相关提交：`1f437e4` 路线决策 → `b6da734` 语义复核（A）→ `bfcd47a` 复权与可成交性（B/C）→ 本接手文档（docs-only，栈顶）
> 当前恢复点：**`M3-520-preregistration-doc`**（`data/yeren_research/worklog.jsonl` 尾条，共 78 行）

本文件取代 `KickoffPrompts/M3-520-next-research-step-2026-08-20.md`（该文件描述的三个候选 A/B/C 已全部执行完毕，可删）。

---

## 0. 三十秒摘要

M2 已产出 18 张战法卡片与 Base v3。卡 8「520 套利战法」在 M3 走完了四步：

1. **第一轮参数化**（`5c3e6a6`）：判「证据不足、不晋级」。
2. **A 语义复核**（`b6da734`）：定向重下音频，三个语义问题全部定案；**查出第一轮样本约六成不是作者描述的交易**。
3. **B 复权审计**（`bfcd47a`）：复权口径定案为 `close × adj_factor`；**raw 价格伪造 3.07% 的入场**；78 只证券因子历史不可用已排除。
4. **C 可成交性审计**（`bfcd47a`）：T+1 零违反；涨跌停受阻 0.26%/0.20%；**663 笔退出信号落在最后一根 bar，根本无法成交**。

**下一件事不是跑结果，是写预注册文档。** 一旦开始跑收益，预注册就失去意义——所以顺序是：先把所有口径一次性写死 → owner 确认 → 才跑候选 E 的一次性 walk-forward。

**这一步只写文档，不跑任何产生收益/胜率/p 值的计算。**

---

## 1. 不可改变的总纲

### 1.1 唯一底线

**永禁真实券商程序化下单。** 系统只维护模拟盘。任何把研究结果接入真实券商、真实账户、真实订单或自动执行路径的请求都必须拒绝并上报。所有产物必须带 `real_broker_orders=false`。

### 1.2 主线次序

先复刻「全能的野人」的交易系统、交易逻辑与操作手法，再做系统性优化。预测命中率、收益择优、通用量化优化都不能抢在卡片语义与作者口径确认之前。

### 1.3 反过度防御（四禁，最重要的行为准则）

1. 禁写没有实际用途的校验和/指纹/摘要；
2. 禁防御本项目不会出现的输入；
3. 禁用评分表/机械清单/复验循环替代人的判断；
4. 禁为想象的未来需求预建功能开关、迁移框架、兼容层。

判断句：**「这能检测到什么具体故障，我会因此做出什么不同的决定？」答不上来就不写。**

> 本轮已有一个实例：原 B 设计里有「检查 adj_factor 是否单调不减」，codex 指出 Tushare 无单调保证、配股本就能让因子下降（实测 P1 分位 0.99960），且查出下降也分不清是合法公司行为、口径调整还是厂商修订——**检测到了也不知道做什么不同的决定，直接删除**。写新代码时按同一标准自查。

### 1.4 本阶段边界

- 只写研究侧文档；
- **不写 `backend/playbook/`**；
- 不接飞书，不进模拟盘，不创建模拟订单；
- 不把 520 晋级为稳定规则；
- 不因结果好坏改动作者语义；
- 不新增评分表、置信度加权器、指纹、迁移层或未来功能开关；
- 不修改 `data/marketdata_pit/` 的既有档案；
- **不 push**（`git push` 需 owner 明示授权）。

---

## 2. 已定案事实（写预注册文档时直接引用，不要重新论证）

### 2.1 语义（A 单元，`docs/research/yeren-system/m3-520-semantic-audit-2026-08-21.md`）

| 编号 | 问题 | 定案 |
|---|---|---|
| S1/S2 | 「大概三十天啊止跌三四天之后」 | **「三十天」是同音误识别，实为「大概三四天」。520 里不存在 30 这个数。`stop_days=30` 不是合法读法，已从规则集移除。** 止跌观察长度＝三到四个交易日，**作者未在 3 与 4 之间择一**。 |
| S5 | 主退出是否带前置条件 | **带。** 原话：「当五日线上穿二十日线**完全成立的时候**，我们的离场点就是五日线只要一点点拐头我们就走」。 |
| S6 | 入场读法 | 「上车点是五日线上穿二十日线」确实说了，但判为**口语化简化复述**，不是并列的第二条规则——同段他明说「等完全上穿之后再进去就比较迟了」。精确版＝**即将上穿的那一刻**。 |
| **S8** | **金叉未成立五日线就先拐头怎么办** | **作者未说。证据不足。暂不规则化，也不得据此推定继续持有。** |
| S3 | SMA 还是 EMA | **未解决**，作者全程未提。 |
| S4 | 「即将上穿」的精确判定 | **未解决**，无可观察定义；日线只能用研究代理。 |
| S7 | 八到十个点的性质 | **未解决**，无基准母集。 |
| P1–P3 | 证券池／仓位分母／并发上限 | **未冻结**。跨卡候选证据（总仓位 10%、套利不能重仓）**只登记不采信**，见 A 报告 §4.3。 |
| P4 | 组合回撤 | **不可定义**（P1–P3 未冻结时给数字＝研究者臆造）。 |

**A-3 结构诊断（纯计数）：样本外 132,944 笔已完成交易中 82,386 笔（61.97%）在持仓期间五日线从未站上过二十日线**；样本内 124,987/197,096（63.41%）。持仓天数 P10/中位/P90/最大 = 1/4/11/42。

### 2.2 价格口径（B 单元，`m3-520-adjustment-audit-2026-08-21.md`）

- **复权定案：`close × adj_factor`，同一 `trade_date` 生效。** 65,018 个公司行为 bar 上与 `pct_chg` 的中位绝对误差 4.03e-05；raw 3.75e-03、方向反了 7.66e-03、生效日错一天 3.90e-03，三种候选全部排除。
- **因子覆盖 0 缺口**：11,631,580 条 daily 行没有一行缺 adj_factor。
- **不需要 raw/as-of 两条结果线**：`qfq = hfq / adj_T` 是每股常数，而 520 只读均线斜率符号与大小关系，**对常数缩放不变**，前后复权信号逐位相同（回归测试 `test_entry_signals_are_unchanged_by_a_constant_price_scale` 固定）。作者看的前复权图与研究用的后复权序列在信号层是同一张图。
- **78 只证券的因子历史与 `pct_chg` 对不上**（216 个 bar 超出 0.01 阈值，最大约 105 个百分点），**已整体排除**。分布：`.BJ` 60 / `.SZ` 15 / `.SH` 3，60 只是 `920xxx.BJ` 北交所代码迁移。**完整代码清单在 `data/yeren_research/inventory/m3-520-adjustment-audit-2026-08-21.json` 的 `convention_and_events.misaligned_security_codes`，预注册文档必须引用该字段，不要手抄。**
- **raw 伪造信号**：131,991 个样本外入场里 4,059 个（3.07%）复权后不存在，另漏掉 1,291 个。机理＝除权跳空滚出 5 日窗口时恰好凑齐 520 的全部四项入场条件。
- **19.06%（25,159）的入场信号其均线依赖窗口内含公司行为**。注意窗口是 `long_window + 1 = 31` 根，不是 30 根（30 日斜率要比较 `ma_long[i]` 与 `ma_long[i-1]`，后者用到 `closes[i-30..i-1]`）。
- **PIT 严谨表述（后续每份结果文档必须带）**：**单一终态 vintage 上按日期截断的历史复权重建，算法层无未来日期访问；知识时点层的真正 PIT 性无法证明。**（2,779/2,826 快照为 2026-06-16 单次回填、每 trade_date 仅 version 1。）
- 污染强季节性：6 月每万行 420.6 次跳变，是 3 月 1.67 的 **250 倍**，5—7 月占绝大多数。

### 2.3 可成交性（C 单元，`m3-520-executability-audit-2026-08-21.md`）

| 项 | 入场侧 | 退出侧 |
|---|---:|---:|
| 执行 bar | 129,195 | 128,312 |
| 开盘即涨停（买不进） | 340（0.263%） | — |
| 开盘即跌停（卖不出） | — | 253（0.197%） |
| 因缺失交易日顺延成交 | 52 | 20 |
| 最长顺延（交易日） | 10 | 45 |
| `stk_limit` 无该 bar 记录 | 11 | 119 |

**永远无法成交的信号**：入场信号后完全没有下一根 bar **7 个**；窗口末未获退出成交的交易 **883 笔**，其中 **663 笔**的退出信号落在该证券最后一根可用 bar 上。**这 663 笔第一轮是按收盘价标记的，预注册时必须单列为「无成交事实」。**

**T+1 零违反**：0 笔在买入当根 bar 卖出；31,579 笔（24.61%）在次根 bar 卖出（合法边界）。

**三次审计威胁排序：A 语义 61.97% ≫ B 复权 3.07% ≫ C 执行 0.26%。**

---

## 3. 本 session 的工作单元：编写 520 预注册文档

### 3.1 目标与文件名

建议文件名：`docs/research/yeren-system/m3-520-preregistration-2026-08-21.md`（跨日执行则用实际日期）。

这份文档的作用是：**在看到任何新的收益数字之前，把全部口径一次性写死。** 写完之后 owner 确认，然后才允许跑候选 E 的一次性 walk-forward；**冻结之后禁止按 OOS 结果回改任何一项**。

### 3.2 必须写死的条目（缺一不可）

#### （1）规则语义

- 入场：止跌 N 天后五日线拐头、二十/三十日线仍向下、五日线仍低于二十日线且差距收窄，在「即将上穿」处入场。
  - **`stop_days` 必须在 3 与 4 之间选一个并说明依据**。作者说的是范围，**不能因为回测好看而选**——建议明写「选 3，依据＝范围下界、更早触发、样本更大；这是研究者的操作化选择，不是作者冻结的参数」，或选 4 并同样说明。**两个都跑然后挑一个＝违规。**
  - 「即将上穿」的研究代理（当前＝5SMA<20SMA 且 gap 收窄）必须写明它是研究代理，S4 未解决。
- 退出（**这是本次最关键的改动**）：按卡 8 修订后的第 4 条——**离场规则适用于五日线上穿二十日线完全成立之后**；进入该阶段后五日线一点点拐头就走；完全离场点是五日线下穿二十日线。
- **S8 的处理必须显式写出**：金叉未成立五日线就先拐头时怎么办。作者没说，所以只能在下列三种里**预先选一种并声明它是研究者选择**：
  - (a) 视为形态失败，按同一「拐头」信号离场（＝第一轮的做法）；
  - (b) 继续持有直到金叉成立或触发下穿；
  - (c) 该笔交易整体登记为「不属本卡语义」，从主口径剔除、单独统计。
  **不要三种都跑再挑。** 建议 (c) 为主口径 +(a) 作为披露性对照，因为 (c) 最忠实于「本卡只描述了金叉成立后的退出」，而 (a) 恰好是已被证伪的那 61.97%。理由必须写进文档。
- 均线类型：SMA（S3 未解决，SMA 是研究者选择，必须声明）。

#### （2）价格与数据口径

- 信号价格：`close × adj_factor`（同一 trade_date）。
- 成交与涨跌停判定：**原始 open 与原始 `up_limit`/`down_limit`**（涨跌停限制实际撮合价格，不随复权变化，两者不得混算）。
- 证券排除：`misaligned_security_codes` 全部 78 只（引用 JSON 字段）。
- 母集：daily 中实际出现的 `.SH`/`.SZ`/`.BJ`，≥30 根日线。**是否额外排除 ST／次新／北交所整体，必须现在决定并写明理由**，不能事后加。
- PIT 限制声明（§2.2 那句原话）。

#### （3）成交约束（C 单元六条提案，逐条确认或修改后冻结）

1. 价格分工（信号用复权、成交用原始）；
2. 入场执行 bar 的 open ≥ `up_limit`（排除哨兵 99999.999）→ **该入场信号直接作废**，不顺延、不改价；
3. 退出执行 bar 的 open ≤ `down_limit`（排除哨兵 0.00–0.01）→ **退出不成立，继续持有到下一个可成交 bar**（保守方向）；
4. `stk_limit` 无该 (证券, 日期) 记录 → **不默认可成交**，单独标记计数；
5. 停牌缺行只顺延不伪造，**登记顺延天数**，超过 5 个交易日单独标记；**完全没有下一根 bar 的信号（入场 7 个、退出 663 笔）不得按收盘价当作已实现，单列为「无成交事实」**；
6. T+1 实测无违反，不加额外约束。

#### （4）费用模型（**必填，缺了不许跑**）

C 单元没有建模，这是预注册的必填项：佣金（含最低 5 元）、印花税（卖出千分之一）、过户费、滑点假设、整手（100 股）约束。**数值必须现在写死并说明来源**，不能事后调。

#### （5）样本、统计与随机性

- 样本切分：沿用固定日历切分 20150105–20221230 / 20230103–20260819，**或**给出新的 walk-forward 切分方案并说明为什么。**不得按结果调整切点。**
- placebo：重复次数、种子、匹配方式（同证券／同窗口／同持有长度）全部写死。
- 判据：**什么结果算通过、什么算不通过，必须现在写。** 不能跑完再定。注意 B5「本金与回撤优先」，但 **P1–P3 未冻结 → 组合回撤仍不可定义**，所以判据只能建立在交易级统计 + placebo 上，且必须明说它不构成组合级盈利证明。
- **单笔 MAE 不是组合最大回撤**，文档里不得混用。

#### （6）不做事项与不可识别项

- 四类日线 PIT 不可识别项（X1 集合竞价成交与排队／X2 盘中停牌影响／X3「即将上穿」的盘中判定／X4 部分成交与整手容量）必须原样登记。
- 明写：本预注册不涉及 `backend/playbook/`、模拟盘、飞书、真实券商；`real_broker_orders=false`。

### 3.3 停止条件

- 上述 (1)–(6) 六组全部写死，每一项标注**来自作者 / 研究者选择 / 不可识别**三态之一；
- **本 session 不跑任何产生收益、胜率或 p 值的计算**；
- 文档写完即停，**等 owner 确认后才进候选 E**。

### 3.4 明确不做

- 不跑候选 E；
- 不为了「先看看」而跑一次带收益的重放；
- 不改 A/B/C 三份报告的结论（发现错误可以改，但要说明）；
- 不改卡 8（除非发现与三份报告矛盾，且要说明）；
- 不修改既有 observation/hypothesis 以迎合预注册；
- 不 push。

---

## 4. 开工检查（逐条跑，核对预期输出）

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
## agent/m2-evidence-reconstruction...origin/agent/m2-evidence-reconstruction [领先 12]
agent/m2-evidence-reconstruction
<hash>  docs: add m3-520 preregistration handoff        <- 本接手文档
bfcd47a research: settle 520 price convention and executability
b6da734 research: resolve 520 semantics by audio review and diagnose the cross gate
M3-520-executability-audit | completed | M3-520-preregistration-doc
```

（`git log --oneline -3` 只显示三条，`1f437e4` 在第四条。）

工作树应干净。

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q tests/yeren_research/    # 预期 85 passed
$PY/ruff check backend/ scripts/                                        # 预期 All checks passed!
```

取排除证券清单（预注册文档必须引用，不要手抄）：

```bash
python3 -c "
import json
d=json.load(open('data/yeren_research/inventory/m3-520-adjustment-audit-2026-08-21.json'))
c=d['convention_and_events']
print(c['misaligned_securities'], 'securities,', c['misaligned_bars'], 'bars, tol', c['alignment_tolerance'])
print(' '.join(c['misaligned_security_codes']))"
```

预期第一行：`78 securities, 216 bars, tol 0.01`。

核对三份审计的关键数字：

```bash
python3 -c "
import json
g=json.load(open('data/yeren_research/inventory/m3-520-cross-gate-diagnostic-2026-08-21.json'))
print('A gate OOS:', g['windows']['out_of_sample']['closed_never_crossed'], '/',
      g['windows']['out_of_sample']['closed'], '=', g['windows']['out_of_sample']['closed_never_crossed_share'])
a=json.load(open('data/yeren_research/inventory/m3-520-adjustment-audit-2026-08-21.json'))
print('B signal diff:', a['signal_difference'])
e=json.load(open('data/yeren_research/inventory/m3-520-executability-audit-2026-08-21.json'))
print('C unfillable:', e['signals_with_no_execution_bar_at_all'])"
```

预期：

```
A gate OOS: 82386 / 132944 = 0.619705
B signal diff: {'securities_excluded_for_misalignment': 78, 'entries_on_both_price_forms': 127932, 'entries_only_on_raw': 4059, 'entries_only_on_adjusted': 1291}
C unfillable: {'entry_signals_without_any_next_bar': 7, 'trades_without_exit_execution_bar': 883, 'exit_signals_on_the_final_stored_bar': 663}
```

引用原话复核（**引用纪律见 §6**）：

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
$PY/python -m scripts.yeren_research.evidence_quote \
  data/yeren_research/observations/7675708713827772596.json \
  767570-520-arbitrage-exit
```

---

## 5. 先读的文件（按顺序）

1. `CLAUDE.md`、`AGENTS.md` —— 反过度防御四禁与主线次序
2. `docs/research/yeren-system/m3-520-semantic-audit-2026-08-21.md` —— A 单元，语义定案 + 61.97% 诊断
3. `docs/research/yeren-system/m3-520-adjustment-audit-2026-08-21.md` —— B 单元，复权定案 + 78 只排除
4. `docs/research/yeren-system/m3-520-executability-audit-2026-08-21.md` —— C 单元，六条执行口径提案 + 663 笔无成交事实
5. `docs/research/yeren-system/playbook-cards-batch2-2026-08-20.md` 的卡片 8 —— 已按 A 修订
6. `docs/research/yeren-system/base-v3-spec-2026-08-20.md` —— 现行 Base，证据分类定义
7. `docs/research/yeren-system/m3-520-parameterization-2026-08-20.md` —— 第一轮报告（**注意：§6.2 的 `stop_days=30` 行已作废**）
8. `docs/research/yeren-system/m3-520-next-research-decision-2026-08-21.md` —— 路线决策，§7 列了各阶段的放行条件
9. `scripts/yeren_research/m3_520.py` —— 规则实现（**注意 `early_exit_signal` 尚未加 S5 前置条件，预注册文档要说明 E 阶段该怎么改**）

---

## 6. 研究纪律（逐条硬性）

- **引用原话只由 `sentences[start:end+1]` 无分隔拼接**；`observation` 按 `aweme_id` 取最高版本（目录 1135 文件 / 1111 唯一 id，`-v1.1`/`-v1.2` 并存）。
- **绝不取 transcript 的 `text` 字段**：1110 个 transcript 里 857 个的 `text` 与 `sentences` 拼接不一致。
- **不手打「看似相同」的原话**，一律用 `evidence_quote`。
- ASR 更正只写进复核 JSON 与卡片注记，**不静默改写 observation 的 `raw_text`**（它忠实记录当时的 transcript 状态，改了会破坏可复核性）。
- 事实 / 解释 / 研究代理 / 待 owner 决策**分栏写**。
- 数字没有作者来源时标记 **researcher-added**。
- 不把 placebo p 值写成「策略已稳定盈利」；不把单笔 MAE 写成组合最大回撤；不把未成交头寸写成已实现收益。
- 不用交易结果反向决定 `stop_days`、退出读法、价格口径、S8 处理方式。
- 不修改既有 observation/hypothesis 以迎合新结论；不修改 `data/marketdata_pit/`。
- 面向 owner 的回复用中文；代码、注释、commit message 用英文；conventional commits。

---

## 7. codex 门（代码任务强制，docs-only 豁免）

本单元**如果只写文档就不需要 codex review**。若写了任何代码：

```bash
# cycle 1：审未提交改动
codex review --uncommitted
# 复验
codex exec --sandbox read-only "复验上一轮的 findings 是否已修复，只报告缺陷并按 P0/P1/P2/P3 分级"
```

**注意事项（本轮踩到的）：**
- `codex review` 单次要跑 8–20 分钟，**必须后台跑**（`setsid nohup bash -c "timeout 2400 codex review --uncommitted > out 2>&1 < /dev/null" &`），前台会超时；
- `codex exec` 的 prompt 含引号时会破坏 shell 解析，**把 prompt 写进文件再 `"$(cat file)"` 传入**；
- **不要用 `pgrep -f <脚本名>` 做等待循环**——监控进程自己的命令行也含该字符串，会自匹配导致永不退出；
- codex 的 findings 是真 bug。本轮 B/C 两批共 **4 条 P1 + 5 条 P2 全部成立并修复**，包括我自己没看出来的均线依赖窗口差一根、终端信号被漏记。

**本轮已知的语言陷阱（写新代码时注意）：**
- `pandas.read_csv(usecols=[...])` **不按 usecols 顺序返回列**，保持文件列序；用 `itertuples` 解包前必须显式 `frame = frame[["a","b","c"]]`。本轮因此错过一个静默 bug，是冒烟测试抓出来的。

---

## 8. 长时任务的跑法（本轮验证过）

```bash
SP=<scratchpad>
PY=/home/ps/anaconda3/envs/zhanglan/bin
setsid nohup env FEISHU_INTERACTIVE_ENABLED=false $PY/python -m scripts.yeren_research.<module> \
  > $SP/out.json 2> $SP/out.err < /dev/null &
# 轮询 out.json / out.err 非空即结束
```

- 全窗（2,826 快照 / 11.6M 行）加载在页缓存冷时约 4–6 分钟，热时约 40 秒；
- `pytest` 必须带 `FEISHU_INTERACTIVE_ENABLED=false`，否则会连飞书；
- 跑长任务前先用短窗冒烟（`--start-date 20250101 --split-date 20250630 --end-date 20250731`），本轮靠它抓到了列序 bug。

---

## 9. 交付与停止条件

本 session 至少要留下：

1. 一份 520 预注册文档，§3.2 的六组条目全部写死，每项标注来源三态；
2. 一条新的 worklog JSONL 记录（含 inputs / outputs / findings / `real_broker_orders=false` / `resume_from`）；
3. 本地 commit（conventional commit，英文 message，**不 push**）；
4. 更新 memory：`MEMORY.md` 首条 hook + `project-midterm-rearch-yeren-playbook-2026-08-12.md` 详情。

**写完预注册文档即停，等 owner 确认再进候选 E。** 无论如何都不要进入生产 playbook、模拟盘、飞书或真实券商路径。

---

## 10. 待 owner 决策的积压项（写进文档，不要自行决定）

1. **是否 push**——12 个本地提交全部未 push。
2. **是否跨卡援引套利仓分母**（总仓位 10%、套利不能重仓）到 520——当前只登记为候选证据，未采信。
3. **卡 8 是否还需改动**——A 单元后已改第 4/5/6/8/9/10 条，owner 尚未过目。
4. **`stop_days` 选 3 还是 4**——如果不想由研究者定，这一项必须留给 owner；但**不能等跑完结果再定**。
5. **S8 三选一**——同上，建议 (c) 但需 owner 点头。
6. **费用模型的具体数值**——研究者可以给一套市场惯例值，但 owner 可能有实际券商费率。
