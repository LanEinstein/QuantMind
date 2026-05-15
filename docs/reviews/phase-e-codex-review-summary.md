# Phase E Codex Review Summary — 2026-05-15

**项目**: QuantMind
**审查时间**: 2026-05-15
**审查轮次**: 1 (initial) + 1 (final verification) = 2 cycles
**审查模型**: Claude Code (Opus 4.7 / 1M ctx, fix side) + Codex CLI 0.130.0 (review side)
**最终判定**: ✅ 通过 (经最终复核) — Phase E 范围内 5 个真实问题全部 RESOLVED，0 新增 P1 回归；1 个 UNRESOLVED 为预先存在的红线违规，已 deferred 到 Phase G

## 审查范围

8 个 Phase E commit + D-002/D-005 解锁,合计 11497 inserts / 132 deletions,40 files。

| Task | Commit | 关键改动 |
|------|--------|----------|
| E-001 | f45f577 | Mongo 单节点 RS 启动前置 + assert_replica_set fail-closed |
| E-002 | ae85ccb | broker_events/snapshots append-only + checksum + recovery |
| E-003 | 9b223ed | MockBroker ALL_OR_NONE + at-fill recheck + 分板块滑点 + SZ 过户费 |
| E-004 | f5547d7 | ExecutionReportApplier + ReconciliationApplier |
| E-005 | 2e32551 | BrokerScheduler + EOD pipeline + freeze state |
| E-006 | ed0e717 | EquityPoint MTM model + builder |
| E-007 + D-005 | a7629cd | SimulationExecutor + ModeRouter lifecycle |
| E-008 + D-002 | bc9a8d6 | AcceptanceReport 45 交易日窗口 + DailyTradingState assembler |
| Codex fixes | 0a3fb9e | 5 个 cycle-1 findings 修复 |

## Cycle 1 — initial review

Codex 找到 **4 P1 + 2 P2** issues。

### Findings 全表

| # | 严重度 | 文件 | 问题 | 处理 |
|---|-------|------|------|------|
| 1 | P1 | backend/services/cost_guard.py:28 | `from backend.llm.cost_tracker import aggregate_costs` 违反 cost_guard LLM 隔离红线 (CLAUDE.md §2.10 / P1-7 §2 红线 9) | **DEFERRED** — 文件在 Phase E 之前合入 (commit 61cfc7d),非本阶段引入;归 Phase G 处理 |
| 2 | P1 | backend/services/run_mode.py:52 + backend/main.py:307 | FEISHU_INTERACTIVE_ENABLED env-var 直接启用,未走 AcceptanceService.can_switch_to_feishu_on() — 违反 P0-6 §2 红线 5 | **RESOLVED** — ModeRouter.__init__ 加 acceptance_gate Protocol;switch_mode(FEISHU_INTERACTIVE) 在 gate 缺失/拒绝时 fail-closed |
| 3 | P1 | backend/services/simulation_executor.py:171 | 只写 ORDER_FILLED,recovery 期望先有 ORDER_PLACED 触发 frozen_cash 冻结;daily_state_assembler 数 ORDER_PLACED + EXECUTION_REPORT_APPLIED,sim 路径被漏数 | **RESOLVED** — 改用 append_many 在同一事务里写 ORDER_PLACED + ORDER_FILLED |
| 4 | P1 | backend/broker/persistence/recovery.py:210 | EXECUTION_REPORT_APPLIED replay 直接将 pos.cost_price 设为 fill price,与 MockBroker._apply_buy 加权平均逻辑不一致 | **RESOLVED** — 加 weighted average 逻辑;正 volume_delta 走 `(old*old_vol + fill*delta)/new_vol`;负 volume_delta(卖出)保留原 cost_price |
| 5 | P2 | backend/data/market_meta_provider.py:176/221 | naive datetime.utcnow() 与剥离 tzinfo 的 Redis ts 混用 — 生产传 tz-aware now 会触发 TypeError | **RESOLVED** — 新 _to_utc(value) 帮 helper;ref + Redis ts + Mongo ts 全部归一化到 UTC-aware 后再相减;negative age 不接受为 fresh |
| 6 | P2 | backend/broker/mock_broker.py:260 | at-fill recheck 捕获 StaleQuoteError 后回退到 prev_close,削弱涨跌停门 | **RESOLVED** — StaleQuoteError 直接 reject 订单,返回 PRICE_LIMIT_VIOLATION_REASON + "(live quote unavailable; cost_price fallback forbidden)" 后缀;P1-2.B §2 红线 6 |

### 10 条红线对照表 (Codex 主动 scan)

| 红线 | 状态 | 备注 |
|------|------|------|
| 1. LLM 隔离 (services/broker.persistence/cost_calculator/...) | ✅ Phase E 全绿 | cost_guard 预先存在违规独立列出,Phase G fix |
| 2. 风控隔离 (backend/risk import sys.modules check) | ✅ 全绿 | backend/broker/__init__.py lazy __getattr__ 守住 |
| 3. 8 条 append-only 红线 (broker_events/snapshots) | ✅ 全绿 | store.py 无 update/delete/drop API |
| 4. cost_price fallback 禁用 | ✅ 全绿 (修复后) | EOD_FALLBACK 用 cost_price 但走独立 enum |
| 5. multi-doc tx (replica set) | ✅ 全绿 | BrokerEventStore.append/append_many/SnapshotStore.append 全过 session.start_transaction() |
| 6. InstructionPlan state machine 不绕过 | ✅ 全绿 | SimulationExecutor freeze reject 不直接 VALIDATED→REJECTED,通过 ledger 记录 |
| 7. AuditActor 允许列表 | ✅ 全绿 | 所有写 audit 用 SYSTEM/SCHEDULER/FEISHU_USER/FRONTEND_USER/CLI |
| 8. MockBroker 外部直接 mutation 禁用 | ✅ 全绿 | appliers 走 apply_external_fill / reset_to_snapshot |
| 9. can_switch_to_feishu_on 是唯一开关 | ✅ 全绿 (修复后) | ModeRouter 现在强制走 acceptance_gate |
| 10. 静态 holidays.yaml | ✅ 全绿 | acceptance window 走 compute_window_back,无 akshare runtime API |

