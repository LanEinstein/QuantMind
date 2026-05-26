# U-D4c — Codex Review Summary (信号产出修复:Line-1 多候选辩论 + 日 ¥20→¥100)

> 任务:U-D4c(plan.html phase U,depends U-D4b,P0)。
> Amendment:`docs/decisions/P1-7-amendment-2026-05-26-multi-candidate-debate-and-100rmb-budget.md`。
> 命令:`codex review --uncommitted`(cycle 1)+ `codex exec --sandbox read-only`(verify)。codex-cli 0.133.0,全程 `</dev/null` 前台(防 stdin deadlock)。

## 范围
让 Line-1 按 `CandidateSelector` shortlist **顺序多候选辩论**(REJECTED/HOLD/DEGRADE/非-BUY → fallthrough 下一只),收集 VALIDATED BUY 篮子(basket,owner 拍板);日 LLM hard ¥20→¥100;fan-out 受 `max_debates_per_day`(默认8)+ 日 ¥100 真·预留双重 fail-closed bound。改动:`line1_runner.py`(篮子 loop + `CommittedBuy`/`RoutedBuy`/`Line1SelectionMode`/`BUDGET_EXHAUSTED`)、`line1_context_provider.py`(committed-buy cash/positions 线程)、`cost_guard.py`(¥20→¥100)、`redline-check.sh` [M-005] 断言、+ 测试。

## Cycle 1 findings(3:1 P1 + 2 P2)—— 全修

| # | 级别 | 文件 | 问题 | 修复 |
|---|------|------|------|------|
| 1 | **P1** | `line1_context_provider.py` build_lead_context | 篮子模式下 `DailyTradingState.today_new_instruction_count` 仍用 `rs.today_instruction_count`,不含本 run 已路由的 BUY → check-10(≤5单/日)对篮子不 bind(如当日已 4 单仍可路由多只)。 | `today_new_instruction_count = rs.today_instruction_count + len(committed)` → check-10 跨篮子正确 bind。 |
| 2 | **P2** | `line1_context_provider.py` `_apply_committed` | committed 仅按 notional 扣现金,但 RiskEngine check-4 按 `price×volume×1.001` 校验 → 后续候选现金被高估(各自单独通过,合计超原始现金)。 | 扣 `notional × _FUND_SUFFICIENCY_BUFFER`(1.001)镜像 check-4;`total_assets` 重算(累计手续费=真实回撤)。 |
| 3 | **P2** | `line1_runner.py` run() loop | `BuilderEarlyReturn` 是 run 级冻结(切换/对账ticket/熔断冷却/数据质量/EOD)但 loop 当普通非路由候选继续辩论余下 shortlist → 浪费 LLM 预算/debate slot,且可能误以 BUDGET_EXHAUSTED 收尾而非 surface 冻结。 | `if last.outcome is EARLY_RETURN: break` → 停止 walk,surface 冻结。 |

新增针对性回归:`test_basket_bounded_by_daily_order_cap`(P1)、`test_basket_stops_on_run_level_freeze`(P2)、threading 测试加 buffer + count 断言(P2/P1)。

## Verify(read-only)
3 findings 全 **RESOLVED**(逐条 line 证据);确认这些修复**未引入**新 P0/P1/P2 correctness bug。

## 门禁
- `pytest --cov-fail-under=70`:**3837 passed / 13 skipped**(基线 3828→+9:6 篮子 loop 测试 + 2 provider threading + 2 codex-fix 测试 − 1 重命名/合并),cov **90.47%**。
- `ruff`:全绿。
- `bash scripts/redline-check.sh`:全绿(M-004 单一构造点 / M-005 4 ceilings[日 hard 现 ¥100]/ X-018 / N-005 / L-004 / K-006)。
- 安全地基 + RiskEngine 红线全留(永禁真实下单 / 飞书人工 / 127.0.0.1 / LLM 不写决策 / 单一构造点 / 禁涨停 BUY / long-only / 仓位三连 / 熔断)。lead 涨停被拒 = RiskEngine 正确履职 → 辩下一只,绝不绕过拒单。

## 结论
P0/P1/P2 清零 → 可 commit。
