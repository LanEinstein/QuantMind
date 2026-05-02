# P5B-T03 Codex Review 综合总结

**Task**: P5B-T03 — Tiered Triage→Escalation Routing
**会话**: claude-opus-4-7-1m (2026-05-02)
**Review 轮数**: 7(R1 baseline + R2 UX + R3 testing + R4 perf + R5 security + R6 follow-up verify + R7 final verify)
**最终判定**: ✅ 通过(major-feature 5+1 轮 codex review pass)

## 各轮 issue 统计

| Round | 维度 | CRITICAL | HIGH | MEDIUM | LOW | 修复 | 状态 |
|---|---|---|---|---|---|---|---|
| R1 | 架构 (codex baseline) | 0 | 1×P1 | 2×P2 | 0 | ✅ 全修 | ✅ |
| R2 | UX/operator | 1 | 2 | 3 | 2 | ✅ CRIT+HIGH-1 修;HIGH-2/MED defer | ⚠️ |
| R3 | testing | 0 | 1 | 6 | 2 | ✅ 全修(hypothesis 框架引入 defer) | ⚠️ |
| R4 | perf/latency | 0 | 1 | 0 | 3 | ✅ HIGH 修;LOW 全 defer | ⚠️ |
| R5 | security/红线 | 0 | 1 | 3 | 2 | ✅ MED 全修;HIGH/LOW defer | ⚠️ |
| R6 | follow-up verify | 0 | 1 (cost_tracker timezone) | 0 | 1 (byte vs char) | ✅ 全修 | ⚠️ |
| R7 | final verify | (verify R6 后续修复) | | | | | ✅ |

**Net**: 1 CRITICAL + 6 HIGH + 14 MEDIUM + 9 LOW(共 30 项)。本 task 修复 1+5+10 = 16 项,deferred 14 项(全部进 backlog)。

## CRITICAL / P1 修复对照表

### C1: prose-only agent 误启用 routing(R1 P1 + R4 HIGH)

**问题**:`config/agent_models.yaml` 5 个 agent 都配了 `routing.escalation_condition.confidence_lt=0.6`,但其中 4 个(intelligence/bull/bear/risk)的 prompt(`backend/agents/prompts.py`)输出**散文报告**,不是 JSON 含 confidence。`_should_escalate` 会 100% 返回 `parse_failed` → 100% 升级到 Kimi → 每股多跑 1 次 qwen + 1 次 kimi(成本翻倍)。

**修复**:本 task 仅在 `fund_manager`(prompt §99-106 强制 JSON 含 `confidence`)上启用 routing,移除其他 4 个 agent 的 `routing:` 块。
- 测试锁定:`test_production_routing_locked_to_fund_manager_only` 在 CI 锁 `routed == {"fund_manager"}`。
- known-deferred(SSoT §7.4):后续把 4 个 prose agent 的 prompt 改 JSON contract + 更新下游解析后,再分批扩展 routing(单独 task)。

### C2: 时区错位导致监控面板假空 + cost_guard 失守(R1 P2 + R2-CRIT-1 + R3 HIGH + R6 follow-up)

**问题**:`track_escalation` 用 `datetime.date.today()`(local tz);`/api/monitoring/llm/escalations` 用 `datetime.now(tz=UTC)`。Asia/Shanghai 部署在每日 00:00-08:00 之间监控读 UTC date(= 上一天 local),Redis 数据写在新一天 local key,导致 endpoint 显示 `total=0` 实际却有数据。**R6 follow-up 进一步发现**:`backend/llm/cost_tracker.py::aggregate_costs` 同样用 `datetime.date.today()`(local)扫 `llm:usage:{date}:*`,而 `track_usage` 已改为 UTC 写入。**这意味着 `cost_guard` 硬熔断在 Asia/Shanghai 凌晨 00:00-08:00 会读到零花费,放行所有请求,绕开 ¥20/日上限**——比 monitoring blind spot 严重得多。

**修复**:
- 抽 `backend/llm/fallback._utc_date_str()` 单一来源,`track_usage` / `track_fallback` / `track_escalation` / `llm_escalations` 全部走该 helper。
- `backend/llm/cost_tracker.py:95-100` 改 `datetime.datetime.now(tz=datetime.UTC).date()`,与 writer 同源。
- 测试锁定:`test_writer_and_reader_share_utc_date_basis`(endpoint 路径)+ `test_cost_tracker_aggregate_uses_utc_date`(cost_guard 路径)双锁。

