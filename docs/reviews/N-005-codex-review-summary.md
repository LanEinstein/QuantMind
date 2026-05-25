# N-005 Codex 跨模型代码审查报告

**任务**: N-005 — `backend/monitoring/` CLAUDE.md + 模块契约 + redline `[N-005]` + ★MVP gate 双线端到端
**审查时间**: 2026-05-25
**审查轮次**: codex 1-cycle(N-005)+ read-only verify + **用户 /code-review(4 并行 reviewer 跨任务全 Phase N 审查)**+ /code-review 修复后 codex 复审 1-cycle + verify
**最终判定**: ✅ 通过(经多轮复核)
**审查范围**: `tests/monitoring/test_mvp_e2e.py` + `test_module_contract.py` + `scripts/redline-check.sh [N-005]` + `backend/monitoring/CLAUDE.md` + /code-review 触发的生产修复(`instruction_plan_builder.py` / `sell_signal.py` / `add_position.py`)

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 发现问题总数 | 6(P2 × 4 / Medium × 2[/code-review];0 P0/P1)|
| 已修复 | 6(2 codex N-005 + 2 /code-review Medium + 2 codex 复审 P2 + 1 /code-review Low)|
| 误报排除 | 0 |
| 接受为已知 low | 2(reserve_budget 非预算异常 reserved 泄漏 fail-closed+TTL 自愈;SELL post_cash 不含手续费 display-only)|

## 第 1 轮 — codex `review --uncommitted`(N-005 staged)

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P2 | test_module_contract.py + redline `[N-005]` | guard 未禁 `agents_team`(Line-1 LLM 辩论路径 run_shortlist/fund_manager)→ monitoring 可 import 它把多 agent LLM 漏回 Line-2。 | ✅ FIXED(加 `agents_team` 入 FORBIDDEN + grep)|
| 2 | P2 | redline-check.sh `[N-005]` | grep 只匹配 dotted import,漏 `from backend import llm` / `from ..agents import x`。 | ✅ FIXED(扩 ERE 覆盖 dotted/name-level/relative 三形式;AST pytest 为权威 guard)|

## /code-review(用户触发,4 并行 reviewer,跨整个 Phase N)

整体判定:**substantially clean,无 Critical/High**。z-score/EWMA/布林/RSI/ATR/Van Tharp 数学全部追踪验证正确;¥20 硬上限不可绕过;4 cost 常量字节不变;反注入(含 U+2028/2029)稳健。发现:

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 3 | Medium | instruction_plan_builder.py `_record_audit` | Line-2 早返(冻结)的 audit 事件无 `LINE2-MON-` 判别符,与 Line-1 冻结无法区分。 | ✅ FIXED(`_record_audit` 加 `extra_payload`,monitoring 早返 stamp `signal_id`+`line=line2`)|
| 4 | Medium | instruction_plan_builder.py `_build_monitoring_plan` | SELL 在 `total_assets<=0` 时 `_derive_position_summary` 抛 ValueError(RiskEngine check 5/8 对 SELL 早返 passed 不挡零 NAV)→ 退出路径崩溃而非干净降级。 | ✅ FIXED(Line-2 scoped 零 NAV guard → 零化 PositionSummary,不动共享 Line-1 helper)|
| 5 | Low | sell_signal.py / add_position.py | 持仓码后缀(.SH/.SZ)未归一 → 后缀持仓静默丢 SELL/ADD(fail-open miss)。 | ✅ FIXED(见下,end-to-end)|

## 第 2 轮 — codex 复审 /code-review 修复(发现 #5 半修)

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 6 | P2 | sell_signal.py:110 / add_position.py:413 | #5 仅归一了 intent 查找键,但 `make_sell_context`/`make_add_context` 仍把**原始后缀** positions 传给 builder → RiskEngine `_check_fund_sufficiency` 精确匹配 `p.code==order.code` → "No position" 拒单(半修,反更糟)。 | ✅ FIXED(end-to-end:新增 `normalize_position_codes`,两个 context builder 都传 `normalize_position_codes(positions)`;intent/order/positions/stock_meta 全 bare 对齐)|

## 最终验证(read-only closure check)

- N-005 P2(#1/#2)verify:codex AST 探针确认 guard 覆盖全 import 形式(其 pytest 因 read-only 沙箱无 tempdir 崩,本地 7 测试通过)。
- 后缀修复(#6)verify:**PASS** — codex 直接 end-to-end Python check 确认 `510300.SH` 的 SELL+ADD 均 VALIDATED;两 P2 RESOLVED,无 P1 回归。

## 门禁

- pytest:**全量 3594 passed / 11 skipped**(基线 3506 → +88 Phase N 新测试),0 失败。monitoring 包覆盖率 ≥80%(新模块 anomaly 95% / sell_signal 98% / add_position 94% / degrade 100%)。
- ruff:全绿(monitoring `backend.{broker,data,risk}` 经 per-line `# noqa: TID251`)。
- redline-check:全绿(含新 `[N-005]` Line-2 隔离 + M-004 单一构造点 AST)。

## ★MVP gate(R0 §7)达成

`test_mvp_e2e.py` 双线端到端 on 版本化快照、**0 真实 LLM**(stub router 注入)、首次接全链(Line-1:screen→budget→select→run_shortlist 辩论→to_fund_manager_output→assemble_plan 14-check→render_buy_signal;Line-2:suspension partition→anomaly scan→SELL/ADD→assemble_monitoring_plan→render)、J-005 N 日(5 日)预演无 reset trigger。MVP = K+L+M+N 双线端到端 on 快照 ✅(无自进化/无 MiroFish 核心/无全异动栈)。

> 本报告由 Claude Code + Codex CLI(逐任务审查)+ Claude /code-review(4 并行 reviewer)协同生成。
