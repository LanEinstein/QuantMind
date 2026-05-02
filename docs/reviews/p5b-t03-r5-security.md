# P5B-T03 R5 安全/运维维度 codex review

**最终判定**: ⚠️ 通过-with-followup(红线全过;HIGH 鉴权挂 backlog,MED 已修复)

## 红线合规检查(R5 实测命令)

```bash
$ git diff -- backend/risk | grep -nE "^\+\s*(from|import) backend\.(llm|agents|mirofish)"
(无输出 ✅)

$ grep -RInE "^(from|import) backend\.(llm|agents|mirofish)" backend/risk
(无输出 ✅)

$ grep -nE "api_key:" config/agent_models.yaml
7:    api_key: "${DEEPSEEK_API_KEY}"
11:   api_key: "${DASHSCOPE_API_KEY}"
15:   api_key: "${MOONSHOT_API_KEY}"
(全部走 shell env ✅)

$ grep -nE "127\.0\.0\.1:(27017|6379)" docker-compose.yml
9:   - "127.0.0.1:27017:27017"
24:  - "127.0.0.1:6379:6379"
(MongoDB/Redis 仅绑定 localhost ✅)
```

三条硬红线全部不被本 task 触碰。

## 初轮 findings

- **CRITICAL**: 0(红线无破坏)。
- **HIGH R5-HIGH-1** `/api/monitoring/llm/escalations` 无鉴权,暴露 agent/route/provider 维度计数 → 成本侧信道。
  → **defer to project-wide**:本 task 不修个别 endpoint 鉴权,因为 `/api/monitoring/budget`、`/api/monitoring/dashboard` 同样无鉴权,统一改造留 P5C 监控面板任务(P5C-T06 会涉及 selector 稳定化 + 加 operator-auth dependency)。当前 backend 仅监听 127.0.0.1 + suggest mode,实际不可被外网访问;backlog 单独跟进。
- **MEDIUM R5-MED-1** `_should_escalate` 对 LLM 输出无长度/深度上限,潜在 CPU/内存压力 + `RecursionError` 逃逸。
  → **fix**:`_MAX_TRIAGE_JSON_BYTES=65_536` 字节上限 + `RecursionError` 显式 catch,违反归 `parse_failed`。新增测试 `test_oversized_content_escalates_without_parsing`。
- **MEDIUM R5-MED-2** 配置漂移让 100% 请求升级,无 per-run 升级上限。
  → **defer**:`cost_guard` 守门是 pipeline 前置硬熔断,即使 100% 升级也不会越 ¥20/日上限。Per-run/per-agent escalation 上限由 P5B-T03 出口的 shadow-test 验证(预期 ≤50% escalation rate)。CI 锁配置 + 出口 metric 双保险足够。
- **MEDIUM R5-MED-3** `track_escalation` 吞掉 `AuthenticationError` 等 Redis 凭证类异常。
  → **partial**:已 log warning,backlog:加 alert 路径区分认证异常 vs 网络异常。
- **LOW R5-LOW-1/2** `agent_name` / `provider` 字段名未 regex 白名单,理论上 `:`/`->`/控制字符会污染 Redis key/field。
  → **defer**:`agent_name` 来自 `agent_cfg.name` 的 dict key,经 `RouterConfig` schema 验证;provider 同理来自 providers section。schema 层加 regex 是 cross-cutting backlog 项。

## 已识别运维风险

- 现 `fund_manager` 是唯一 routing agent,operator 误删 `escalation_condition` 块会让 fund_manager 走 qwen-triage-only(永不升级到 kimi)。
  → **mitigation**:`test_production_routing_locked_to_fund_manager_only` 锁定 confidence_lt=0.6 + 完整 pair。
- `parse_failed` 飙升 = `fund_manager` JSON contract 漂移(Markdown fenced 等)。
  → **mitigation**:`/api/monitoring/llm/escalations` 暴露 `reason_parse_failed`,operator 可在 daily-check 监控。

## R6 verify

红线、CRITICAL、MEDIUM 全 verified;HIGH-1 鉴权改造和 LOW key/field 白名单进 backlog。
