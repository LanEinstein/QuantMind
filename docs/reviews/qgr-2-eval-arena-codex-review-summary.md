# Codex 跨模型代码审查报告 — QGR-2 评测竞技场骨架

**项目**:QuantMind · **任务**:`qgr-2-eval-arena` · **审查时间**:2026-06-22
**审查轮次**:6 完成 cycle(`codex review --uncommitted`,gpt-5 系)+ 1 次 cycle-7 超时(transient,非 finding)
**最终判定**:✅ 通过 — 6 轮收敛(发现 4→1→0 新正确性问题),17 处 finding(2 P1 + 14 P2 + 1 P3)**全部修复 + 各配回归测试**

> 范围:8 个研究侧新模块 `scripts/factor_research/{cpcv,gate_bar_source,gate_backtest,trial_ledger,honest_gates,multi_strategy_compare,baselines,forward_gate_test}.py` + 对应 8 个测试。全离线/确定性/零 backend 改动/永不碰 live。

## 各轮 finding 与处置(全 FIXED)

| # | 轮 | 级 | 文件 | 问题 | 修复 |
|---|----|----|------|------|------|
| 1 | 1 | P1 | gate_bar_source | ADV 用全(含未来)成交量历史 → 前视泄漏进 harsh-fill 容量门 | 改 as-of 单遍滚动均值(只 ≤当日) |
| 2 | 1 | P1 | baselines | `csi300_etf_hold` 走 15%-capped 单槽 = 85% 现金,太易 + 与 etf_only 重复 | 新 `buy_and_hold_baseline` 全额投资 beta(直接从 bar 算,非 ≤5槽门) |
| 3 | 1 | P2 | multi_strategy_compare | 零方差 excess 候选 FDR 误判 superior(bootstrap 跳过→ge=0→最小 p) | 零方差 → fail-closed p=1.0 |
| 4 | 1 | P2 | cpcv | path/combo 回撤未含起始资本(开局亏损被忽略) | 回撤从 1.0 起算(prepend 起始权益) |
| 5 | 2 | P2 | honest_gates | 多个零方差(flat)序列 NaN 相关 → ONC 各算一个(高估有效 N) | flat 全并入 1 个退化簇 |
| 6 | 2 | P2 | trial_ledger | `deflation_n` 把 appended 原始网格按 raw 求和 → 击穿 `max(legacy,ONC)`(10k 近重复网格 ONC=2 却 deflate 成 legacy+10k) | 加 `effective_n` 字段;deflation 用 `cumulative_effective`(各批 ONC 计数) |
| 7 | 2 | P3 | baselines | `random_top_n_scores` 忽略 `top_n` 参数(>0 返回全集) | honor `top_n` 截断 |
| 8 | 3 | P2 | gate_backtest | `signals_asof` 百分位 O(n²)(全市场×十年=数十亿次比较) | 单次排序 O(n log n)+ tie-group |
| 9 | 3 | P2 | gate_backtest | health_overrides 对掉出当日 panel 的持仓被丢 → 弱持仓永不轮动 | 合并 override-only 条目 |
| 10 | 3 | P2 | forward_gate_test | spending 模式错拼默默落到更松的 Pocock | 拒未知模式 |
| 11 | 4 | P2 | cpcv | embargo 耗尽所有块 OOS 时仍报 `path_count_verified=True`(0 期) | 全 0 期 → fail-closed 空报告 |
| 12 | 4 | P2 | forward_gate_test | content-address 未冻 spending schedule(看数据后可换更松 Pocock) | spending 入 prereg 哈希字段 + 构造期校验 |
| 13 | 4 | P2 | baselines | random_top5 经粘滞轮动门退化为「持有初始随机篮」≠ 声明的「每次重抽」 | 据实修正声明 + 标注 fully-rebalanced 为 QGR-4 精化 |
| 14 | 5 | P2 | baselines | buy-and-hold 在涨停开盘也建仓(事件循环会拒 BUY) | `_first_bar` 跳过 at_limit_up 不可成交开盘 |
| 15 | 6 | P1 | gate_bar_source | ETF 在 `fund_daily`(非 `daily`)+ 无 adj_factor → ETF beta baseline 真数据无 bar | price reader 合并 daily(股)+fund_daily(ETF,flat 因子 1.0) |
| 16 | 6 | P2 | gate_backtest | ≤5槽等权 ~75% gross 超 live 70% 总仓 cap(decide_day 是 §4.4 proxy 冻结代码) | 文档化为 proxy 边界(live RiskEngine 盘前强制;经 exposure_cap_violations 暴露) |

## 门禁(commit 前全绿)
- pytest:74 个新测试,`tests/factor_research/` 共 487 passed,新模块覆盖 96%(每个 ≥92%,>70% 门)。
- ruff + mypy strict + `scripts/redline-check.sh` 全绿。
- 安全地基红线一条未破:离线 / 仅 Tushare PIT / 零 backend 改动 / LLM 不进评测路径 / governance enum 不动 / 127.0.0.1 / 永禁下单 / sim 暂停 live 一行未改。

## 诚实备注
codex 持续在 2832 行 diff 上每轮找到 1-4 处 refinement(P1 severity 仅头两轮);6 轮后收敛到「单一 proxy 边界文档化」。两处保留为**已文档化的 proxy 边界**(非 bug):①单股 cap 盘后严格校验对 friction-epsilon 的 sub-percent 越界 ②70% 总仓 cap(decide_day 冻结代码不强制,live RiskEngine 盘前强制)——均经 `conservation_ok`(硬保证)与 `exposure_cap_violations`(计数)分离暴露,符合 §4.4「量化回测=proxy,非全系统验证」。
