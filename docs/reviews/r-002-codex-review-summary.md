# R-002(策略生命周期 + rqalpha 差分 oracle)codex review 摘要 — 2026-06-12

> 范围:R-002 未提交 diff(`backend/strategy_evolution/{lifecycle,backtest_oracle,anti_overfit}.py` + 4 测试文件 + redline `[R-002]` + 模块 CLAUDE.md)。
> 调用:`codex review --uncommitted`(1 cycle;codex 自行复跑了 redline + mypy --strict 均过)。
> 结论:**3 P1,全部修复 + 回归测试;0 P0/P2。**

## Findings 与修复

| # | 级别 | 位置 | 问题 | 修复 |
|---|------|------|------|------|
| 1 | **P1** | `backtest_oracle.py` `compare_equity_curves` | 截断/错日历的 oracle 曲线只要与 MockBroker 共享 1 个平静交易日就能 `divergent_days==0` → **CONSISTENT**,而绝大部分窗口根本没被交叉校验(CONSISTENT 是晋升 pass 条件) | 新 `MIN_OVERLAP_RATIO=0.9`:共享日必须覆盖 MockBroker 曲线(权威窗口)≥90%,否则 `INSUFFICIENT_OVERLAP`(fail-closed)。回归:截断 1/20 日、80% 边界 |
| 2 | **P1** | `backtest_oracle.py` `run_differential_check` | 缓存/配错的 runner 返回**另一个 strategy_hash** 的结果仍被拿来比对,可对错误策略报 CONSISTENT | 三重 hash 纪律:`compare_equity_curves` 对任一输入 hash ≠ 请求 hash 直接 raise(caller bug 必须响);`run_differential_check` 对 mock 输入错 hash raise、对 oracle 返回错 hash 降级 `ORACLE_UNAVAILABLE`(留 detail)。回归 3 条 |
| 3 | **P1** | `lifecycle.py` `MongoLifecycleLedger.record_transition` | 只校验 caller 提供的 record:重试持陈旧 CANDIDATE 视图可在 RETIRED 之后 append candidate→shadow 事件,`current_state` 信最后一条 → **终态被改写** | append 前重新 fold 账本:新 `StaleLifecycleRecordError` —— 无 lifecycle / caller 状态 ≠ 折叠态均拒,转移校验以折叠态为准。回归:RETIRED 后陈旧重试被拒且终态不变 |

## 门禁状态(修复后)
- tests/strategy_evolution 55 passed;全量 pytest 5246 passed(见 SESSION_LOG)
- ruff / redline(含新 `[R-002]`)/ mypy --strict(3 新模块)全绿
- 红线自查:rqalpha 仅 test-time(redline grep + AST 契约 + lazy import + 无 vendor 目录);MockBroker 单一镜像未动;ACTIVE 晋升 registry-gated(R-001);RETIRED 终态 + no-re-proposal;LICENSE 已读(Apache 2.0 + 商用需米筐授权 → 非商用可选依赖,不 vendor 不抄)。
