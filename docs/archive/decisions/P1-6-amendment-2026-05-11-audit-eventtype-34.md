# P1-6 Amendment 2026-05-11 — AuditEventType 27 → 34 类(P2-2 派生:新增 7 类自进化生命周期事件)

## 元数据

| 字段       | 值 |
|-----------|----|
| amendment 编号 | P1-6-amendment-2026-05-11-audit-eventtype-34 |
| 主决策     | `docs/decisions/P1-6-secrets-shell-env-12month-event-driven-rotation-loopback-only-no-local-auth-audit-mongo-jsonl-dual-write.md` |
| 触发决策   | `docs/decisions/P2-2-self-evolution-conservative-three-paths-shadow-validate-feishu-notify-file-registry.md` §1.6 + §2 红线 12 |
| 前序 amendment | (1) `docs/decisions/P1-6-amendment-2026-05-10-audit-eventtype-26.md`(P1-7 派生;22 → 26 类;新增 monthly_budget_50/80/100pct_reached + kimi_daily_cap_4cny_breached)(2) `docs/decisions/P1-6-amendment-2026-05-10-audit-eventtype-27.md`(P2-4 派生;26 → 27 类;新增 execution_report_parse_failed)|
| 日期       | 2026-05-11 |
| 状态       | ✅ 已锁定 |
| 性质       | **非破坏式扩展**(P1-7 amendment 后 26 类 + P2-4 amendment 后 27 类不变;追加 7 类自进化生命周期事件归类 5;P1-6 主文档锁定的类别 1-4 不变本 amendment 新建类别 5)|

## 触发原因

P2-2 自进化机制边界 dedicated session 锁定后,用户 Round 3 Q2 选 "中等 27→34 类"(AuditEventType enum 扩展 7 类覆盖自进化主生命周期)。理由:

- P2-2 决策锁定 5 个自进化主流程节点(LLM 提议 / shadow validate / 飞书通知 / amendment 草案起草 / 人工 promote/reject),需要相应 audit event_type 反向审计
- P2-2 §2 红线 12 锁 `AuditEventType` 7 类新事件归类 5 自进化生命周期(本 amendment 新建类别 5)
- 最小集 27→30 类反向审计不完整(遗漏 rolled_back / amendment_drafted / rag_ingested / rag_rejected)
- 完整 27→38 类引入 exemplars/frontier 调试事件违反 P1-6 §2 红线 17 "调试性事件不入 audit 走 quantmind.jsonl"

## 变更内容

### `backend/audit/models.py::AuditEventType` enum 扩展

**P2-4 amendment 后状态(27 类)** + **本 amendment 新增 7 类**:

