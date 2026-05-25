# U-C2 Codex Review Summary — Line-2 日线异动 runner

> Task: U-C2 `backend/orchestration/line2_daily_runner.py`(Line-2 持仓监控日线异动生产编排,确定性零 LLM)。
> Cycle: 1 review (`codex review --uncommitted`) + 1 read-only verify (`codex exec --sandbox read-only`)。
> 结论:cycle 1 **no findings**;verify 抓 **1 P2**(显式空 signal_id 绕过前缀校验),已根因修复 + 回归测试。**无 remaining P0/P1/P2**。

## 范围

新增 `backend/orchestration/line2_daily_runner.py` + `tests/orchestration/test_line2_daily_runner.py`(7 测试,97% 覆盖)。

runner 链(确定性、零 LLM、零 redis):`T-1 EOD 快照 → partition_by_suspension(停牌干净降级)→ AnomalyDetector.scan(日线统计 PIT 可 replay)→ evaluate_sell_intents(读 settled available_volume T+1)→ assemble_monitoring_plan(单一构造点 14-check)→ RouteCoordinator`。`LINE2-MON-` signal_id 前缀标记无辩论监控路径。import-clean(无 `backend.{api,broker,risk,llm,agents,agents_team,mirofish,data}`);重对象经注入式 `Line2DailyContextProvider`(U-D1 调度层构造,经 `make_sell_context`)。

## Codex cycle 1

**no findings** — "composes the intended anomaly scan, SELL intent evaluation, monitoring plan assembly, and RouteCoordinator path without apparent correctness regressions."

## Codex verify(read-only)— 1 P2

### P2 显式空 `signal_id=""` 绕过 `LINE2-MON-` 前缀校验
**发现**:`sid = signal_id or default` 把显式 `""` 当 falsy → 静默套用默认 `LINE2-MON-...` 前缀并继续,违反「caller 传的非前缀 id 必须 raise」不变量。

**根因修复**:改为 `sid = default if signal_id is None else signal_id` —— **仅 None 套默认**,显式 `""`(或任何非前缀串)走前缀校验并 `ValueError`。新增回归断言(空串与 `SIG-not-monitoring` 同样 raise)。

> 注:U-C1 的 `signal_id or default` 不受影响 —— Line-1 的 signal_id 仅是关联 tag,无前缀不变量,空串套默认是可接受行为。

## verify 确认的不变量

- **零 LLM + 隔离**:直接 import 仅 `monitoring/services/integrations/models/marketdata_snapshot/orchestration`,无 `backend.{api,broker,risk,llm,agents,agents_team,mirofish,data}`。
- **单一构造点**:runner 不自构造 InstructionPlan;SELL 仅来自 `builder.assemble_monitoring_plan`。
- **无双重执行**:仅经 `RouteCoordinator.route`(无 `SimulationExecutor` import/调用),路由前 `ledger.open_for_plan`(幂等 PLAN_DRAFTED)。
- **仅路由 VALIDATED SELL**:`BuilderEarlyReturn`→EARLY_RETURN、非 VALIDATED→REJECTED,均不发。停牌持仓 `partition_by_suspension` 干净降级不卖。

## 门禁

- `tests/orchestration/test_line2_daily_runner.py`:7 测试通过,`line2_daily_runner.py` cov **97%**。
- 触及文件 ruff `All checks passed!`;`scripts/redline-check.sh` 全绿(N-005 monitoring 隔离 + M-004 单一构造点 + X-018 orchestration 隔离不破)。
- 全量 **3642 passed / 11 skipped**(基线 3635 → +7)。
- import-clean AST 自检测试锁定 runner 无禁用子包直接 import(含 agents_team)。
