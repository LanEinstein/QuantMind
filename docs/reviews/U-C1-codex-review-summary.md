# U-C1 Codex Review Summary — Line-1 生产 runner

> Task: U-C1 `backend/orchestration/line1_runner.py`(Line-1 全市场选股生产编排）。
> Cycle: 1 review (`codex review --uncommitted`) + 1 read-only verify (`codex exec --sandbox read-only`)。
> 结论:**3 P2 全部根因修复 + 回归测试;verify 判定 no remaining P0/P1/P2**。

## 范围

新增 `backend/orchestration/line1_runner.py`(Line-1 生产 runner)+ `tests/orchestration/test_line1_runner.py`(11 测试,97% 覆盖)+ 精确化 `tests/orchestration/test_no_stray_route_callers.py`(U-B2 守门测试,为 runner 合法调 `RouteCoordinator.route` 让路)。

runner 链:`screen → BudgetTierPolicy(¥10万=Normal)→ CandidateSelector → 一次 4-agent 辩论(run_shortlist,cost_guard 真·预留)→ to_fund_manager_output → assemble_plan(14-check 单一构造点)→ RouteCoordinator`。import-clean(无 `backend.{risk,broker,data,llm,agents,mirofish,api}`;重对象经注入式 `Line1ContextProvider` 由 U-D1 调度层构造)。

## Codex cycle 1 — 3 P2

### P2-1 ETF concentration-exception 模板在 BUY 路径不可达 → 死分支
**发现**:runner 按 `lead_affordability.concentration_exception` 选 `ETF_CONCENTRATION_EXCEPTION` 模板,但 `assemble_plan` 的 **BUY 路径(`instruction_plan_builder.py:845` 的 `validate_order`)根本没传 `concentration_exception`**(只有 Line-2 monitoring 路径 `:1061` 传了)。因此 over-15% 白名单 ETF 的 BUY 会被 RiskEngine fail-closed REJECTED,该模板分支对 VALIDATED plan 永不可达。

**根因修复(scoped + 上报)**:U-C1 是 **runner** 任务,不顺带改单一构造点 builder + RiskConfig concentration setup(那是 Micro/Small tier 的 builder 增强,超出 Normal-tier ¥10万 MVP 范围,且 concentration_exception 在 Normal tier 永不授予)。runner 现对 VALIDATED BUY 一律 `NORMAL_COMPLIANT`(builder 只会 validate 标准 15% 内的 BUY),删除死分支 + 移除未用的 `lead_affordability` 参数,并加注释说明「BUY 路径 concentration_exception 穿透是 builder follow-on,已上报 owner」。fail-closed,不碰 M-004。**已上报 owner 的 builder follow-on**:`assemble_plan` BUY 路径未将 `concentration_exception` 穿透进 14-check(仅 monitoring 路径穿透),Micro/Small tier 的 over-15% 白名单 ETF 买入当前 fail-closed 到 REJECTED。

### P2-2 VALIDATED 非-BUY(SELL)plan 落到 BUY-only renderer → 崩溃
**发现**:若 fund_manager 提议卖出且账户持有 lead,`assemble_plan` 可产出 VALIDATED SELL;原 `_route` 仅排除 HOLD/REJECTED,SELL 会落到 BUY-only 的 `render_buy_signal` → raise → 崩日常运行。

**根因修复**:`_route` 在 HOLD + 非-VALIDATED 检查之后、render 之前新增 `plan.side is not InstructionSide.BUY` 守门,返回新 outcome `Line1Outcome.NON_BUY_DISCARDED`(log warning)。Line-1 只路由 BUY(SELL 是 Line-2 确定性监控的职责,不在 Line-1 自动卖)。新增回归测试 `test_validated_sell_is_discarded_not_rendered_as_buy`(`hold_lead=True` 使 SELL 可 VALIDATE)。

### P2-3 守门测试 `ImportFrom.names` 旁路
**发现**:精确化后的 `_imports_simulation_executor` 只查 `node.module`,漏掉 `from backend.services import simulation_executor`(模块名在 `names` 里)与相对 `from . import simulation_executor`(`node.module is None`)——这类模块仍能 import executor 模块并调 `.route(...)` 却被整段跳过 = 守门旁路。

**根因修复**:同时检查 `ImportFrom.names` 别名 + 纯 `Import` 别名;扩展 planted-violation 自检覆盖三种 import 形式。

## 附带修复(同一 review 触及,根因修)

- **生产缺口:无人 `open_for_plan`**。SimulationExecutor.route 与 InstructionDispatcher 都假设 ledger 已开,但全仓**无任何生产调用方** open_for_plan(仅测试调)。runner 作为 plan 生命周期生产编排根,在路由前对每个构造出的 plan 幂等 `ledger.open_for_plan(plan)`(PLAN_DRAFTED),补上该缺口。

## Cycle 1 verify(read-only)

复述三处修复跑 `codex exec --sandbox read-only`:**No remaining P0/P1/P2 found**。Codex 直接 in-memory 探针确认:① runner 无 `ETF_CONCENTRATION_EXCEPTION`/`concentration_exception`/`lead_affordability` 路径;② VALIDATED SELL 探针返回 `non_buy_discarded` + `route_outcome=None` 不触发 renderer;③ 守门对 4 种 import 形式(`from pkg.mod import X` / `from pkg import mod` / `from . import mod` / `import pkg.mod as x`)均返回 True。

## 门禁

- `tests/orchestration/test_line1_runner.py`:11 测试通过,`line1_runner.py` cov **97%**。
- 触及文件 ruff `All checks passed!`;`scripts/redline-check.sh` 全绿(X-018 orchestration 隔离 + N-005 monitoring 隔离 + M-004 单一构造点 AST 不破)。
- 全量 **3635 passed / 11 skipped**(基线 3624 → +11)。
- import-clean AST 自检测试锁定 runner 无 `backend.{risk,broker,data,llm,agents,mirofish,api}` 直接 import。
