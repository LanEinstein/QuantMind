# 接手 kickoff prompt — QGR-4 第一刀:EXIT-veto 事件循环消融(新干净上下文)

> owner 2026-06-26 同意『下一干净 session = 把批 A EXIT 赢家(ideal_amplitude)+ QGR-3 快腿排序器跑进事件循环竞技场,用 owner 真判据(绝对净盈+MDD≤8%)量它,顺带立 QGR-4 baseline』。**下面整段可直接粘贴到新 session。**

---

你是 Claude(Opus 4.8 / 或 Fable 5),接续 QuantMind 的**「主力意图大数据研究纲领」**。这是**全新上下文 —— 先读下列文档,再动手**(你没有上一个 session 的记忆,文档是权威)。

## 先读(按序)
1. `docs/research/mfi-batch-a-crowding-results-2026-06-26.md` —— **批 A 真结果(本刀的起点)**:A1 PASS(拥挤→前向左尾显著更肥,配对 t +14~+22 跨子期稳定=非对称 §2.1 在真 A 股成立)/ **A2 PASS = `ideal_amplitude_20d` 是赢家**(size-中性后仍显著负 t=−15 + 正交 |corr| 0.34 vs max_20d = 真·正交 EXIT 轴)/ A3 MIXED(bias=ret_20d 换皮、blowoff size-turnover 换皮 DSR 不过)。
2. `docs/research/mfi-batch-a-crowding-exit-spec-2026-06-26.md` —— 批 A spec(EXIT-gate 框架 = REDUCE/veto 非排序 alpha;§3 严格性管线;§7 证伪台账)。
3. `docs/research/quant-first-gate-rearch-plan-2026-06-21.md` —— **QGR 总框架**(重点 §2.1 系统真实角色〔09:35 选股→≤5 槽→5td 持有→轮动〕+ §4 两层评测 + §6 基建盘点 reuse/extend + §7 QGR-4 行=策略搜索/选择+两条腿)。
4. `docs/research/main-force-intent-lowbase-transition-system-design-2026-06-26.md` §6.6 —— **三臂消融/placebo 纪律**(本刀直接套用)。
5. `docs/research/qgr-2-eval-arena-freeze-spec-2026-06-22.md` —— **评测口径已冻结**(主目标=净 P&L + MDD≤8% 硬约束 + 换手;5td canonical + 10td 稳健 + 快腿 T+1 公平比)。
6. `CLAUDE.md` §2 红线 + 原则0 + `docs/plan.html` SESSION_LOG(2026-06-26 条)+ memory `project-main-force-intent-research-program-2026-06-26.md` + `project-quant-first-gate-rearch-2026-06-21.md`。

## 已确立的方向(不要重新论证,直接执行)
- **批 A 已确认 EXIT 边**:拥挤/过度延展预测**崩盘概率/左尾**(非均值);`ideal_amplitude_20d` 是唯一正交、size-中性存活的 EXIT 因子(赢家)。本刀 = **把它从 IC/尾部层兑现成 owner 判据(绝对净盈+MDD≤8%)**——我上一刀写的诚实 caveat 正是「可部署用法=long-only veto,净 P&L 是 deferred 的事件循环测试」,这刀就做这件事。
- **判据 = 绝对净盈 + MDD≤8%**(QGR-2 已冻),**去 CSI300 超额硬门**(仅披露)。系统真实角色 = ≤5 槽 / 5td 最短持有 / 轮动 / T+1 / 分板块滑点 / 涨停不可成交。
- **这是 QGR-4 第一刀(EXIT-veto 消融),不是 QGR-4 全量候选搜索**——先把 baseline + veto 价值钉死,全量 SPA 候选搜索是后续。

## 第一刀(本 session 目标)= 事件循环三臂消融
> 复用既有竞技场,别重造。**先核实 API 再动手**:`gate_backtest.py`(事件循环→BacktestResult 净 P&L/MDD/换手)、`gate_bar_source.py`(PIT→bar:qfq as-of/真 stk_limit/ETF 经 fund_daily/as-of ADV 无前视)、`baselines.py`(可部署 baseline 面板)、`multi_strategy_compare.py`(SPA/Romano-Wolf/BH-BY)、`honest_gates.py`(DSR-HAC/ONC)、`trial_ledger.py`(非清零含 legacy 块)、`cpcv.py`(真 CPCV 路径)。**先 grep/读它们的签名 + 确认是否已有「面板驱动的 gate 选股策略」适配器**(QGR-2 建了引擎,QGR-4 用因子选择驱动它)——有就 reuse,没有就建一个小适配器,别动引擎字节。