```python
# backend/audit/models.py(P2-2 amendment 后状态;34 类)
class AuditEventType(str, Enum):
    """锁定 34 类 audit event_type — 任何新增必走 P1-6-amendment。"""

    # ============================================
    # 类 1 — 两唯二写入端点调用(2)
    # P1-6 主决策锁定不变
    # ============================================
    EXECUTION_REPORT_SUBMITTED = "execution_report_submitted"
    RECONCILIATION_TICKET_DECIDED = "reconciliation_ticket_decided"

    # ============================================
    # 类 2 — 模式切换 + 冻结源 + 生命周期事件(11)
    # P1-6 主决策锁定不变
    # ============================================
    MODE_SWITCH_INITIATED = "mode_switch_initiated"
    MODE_SWITCH_COMPLETED = "mode_switch_completed"
    FREEZE_SOURCE_SWITCH_ACTIVATED = "freeze_source_switch_activated"
    FREEZE_SOURCE_TICKET_OPEN = "freeze_source_ticket_open"
    FREEZE_SOURCE_CIRCUIT_BREAKER_COOLDOWN = "freeze_source_circuit_breaker_cooldown"
    FREEZE_SOURCE_DATA_QUALITY = "freeze_source_data_quality"
    FREEZE_SOURCE_EOD_PIPELINE_FREEZE = "freeze_source_eod_pipeline_freeze"
    MOCKBROKER_RESET = "mockbroker_reset"
    SYSTEM_INTERRUPTED = "system_interrupted"
    BROKERSCHEDULER_STARTED = "brokerscheduler_started"
    BROKERSCHEDULER_STOPPED = "brokerscheduler_stopped"

    # ============================================
    # 类 3 — 凭证生命周期 + 飞书收发(7)
    # P1-6 主决策锁定不变
    # ============================================
    CREDENTIAL_ROTATED = "credential_rotated"
    CREDENTIAL_REVOKED = "credential_revoked"
    CREDENTIAL_LEAK_INCIDENT = "credential_leak_incident"
    FEISHU_LONGCONN_CONNECTED = "feishu_longconn_connected"
    FEISHU_LONGCONN_DISCONNECTED = "feishu_longconn_disconnected"
    FEISHU_MESSAGE_RECEIVED = "feishu_message_received"
    FEISHU_MESSAGE_SENT = "feishu_message_sent"

    # ============================================
    # 类 4 — 异常 + 拦截事件(13 = 8 + 4 + 1)
    # P1-6 主决策 8 + P1-7 amendment 4 + P2-4 amendment 1
    # ============================================
    STATE_MACHINE_ILLEGAL_TRANSITION = "state_machine_illegal_transition"
    RISK_ENGINE_CHECK_REJECTED = "risk_engine_check_rejected"
    BUILDER_EARLY_RETURN = "builder_early_return"
    MOCKBROKER_PRICE_LIMIT_VIOLATION_AT_FILL = "mockbroker_price_limit_violation_at_fill"
    DATA_QUALITY_BREACH = "data_quality_breach"
    RECONCILIATION_TICKET_OPEN_OR_EXPIRED = "reconciliation_ticket_open_or_expired"
    LLM_CALL_TIMEOUT_30S = "llm_call_timeout_30s"
    DAILY_COST_CEILING_20CNY_BREACHED = "daily_cost_ceiling_20cny_breached"
    # P1-7 amendment(4 类):
    MONTHLY_BUDGET_50PCT_REACHED = "monthly_budget_50pct_reached"
    MONTHLY_BUDGET_80PCT_REACHED = "monthly_budget_80pct_reached"
    MONTHLY_BUDGET_100PCT_REACHED = "monthly_budget_100pct_reached"
    KIMI_DAILY_CAP_4CNY_BREACHED = "kimi_daily_cap_4cny_breached"
    # P2-4 amendment(1 类):
    EXECUTION_REPORT_PARSE_FAILED = "execution_report_parse_failed"

    # ============================================
    # 类 5 — 自进化生命周期(7)★ 本 amendment 新建类别 5 ★
    # P2-2 派生
    # ============================================
    PROMPT_VERSION_PINNED = "prompt_version_pinned"
    """用户在 prompts.lock.json 锁定新 production alias 版本;reason_namespace='self_evolution_lifecycle'"""

    PROMPT_VERSION_ROLLED_BACK = "prompt_version_rolled_back"
    """用户在 prompts.lock.json 回滚到前一版本;reason_namespace='self_evolution_lifecycle'"""

    RAG_DOCUMENT_INGESTED = "rag_document_ingested"
    """rag_ingester 写入 data/rag/{source}/{date}/{doc_id}.md + provenance.jsonl 成功;reason_namespace='self_evolution_lifecycle'"""

    RAG_DOCUMENT_REJECTED_NON_WHITELIST = "rag_document_rejected_non_whitelist"
    """rag_ingester 拒绝非白名单源文档入库;reason_namespace='self_evolution_lifecycle'"""

    SHADOW_EVOLUTION_RUN_COMPLETED = "shadow_evolution_run_completed"
    """evolution_shadow_run cron 跑完一个 artifact 的 shadow validate;outcome=SUCCESS(challenger 胜)/ FAILURE(失败或 challenger 输);reason_namespace='self_evolution_lifecycle'"""

    EVOLUTION_AMENDMENT_DRAFTED = "evolution_amendment_drafted"
    """amendment_drafter 起草 docs/decisions/pending/{artifact_id}.md 成功;reason_namespace='self_evolution_lifecycle'"""

    EVOLUTION_FEISHU_NOTIFIED = "evolution_feishu_notified"
    """evolution_feishu_notifier 发送飞书主动通知成功;reason_namespace='self_evolution_lifecycle'"""
```

### P1-6 §2 红线 12 同步更新

> **`AuditEventType` enum 锁定 34 类 event_type**(P2-2 amendment 后;P1-6 主决策 22 + P1-7 amendment 4 + P2-4 amendment 1 + P2-2 amendment 7 = 34 类):任何新增/删除/重命名必走 `P1-6-amendment-{date}-{原因}.md`;严禁 magic string `event_type` 写入 audit(必须用 enum)。

### 与 P1-6 §1.7 audit schema 兼容性

新增 7 类完全复用 P1-6 §1.7.1 `AuditEvent` frozen Pydantic schema 不变:

