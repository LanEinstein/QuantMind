# P1-7 修订 — 2026-05-24 cost_guard 事后 trailing-stop → 真·预留硬上限 + fan-out cap

> **修订基准**: [P1-7 成本预算 LLM only 日 ¥20 / 月 ¥440 / Kimi ¥4 + soft degrade](./P1-7-cost-budget-llm-only-monthly-440-daily-20-kimi-cap-4-soft-degrade-feishu-alert.md)
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §2 第 6 项 + §8 预算真相
> **修订日期**: 2026-05-24
> **触发**: Codex round-2 核实 `cost_guard.py:349 assert_budget_allows` 读累计 `spent_today`、**已 breach 才 raise** = **事后 trailing-stop**,**不符** CLAUDE.md §2.2「¥20/日 hard 全 LLM 暂停」。且全市场选股的**乘法 fan-out**(¥0.8 × 20 候选 = ¥16)是真杀手,而非单次辩论单价(单次 4-agent 仅 ~¥0.4)。

## 1. 修订前(P1-7 原锁定)

- `cost_guard` 4 常量:日 ¥20 hard(唯一全 LLM 熔断)+ soft 0.7(¥14)+ 月 ¥440 soft + Kimi 日 ¥4。
- `assert_budget_allows` 在调用前检查,但读**事后累计** `spent_today`,仅当**已**进入 `hard_breach` 才 raise → 跨越 ¥20 的那次调用**仍完成**。
- `SoftDegradeManager` ¥14 关 Kimi escalation;单 LLM 调用 30s + **0 重试**。
- 成本率(`fallback.py:48`):deepseek 0.2/0.2,qwen 1.0/1.0,kimi 2.1/8.4 RMB/M。

## 2. 修订后(本 amendment 锁定)

### 2.1 ¥20/日 hard → 真·预留(pre-call reservation)

- 调用前 **preflight 估算** `max_tokens` 成本 → **预留**(Redis 原子 INCR 到 `llm:usage:{utc_date}:reserved`)→ 若 `reserved + spent > ¥20` 则**拒绝该调用**(不再让它完成)→ 调用后用实际用量**对账**(`reserved` 释放,`spent` 落实)。
- 这把 CLAUDE.md §2.2「¥20 hard 暂停」从文字落到代码:**跨越 ¥20 的调用不发生**,而非事后才停下一个。
- 4 常量(¥20/0.7/¥440/¥4)数值**不变**;改的是**执行语义**(预留 vs 事后)。

### 2.2 fan-out cap(真杀手防护)

- 新增常量 `max_debates_per_day`(初值与 `total_daily_cap` 协调)。
- **红线**:「**一次辩论 / 每日 shortlist,不是 per candidate**」——辩论在收敛后的 shortlist 上**跑一次**,不是每个候选跑一次(否则 ¥0.8 × 20 = ¥16 瞬间吃满)。
- Line-1 **一日一次**(09:00 slow);Line-2 **纯量化轮询(零 LLM)**,LLM **仅触发式**(去重 + 日上限 `max_anomaly_llm_per_day`)。

### 2.3 failover / 重试 / context 防护

- **per-stage retry cap**:§2.2「单 LLM 调用 0 重试」必须在新 LangGraph 辩论编排里**存活**;failover(kimi→qwen→deepseek)计费 = 部分完成 + fallback,故 **budget-aware fallback**(预算紧时直接降级而非重试)。
- **轮间 context**:`debate_round_count` 多轮时 round-2 prompt 携带 round-1 transcript → prompt token 超线性增长;**inter-round 摘要** + **per-call input token cap**。

### 2.4 统一计数器(防绕过)

- **所有** LLM 花费(Line-1 信息汇总 / MiroFish / 辩论 / 交易员 / Line-2 异动触发 / Phase R shadow)**必须**写**同一** `llm:usage:{utc_date}:*` Redis key 簇(UTC 日期分桶,继承 `cost_tracker.py` 已修的日期 pin)。任何旁路路径不写该计数器 = 静默绕过 ¥20 cap = 违规。

### 2.5 shadow sub-budget

- Phase R `evolution_shadow_run`(22:00)是**一等成本消费者**(复盘全天 transcript,context 可能比 live 还大)。给**独立 sub-budget**;在 live 花费已知后运行,**日余额低时降级 / 跳过**。**MVP 阶段 shadow OUT**(P2-2 amendment),自然消此风险。

## 3. 实施期任务调整

- `backend/services/cost_guard.py`:`assert_budget_allows` 改 reserve-then-reconcile;新增 `reserve_budget(estimated)` / `settle_budget(actual)` + `max_debates_per_day` / `max_anomaly_llm_per_day` 常量(runtime 不可改)。
- `backend/agents_team/` 辩论编排:一次辩论/shortlist + per-stage retry cap + inter-round 摘要 + input cap;经 `cost_guard` 预留。
- `redline-check.sh` 加子检:`cost_guard` 含 reserve/settle + 新常量;grep 确认无旁路 LLM 调用不写 `llm:usage:{utc_date}`。
- `SoftDegradeManager` ¥14 关 Kimi escalation 不变。

## 4. 红线清单(本 amendment 之后)

1. ¥20/日 hard = **真·预留**(preflight 估算 + 预留 + 拒超 + 对账);跨越 ¥20 的调用**不发生**。4 常量数值不变。
2. `max_debates_per_day` + **一次辩论/每日 shortlist(非 per candidate)**;Line-1 日一次,Line-2 纯量化轮询 + LLM 仅触发式(去重 + 日上限)。
3. per-stage retry cap(§2.2「0 重试」存活)+ budget-aware fallback;轮间 inter-round 摘要 + per-call input cap。
4. **所有** LLM 花费写**同一** `llm:usage:{utc_date}` 计数器;旁路不写 = 违规。
5. Phase R shadow 独立 sub-budget + 日余额低降级/跳过;MVP 阶段 shadow OUT。
6. `cost_guard` + `SoftDegradeManager` 严禁 `import backend.{llm,agents,mirofish,data}`(P1-7 §1 不变);常量 runtime 不可改 + hot-reload 禁用。
7. 告警仅飞书 + audit + Phase B 成本面板;dedup_15min;严禁 SMTP/Slack/Discord(P1-7 §1.7 + P0-2-amendment-2026-05-16 不变)。

## 5. 修订记录追加

`docs/plan.html` Phase M 任务(cost 硬上限随 agents_team 落地)+ 修订记录 + SESSION_LOG 同步追加。CLAUDE.md §2.10 成本预算补充「¥20 真·预留(非事后)+ max_debates_per_day + 一次辩论/shortlist + 统一 utc_date 计数器」。
