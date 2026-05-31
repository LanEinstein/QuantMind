# P-003 代码审查总结 — Line-1 配比接线(provider clamp + runner prime)

**任务**: P-003(provider `prime_allocation` + `build_lead_context` clamp `volume=min(max_compliant, 配比目标)`;runner Protocol + hasattr 守护调用)
**审查日期**: 2026-05-31
**审查工具**: codex 仍撞额度(reset 10:12,审时 09:55)→ 回退 `claude /code-review` high(独立 finder agent 逐条核 7 不变量 + verify)
**最终判定**: ✅ 通过(7/7 不变量 confirmed;material finding 修;1 dismiss 有据)

## 7 不变量核验(全 confirmed)
1. **只压不放** ✅ `volume = min(volume, target_lots)`,绝不抬高 max_compliant。
2. **0 手 = 跳过不强转 1 手** ✅ 在构造 brief/context 前返回跳过;runner 当 fall-through。
3. **确定性/无 mid-walk 再分配** ✅ `prime_allocation` 仅读 walk-start account/positions 算一次;clamp 用 walk-start 目标 + committed-adjusted max_compliant 组合(篮子 ≤ deployable ∩ ≤ cash)。
4. **key 匹配** ✅ prime 用 `row.code`,clamp 用 `lead.code`,同 shortlist 同串。
5. **单一构造点** ✅ prime/clamp 不构造 InstructionPlan;brief + AssemblyContext 两处 `proposed_volume` 同一 clamped 值。
6. **back-compat** ✅ 无 policy → prime no-op → `_target_cash_by_code=None` → 不 clamp;runner hasattr 守护对无此方法的 provider 无害。
7. **缺名兜底** ✅ `compute_target_cash` 覆盖全 shortlist;`.get(code, 0.0)` 兜底 → 0 → 跳过(fail-safe,绝不无界买)。

## 发现与处置

| # | 维度 | 文件 | 问题 | 处置 |
|---|------|------|------|------|
| 1 | 审计/可观测(material)| line1_context_provider.py / line1_runner.py | 0 手配比跳过复用 `Line1QuoteDegrade` → runner 归类 `QUOTE_DEGRADED` + 渲染"行情不可用"通知,语义错误(行情其实正常,只是配比当日不部署)| **FIXED** 新增独立 `Line1AllocationSkip` 类型 + `ALLOCATION_SKIPPED` outcome + runner 独立分支(只 log 不渲染 quote 通知);handoff §5 已许可"新 skip 分支"。(注:旧路径 notice 其实只渲染+记长度未真发,但 outcome 标签错 + 未来若接 dispatch 会误扰 owner → fix-deep)|
| 2 | 类型/语义 | line1_context_provider.py prime_allocation | `sigma_by_code` 含 `volatility_20d: float\|None`,None 进 inverse_vol_weights 静默等权 | **DISMISS(加注释)** 这是 amendment §2.1 **有意** 的等权兜底(历史不足),非 bug;inverse_vol_weights 已安全处理;补一行注释说明 None→等权 intended。|

## 加固测试
- `test_prime_allocation_clamps_volume_to_inverse_vol_target`:primed < baseline,== 独立重算 target_lots,AssemblyContext 同值。
- `test_prime_allocation_zero_lot_target_degrades_not_one_lot`:断言返回 `Line1AllocationSkip`(非 `Line1QuoteDegrade`)。
- `test_no_allocation_policy_leaves_volume_at_max_compliant`:back-compat。
- runner:`test_basket_falls_through_allocation_skipped_lead_to_next`(fall-through + 不烧辩论)+ `test_all_allocation_skipped_zero_buy`(全跳 → ALLOCATION_SKIPPED,不发单不辩论)。
- FakeProvider 加 no-op `prime_allocation` + `alloc_skip_codes`(原 56 line1 测试仍绿)。

## 门禁
- `pytest tests/services/test_line1_context_provider.py tests/orchestration/test_line1_runner.py`:55 passed。
- 全量:4298 passed(`FEISHU_INTERACTIVE_ENABLED=false`)。
- `ruff`:All checks passed。redline:全绿;单一构造点 grep 空。
