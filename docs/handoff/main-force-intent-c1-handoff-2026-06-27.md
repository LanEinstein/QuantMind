# 主力意图研究 · C1 接手 prompt(新 session 续推:避顶部 EXIT + 入场确认门,在 C0b 端到端模拟器上)

> **用法**:新 session 把下面「PROMPT」整段交给模型。三步法已完成(① 读文档 ② 大纲+codex 两轮收敛 ③ plan mode 详细计划+科学评价协议,owner 已批);**C0a(amendment/erratum/大纲)+ C0b(端到端模拟器+EXIT 执行契约)已本地 commit,push 待 owner**。本次 = 按已批计划 `misty-doodling-pnueli` 推进**下一刀 C1**。**push / 数据摄取 / live 激活 / 「稳定可观」阈值冻结 = owner-gated。**

---

## PROMPT(整段复制到新 session 后)

你是 Claude(Opus 4.8 / Fable 5),接手 QuantMind「主力意图研究 / 量化第一闸门」**C1 刀**。**clean context —— 先读文档,文档是权威,你没有上个 session 记忆。** 三步法已完成、详细计划已 owner 批准;C0a/C0b 已本地 commit(push 待 owner)。**本次按已批计划推进 C1(避顶部派发 EXIT + 入场确认门),不重做规划。每完成一个有意义节点向 owner 报告等确认;不自动 push、不自动摄取数据。**

### 第一步:读文档(按序,权威;别假设,grep 核实)

1. **已批详细计划 + 科学评价协议**:`docs/research/system-roadmap-outline-2026-06-27.md`(大纲 §1-§7 组件/方法/刀序 + §9 owner 决策落定 + §10 codex 两轮收敛)。**注**:plan mode 文件 `/home/ps/.claude/plans/misty-doodling-pnueli.md` 是 owner 批准的详细计划(Part A 评价协议 / Part B 组件信号 / Part C 任务分解),若可读优先读它;不可读则大纲 + 本 handoff 已覆盖其全部内容。
2. **判据 + owner 新原则(决策边界)**:`docs/decisions/qgr-criterion-rebar-amendment-2026-06-27-avoid-top-dynamic-exit-swing.md`(§9 Erratum **避顶部=左尾风险假说非择顶** + §10 P-A..P-E 指针)+ `docs/decisions/qgr-confirmation-stop-swing-sizing-amendment-2026-06-27.md`(**P-A 确认门 / P-B 强制止损两条腿 / P-C 做T profit-gate / P-D 安全底线 / P-E 仓位:取消单股15%cap+置信集中~60%+≥40%现金buffer严禁梭哈**)。
3. **C0b 中枢交付(你要在它上面建 C1)**:`scripts/factor_research/e2e_simulator.py` + `docs/research/qgr-c0-exit-execution-contract-2026-06-27.md`(EXIT 执行契约,**frozen**)+ `tests/factor_research/test_e2e_simulator.py`(byte-exact 不变量等)。
4. **评测口径 + 两层评测**:`docs/research/qgr-2-eval-arena-freeze-spec-2026-06-22.md`(§1.2 MDD≤8% 已被判据重定**作废**,其余口径有效)+ `quant-first-gate-rearch-plan-2026-06-21.md` §4。
5. **避顶部信号本土证据 + 已验因子**:`docs/research/mfi-batch-a-crowding-results-2026-06-26.md`(**A1/A2 PASS**:`ideal_amplitude_20d` 是正交 size-neut EXIT 因子;拥挤=崩盘概率/左尾)+ `main-force-intent-research-program-macro-direction-2026-06-26.md` §2.10③(退出侧派发结构:放量滞涨+获利盘饱和+OBV背离)。
6. **结构墙背景(为何要 C0b 直接 SELL 环)**:`qgr-4-exit-veto-ablation-results-2026-06-26.md` + `b1-regime-derisk-results-2026-06-26.md` + `b2-defensive-sleeve-results-2026-06-27.md`。
7. **协作 + 进度**:`CLAUDE.md` §2/§3 + 研究专项原则 0-7 + `docs/plan.html` SESSION_LOG 顶 1 条(三步法+C0a/C0b)+ memory `~/.claude/projects/-home-ps-papers-QuantMind/memory/project-main-force-intent-research-program-2026-06-26.md`(全细节+坑)+ `feedback-owner-trading-principles-2026-06-27.md`(P-A..P-E)+ `MEMORY.md` 顶部活跃线。