| 字段 | 自进化 7 类事件取值约束 |
|------|------------------------|
| `event_id` | UUIDv4(默认生成)|
| `timestamp` | UTC ISO8601(默认生成)|
| `event_type` | `AuditEventType.PROMPT_VERSION_PINNED` / `..._ROLLED_BACK` / `RAG_DOCUMENT_INGESTED` / `..._REJECTED_NON_WHITELIST` / `SHADOW_EVOLUTION_RUN_COMPLETED` / `EVOLUTION_AMENDMENT_DRAFTED` / `EVOLUTION_FEISHU_NOTIFIED` 7 选 1 |
| `actor` | `AuditActor.SYSTEM`(amendment_drafter / rag_ingester / evolution_feishu_notifier 自动)或 `AuditActor.SCHEDULER`(BrokerScheduler 第五 cron 触发 shadow_evolution_run);**严禁** `LLM` / `FRONTEND_USER` / `FEISHU_USER` actor 写自进化类 audit(P2-2 §2 红线 12)|
| `actor_detail` | 自进化模块名(`evolution_dispatcher` / `shadow_chain` / `amendment_drafter` / `rag_ingester` / `evolution_feishu_notifier` 等)|
| `resource_type` | `'self_evolution_artifact'`(统一 resource_type;7 类共用)|
| `resource_id` | artifact_id(prompt_version=v3 / rag_doc=arxiv-2024-XXXXX / risk_proposal=RPP-2026-... / exemplar=EXM-2026-...)|
| `payload` | 按 event_type 不同:`{artifact_type, shadow_metrics?, amendment_path?, feishu_message_id?}` 等;PROMPT_VERSION_PINNED 含 `from_version` + `to_version`;ROLLED_BACK 含 `from_version` + `to_version` + `rollback_reason` |
| `outcome` | SUCCESS(通过 / 入库 / 发送成功)/ FAILURE(失败 / 拒绝)/ PARTIAL(challenger 部分门槛过但未占优)|
| `correlation_id` | 链路追踪 — 同一 artifact 的所有事件共享 correlation_id;便于跨事件查询 |
| `reason_namespace` | `'self_evolution_lifecycle'`(统一命名空间;7 类共用)|

### 各事件 payload 详细约束

#### PROMPT_VERSION_PINNED(类 5)

```python
payload = {
    "artifact_type": "prompt_version",
    "agent_name": "fund_manager",  # 或其他 9-Agent
    "from_version": "v1",
    "to_version": "v2",
    "amendment_id": "P2-2-amendment-2026-XX-XX-prompt-{agent}-v{N}",
}
```

#### PROMPT_VERSION_ROLLED_BACK(类 5)

```python
payload = {
    "artifact_type": "prompt_version",
    "agent_name": "fund_manager",
    "from_version": "v2",
    "to_version": "v1",
    "rollback_reason": "production_metrics_degraded" | "user_manual_revert" | "shadow_post_promote_failure",
    "amendment_id": "P2-2-amendment-2026-XX-XX-rollback-{agent}",
}
```

#### RAG_DOCUMENT_INGESTED(类 5)

```python
payload = {
    "artifact_type": "rag_document",
    "doc_id": "arxiv-2024-XXXXX",  # 或 github-qlib-v0.X.Y 等
    "source_type": "arxiv" | "semanticscholar" | "openreview" | "github" | "changelog",
    "source_url": "https://arxiv.org/...",
    "scanned_at": "2026-05-11T22:00:15Z",
    "llm_summary_model": "deepseek-v4-pro",
    "llm_tokens": 1234,
}
```

#### RAG_DOCUMENT_REJECTED_NON_WHITELIST(类 5)

```python
payload = {
    "artifact_type": "rag_document",
    "attempted_url": "https://twitter.com/...",  # 非白名单
    "attempted_source_type": "twitter",  # 非白名单
    "rejection_reason": "non_whitelist_source",
    "scanned_at": "2026-05-11T22:00:30Z",
}
```

#### SHADOW_EVOLUTION_RUN_COMPLETED(类 5)

```python
payload = {
    "artifact_type": "prompt_version" | "rag_document" | "risk_proposal" | "exemplar",
    "artifact_id": "...",
    "shadow_window_days": 45,
    "shadow_metrics": {
        "instruction_completeness_rate": 0.97,
        "execution_report_parse_accuracy": 0.99,
        "data_missing_rate": 0.005,
        "llm_timeout_rate": 0.03,
        "signal_generation_success_rate": 0.96,
        "max_drawdown": 0.06,
        "cumulative_pnl_cny": 18234.5,
        "csi300_excess_return": 0.012,
    },
    "production_metrics": {... 同上 ...},
    "challenger_winning": True,  # 或 False
}
```