1. **baseline 臂** = 「快腿排序器(QGR-3 幸存 **{rev_1d, max_5d, turn_spike}**,size+行业中性化)→ ≤5 槽 → 5td 轮动」过事件循环 → 绝对净 P&L + MDD + 换手。**选股驱动可直接读已建面板 `data/factor_research/panel_train_val_crowding.csv`**(gitignored,326854 行/498 日;**它已同时携带** carry cluster + QGR 快腿 + crowding {bias/ideal_amplitude/blowoff} + neut 输入 industry_l1/log_circ_mv + fwd_ret + at_limit 旗标 —— 无需重建面板);**fills 经 gate_bar_source 读快照 store**(非面板)。
2. **EXIT-veto 臂** = 同排序器,但**剔除 top-拥挤分位**(`ideal_amplitude_20d_neut`,即面板 neut 列;高=该躲)出买入集 = 可部署 long-only veto。
3. **placebo 臂(§6.6 必做)** = 剔除**同通过率的随机/size-匹配分位**,证 veto 不只是「少买=降换手/降暴露」的假象。
4. **三臂 + regime 分层**:{牛/熊/震荡 × 大/小盘领涨}(+ 主题 on/off 若可得)报净 P&L + **MDD** + 每笔净 payoff;判据 = **veto 臂 MDD 改善(控回撤)且净 P&L 不被毁,且超过 placebo**(R5「不被均值掩盖某 regime」守门)。
5. **击败可部署 baseline 面板(codex-P1 long-beta 守门)**:随机 top5 / 现役 screener / 纯流动性筛 / ETF-only(510300)/ CSI300-ETF 买入持有(复用 `baselines.py`)——否则牛市高 beta 篮"看着盈利"无技艺。
6. **公平比 + 非清零账本**:SPA(最优是否真)+ Romano-Wolf StepM(哪些真)三臂排名;DSR-HAC(overlapping 持仓自相关)on 净 P&L 序列;**legacy floor N≈2382 deflation**,本批 append `qgr.exit_veto` trial family(改判据/换框架**不清零** mining 债,codex P0)。CPCV 按日期分组(绝不拆同日截面)。
7. **诚实判据(FAIL 报 FAIL)+ codex 前置门**:写 `docs/research/qgr-4-exit-veto-ablation-results-YYYY-MM-DD.md`——EXIT veto 在 owner 判据下**控不控回撤**?净 P&L 撑不撑得住?报。过不了就明确报 FAIL(信反过拟合门,QGR 原则#1)。代码任务 commit 前 codex `/code-review high`(CLI 撞限流的既定兜底),修完 P0/P1/P2 → 分任务 feature commit(本地)+ 回填 plan.html SESSION_LOG + memory。

## 强制约束(违反即停)
- 严格性不可旁路;**FAIL 报 FAIL**;**有代码编写的任务 commit 前过 codex 前置门**;docs-only 豁免。
- **size/行业中性化 + 删最小 30%**(面板已删 30%;排序/veto 用 neut 列,否则重蹈 round-1..4 size-tilt 死法)。
- **train_val only**:test 窗口封存(test 2025-06-04..2026-06-12;embargo 2025-05-06..06-03)——本竞技场是 development 层 = **provisional 非 go-live**;真 OOS/前向确认 = B 层 gate(QGR-6),**不在本 session**。`split.assert_all_not_test` 必经。
- **绝不碰** `backend/` value-sleeve 域(AF-*)+ `scripts/factor_research/` **既冻结字节 / locked split**(round-1..4/QGR 面板字节不变;改了 = 违规)。**不动 `backend/backtest` / `gate_backtest` / `gate_bar_source` 引擎字节**(只加适配器/策略,不改引擎)。
- **不接入 `moneyflow` 信号路径**;**北向仅历史**(日度 2024-08 已死);**不把「主力意图」当产品宣称**(可交易内核诚实命名「拥挤/blow-off EXIT 闸」)。
- **改决策边界先落 amendment**(本 session 是离线研究,**不改 live 一行**——若要动 live 持仓机制/screener 才需 amendment)。
- **sim 暂停**贯穿;**push origin main 须 owner 授权**,commit 落本地。**面向 owner 报告用中文**,thinking 英文,代码/commit 英文。
- **重活分批跑、别一次 fan-out 太多 agent**(限流教训,reset 2pm Asia/Shanghai)。

## 已知坑 / 提效
- **面板已存在,别重建**(`panel_train_val_crowding.csv` 326854 行,含本刀所有选股因子)。若确需新列 → 后台跑构建(~70 分钟/498 日,**别前台 2min timeout**),用 `nohup ... &` + Bash run_in_background 监视面板文件出现。
- **codex CLI 撞限流** → `/code-review high` 兜底(本纲领既定;42 agent 多角度+独立 verify,findings 实在,认真修)。
- **先核实再 reuse**:gate_backtest 的 strategy 接口形态、是否已有 panel→选股适配器、QGR-3 幸存集的精确构造(`qgr_factor_diagnostics` 幸存 {rev_1d,max_5d,turn_spike};rev_3d/n_limit_up_5d 已被 drop)——别假设,grep 确认。
- **两条腿(QGR 决策2)**:本刀聚焦 5td canonical 腿(现状机制);5-10td vs T+1 公平比作 stretch goal(改 ≤5槽/5td 最短持仓机制属独立 amendment,不在本刀)。

## 一句话
先把批 A 的赢家(`ideal_amplitude` EXIT veto)在事件循环里证明它真能**控回撤**(owner 判据),顺带立起 QGR-4 baseline —— 这是把「确认的发现」变成「可上线候选」的唯一干净下一步。**严格性不可旁路 + FAIL 报 FAIL + 不碰 value-sleeve/冻结字节 + sim 暂停 + push 待授权。**

先按项目协议梳理本 session 子任务清单(plan.html/QGR §7 无对应细目则按本 prompt 建任务再认领),再动手。
</content>