**第一步产出**:向 owner 报一句话总结「已做:三步法+计划批准+C0a(amendment)+C0b(端到端模拟器 byte-exact+EXIT 契约,codex COMMIT-SAFE);待做:**C1 避顶部 EXIT + 入场确认门** → C3 做T → C2 减持 → C4 主旋律 → C5 整合+1万本金真实模拟」。

### 第二步:C1 实施(amendment 已含 P-A..P-E → 直接写码;每节点本地门禁 + codex 前置门)

> **C1 = 在 C0b 模拟器上验证避顶部 EXIT(X1)+ 入场确认门(E7)是真避损边,还是机械减暴露/换手伪影。** 子步骤(可分 commit;X1 是「第一要务」最干净一刀,E7 可作 C1b 后置):

**C1.1 避顶部触发面板(raw PIT 原语,firewalled train_val)**
- 复用 `scripts/factor_research/build_crowding_panel.py`(已建 `ideal_amplitude_20d`/`blowoff_20d`/`bias_20d`,读 RAW high/low + 删最小 30% + neutralize)+ 加 **OBV/量价背离 + 放量滞涨**(raw `daily` 派生,从零验符号)。
- **size/行业中性化**(`neutralize.neutralize_panel`,SW-L1 + log circ_mv,winsor 0.01,min_obs 20)+ **删最小 30%**;**cyq 仅可选/披露 conditioning 不作承重**(final-vintage 泄漏,§A7)。
- **泄漏门**:`leak_probe.check_future_leak`(cyq_perf 已在 MARKET_TRADE_DATE_ENDPOINTS;扰动复权/股本公司行动字段)。
- **firewall**:`arena_ablation.firewalled_ranker_table` 断言 bar-read ⊆ train_val。
- **从零验**(§A2 大数据条件研究):IC + 崩盘概率条件(top-decile vs rest 配对尾Δ |t|≥3)+ 正交化 vs carry/QGR/reversal(|corr|≤0.7)+ **discovery/calibration/validation 四路切分**(阈值/符号在 discovery 定,committed 在评测前;codex R1-#10)。

**C1.2 避顶部 EXIT overlay(实现 `ExitOverlay` Protocol)**
- 新 `scripts/factor_research/avoid_top_overlay.py`:类实现 `orders_for_day(ctx: ExitOverlayContext) -> tuple[OrderIntent,...]`。每日对每个 `ctx.held`(HeldContext:cost_cents/market_value_cents/volume/age/unrealized_pnl_cents)查避顶部触发(C1.1 面板值);**确认滚顶**(P-A EXIT 对称:动能转弱确认,非仅高位/延展)→ emit SELL(full 或减仓);**P-B 强制止损硬触发不等确认**(破止损位即卖);**只增卖压永不松止损**;**queue**:不可卖次日 re-emit(契约 §2,overlay 有状态)。
- 阈值从 C1.1 discovery 定,**committed 在评测前**;契约(`ExitExecutionContract`)**不改**(改 = amendment + debit 账本)。

**C1.3 入场确认门 E7(P-A,可后置为 C1b)**
- 不追飞刀:候选 ≠ 立即买,须**市场确认上涨已启动**(首次收复区间/动能/放量确认 的确定性 PIT 代理)→ T+1 入。实现 = **ScoreProvider 包装器**(withhold 未确认候选,延迟入场)或独立 arm;**须证非「晚入=少吃下跌暴露」的机械效应**(同延迟随机入场 placebo)。

**C1.4 三臂 + EXIT-专属 placebo + P&L 四分解(头号防自欺,codex R1-#3/#16)**
- 复用 `exit_veto_ablation.py` / `arena_ablation.py` 模板(fork)。臂:`baseline`(无 overlay,= C0b byte-exact 基线)vs `+avoid_top` vs **`+同频随机持仓 EXIT placebo`**(同 EXIT 频率/持仓龄/size/行业/未实现盈亏/入场分,随机挑在位名卖 + 同现金再部署 + 同再入锁)+ `+同卖出日历 placebo`(同腾槽日期随机挑名)。
- **codex R2-M1**:≤5 持仓下精确匹配稀疏 → 定**回退匹配规则 + 平衡诊断 + fail-closed(匹配不足→空报告/降级)**。
- **P&L 四分解(codex R2-M2,冻结反事实代数)**:净盈 = ①避损 −②错失 +③再部署 −④成本;**避顶部要赢须 ①>②+④ 且 ③非主因**;算法**不可游戏化**(冻结 horizon/no-X1 baseline/再入锁/再部署记账/归因规则)。

**C1.5 反过拟合四门 + 制度分层 + 判据**
- **四门(不放宽)**:`honest_gates.deflated_sharpe_hac(...,n_trials=ledger.deflation_n_trials(onc_n),hac_lag)` **DSR≥0.95** / PBO / `multi_strategy_compare.compare_strategies(family="qgr.avoid_top")` SPA + Romano-Wolf(预声明 family + block bootstrap seed 20260622)。
- **非清零账本**:`trial_ledger` append family `qgr.avoid_top`(kind=ablation,**hash 先于结果**,codex R1-#17);deflation N = max(累计有效, 本批 ONC)。
- **制度分层(含股灾)**:净盈/避顶部命中/**missed-rally/false-exit/再入损失**/MDD 分 {牛/熊/震荡}(`exit_veto_ablation._classify_regimes`)× 6 股灾切片(2015-06/2016-01/2018/2020-02/2022/2024-02);逆境非永久套牢。
- **判据**:净 trade-off ①>②+④ + 严格胜两 placebo(paired-t≥2)+ DSR≥0.95;**FAIL 报 FAIL**(避顶部左尾真但作 EXIT-on-held 净 trade-off 须实证,batch-A 仅证 GROSS 尾部非净盈)。

### 第三步:报告 + 记账(每节点)

- 结果文档 `docs/research/c1-avoid-top-exit-results-2026-06-27.md`(逐臂 + placebo + P&L 四分解 + 制度分层 + 四门;FAIL 报 FAIL,provisional 诚实标)。
- SSoT:`docs/plan.html` SESSION_LOG + 修订记录;memory 回写。**docs commit 单独**;code commit feature 粒度。**push 待 owner 授权**(C0a/C0b/B1/B2 等本地 commit 一并待授权)。

### 红线(全留,违反即停)

sim 暂停贯穿 · 永禁真实下单 · 离线 · 仅 Tushare 官方 SDK · PIT 字节存档+checksum · **train_val only(test 封存,真 OOS=owner-gated look-once)** · 防火墙 bar-read⊆train_val · **研究/评测零 LLM**(live evidence-only) · 做 T 守 T+1 · 不接 moneyflow 主路径 · 北向仅历史 · **不做 L2** · governance enum 不动 · **不碰 backend value-sleeve(AF-*)/冻结面板字节/引擎字节**(C0b 已 byte-exact 核对;C1 只加 overlay/panel) · **EXIT 执行契约 frozen 不改**(改 = amendment + debit 账本) · 改判据/决策边界先落 amendment · codex 前置门 · **反过拟合四门不放宽** · FAIL 报 FAIL · **push/摄取/live 激活/「稳定可观」阈值冻结 = owner-gated** · 报告中文/代码 commit 英文。

---

## 复用件速查(C0b API + 已建模块;grep 核实)

**C0b 端到端模拟器(`scripts/factor_research/e2e_simulator.py`,C1 的舞台)**:
```python
run_e2e_backtest(*, spec: BacktestSpec, bar_source, provider, strategy_config,
    friction_params, exit_overlay: ExitOverlay | None = None,
    contract: ExitExecutionContract | None = None, harsh_config=None) -> E2ERunResult
# E2ERunResult: backtest_result(BacktestResult,byte-exact 可比)/ overlay_orders
#   (tuple[OverlayOrderRecord]) / contract_id / overlay_sell_signals / overlay_buy_signals
# ExitOverlay Protocol: orders_for_day(ctx: ExitOverlayContext) -> tuple[OrderIntent,...]
# ExitOverlayContext: day / current_index / view(PortfolioView) / bars(今日) /
#   rotation_decision_orders / held(tuple[HeldContext]); held_by_code 属性
# HeldContext: code/volume/cost_cents/market_value_cents/holding_age_trading_days
#   + unrealized_pnl_cents 属性(= market_value − cost*volume,for 做T profit-gate/止损)
# OrderIntent(code, side_is_buy: bool, volume: int) — SELL=side_is_buy False
# NoOpExitOverlay() = 不变量参照(overlay-off ≡ 冻结 run_backtest byte-exact)
# ExitExecutionContract(...frozen...).contract_id  — 改字段 = 改契约,须 amendment
```
**关键**:overlay 注入的 SELL/做T(BUY)在**次日**经冻结 barrier 成交;不可卖 lapse → overlay 须**次日 re-emit**(queue);持仓 cost/mark 在 `ctx.held` 里(via EquitySnapshot.PositionMark)。

**arena / 数据 / 因子 / 统计**(`scripts/factor_research/`):
- `arena_ablation.py`:`firewalled_ranker_table`(PIT 防火墙 train_val)/ `ledger_n_trials` / `strong_protected_health` / `hold_baseline_arm`。
- `gate_bar_source.py`:`PitBarSource(store, trading_days, universe, asof=None, adv_window=20)`。
- `gate_backtest.py`:`default_friction()`(¥5 min佣金/印花0.1%/过户0.00341%/分板块滑点1.5/1.5/3.5/1.5bp,**做T 成本已现成**)/ `default_strategy_config()` / `PanelScoreProvider`。
- `build_crowding_panel.py` + `crowding_factor_diagnostics.py`:batch-A 避顶部因子(ideal_amplitude/blowoff/bias,IC从零+崩盘概率条件)。`neutralize.py`(size/行业中性)/ `leak_probe.py`(future-NaN+cyq)/ `cyq_perf_pit.py`(筹码,模型派生存疑可选)。
- `honest_gates.py`(DSR-HAC/ONC)/ `multi_strategy_compare.py`(SPA/RW)/ `cpcv.py` / `trial_ledger.py`(非清零,现含 batch-A/exit_veto/derisk_regime/defensive_sleeve)。
- **模板可 fork**:`exit_veto_ablation.py`(三臂+placebo+paired-t+DSR)/ `defensive_sleeve_ablation.py`。

**数据资产**:`data/marketdata_pit/`(gitignored,~29GB/23 端点,**禁重下**,清单 `docs/research/data-inventory-marketdata-pit-2026-06-21.md`);Tushare 权限 memory `reference-tushare-entitlements-8000-2026-06-20`。

## 速查命令(预期输出)

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
# 门禁(基线:factor_research 666 passed)
$PY -m ruff check scripts/factor_research/<f>.py tests/factor_research/<t>.py   # All checks passed!
$PY -m mypy scripts/factor_research/<f>.py        # 逐文件跑(多文件一次会假报 "found twice")
FEISHU_INTERACTIVE_ENABLED=false $PY -m pytest tests/factor_research -q          # 666+ passed
grep -rnE "import backend\.(llm|agents|mirofish|risk)" scripts/factor_research/<f>.py   # 必空
# codex 前置门(代码任务 commit 前;前台 + </dev/null 防 stdin deadlock;撞限流→/code-review high)
cd <repo> && codex exec --sandbox read-only "<review prompt>" </dev/null
# 真实模拟(PitBarSource 全窗 ~40min;后台 nohup + python -u;detached 不自通知须 ScheduleWakeup 轮询)
nohup $PY -u -m scripts.factor_research.<ablation> --out data/factor_research/<name>_result.json > <log> 2>&1 &
```

## 本地 commit(push 待 owner;按时间倒序)

- `f5b7178` docs(plan) C0a/C0b SSoT · `f38a5f7` feat C0b 端到端模拟器+EXIT 契约 · `01e10e5` docs C0a 大纲+amendment+erratum · `f4d026d`/`9a7015b` B2 · `4d6d1af`/`1dd576f` B1 · `65e067d`/`b11fcef` QGR-4 · `c98f710`..`10108c8` batch-A。**全未 push origin/main。**

## owner 决策点(C1 中/后需拍板)

1. **「稳定可观」阈值冻结**(跑 C5 前;C1 也用同口径判 trade-off):§A8 提案 = CPCV frac_positive≥0.80 / 成交≥30 round-trip / 研究资本年化净盈≥12-15%(占位)/ 永久套牢=FAIL。**owner 确认/调整后冻结,看结果前**(codex R1-#11)。
2. **E7 入场确认门** 现在并入 C1 还是后置 C1b?(X1 避顶部是「第一要务」最干净一刀。)
3. **push** C0a/C0b + 历史本地 commit。
4. **C2 减持**:`stk_holdertrade` owner-gated 摄取(C1 后)。

---

## 给 owner 的一句话(怎么用)

把上面「PROMPT」整段复制到新 session。模型会:① 读文档报「已做/待做」一句话总结 → ② 按已批计划做 C1(避顶部 EXIT + 入场确认门:大数据条件研究 + C0b 模拟器三臂 + EXIT-专属 placebo + P&L 四分解 + 四门 + 制度分层含股灾,FAIL 报 FAIL),每节点报你确认 → ③ 出 C1 结果文档 + 记账。**每节点停下等你拍板,不自动 push/不自动摄取/不烧 test。** 当前 C0a/C0b 已本地 commit、门禁全绿、codex COMMIT-SAFE,push 待你授权。
