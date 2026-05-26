# U-C4 Codex Review Summary — concentration_exception 入场路径端到端穿透

> Task: U-C4 —— 把已批准的预算自适应 ETF `concentration_exception`(P0-7-amendment-2026-05-24 §2.3/§2.4)正确接线到两条**入场**构造点。
> 这是 U-C1 codex review 上报的 builder follow-on(见 `docs/reviews/U-C1-codex-review-summary.md` P2-1)。
> **不改决策边界 → 无 amendment**(只是实现既有 amendment)。
> Cycle: 1 review (`codex review --uncommitted`) + 1 read-only verify (`codex exec --sandbox read-only`),均 `</dev/null` 前台。
> 结论:**cycle 1 的 1 个 P2 根因修复 + 回归验证;verify 判定 no remaining P0/P1/P2**。

## 范围

把「意图 flag」`concentration_exception` 送达两条**入场**路径的 RiskEngine,**授予与否仍由引擎独立判定**(flag 非绕过):

- **Line-1 BUY**(`backend/services/instruction_plan_builder.py`):`AssemblyContext` 新增 `concentration_exception: bool = False` 字段;`assemble_plan` BUY/SELL 路径 `:845` 的 `validate_order(...)` 新增 `concentration_exception=context.concentration_exception`(对齐 monitoring 路径 `:1062` 写法)。
- **Line-2 ADD**(`backend/monitoring/add_position.py`):`make_add_context` 新增 keyword `concentration_exception: bool = False`,写入返回的 `MonitoringAssemblyContext.concentration_exception`(该字段早已存在,builder monitoring 路径早已穿透,只是没人填 True)。`make_sell_context` **不动**(SELL 是退出,卖出不增加集中度)。
- **U-C1 runner**(`backend/orchestration/line1_runner.py`):`build_lead_context` 成为 flag 的**单一入口**,把预算档 affordability 的 `concentration_exception` 同时穿进**辩论 `TeamContext`** 与 **`AssemblyContext`**;`_route` 改为读引擎权威结果 `concentration_exception_granted`(在 `plan.risk_summary` 的 `position_limit` 行)选 `ETF_CONCENTRATION_EXCEPTION`(带飞书确认块)vs `NORMAL_COMPLIANT`,而非 re-derive affordability flag。删掉 #37 的「follow-on 上报」死分支注释,改为「已穿透」。

效果:Micro/Small 档 over-15% 白名单 ETF 的 BUY/ADD 不再 fail-closed→REJECTED,owner 能收到该合规信号(带人工确认)。Normal 档(¥10万 MVP)永不授予例外故无感。

## Codex cycle 1 — 1 P2(根因修复)

### P2 辩论上下文未穿透 → 决策/审计与路由结果自相矛盾
**发现**:Small/Micro ETF 例外买入时,flag 只穿进了 `make_assembly_context`(辩论**之后**),但更早的 `run_shortlist` 调用里 `risk_gate_node` 用的是 `lead_ctx.team_context`,其 `concentration_exception` 仍为 `False` → 辩论对**同一笔订单**记录 `risk_passed=False` / `decision=REJECTED`,而随后 `assemble_plan` 却 VALIDATE 并路由 → 辩论 state/audit 与路由出的 plan 互相矛盾。

**根因修复**:把 flag 的入口收敛到**单一点** `provider.build_lead_context(lead, *, concentration_exception)`:provider 把它同时写进辩论 `TeamContext`(辩论 risk-gate 不再记录矛盾的 REJECTED)与 `AssemblyContext`(权威 14-check),消除分叉 by-construction。相应地 `AssemblyContextFactory` Protocol 不再单独带该参数(避免同值两处传、可能漂移)。runner 由 `assessment.affordable` 取 lead 的 affordability flag,经 `build_lead_context` 一次性传入。

## 红线守门(本任务最重要,已 verify)

- **RiskEngine `_grant_concentration_exception` 仍是唯一授予方**(`backend/risk/engine.py:438`):flag=True **单独不足以**授予 —— 需同时满足 `enabled` + `board==etf` + 在引擎自有 `etf_whitelist` + 结果持仓 ≤ `max_lots × lot_size`;**个股 / 非白名单 ETF / 超 max_lots 一律仍 REJECTED**。本任务**未削弱**该校验,只把意图 flag 正确送达。
- **InstructionPlan 单一构造点(M-004)不破**:`grep "InstructionPlan(" ⊆ {model, instruction_plan_builder, tests}`,redline AST 守门 green。
- **orchestration 隔离(X-018)/ monitoring 隔离(N-005)/ L-004 14-check 不变**:redline-check 全绿。

## Cycle 1 verify(read-only)

复述四处修复跑 `codex exec --sandbox read-only`:**No remaining P0/P1/P2 found**。Codex 直接验证:① `_grant_concentration_exception` 仍 sole grantor(flag 仅 enable,引擎仍要 ETF+白名单+enabled+≤max_lots);② 四处穿透点正确(builder BUY `:845` / monitoring `:1062` / `make_add_context` / `make_sell_context` 保持默认 False);③ runner 从引擎 marker(非上游 flag)选模板;④ 单一构造点 AST 仍只在 model+builder。focused 18 测试 + 引擎集中度套件(个股/非白名单/超手)全绿。(Codex sandbox 跑全 Line-1 orchestration 路径因 agents graph 超时,与既有非-U-C4 happy-path 同样超时,非本任务回归 —— 本地全量 3649 passed 已证。)

## 门禁

- 触及文件 ruff:`All checks passed!`。
- `scripts/redline-check.sh`:全绿(M-004 单一构造点 AST / X-018 orchestration 隔离 / N-005 monitoring 隔离 / L-004 concentration 再校验 + 14-check 均不破)。
- 覆盖率:`line1_runner.py` 97% / `add_position.py` 94% / `instruction_plan_builder.py` 88%(均 >70%)。
- 全量 **3649 passed / 11 skipped**(基线 U-C2 后 3642 → +7 新测试)。

## 新增/覆盖测试

- `tests/test_instruction_plan_builder_assemble.py::TestConcentrationExceptionThreading`:① Small over-15% 白名单 ETF + flag → VALIDATED(断言 `risk_summary` 含 `concentration_exception_granted`);② 无 flag 同输入 → REJECTED(`position_limit`);③ **对抗**(个股 600000 / 非白名单 ETF 510999 + flag → 仍 REJECTED,无 granted marker)。
- `tests/monitoring/test_add_position.py`:① `make_add_context(concentration_exception=True)` → over-15% 白名单 ETF ADD → VALIDATED;② **对抗**个股 ADD flag=True → 仍 REJECTED。
- `tests/orchestration/test_line1_runner.py::test_small_tier_etf_routes_concentration_exception_template`:Small 档(¥5,000)ETF lead(1 手 ¥1,200 > 15%)→ ROUTED + `tier==small` + `concentration_exception_granted` + 飞书文本走 `ETF_CONCENTRATION_EXCEPTION` 模板(含「确认执行请回复:确认 <id>」);Normal 档普通 BUY 仍 `NORMAL_COMPLIANT`(既有 `test_feishu_mode_routes_validated_buy`)。
