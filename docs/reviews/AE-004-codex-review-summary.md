# AE-004 review summary — `backend/backtest/` 确定性 harness + 双 lane oracle + 封闭不变量

> **门禁**:CLAUDE.md §3 强制 codex-review 前置门。**codex CLI 撞 usage limit(实测 2026-06-15,恢复 2026-06-18 13:27)→ 回退 `/code-review high`**(owner 既定 fallback,印证 [[feedback_codex_rate_limit_fallback]])。本轮 = high effort 7 finder angle(line-by-line / removed-behavior / cross-file / reuse / simplify / efficiency / altitude)× 6 候选 → verify。
> 日期:2026-06-15。范围:7 新模块(friction/event_loop/portfolio/invariants/golden_vector/strategy/harness)+ `__init__` + 8 测试文件。

## findings 与处置

| # | 级别 | finding | 处置 |
|---|------|---------|------|
| F2 | 🔴 HIGH | **敞口 cap 误报 DIVERGENT**:策略按决策日**收盘价**定量(≤15%),但不变量按次日**成交价**估值;gap-up 开盘把合规仓位推过 15% → 整轮误判 DIVERGENT。 | **已修**:`_record_buy_exposures` 改按 `last_close`(成交时仍持决策日收盘价 = 策略定量所用价)估值,杜绝 gap-up 误报。回归测试 `test_gap_up_fill_does_not_false_trip_exposure_cap`。 |
| F3 | 🔴 HIGH | **无负现金护栏**:策略按收盘价(无摩擦)预留现金,实际按成交价+摩擦扣减;gap-up 可致 `_cash` 转负,纯代数现金守恒不变量察觉不到。 | **已修**:`_fill_pending` 加可负担门(BUY `net_cents > cash` → lapse,镜像现金账户拒单)。回归 `test_unaffordable_gap_up_lapses_without_negative_cash`(并断言每个权益点 cash≥0)。 |
| F4 | 🟠 MED | **首日开仓持仓崩溃**:opening position 若首日无 bar 且无前收 → `portfolio.mark` 抛未捕获 `PortfolioError`,整轮崩。 | **已修**:`last_close` 用 opening lot 的 `cost_cents` 预种,首日可 carry-forward 估值。 |
| F6 | 🟠 MED | **golden-vector scores 永不填充**:harness 构造 `DecisionVector` 不带 scores → 任何带 scores 的金标准向量恒报 `<missing>`,Lane-2 分数 oracle 永假阴。 | **已修**:`DayDecision.scores` 新增并由 `decide_day` 填 shortlist 各码 quant 分;harness 透传进 `DecisionVector`。回归 `test_decision_vectors_carry_scores_and_diverge_on_tamper`。 |
| F7 | 🟢 LOW | **`slippage_cents` 恒 0**:`apply_board_slippage=False` 下 econ.slippage=0,harsh 冲击不体现于审计字段。 | **已修**:记录 harsh 冲击 `|fill−open|×vol` 为 slippage_cents。 |
| F5 | 🟢 LOW | friction 负 net SELL **clamp** 而 broker **raise**,注释却称"mirror"(实为相反)。 | **已修**:注释如实改写(回测 clamp 不中止,broker 拒单;lot+price floor 令该极端实际不可达)。 |
| F11 | 🟢 cleanup | harness 把 `bars` 宽化为 `Mapping[str, object]` + 8 处 `# type: ignore[attr-defined]`,自废 DayBar 静态检查。 | **已修**:`bars` 类型收窄回 `Mapping[str, DayBar]`,删除全部 8 处 ignore。 |
| F1 | ⚪ verify | fill 价经 int分→float元→harsh→float元→to_cents 往返,疑跨平台/numpy 漂移。 | **驳回为非 bug + 加注释**:harsh_fill 纯 Python float(IEEE-754,无 numpy → NEP-50 不适用),`to_cents` round-half-even 为权威量化;零冲击往返回精确开盘价。determinism 测试实证 bit-for-bit。 |
| F8/F9/F10 | ⚪ reuse | portfolio/invariants/`_max_drawdown_pct` 与 `golden_replay`/`equity_kpis` 有 lot 会计/现金守恒/回撤重复。 | **本轮不重构**:两路径服务不同模式(recorded-replay vs forward-sim+friction),合并会动到刚交付的 AE-003 同源证明,风险>收益;记为已知 acceptable 重复。 |
| F12 | ⚪ altitude | `clock.assert_readable` 在唯一调用点恒真(loop 只读 current_day)。 | **保留**:作廉价结构断言(nautilus 单调时钟意图留痕);真正 look-ahead 防护在 BarSource as-of + T+1 屏障。 |
| F13 | ⚪ altitude | 敞口"每买入点"校验 vs amendment"每权益点重验"。 | **保留为已决设计**:逐 MTM 点校验会因升值误报(单股与总仓皆然);敞口只能经买入主动增加,买入点校验覆盖全部主动增仓,升值漂移非违规。已 docstring 说明。 |

## 门禁结果(修复后)
- 全量 **5944 passed / 14 skipped / 90.06% cov**(基线 #90 5887)。
- 新模块覆盖:invariants/golden_vector 100% · friction 97% · event_loop 98% · strategy 98% · portfolio 95% · harness 94%(`rqalpha_entry` venv-only 0% 预期)。
- ruff(format+check)+ mypy strict(11 src)+ redline(`[BACKTEST]` import 隔离 + Ref 前视 + 裸 float 比较;`[R-002]` + `[AB-008]`)全绿。
- 安全地基红线一条未破:import 隔离(禁 llm/agents/mirofish/api/broker;strategy_evolution 仅 harsh_fill_model)/ 零 LLM / 永不实时 / 整数分定点 / Line-2 盘中不进环。
