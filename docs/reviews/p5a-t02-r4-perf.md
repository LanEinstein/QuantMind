# P5A-T02 R4 性能 + 错误处理维度复审

**判定**: ✅ 通过(综合报告 cycle 4 + cycle 5 验证)

## 性能特性

| 路径 | 开销 | 评估 |
|------|------|------|
| `get_budget_state` | 1 次 Redis SCAN (今日 keys) + 短聚合 | 与既有 dashboard `/api/monitoring/dashboard` 数量级一致,可忽略 |
| `assert_budget_allows` (前置) | 同上 | 加在 `_run_and_persist` 入口,不阻塞 LLM 调用前的 markup |
| `asyncio.Lock` 序列化 | 单实例最多 1 次 `_run_and_persist` 并发 | eval-period 5 股 watchlist + 10s sleep,manual API 罕用,排队代价极小 |

## 错误处理决策

- 数据腐败 (NaN/Inf/负值): **fail-closed** (drop entry / hard_breach state)
- 基础设施故障 (Redis ConnectionError): **fail-open** (scheduler proceed,日志 warning)

理由见综合报告 §"关键设计决策" 第 2 节。

## 已知限制 (Cycle 4 备注)

`daily_budget=1e16` 时浮点精度使 sentinel `+1.0` 等于 daily_budget,但 `status` 字段直接驱动判定,数值比较已脱钩,实际无影响。

完整记录见 [`p5a-t02-codex-review.md`](./p5a-t02-codex-review.md)。
