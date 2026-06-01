# Phase V(≤5 槽组合轮动)代码审查总结 — 2026-06-01

> **审查方式**:先跑 `codex review --uncommitted`(28 文件 / 5071 行 diff),**撞 600s timeout 未完成**(exit 143;diff 过大,codex 自身建议 >500 行拆分)→ 按 owner 既定 fallback(memory `feedback_codex_rate_limit_fallback`)回退 **Claude `/code-review high`**(4 并行 finder agent:slot_portfolio 逻辑 / orchestration 接线 / 红线+removed-behavior / cleanup+测试缺口)。
> **结论**:发现 **6 条真问题(全修)** + 数条 dismiss(已核实不可达/有意为之)。修复后门禁全绿:`ruff` ✅ / `redline` ✅ / `pytest` **4462 passed / 13 skipped** / slot_portfolio 覆盖率 97–100%。

## 已修复(6)

| # | 文件 | 问题 | 修复 |
|---|------|------|------|
| 1 | `slot_portfolio/rotation_intent.py` `resolve_expiry` | FALLBACK_BEST 用裸 `math.isfinite` 而非 `[0,1]` 边界(SELL 侧 `_is_pct` 用了边界)→ finite-but-corrupt 分位(如 1.7)可驱动 fallback BUY | 加 `0.0 <= pct <= 1.0` 边界,与 SELL 侧 fail-closed 对齐 |
| 2 | `orchestration/rotation_runner.py` `_percentile_map` | 用 top-N **池内 rank** 当 `line1_percentile`,但 screener `score` 本就是**全 survivor 截面**百分位混合([0,1]);池内 rank 让全市场强(top-2%)但居 top-N 末位的持仓被误判『弱』→ 误 churn 强持仓 | 直接用 `composite score` 作 `line1_percentile`(全市场质量度量;忠于 P40/P75 全市场阈值契约)。ship-first `line1_percentile == composite_score`,rank-distinct 百分位待 screener 暴露全宇宙 rank |
| 3 | `orchestration/rotation_runner.py` `_resolve_open_intents` | 先判 `challenger_held` 再判 `incumbent_held` → 到期时挑战者码恰被(无关)持有而 incumbent SELL 未执行 → 误记 RESOLVED『已轮动』(审计失真) | 改为**先判 `incumbent_held`**(仍持=SELL 没执行=lapse),再 challenger_held(已轮动),再 fallback |
| 4 | `services/rotation_context_provider.py` `incumbent_health` | (a) `available_volume` 未按手取整 → 奇数股(公司行动残股)轮动 SELL 被 RiskEngine check-3(volume%lot≠0)拒、槽位卡死;(b) `suspended` 写死 False → 停牌码可被提议非可执行 SELL | (a) `(raw//100)*100` 取整(对齐 Line-2);(b) `suspended = 无 frame bar`(停牌/退市无新 bar 代理)。limit_down 由 RiskEngine check-12 兜底故仍 False |
| 5 | `tests/orchestration/test_line1_runner.py` | holdings-aware Line-1 排除(V-004 核心不变量)**无测试** | +2 测试:held 码排出 BUY 候选(shortlist 3→2)/ 无 held_codes 时 holdings-blind 向后兼容 |
| 6 | `tests/orchestration/test_rotation_runner.py` | cooldown↔churn 的**真·交易日**接线(runner 折叠 ledger PROPOSED 日期 + provider trading_days_between → churn gate)无集成测试 | +1 测试:预置已 resolve 的近期 incumbent 轮动 → 断言 `incumbent_cooldown` block 且不发 SELL |

## Dismiss(已核实 — 非 bug / 不可达 / 有意为之)

- **`today_instruction_count=0` 致『第 6 笔』突破 ≤5/日**(reviewer P1):**不可达**。轮动仅在满仓(held==max)提议;满仓时 Line-1 每笔新名 BUY 被 check#6 拒(已达持仓上限)→ 二者同一 run 互斥。已在 `main.py` `_run_rotation_step` 加注释说明该结构性安全。`today_instruction_count` 是 broker_events 计数(U-D3 前 0,与 Line-1 共享的既有限制),RiskEngine check-10 仍是每日硬权威。
- **append-only JSONL 三处重复**(`rotation_intent` / `entry_rank` 各自实现,vs `marketdata_snapshot/_jsonl`):**有意隔离**。slot_portfolio CLAUDE.md 依赖锁定为「stdlib + pydantic/structlog/filelock + 内部」;引 `marketdata_snapshot._jsonl`(私有下划线模块)= 跨子包私有 API 耦合。~15 行/处,可接受。
- **每 run 重载事件日志 5×(open_intents/cooldown/block 各 load_events)+ 重复 screen**:日 cron 频次,可接受(reviewer 同意)。append-only 永不压缩,历史增长 O(n);未来若进热路径再合并为单次 fold。
- **incumbent cooldown 对『被拒/lapse 的轮动』仍生效**:fail-safe 偏保守(不丢钱,仅延后部分合法轮动),amendment 价值取向认可保守。
- **`is_expired` 同日到期边界**(`next_rebalance_close==created`):生产接线 `next_rebalance_close=None` → expires_at = 3 交易日后 > created,恒安全,不可达。
- **n<=1 单候选边界**:score-as-percentile 后单名返回其 score;fail-closed 向不动作。

## 安全地基红线复核(全 PASS)

单一构造点(轮动 SELL 经 `assemble_monitoring_plan`,slot_portfolio 不构造 InstructionPlan,新 `[V-002]` AST 守门)/ import 隔离(slot_portfolio 无 llm/agents/mirofish;rotation_runner 无 risk/broker/data)/ RiskEngine 14-check 不变(check#6 仅 docstring,risk_summary min=max=14)/ LLM 不写决策字段(轮动全确定性)/ config runtime-immutable / fail-closed(数据)+ fail-open(基建,轮动错误不阻断 Line-1 BUY)。