#### EVOLUTION_AMENDMENT_DRAFTED(类 5)

```python
payload = {
    "artifact_type": "...",
    "artifact_id": "...",
    "amendment_path": "docs/decisions/pending/{artifact_id}.md",
    "llm_summary_model": "deepseek-v4-pro",  # amendment_drafter LLM
    "llm_tokens": 2345,
}
```

#### EVOLUTION_FEISHU_NOTIFIED(类 5)

```python
payload = {
    "artifact_type": "...",
    "artifact_id": "...",
    "feishu_message_id": "...",  # 飞书 webhook 返回的 message_id
    "amendment_path": "docs/decisions/pending/{artifact_id}.md",
    "dedup_key": "evolution_pending_{artifact_id}",  # Alerter dedup_15min
}
```

### 告警通道(继承 P1-7 §1.7)

类 5 自进化生命周期事件**仅**写 audit 不主动告警,**除了** EVOLUTION_FEISHU_NOTIFIED 触发飞书通知:

- PROMPT_VERSION_PINNED → 仅 audit(用户手动 pin 后系统记录)
- PROMPT_VERSION_ROLLED_BACK → 仅 audit + 可能触发 ERROR 级告警(若 rollback_reason='production_metrics_degraded' 因为这表示自进化产物上线后表现下降需要复盘)
- RAG_DOCUMENT_INGESTED → 仅 audit
- RAG_DOCUMENT_REJECTED_NON_WHITELIST → 仅 audit + 飞书 warning(被攻击迹象 — 系统在 22:00 frontier 时尝试 ingest 非白名单源)
- SHADOW_EVOLUTION_RUN_COMPLETED → 仅 audit(通过或失败均仅记录)
- EVOLUTION_AMENDMENT_DRAFTED → 仅 audit
- EVOLUTION_FEISHU_NOTIFIED → audit + **触发飞书主动通知**(走 FEISHU_CUSTOM_BOT_WEBHOOK_URL;Alerter dedup_15min;P2-2 §1.6 锁)

### TTL 与双写继承 P1-6 §2 红线 13+14 不变

- Mongo `audit_events` collection TTL 180 天(继承)
- JSONL `logs/audit.jsonl` 30 天双写(继承)
- Mongo failure 时 fail-open(JSONL 兜底 + warning 不阻主路径;继承 P1-6 §2 红线 15)

## 与 P1-6 主决策红线兼容性核查

- **P1-6 §2 红线 11(audit_events append-only insert-only)** ✅ 兼容:新 7 类同款约束严禁 update/delete/replace
- **P1-6 §2 红线 12(任何新增必走 amendment)** ✅ 兼容:本 amendment 即满足
- **P1-6 §2 红线 16(LLM 严禁写 audit_events)** ✅ 兼容:7 类自进化 audit 由 SYSTEM/SCHEDULER actor 写;严禁 LLM/FRONTEND_USER/FEISHU_USER actor 直接写
- **P1-6 §2 红线 17(4 类事件强制写 audit + 调试性事件不入)** ✅ **强化**:新建类 5 自进化生命周期强制写;调试性事件(LLM raw token / GEPA 中间 candidate / arxiv scan 失败重试)走 quantmind.jsonl 不入 audit
- **P1-6 §2 红线 18(凭证类 audit 仅 fingerprint 严禁 plaintext)** ✅ 兼容:本 amendment 不涉及凭证
- **P1-6 §2 红线 19(audit 查询入口仅 CLI + GET API)** ✅ 兼容:可通过 `scripts/query_audit.py --event-type SHADOW_EVOLUTION_RUN_COMPLETED` 等查询;GET `/api/audit/events?event_type=...` 同款

## 与 P0+P1+P2 其他决策协同

