# Codex Review — D1-a 盘中止损阈值按个股波动分位自适应(drawdown_threshold)

> 任务: dossier D1 第一步 —— `DRAWDOWN_STOP` 阈值固定 5% → 个股 |日收益| 分位派生。
> Amendment: `P0-7-amendment-2026-06-03-adaptive-intraday-thresholds`
> 变更: 新 `monitoring/intraday_calibration.py` + `monitoring/intraday_triggers.py` + `orchestration/line2_intraday_runner.py` + `main.py` + 3 test 文件
> 命令: `codex review --uncommitted`(3 轮:2 轮各修 PIT P2 + 第 3 轮 clean)

## P2(全修,均 PIT/provenance — 印证 [[feedback_codex_findings_real]])

- **cycle-1 [P2]×2(PIT 双洞)**:
  - **记录实际阈值**:派生 `dd_threshold` 只是局部变量,`IntradaySellIntent` 不带它 → `_sell_record` 仍写静态 5% → 自适应收紧到 3% 触发的 −4% 止损被记成 5% → 离线 replay 重算 −4% vs 5% 不触发 → **误拒真信号**。**修**:`IntradaySellIntent` 加 `effective_drawdown_threshold`,`_sell_record` 用它。
  - **config_hash 漏校准版本**:hash 只纳 dataclass 值 + intraday_triggers 版本,漏 `intraday_calibration.FEATURE_CODE_VERSION` → 只 bump 派生 maths 版本而参数不变时 hash 不变 → 陈旧 manifest 不 fail-closed。**修**:`drawdown_calibration` 入 hash 为 `{version, config}`。
- **cycle-2 [P2]**:只在 `DRAWDOWN_STOP` 分支填 effective threshold → 自适应**放宽**到 7.5% 时 −6% 改由 `WEIGHT_TRIM`/`THESIS_QUANT_BREAK` 触发,该 intent `effective_drawdown_threshold=None` → record 回退静态 5% → manifest 暗示"应触发 drawdown stop" → replay 不一致。**修**:`dd_thr` 贯穿**每个** sell intent 构造(ATR/drawdown/take_profit/weight_trim/thesis_break 全填)。

## cycle-3 — verify(COMMIT-SAFE)
> "No actionable correctness issues were identified in the current changes. The modified targeted tests and redline checks pass."

## 设计要点
- 个股 |日收益| 90 分位 × 1.5,clamp `[3%, 12%]`(floor 防过紧 churn / ceiling 防止损放过宽);历史不足→None→回退固定 5%(永不更松)。META 参数 frozen `DrawdownCalibrationConfig`(runtime-immutable,只离线 P2-2 重校准 + shadow + 人工 gate)。env `QUANTMIND_LINE2_ADAPTIVE_DRAWDOWN_ENABLED` 默认 OFF(None=v4 bit-for-bit);`FEATURE_CODE_VERSION` v4→v5。
- 高波动股 → 更宽阈值(不被正常摆动误止损);低波动股 → floor(异常小跌即止损)。

## 门禁
- 全量 pytest:**4646 passed / 13 skipped**、coverage **90.70%** > 70%。ruff clean;redline(N-005 monitoring 隔离 + M-004)ALL PASS。

## 安全地基(一条未破)
零 LLM(阈值仅派生自持久化日线 closes,无外部输入);config runtime 不可改(frozen + pinned + 重启);PIT 可复现(派生自 PIT closes + `{version,config}` 入 config_hash + 实际阈值入 record + v5 fail-closed);RiskEngine 纯函数 / 单一构造点 / monitoring import 隔离不变;自进化 7 禁 + 人工 gate;场外信息严禁进数值阈值。
