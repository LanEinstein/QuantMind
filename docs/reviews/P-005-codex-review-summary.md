# P-005 代码审查总结 — Line-2 止盈(+1R 分批)+ 超配减仓触发

**任务**: P-005(`IntradayTriggerKind` +TAKE_PROFIT/WEIGHT_TRIM;并入 `evaluate_intraday_sell_intents`;优先级 ATR>回撤>止盈>减仓;去重键 (code,side)→(code,trigger_kind);config 4 新参数入 replay hash)
**审查日期**: 2026-05-31
**审查工具**: codex review --uncommitted(完整出结论)
**最终判定**: ✅ 通过(codex 报 1×P2,已修)

## 发现与处置

| # | 级别 | 文件 | 问题 | 处置 |
|---|------|------|------|------|
| 1 | P2 | intraday_triggers.py:83 | 新增 TAKE_PROFIT/WEIGHT_TRIM 改变确定性触发输出,但 `FEATURE_CODE_VERSION` 仍 `/v1` → 改动前后 manifest 可能同版本 → replay/version gate 无法对陈旧触发码 fail-closed(违反模块注释承诺)| **FIXED** 升 `/v1`→`/v2`(改触发数学必升版,模块注释要求);无测试硬编码旧版本,137 Line-2 测试绿 |

## 实现要点(红线遵守)
- **优先级 ATR>回撤>止盈>减仓**:每 code/tick ≤1 intent;保护性止损永不被止盈遮蔽。
- **止盈**:R=`atr_stop_mult`×close_atr;live≥cost+`r_multiple`×R 且净盈利 → 卖 `floor(settled×tranche_fraction/100)×100`;sub-1-lot 跳过(永不卖 0);余仓续交 ATR 移动止损。
- **减仓**:weight=vol×live/total > `max_single_stock_pct`×(1+`trim_band`)(16.5%)→ 减回 `trim_target_pct`(13%);trim 量 clamp 到 settled `available_volume`(T+1)+ 整手。
- **去重键 (code,side)→(code,trigger_kind)**(`_kind_of`):不同触发独立去重(drawdown 不遮后续 take_profit);同 kind 当日仍去重(防 30s 刷屏)。
- **复用 IntradaySellIntent** → context/render/manifest 全不改;`evidence_id=MARKET-{code}-{kind}`;`anomaly_reason` 带"止盈 +1R"/"超配回调"。
- **back-compat**:`account=None`(legacy 调用)→ 只跑原 ATR/回撤两触发;`take_profit_already_taken` 默认空(P-006 接 ledger 派生 gate)。
- **config 4 新参数**(r_multiple/tranche_fraction/trim_band/trim_target_pct)入 `dataclasses.asdict` → `_compute_config_hash` → replay manifest(改任一 → 新 hash → 陈旧 replay fail-closed);单股 cap 复用 runner `_add_cfg.max_single_stock_pct`(无新常量)。
- **monitoring import-clean**(零 backend.{llm,agents,agents_team,mirofish});不构造 InstructionPlan(单一构造点)。

## 测试(+12 evaluator + 2 runner)
- evaluator:止盈触发+减半量 / 已 taken 抑制(P-006 gate 参数)/ sub-1-lot 跳过 / 水下不止盈 / 减仓触发+减回量 / 带内不减 / 止盈>减仓优先级 / 回撤>止盈优先级 / 无 account 不触发(back-compat)。
- runner:止盈端到端 routed SELL(`-510300-SELL-`)+ 去重键独立(同 code 先止盈后回撤不被去重,2 sends)。
- 原 Line-2 测试全绿(137 passed)。

## 门禁
- `pytest tests/monitoring tests/orchestration/test_line2_intraday_runner.py`:137 passed;全量 4317 passed(`FEISHU_INTERACTIVE_ENABLED=false`)。
- `ruff`:All checks passed。redline:全绿;monitoring import 隔离空。
