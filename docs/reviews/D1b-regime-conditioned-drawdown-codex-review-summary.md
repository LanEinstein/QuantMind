# Codex Review — D1-b 盘中止损 regime 条件化(熊市收紧 drawdown)

> 任务: dossier D1 (ii) —— 用确定性 `classify_regime` 在 D1-a 个股自适应上加市场 regime 维度(熊市收紧 drawdown 止损)。
> Amendment: `P0-7-amendment-2026-06-03-regime-conditioned-drawdown`
> 变更: `intraday_calibration.py` + `intraday_triggers.py` + `line2_intraday_runner.py` + `main.py`(含新纯函数 `_observable_index_closes`)+ `data/scheduler.py` + 4 test 文件
> 命令: `codex review --uncommitted`(5 轮:4 轮各修 P2 + 第 5 轮 clean)

## 机制
熊市 regime → drawdown 阈值 × `bear_multiplier`(0.8,收紧 20%),再 clamp `[3%,12%]`。`is_bear` 由 `intraday_triggers` 从 `MarketRegime` 判定后以 bool 传纯计算层(`intraday_calibration` 仍零 backend.* import)。env `QUANTMIND_LINE2_REGIME_DRAWDOWN_ENABLED` 默认 OFF;regime=None/未启用 = D1-a bit-for-bit。两 `FEATURE_CODE_VERSION` bump(triggers v5→v6 / calibration v1→v2);`regime_drawdown_enabled` flag + bear_multiplier 入 config_hash(PIT)。

## P2(全修,均真 bug — 印证 [[feedback_codex_findings_real]])

- **cycle-1 [P2] regime 恒失效**:`main.py` 的 `index_closes` 原**硬编码 `()`**(U-D3 未接)→ `classify_regime` 恒 NEUTRAL → D1-b **及既有 ADD 熊市禁补**恒失效。**修**:从持久化 `index_prices` 读基准 000300 日收盘(顺带激活既有设计的『ADD 熊市禁补』,方向严格保守)。
- **cycle-2 [P2] 0 收盘误判 BEAR**:`get_index_history` 把缺失收盘 coerce 成 0,杂 0 拉低 MA → 误 BEAR(误阻 ADD + 误收紧 SELL)。**修**:只取 finite-positive 收盘。
- **cycle-3 [P2] PIT/freshness**:无界读会喂入未来回填行 / 陈旧行。**修**:抽纯函数 `_observable_index_closes` —— 只取 ≤T-1 可观测 + 最新收盘 >15 日陈旧 → `()` → NEUTRAL;query 也 `end_date=T-1` 兜底。
- **cycle-4 [P2] fresh deploy 数周失效**:日线 cron 仅回填 `BENCHMARK_BACKFILL_DAYS=5` 天 → fresh/reset `index_prices` 要数周才够 classify(需 >20 收盘)。**修**:`BENCHMARK_BACKFILL_DAYS` 5→60(单次 cron ≥21 交易日,Spring-Festival 也够)。

## cycle-5 — verify(COMMIT-SAFE)
> "No discrete bugs introduced by the current changes were identified. The regime-conditioned drawdown wiring appears consistent with the tests and default-off behavior."

## 门禁
- 全量 pytest:**4659 passed / 13 skipped**、coverage **90.68%** > 70%。ruff clean;redline(N-005/M-004)ALL PASS。

## 安全地基(一条未破)
零 LLM(regime 纯市场指数派生,严禁 LLM/新闻进阈值);config runtime 不可改(frozen + pinned);PIT 可复现(regime 自 PIT 指数 + finite/observable/fresh 三重守门 + flag/multiplier 入 config_hash + 实际阈值入 record);RiskEngine 纯函数 / 单一构造点 / monitoring import 隔离不变;自进化 7 禁 + 人工 gate;止损只会更紧不更松(clamp floor 3%)。
