# Phase AA(模拟盘自动驾驶)codex review 摘要 — 2026-06-12

> 范围:AA-001..AA-005 全套未提交 diff(28 改动文件 + 7 新文件,~1200 行新增)。
> 调用:`codex review --uncommitted`(1 cycle,按 codex-review skill;前台 + `</dev/null`)。
> 结论:**1 P1 + 4 P2,全部修复 + 回归测试;0 P0。**

## Findings 与修复

| # | 级别 | 位置 | 问题 | 修复 |
|---|------|------|------|------|
| 1 | **P1** | `backend/services/acceptance_report.py` `_evaluate_full` | 政策切段后、下一次 EOD 重算前,昨日(旧政策)PASS 报告仍可授权 `feishu_interactive` —— 段 clamp 只影响**下一次** compute,绕过新政策应有的全新 45 日 warm-up | 新 `AcceptanceService.set_active_policy(policy_hash)`(boot 接线于 `main.py`);`_evaluate_full` 对最新报告做 `policy_hash != active` 拒绝(reason `full:latest_report_from_previous_policy`);active=None(legacy/manifest 失败)保持原行为。回归:`TestActivePolicyGate` 3 条 |
| 2 | P2 | `backend/main.py` 18:00 归因回调 | `broker.get_trades()` 是内存态——fill 与 18:00 之间重启则为空,会落一条**错误的空记录**并被幂等 `exists()` 锁死 | 改读 durable `broker_events`(`order_filled` + `execution_report_applied`,按 `occurred_at` 当日窗 + `sequence` 排序)重建 fill 事实;读失败**抛出** → scheduler retry/DEGRADED audit,绝不落坏证据 |
| 3 | P2 | `backend/main.py` 归因回调 entry-cost 源 | 只读 `open_theses()`,但 17:30 thesis sync 已把**当日清仓**标的的 thesis 关闭 —— 恰好是最需要持有期归因的 SELL 失去 entry_price | 新 `PositionThesisStore.closed_theses_on(trade_date)`(只读 fold,含同日 close);回调合并 open + 当日 closed。回归:`TestClosedThesesOn` |
| 4 | P2 | `backend/services/sim_reconciliation.py` | 同日 equity point ≠ 16:00 收盘点:EOD upsert 被吞而盘中点存在时,会基于陈旧点自动 RESOLVED | 新增 `equity_point.freshness` 校验:`snapshot_at >= snapshot.created_at`(收盘点与 EOD snapshot 共享 pipeline `started` 时间戳,确定性下界);不满足 = 失败 deviation → OPEN 冻结。回归:`TestEquityPointFreshness` 2 条 |
| 5 | P2 | `backend/api/performance.py` | `segment=current` 只 clamp 了曲线日期界,`trades` 全量进 `compute_core_metrics` → 胜率/盈亏比/换手混入旧政策成交 | clamp 生效时按 `traded_at.date()` 过滤 trades 再计算。回归:`TestPolicySegmentParam` |

## 门禁状态(修复后)
- pytest 全量:5189 passed / 13 skipped(基线 5084 → +105)
- ruff / redline-check(含新 `[AA-005]`)/ mypy(review + policy_manifest + sim_reconciliation,--follow-imports=silent)全绿
- 安全地基自查:仅 2 写端点不变(sim 自动对账走进程内 service,无新端点);LLM 零写入(review 模型无自由文本字段 + AST 隔离);单一构造点不破(redline + AST);RiskEngine 未触碰。