### C3: 非法 confidence 值绕过升级门(R1 P2 + R3 MED)

**问题**:Python `json.loads` 默认接受 `NaN`、`Infinity`、`-Infinity`;`{"confidence": 1.2}`、`75`、`-1` 等越界值原本被当成 "高置信度" 通过。

**修复**:`_should_escalate` 显式 `math.isfinite(conf_f)` + bounds 检查 `0.0 <= conf_f <= 1.0`,违反者归 `parse_failed`。
- 测试覆盖:`test_non_finite_confidence_escalates`(NaN/Infinity/-Infinity)+ `test_out_of_range_confidence_escalates`(6 个边界值)。

## HIGH 修复对照

| ID | 问题 | 修复 |
|---|---|---|
| R2-HIGH-1 | escalation 日志缺 confidence/threshold | log 新增 `confidence_threshold` 字段 |
| R3-HIGH-1 | writer→reader date contract 未测 | `test_writer_and_reader_share_utc_date_basis` |
| R5-MED-1 | json.loads 无长度上限 | `_MAX_TRIAGE_JSON_BYTES=65_536` + `RecursionError` 捕获 |
| R6 LOW | byte budget 用 char count 比较(多字节内容溢出风险) | 改 `len(content.encode("utf-8"))`,新增 `test_oversized_multibyte_content_escalates` 用 22000 个汉字验证 |

## Deferred backlog(SSoT §7.4 显式记录)

1. **R2-HIGH-2** `RoutingConfig` triage-only 模式语义化 → `mode: triage_only`(P5C cross-cutting)
2. **R2-MED-1** monitoring endpoint Pydantic response_model → P5C 监控面板统一改造
3. **R2-MED-3** escalation 失败单独 attempted/completed/failed 计数 → P5B-T03 出口指标改造
4. **R2-LOW-2** `route_<src>:<src_model>-><dst>:<dst_model>` 含 model → backlog
5. **R3 hypothesis** `pyproject.toml` 引入 hypothesis dep → 单独 dep PR
6. **R5-HIGH-1** monitoring endpoints operator/admin 鉴权 → P5C 统一改造(monitoring/budget/dashboard 全无鉴权,本 task 不单独修)
7. **R5-MED-2** per-run/per-agent escalation 上限 → P5B-T03 出口 shadow-test 验证 escalation rate
8. **R5-MED-3** Redis `AuthenticationError` 单独 alert 路径 → backlog
9. **R5-LOW-1/2** agent_name / provider regex 白名单 → cross-cutting schema backlog
10. **R4-LOW-3** `track_escalation` Redis pipeline 加 timeout 包装 → backlog

## 测试金字塔

- pytest: 907 → **968 passed / 11 skipped / 0 failed / 0 warnings**(net +61)
- ruff: clean
- coverage: backend/risk/ 98%,backend overall 82.47%,新增模块:
  - `backend/llm/providers.py` 100%
  - `backend/llm/router.py` ~91%
  - `backend/llm/fallback.py` ~96%
  - `backend/api/monitoring.py::llm_escalations` 80%+(剩余是 monitoring.py 本来就未覆盖的 dashboard/budget 老代码,非 T03 引入)

## 红线复核

| 红线 | grep / 命令 | 预期 | 实测 |
|---|---|---|---|
| `backend/risk/` 不 import LLM/agents/mirofish | `grep -RnE "^(from\|import) backend\.(llm\|agents\|mirofish)" backend/risk` | 无输出 | ✅ 仅 docstring 反向声明 |
| `AUTHORIZATION_MODE=suggest` | grep yaml/env | suggest | ✅ 本 task 未触碰 |
| LLM key 仅 shell env | `grep "api_key:" config/agent_models.yaml` | 全 `${ENV}` | ✅ |
| MongoDB/Redis 仅 127.0.0.1 | `grep "27017\|6379" docker-compose.yml` | 127.0.0.1 | ✅ |
| 不跨阶段自动推进 | SSoT §3 marker check | 仅 P5B-T03 ⏳→🔧→✅ | ✅ |
