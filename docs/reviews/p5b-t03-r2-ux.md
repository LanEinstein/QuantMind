# P5B-T03 R2 UX/Operator 维度 codex review

**最终判定**: ⚠️ 通过-with-followup(CRITICAL 已修复;HIGH-2 + MED 留 backlog)

## 初轮 findings

- **CRITICAL R2-CRIT-1** `backend/llm/fallback.py:158` + `backend/api/monitoring.py:264` —— 时区错位,Asia/Shanghai 凌晨监控会显示 `total=0`。
  → **fix**:`_utc_date_str()` 单一来源,writer/reader 共享。新增 `test_writer_and_reader_share_utc_date_basis` 锁定契约。
- **HIGH R2-HIGH-1** `backend/llm/router.py:248` —— `escalating_to_expensive_provider` 日志缺 confidence/threshold,operator 无法解释"为什么升了"。
  → **fix**:日志增 `confidence_threshold` 字段(取自 `agent_cfg.routing.escalation_condition.confidence_lt`);reason 字段已带 `low_confidence` / `parse_failed`。
- **HIGH R2-HIGH-2** `RoutingConfig` 允许只配 triage 不配 escalation,Kimi agent 可能永远走 qwen。
  → **defer**:当前生产配置(`fund_manager` 唯一)是完整 escalation 的;此 footgun 留给后续 `mode: triage_only` 显式语义改造。SSoT §"Phase 5B/T03 backlog" 记录。
- **MED R2-MED-1** endpoint 直接暴露动态 Redis hash,前端契约不稳。
  → **defer**:Pydantic response_model 是 P5C 监控面板统一改造任务,本 task 仅负责数据可读。
- **MED R2-MED-2** "无数据" vs "Redis down" 区分不足。
  → **partial fix**:已区分 `status=ok` / `status=unavailable`;新增的 `status_reason` 字段挂 P5C 待办。
- **MED R2-MED-3** escalation 失败无单独日志。
  → **defer**:由 `_call_provider` 内 retryable exception 上抛,operator 仍能从 fastapi 5xx + structlog 链定位。本 task 不强制。
- **LOW R2-LOW-1** `redis_client is None` 静默跳过。
  → 已通过 R3 合并测试覆盖。
- **LOW R2-LOW-2** `route_qwen->kimi` 不含 model。
  → **defer**:升级为 `route_<src_provider>:<src_model>-><dst_provider>:<dst_model>` 风险面较大(改 Redis schema),挂 backlog。

## Operator-facing pitfalls(已记录)

- `/triage`、`/escalation` 后缀让 `llm:usage:{date}:{agent}/triage:{provider}` 多两行;旧"按 agent 名"聚合的 dashboard 需要适配。**Mitigation**:`_parse_usage_key` 仍按冒号切分,`agent_name = "fund_manager/triage"` 不破坏分隔;commit message 显式提示。
- 当前 YAML 仅 `fund_manager` 启用 routing。后续运维若误删 `escalation_condition` 块,会安静降级为 triage-only(走 qwen 永不升级)。**Mitigation**:`test_production_routing_locked_to_fund_manager_only` 在 CI 锁定生产配置必须包含完整 escalation。
- `parse_failed` 比例若飙升,大概率是 `fund_manager` prompt 漂移(Markdown fenced JSON 等),通过 monitoring endpoint `reason_parse_failed` 可定位;还应配 7 天 shadow-test 用 `scripts/shadow_compare.py`(P5B 出口任务)。

## R6 verify

CRITICAL/HIGH-1 已 verified。HIGH-2、MED-1/2/3、LOW-2 在 SSoT §7.4 已记录为 deferred backlog。
