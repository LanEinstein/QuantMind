# L-004 Codex 跨模型代码审查报告

**任务**: L-004 — RiskEngine 单股 check budget-aware + `concentration_exception` 独立再校验(P0-7-amendment-2026-05-24 §2.4 方案 A)
**审查时间**: 2026-05-24
**审查模型**: Claude Opus 4.7(实现/修复)+ Codex CLI gpt-5.5(独立审查)
**审查轮次**: 1 cycle review + 1 read-only final verification
**最终判定**: ✅ 通过(经最终复核 PASS,2 问题全 RESOLVED,0 新 P1 回归)

---

## 审查范围

`codex review --uncommitted`,6 文件:`backend/broker/models.py`(新增 `ConcentrationExceptionConfig` + RiskConfig 字段)、`backend/risk/engine.py`(check 5 budget-aware + `_grant_concentration_exception` 独立再校验 + validate_order 标记透出)、`backend/services/instruction_plan_builder.py`(`_build_risk_summary` 透传)、`config/risk.yaml`(concentration_exception 段)、`scripts/redline-check.sh`(`[L-004]`)、测试。

## 方案 A(不改 14-check 数)

exception 内置于 check 5(`_check_position_limit`),**不新增 check**;`InstructionPlan.risk_summary` min=max=14 schema 常量不变(redline `[L-004]` 守门)。

## 发现的问题(2 全修)

| # | 严重度 | 文件:行 | 问题 | 处理 |
|---|--------|---------|------|------|
| 1 | P1 | engine.py `_grant_concentration_exception` | 绝对 1 手上限只校验 `order.volume`,持有 1 手 510300 再买 1 手(结果 2 手)仍放行 → 绕过单股限。 | **FIXED** |
| 2 | P2 | engine.py `validate_order` + builder `_build_risk_summary` | check 5 授予的 `concentration_exception_granted` 标注被 validate_order 的裸 passed=True 丢弃,builder 14 行 message 全空 → 例外计划丢失审计/飞书确认标记。 | **FIXED** |

### 修复详解

1. **P1 结果仓位封顶**:`_check_position_limit` 把 `proposed_shares = existing_shares + order.volume` 传入 helper;helper 比较 `proposed_shares <= max_lots × volume_lot_size`(对**结果仓位**封顶,非单笔)。回归 `test_existing_position_stacking_over_lot_cap_rejected`(持 100 + 买 100 = 200 > 1 手 → 拒)。
2. **P2 标记透出**:`validate_order` 捕获 check 5 结果,全通过时若 check 5 授予例外(passed=True + message 含 granted)则返回该带标注结果而非裸 pass;`_build_risk_summary` 在 `result.passed` 分支把该 message 落到匹配 rule 行(其余行仍空)。回归 `test_etf_exception_granted` 经 validate_order 断言标记透出。

## 独立再校验(防单点绕过,§2.3)

`_grant_concentration_exception` 返回 True 须**同时**满足:① 上游 flag(Micro/Small 意图)② config `enabled` ③ `stock_meta.board == etf`(None→fail-close,个股永不享有)④ `order.code ∈` RiskEngine **自有** `etf_whitelist`(非取自 budget_policy)⑤ **结果仓位** ≤ `max_lots × lot_size`。flag 单独永不足以放行。

## 最终验证(read-only 复核)

`codex exec -s read-only`(前台 `</dev/null` 避免 stdin 死锁):**PASS**,2 问题全 RESOLVED,NEW P1 regressions NONE,正常 14 行 pass 仍全空、标记仅真授予时出现。

## 门禁

- pytest 全量 **3325 passed / 11 skipped**(concentration 12 例);`backend/risk/engine.py` 覆盖率 **99%**(>95% gate)。
- ruff:engine + builder + 测试全绿。
- `scripts/redline-check.sh`:全绿,新 `[L-004]`(concentration_exception 段存在 + whitelist 与 budget_tiers 一致 + `_grant_concentration_exception` 存在 + risk_summary min=max=14)。

## 红线确认

- RiskEngine 仍**纯函数读输入字段**;flag 是显式输入参数(默认 False,既有 96 调用方不变),无可被 LLM/builder 自由设置的新分支(实质条件由引擎自有 config + stock_meta 再派生)。
- ETF + 白名单 + Micro/Small(L-003 已锁)才有例外;个股永不享有;结果仓位绝对 1 手 + 飞书确认标记。
- 14-check 数不变(方案 A),risk_summary schema 常量未动。

---

> 本报告由 Claude Code(Opus 4.7)+ Codex CLI(gpt-5.5)协同生成。
