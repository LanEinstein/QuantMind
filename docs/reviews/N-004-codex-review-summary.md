# N-004 Codex 跨模型代码审查报告

**任务**: N-004 — suspension 接入降级 + 异动 LLM 触发式(同一 utc_date 计数器)
**审查时间**: 2026-05-25
**审查轮次**: 1 cycle + read-only final verification
**最终判定**: ✅ 通过(经最终复核)
**审查范围**: `backend/monitoring/degrade.py` + `backend/services/cost_guard.py`(reserve_anomaly_llm_slot + get_daily_budget_state/assert_budget_allows 的 today 线程化)+ `tests/monitoring/test_degrade.py` + `tests/test_cost_guard_anomaly.py` + `tests/test_cost_guard_reservation.py`(date-brittle 修复)

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 发现问题总数 | 1(P2;0 P0/P1/P3) |
| 已修复 | 1 |
| 误报排除 | 0 |

## 第 1 轮 — `codex review --uncommitted`

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P2 | cost_guard.py `reserve_anomaly_llm_slot` | 仅 catch `DailyBudgetExceededError`;若 `reserve_budget` 内抛**非预算**的原始 Redis 错误(如 incrbyfloat 失败),异常会从这条 fail-closed/never-raise 的可选 Line-2 路径传播,且已 claim 的 count/dedup 不回滚。 | ✅ FIXED |

### 修复详情

`reserve_anomaly_llm_slot` 在 `reserve_budget` 调用外加 `except Exception` 分支(与现有 `except DailyBudgetExceededError` 并列):记 warning + 回滚 count(`_safe_decr`)+ 移除 dedup 成员(`_safe_srem`,transient 错误可重试)+ 返回 `None`。`DailyBudgetExceededError`(¥20 硬上限)路径保持原样(回滚 count、保留 dedup —— 当日预算耗尽,重试无意义)。回归测试 `test_reservation_layer_failure_rolls_back_and_skips`。

## 附带修复(pre-existing date-brittle 测试)

`test_cost_guard_reservation.py::test_daily_state_includes_in_flight_reservation`(M-005)在 2026-05-25 RED —— 根因:`get_daily_budget_state` 用 `get_daily_reserved` 读**真·今日** reserved key,而测试 `reserve_budget(today=_DATE=2026-05-24)`。本次给 `get_daily_budget_state`/`assert_budget_allows` 加可选 `today`(线程化到 `get_daily_reserved`;`get_daily_spent` 不动以免影响所有 spend mock),测试 pin `today=_DATE`。生产 `today=None` 行为不变。

## 最终验证(read-only closure check)

**复核状态**: EXECUTED · **复核判定**: **PASS** · 原问题 **RESOLVED** · 新增 P1 回归 **无**。(codex 跑了 14 测试通过。)

## 门禁

- pytest:**全量 3580 passed / 11 skipped**(基线 3506 → +74 新测试),0 失败。degrade 模块覆盖率 100%;reserve_anomaly_llm_slot 主路径全测。
- ruff:全绿(`backend.data` 经 per-line `# noqa: TID251`,保持 `backend.{llm,agents,mirofish}` 禁令)。
- redline-check:全绿。

## 红线遵守

- **Line-2 轮询零 LLM**:anomaly.scan / sell_signal / add_position 全程 0 LLM(纯量化);LLM 仅触发式经 `reserve_anomaly_llm_slot`。
- **触发式 LLM 去重 + 日上限 + 同一计数器**:dedup SET(`{code}:{kind}` 每 UTC 日一次)+ `max_anomaly_llm_per_day` cap + `reserve_budget` 写**同一** `llm:usage:{utc_date}:reserved` 计数器(防绕过 ¥20)。任何限额→返回 None 跳过可选 LLM,永不 raise。
- **停牌干净降级**:`partition_by_suspension` 用 `backend.data.suspension.is_suspended` 把停牌持仓移出活跃扫描集 → `PositionDegrade`(记录,非失败订单);缺 spot 快照不误判停牌(交由 data-quality 冻结)。

> 本报告由 Claude Code + Codex CLI 协同生成。