- **P0-3 §2 红线 5 LLM 严禁拼接飞书消息文本** ✅ 兼容:EVOLUTION_FEISHU_NOTIFIED 飞书消息走 backend/feishu/renderer.py::render_evolution_pending 函数模板硬编码生成
- **P0-4 §1 严格正则 only + LLM 完全不参与回报路径** ✅ 兼容:本 amendment 不引入 LLM 参与 parse 路径
- **P0-6 acceptance 框架 45 交易日** ✅ 兼容:SHADOW_EVOLUTION_RUN_COMPLETED 含 shadow_window_days=45(沿用 P0-6)
- **P0-7 RiskConfig 全锁** ✅ 兼容:本 amendment 不修改 RiskConfig schema
- **P0-10 LLM 字段权限矩阵** ✅ 兼容:新 7 类 audit actor 不含 LLM
- **P1-2.A BrokerScheduler 5 cron** ✅ 协同:派生 P1-2.A-amendment-2026-05-11;新 cron 触发本 amendment 7 类 audit 事件
- **P1-5 §2 红线 1+2 仅 GET 端点** ✅ 兼容:audit 查询入口仅 CLI + GET 不引入新写入端点
- **P1-7 §1.7 告警通道仅飞书 + audit + Phase B 面板** ✅ 兼容:EVOLUTION_FEISHU_NOTIFIED 走 FEISHU_CUSTOM_BOT_WEBHOOK_URL 复用
- **P2-2 §1.6 飞书主动通知触发条件** ✅:本 amendment 是 P2-2 §1.6 + §2 红线 12 的具象实现

## 实施期任务

P2-2 §3 实施期 Phase X-C 任务 H-016 落地本 amendment:

- **H-016**: `backend/audit/models.py::AuditEventType` enum 27 → 34 类升级 + 类别 5 自进化生命周期建立 + 完整性单元测试断言 34 类(继承 P1-6/P1-7/P2-4 累积 amendment 测试)+ 集成测试 7 类新事件端到端写 audit_events + JSONL 双写(actor=SYSTEM/SCHEDULER outcome=SUCCESS/FAILURE/PARTIAL reason_namespace='self_evolution_lifecycle' resource_type='self_evolution_artifact' 完整性)

## 不在本 amendment 范围内

- ❌ 不修改 `AuditActor` enum 5 类(SYSTEM/SCHEDULER/USER/LLM/FRONTEND_USER/FEISHU_USER;继承)
- ❌ 不修改 `AuditOutcome` enum 4 类(SUCCESS/FAILURE/PARTIAL/UNKNOWN;继承)
- ❌ 不修改 `AuditEvent` frozen Pydantic schema 字段集(继承 10 字段)
- ❌ 不修改 audit_events collection 索引(继承)
- ❌ 不修改 TTL 180 天(继承)
- ❌ 不修改 JSONL 双写 30 天(继承)
- ❌ 不引入 LLM 参与 evolution 路径(继承 P2-2 §2 红线 17 自进化模块依赖隔离)
- ❌ 不引入新写入端点(继承 P1-5 §2 红线 5 仅 2 写入端点)
- ❌ 不引入 audit 查询前端页面(继承 P1-6 §2 红线 19;CLI + GET API only)
- ❌ 不修改 P2-4 amendment 后 27 类 AuditEventType(本 amendment 仅追加 7 类归类 5)
- ❌ 不引入 exemplars/frontier 调试事件(继承 P1-6 §2 红线 17 调试性事件不入 audit 走 quantmind.jsonl)

## 与 P1-6 + P1-7 + P2-4 amendment 协同

- 本 amendment 是 P1-6 主决策的**第 3 个 amendment**:
  - 第 1 个:P1-6-amendment-2026-05-10-audit-eventtype-26(P1-7 派生;22→26 类)
  - 第 2 个:P1-6-amendment-2026-05-10-audit-eventtype-27(P2-4 派生;26→27 类)
  - **第 3 个:P1-6-amendment-2026-05-11-audit-eventtype-34(P2-2 派生;27→34 类)** ★
- 累积更新 P1-6 §2 红线 12 锁定为 34 类(P1-6 主文档 + 3 amendment 四层协同)
- 实施期 H-016 任务一次性升级 backend/audit/models.py 含 12 类新增(P1-7 amendment 4 + P2-4 amendment 1 + 本 amendment 7)不需多次部署

---

**P1-6 第 3 次 amendment 2026-05-11 锁定 ✅**

`AuditEventType` enum 22 → 26 → 27 → 34 类;新增 7 类自进化生命周期事件(PROMPT_VERSION_PINNED / PROMPT_VERSION_ROLLED_BACK / RAG_DOCUMENT_INGESTED / RAG_DOCUMENT_REJECTED_NON_WHITELIST / SHADOW_EVOLUTION_RUN_COMPLETED / EVOLUTION_AMENDMENT_DRAFTED / EVOLUTION_FEISHU_NOTIFIED)归类 5 自进化生命周期(本 amendment 新建类别 5);非破坏式追加;P2-2 §1.6 + §2 红线 12 派生;actor=SYSTEM/SCHEDULER 严禁 LLM/FRONTEND_USER/FEISHU_USER 直接写。