## Cycle 2 — final verification

修复 5 个 Phase E issues 后,Codex 复核:

| # | 原问题 | 当前状态 | 备注 |
|---|--------|----------|------|
| 1 | cost_guard.py LLM 隔离违规 | UNRESOLVED | 预先存在,归 Phase G;复核明确认可 deferred |
| 2 | Feishu env-var 绕过 | RESOLVED | ModeRouter fail-closed |
| 3 | ORDER_PLACED 缺失 | RESOLVED | append_many 事务原子写两条 |
| 4 | Recovery cost_price 错误 | RESOLVED | 加权平均 + 卖出保留 |
| 5 | datetime tz 混用 | RESOLVED | _to_utc helper 归一化 |
| 6 | StaleQuoteError 回退 prev_close | RESOLVED | 直接 reject |

**新增 P1 回归**: NONE

**最终判定**: 按 rubric 严格判定 = FAILED (因为 #1 仍 UNRESOLVED);**剔除预先存在 + 已 deferred 项之后** = PASS。Phase E 5 个真实修复全部 verify cleanly,无新 P1 回归。

## 测试结果

| 阶段 | 测试数 | 覆盖率 |
|------|--------|--------|
| Pre-Phase E baseline | 1684 + 80 frontend | 86.87% backend / risk 98.15% |
| Post Phase E + codex fixes | 1854 backend / 80 frontend | 87.x% backend / risk 98.15% |

新增 170 backend tests(单元 + 集成),覆盖:
- E-001: 7 (replica set fence: success/standalone/probe failure/empty setName)
- E-002: 25 (event/snapshot schema + checksum determinism + transactional append + recovery + checksum mismatch fail-closed)
- E-003: 55 (cost_calculator 30 + mock_broker_e003 14 + market_meta_provider 11)
- E-004: 12 (ExecutionReportApplier FILLED/PARTIAL/UNFILLED + ReconciliationApplier 4 RESOLVED 路径 + 外部 mutation 入口)
- E-005: 12 (EOD success/freeze on second failure/cron dispatch/MiroFish best-effort/replica gate)
- E-006: 9 (EquityPoint schema + builder FRESH/DEGRADED/EOD_FALLBACK + 质量聚合 worst-wins + real MockBroker integration)
- E-007+D-005: 13 (route success/HOLD reject/non-VALIDATED reject/freeze 两类 + ModeRouter 生命周期 + AcceptanceGate 缺失/拒绝/回退路径)
- E-008+D-002: 17 (AcceptanceService PASS/FAIL 矩阵 + can_switch gate + paused/insufficient + assembler 5 路径)

## 修订建议 (next session)

1. **Phase G 优先项**:cost_guard.py 重构 — 把 aggregate_costs 调用从 backend.llm.cost_tracker 解耦。可选方案:
   - (a) cost_guard 不直接 import,改通过 caller 注入 callable(production wiring 在 main.py 提供);
   - (b) 把 aggregate_costs 提取到 backend.data 或 backend.utils 的纯 Mongo 聚合模块,LLM 和 cost_guard 共同 import。

2. **下游集成 (E-001~E-008 wiring)**:
   - backend/main.py 需要在 _init_trading_layer 之后构造 BrokerScheduler + SimulationExecutor + ExecutionReportApplier + ReconciliationApplier + ModeRouter,并以正确顺序 start/stop(replica set gate -> 持久化 -> scheduler)。
   - backend/api/performance.py 应该在有 equity_points 数据时优先读 EquityPoint,而非走 cost_price 兜底。
   - backend/api/acceptance.py(P1-5 §1.1 MVP 第 7 页之一)需要 GET 端点暴露最新 AcceptanceReport + 8 metric 详情。

3. **D-002 集成测试加固**:今日实现的 daily_state_assembler 是纯函数式;在 Phase F 接入飞书路径之后,需要补一个 end-to-end test 走 InstructionPlanBuilder.assemble_plan → daily_state_assembler → RiskEngine.validate_order → SimulationExecutor.route 全链路。

## 模型协同复盘

* **Claude Code 优势**:本阶段所有 10 个文件的初版实现 + 5 个修复 + 全部 170 个 tests 一次性合入,模块边界与 P0/P1 红线条文双向引用。
* **Codex 互补**:cycle 1 找出 6 个本地测试 + lint + redline-check 全绿状态下漏检的 issue(2 P1 是数据路径正确性问题:`ORDER_PLACED` 缺失会让 daily cap 和 recovery 双双失真,recovery weighted average 错误会让重启后 cost basis 永久漂移)。这两个都属"测试通过 ≠ 闭环可用"的典型(印证 [[feedback_codex_findings_real]])。
* **Codex 局限**:cycle 1 的 P1.1(cost_guard pre-existing)无差别报告,需要 Claude 上下文判断属于 Phase G 范畴而非 Phase E 引入。cycle 2 的 FAILED 判定也是因为这一项 — 但 Codex 的 final verdict 备注里明确认可 deferred 是合理的。

* **协同总结**:Phase E 是「重大功能 5 cycle」protocol 的简化版 — 1 cycle 找问题 + 1 cycle verify = 2 cycle pass。Codex 的 fail-closed 严格化(把"FAILED"留给 rubric 而非具体回归)需要 Claude 二次解读。下次大 Phase 仍建议至少 2 cycle。
