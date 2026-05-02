# P5B-T03 R1 架构维度 codex review

**最终判定**: ⚠️ 通过-with-followup(初轮 P1 全部修复 + R6 verify)

## 初轮(`codex review --uncommitted`,baseline)

- P1: 0 / P2: 2(均已修复)
- **P1 阻塞** `config/agent_models.yaml:111-117` —— intelligence/bull/bear/risk 4 个 prompt 不是 JSON contract(`backend/agents/prompts.py:67`/`78` 显式 "请直接给出你的看多/看空分析,不要使用JSON格式"),`_should_escalate` 会对每条 triage 输出返回 `(True, "parse_failed")`,导致 100% 升级,本来想省 Kimi 反而每股多跑 1 次 qwen + 1 次 kimi。
  → **fix**:取消 4 个 prose agent 的 `routing:` 配置,只在 `fund_manager`(prompt §99-106 强制 JSON 含 `confidence`)上启用。known-deferred:更新 prose agent prompt 为 JSON 输出后再扩展 routing。
- **P2** `backend/llm/router.py::_should_escalate` —— `1.2`、`75`、`NaN`、`Infinity` 等非法 confidence 当作高置信度通过(Python `json.loads` 默认接受 NaN/Inf)。
  → **fix**:`_should_escalate` 显式 `math.isfinite` + bounds[0,1] 检查,违反者归 `parse_failed`。
- **P2** `backend/llm/fallback.py:158-159` —— `track_escalation` 用 `datetime.date.today()`(local),endpoint 用 UTC,Asia/Shanghai 部署 00:00-07:59 间监控读到的不是当天 key。
  → **fix**:抽 `_utc_date_str()` helper,writer/reader 走同一函数。

## 模块边界与控制流

- `_should_escalate` 抽为 `@staticmethod`,纯输入(routing, response)→输出(bool, reason),无状态依赖,可独立单测。
- triage→escalation 控制流在 `complete()` 单一入口实现,fallback path 不与 escalation 互相吞;`test_escalation_error_not_caught_by_primary_fallback` 锁定该契约。
- 成本拆分:triage 写 `llm:usage:{date}:{agent}/triage:{provider}`,escalation 写 `…/escalation:…`,运营面板可按 stage 聚合。known-side-effect:旧"按 agent 名"聚合的 dashboard 会多两行,在 SSoT §7.4 + commit 备注中说明。

## 红线检查

| 红线 | 验证 |
|---|---|
| `backend/risk/` 不 import `backend.llm`/`agents`/`mirofish` | grep 仅命中 engine.py docstring 反向声明 |
| `AUTHORIZATION_MODE=suggest` | 本 task 未触碰 |
| LLM key 仅 shell env | `config/agent_models.yaml` providers 段保持 `${ENV}` 引用 |
| 不跨阶段自动推进 | 仅 P5B-T03 marker ⏳→🔧→✅,Phase 5B 出口仍 ⏳ |

## R6 verify(post-fix)

见 `docs/reviews/p5b-t03-codex-summary.md`。架构维度 6 项全部 verified。
