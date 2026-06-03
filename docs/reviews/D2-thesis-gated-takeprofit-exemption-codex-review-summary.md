# Codex Review — D2 长期持股豁免强制止盈(thesis-gated take-profit exemption)

> 任务: dossier D2 —— 有 intact PositionThesis 的长期持仓豁免 TAKE_PROFIT + 软 WEIGHT_TRIM,但保 15% 硬顶;止损不豁免。
> Amendment: `P0-10-amendment-line2-2026-06-03-thesis-gated-takeprofit-exemption`
> 变更: `monitoring/intraday_triggers.py` / `monitoring/thesis_break.py` / `orchestration/line2_intraday_runner.py` / `services/line2_context_providers.py` / `main.py` + 4 test 文件
> 命令: `codex review --uncommitted`(6 轮:5 轮各修 1×P2 + 第 6 轮 clean;均前台 `</dev/null`)

## 5×P2(全修,均真 bug —— 印证 [[feedback_codex_findings_real]])

- **cycle-1 [P2] 硬顶 trim floor 残留越界**(`intraday_triggers.py`):`hard_cap_only` 用 floor 取整 → 16% 仓减后仍 15.2% > 15% 硬红线。**修**:硬顶腿 `math.ceil` 向上取整到整手 → 减后 ≤15%;加 post-trim weight ≤15% 不变量断言。
- **cycle-2 [P2] 超额 trim 被 check#9 拒后卡死**(`intraday_triggers.py`):大赢家硬顶 trim 超额>¥50k → RiskEngine 拒(check#9 对 SELL 同样适用)→ runner 当日去重记"已触发"→ 持仓卡越界无重试。**修**:硬顶腿 clamp 到单次 ¥50k(仿 thesis-break exit clamp)→ 有效部分减仓、后续 session 收敛。
- **cycle-3 [P2] 「不在 break map」≠ 确认 intact**(`thesis_break.py`/runner):`evaluate_thesis_breaks` 对缺输入条件**跳过**(SCORE_DECAY 盘中无 score)→ "不在 break" 可能是"没评估"→ 误豁免。**修**:新增纯函数 `intraday_intact_codes` —— 要求**全部盘中可观测条件(ANCHOR_DRAWDOWN+TIME_STOP)可评估且 intact** 才豁免;SCORE_DECAY 显式归日线路径(否则因无盘中 score 永不可全评估 → 功能恒失效)。
- **cycle-4 [P2] thesis 缺必需模板仍豁免**(`thesis_break.py`):模型允许任意 1–8 条件,只校验"存在的"模板 → 缺 ANCHOR/TIME 的 thesis 仍 confirmed。**修**:追踪 required 模板"已见+intact",`_INTRADAY_OBSERVABLE_TEMPLATES <= confirmed_templates` 才豁免。
- **cycle-5 [P2] 损坏/未来日期 → 0 哨兵读作 intact**(`main.py`):`_count_trading_days` 对 malformed/future `trade_date` 返 `0` → TIME_STOP `0>30=False`(intact)→ 损坏 thesis 被豁免。**修**:返 `int|None`(损坏/未来→None),caller 省略 → TIME_STOP 不可评估 → fail-closed(break + 豁免双路径);今日买入=0 合法保留。

## cycle-6 — verify(COMMIT-SAFE)
> "No introduced correctness issues were found in the reviewed changes. The modified targeted tests and ruff check pass."

## 门禁
- 全量 pytest(`FEISHU_INTERACTIVE_ENABLED=false`):**4631 passed / 13 skipped**(+新测试)、coverage **90.69%** > 70%。
- ruff clean;`scripts/redline-check.sh` ALL PASS(N-005 monitoring import 隔离 + M-004 单一构造点不破)。

## 安全地基(一条未破)
零 LLM 决策(豁免=确定性 intact 判定,LLM 文本不进决策);**绝不放松保护性止损**(ATR/drawdown/thesis-break 排在 TP/trim 之前,豁免只作用 else 分支);单股 15% 硬红线**强化不削弱**(硬顶 ceil + ¥50k clamp);env 默认 OFF + 45 日 shadow + 人工 gate;与 THESIS_QUANT_BREAK 解耦(独立 `exempt_theses_by_code` 字段);PIT 可复现(`FEATURE_CODE_VERSION` v3→v4,空集=v3 bit-for-bit);monitoring import 隔离不破。
