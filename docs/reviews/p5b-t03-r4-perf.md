# P5B-T03 R4 性能/延迟维度 codex review

**最终判定**: ⚠️ 通过-with-followup(P1 已修复;LOW backlog)

## 初轮 findings

- **HIGH R4-HIGH-1** prose-only agent 强制 `parse_failed` → 100% escalation,fast pipeline 4 个 agent 至少 +20-75s,slow 6 个 agent +30-105s。直接吃掉 `pipeline_timeout_seconds: 480/900` SLA headroom,且不满足 P5B 出口"日均成本 ≤ ¥1.20"。
  → **fix**:仅 `fund_manager` 启用 routing(JSON contract 唯一),其余 4 个 prose agent 保持 kimi-only。延迟回归到 baseline(~25 min 单股 p95 不变)。后续 prompt 改 JSON 后再扩展。

- **LOW R4-LOW-1** `_should_escalate` 无 JSON 长度上限。
  → **fix**:增加 `_MAX_TRIAGE_JSON_BYTES=65_536` 上限,超长直接归 `parse_failed`,捕获 `RecursionError`。

- **LOW R4-LOW-2** `scan_iter(match=...)` 在 30+ agent × 1 day 是可接受的(Redis 仍扫 keyspace 而非索引);共享 Redis 百万 keyspace 时才会变成尾延迟。
  → **defer**:本 task 单独 Redis,不进入热点;backlog 标记。

- **LOW R4-LOW-3** escalation pipeline 无 Redis 超时包装;慢 Redis 会推后 Kimi escalation 启动。
  → **defer**:`track_escalation` 已是 fire-and-forget(except Exception 全吞),只增加请求 path 一次 ms 级 pipeline 写入,实际不影响 LLM 调用。backlog 可考虑 `asyncio.timeout(0.5)` 包装。

## 单股 latency 估算(post-fix)

| 场景 | Baseline (kimi-only) | T03 fund_manager triage→escalation |
|---|---|---|
| 高置信度(triage 即返回) | ~45-180s | ~5-15s(qwen 单跑) ✅ 节省 30-165s |
| 低置信度 / parse_failed | ~45-180s | ~5-15s + ~45-180s = ~50-195s ⚠ 增 5-15s |
| 不启用 routing 的 4 个 prose agent | ~45-180s | 不变 |

**预期**:fund_manager 对 90% 高置信度 triage 直接通过,p95 显著降;对 10% 低置信度才升级,平均成本下降 ≥40%。需要 7 天 shadow-test(`scripts/shadow_compare.py`,P5B 出口任务)实测。

## 性能边界

- `llm:escalations:{date}:{agent}` 按 agent/day 有界(当前唯一 fund_manager → 1 key/day)。
- `reason_*` 白名单(`low_confidence`/`parse_failed`/`other`)+ `route_<src>-><dst>` 来自配置,常数级。
- `_TTL_DAYS=90` 每次写入刷新,日 key 停写后过期。
- HINCRBY 串行化,无应用锁需求。
- cost-tracking suffix `/triage`、`/escalation` 让 fund_manager 的 `llm:usage:{date}:fund_manager/triage:qwen` 和 `…/escalation:kimi` 各自累计,常数级 +1 keys/day。
- 请求热路径无 N-请求 累计的 list/dict;主要分配是一次 JSON parse(≤64 KB)+ 一次 Redis pipeline 对象。

## R6 verify

CRITICAL prose-agent 过度升级问题已通过减少 routing 范围彻底消除。
