# P1-6 Amendment 2026-05-10 — AuditEventType 22 → 26 类(新增 4 类成本预算事件)

## 元数据

| 字段       | 值 |
|-----------|----|
| amendment 编号 | P1-6-amendment-2026-05-10-audit-eventtype-26 |
| 主决策     | `docs/decisions/P1-6-secrets-shell-env-12month-event-driven-rotation-loopback-only-no-local-auth-audit-mongo-jsonl-dual-write.md` |
| 触发决策   | `docs/decisions/P1-7-cost-budget-llm-only-monthly-440-daily-20-kimi-cap-4-soft-degrade-feishu-alert.md` |
| 日期       | 2026-05-10 |
| 状态       | ✅ 已锁定 |
| 性质       | **非破坏式扩展**(P1-6 §1.8 锁 22 类不变;追加 4 类归类 4 异常 + 拦截事件)|

## 触发原因

P1-7 决策需要将以下 4 类成本预算事件入审计以满足:

1. **月预算 50%/80%/100% 节点动作**(P1-7 §1.6):月度健康度观察 + 提前预警 + 100% 必通知
2. **Kimi daily cap ¥4 触发**(P1-7 §1.5):Kimi 单独熔断需可被 audit 反查与复盘
3. **完整审计闭环**(继承 P1-6 §1.7 fail-closed for data corruption ·vs· fail-open for infra glitches 原则):成本类事件须具备 audit 入口供 acceptance 期间复盘

## 变更内容

### `backend/audit/models.py::AuditEventType` enum 扩展

**原 P1-6 §1.8 锁定 22 类**:
- 类 1(2):EXECUTION_REPORT_SUBMITTED / RECONCILIATION_TICKET_DECIDED
- 类 2(11):FEISHU_INTERACTIVE_TOGGLED / MOCKBROKER_RESET / FREEZE_SOURCE_SWITCH_CHANGED / FREEZE_SOURCE_TICKET_OPEN_CHANGED / FREEZE_SOURCE_CIRCUIT_BREAKER_CHANGED / FREEZE_SOURCE_DATA_QUALITY_CHANGED / FREEZE_SOURCE_EOD_PIPELINE_CHANGED / ADVANCE_DAY_EXECUTED / EOD_PIPELINE_SUCCEEDED / EOD_PIPELINE_FAILED / RECOVERY_SNAPSHOT_CREATED
- 类 3(7):SECRETS_VALIDATOR_PASSED / SECRETS_VALIDATOR_BLOCKED / KEY_FINGERPRINT_CHANGED / KEY_ROTATION_OVERDUE / FEISHU_MAIN_MESSAGE_SENT / FEISHU_WEBHOOK_ALERT_SENT / FEISHU_MESSAGE_RECEIVED
- 类 4(8):STATE_MACHINE_ILLEGAL_TRANSITION / RISK_ENGINE_CHECK_REJECTED / BUILDER_EARLY_RETURN / MOCKBROKER_PRICE_LIMIT_VIOLATION_AT_FILL / DATA_QUALITY_BREACH / RECONCILIATION_TICKET_OPEN_OR_EXPIRED / LLM_CALL_TIMEOUT_30S / DAILY_COST_CEILING_20CNY_BREACHED

**P1-7 新增 4 类(均归类 4 — 异常 + 拦截事件;原 8 类 + 4 = 12 类)**:

```python
# backend/audit/models.py(P1-7 后状态)
class AuditEventType(str, Enum):
    """锁定 26 类 audit event_type — 任何新增必走 P1-6-amendment。"""
    
    # 类 1 — 两唯二写入端点调用(2)
    EXECUTION_REPORT_SUBMITTED = "execution_report_submitted"
    RECONCILIATION_TICKET_DECIDED = "reconciliation_ticket_decided"
    
    # 类 2 — 模式切换 + 冻结源 + 生命周期事件(11)
    # ...(同 P1-6 原 11 类 不变)
    
    # 类 3 — 凭证生命周期 + 飞书收发(7)
    # ...(同 P1-6 原 7 类 不变)
    
    # 类 4 — 异常 + 拦截事件(8 + 4 = 12)
    STATE_MACHINE_ILLEGAL_TRANSITION = "state_machine_illegal_transition"
    RISK_ENGINE_CHECK_REJECTED = "risk_engine_check_rejected"
    BUILDER_EARLY_RETURN = "builder_early_return"
    MOCKBROKER_PRICE_LIMIT_VIOLATION_AT_FILL = "mockbroker_price_limit_violation_at_fill"
    DATA_QUALITY_BREACH = "data_quality_breach"
    RECONCILIATION_TICKET_OPEN_OR_EXPIRED = "reconciliation_ticket_open_or_expired"
    LLM_CALL_TIMEOUT_30S = "llm_call_timeout_30s"
    DAILY_COST_CEILING_20CNY_BREACHED = "daily_cost_ceiling_20cny_breached"
    # P1-7 新增:
    MONTHLY_BUDGET_50PCT_REACHED = "monthly_budget_50pct_reached"
    MONTHLY_BUDGET_80PCT_REACHED = "monthly_budget_80pct_reached"
    MONTHLY_BUDGET_100PCT_REACHED = "monthly_budget_100pct_reached"
    KIMI_DAILY_CAP_4CNY_BREACHED = "kimi_daily_cap_4cny_breached"
```

### P1-6 §2 红线 12 同步更新

> **`AuditEventType` enum 锁定 26 类 event_type**(P1-7 amendment 后;原 22 类):任何新增/删除/重命名必走 `P1-6-amendment-{date}-{原因}.md`;严禁 magic string `event_type` 写入 audit(必须用 enum)。

### 与 P1-6 §1.7 audit schema 兼容性

新增 4 类全部复用 P1-6 §1.7.1 `AuditEvent` frozen Pydantic schema 不变:

| 字段 | 4 类成本预算事件值 |
|------|-------------------|
| `event_id` | UUIDv4(默认生成)|
| `timestamp` | UTC ISO8601(默认生成)|
| `event_type` | 4 新增值之一 |
| `actor` | `AuditActor.SYSTEM`(由 cost_guard 在 SCHEDULER 上下文调用)|
| `actor_detail` | `'cost_guard.check_monthly_thresholds'` / `'cost_guard.assert_kimi_cap_allows'` |
| `resource_type` | `'cost_budget'`(扩展 P1-6 §1.7 resource_type 枚举值列表)|
| `resource_id` | `None`(成本守门是全局事件无单一 resource_id)|
| `payload` | 月节点:`{spent: float, budget: float, pct: float, by_provider: dict}`<br>Kimi cap:`{spent: float, cap: float, calls_count: int}` |
| `outcome` | 月节点:`AuditOutcome.SUCCESS`<br>Kimi cap:`AuditOutcome.BLOCKED` |
| `correlation_id` | `None`(成本守门无单一 InstructionPlan 关联)|
| `reason_namespace` | `'cost_budget_threshold'`(继承 P1-2.C 命名空间区分原则)|

### TTL 与双写继承 P1-6 §2 红线 13+14 不变

- Mongo `audit_events` collection TTL 180 天(继承)
- JSONL `logs/audit.jsonl` 30 天双写(继承)
- Mongo failure 时 fail-open(JSONL 兜底 + warning 不阻主路径;继承 P1-6 §2 红线 15)

## 与 P1-6 其他红线兼容性核查

- **§2 红线 11(audit_events append-only insert-only)** ✅ 兼容:4 类新事件同款约束严禁 update/delete/replace
- **§2 红线 16(LLM 严禁写 audit_events)** ✅ 兼容:cost_guard 写 audit 仅以 SYSTEM/SCHEDULER actor;严禁 frontend_user/feishu_user actor 写 cost_budget event(防伪造;P1-7 §2 红线 19)
- **§2 红线 17(4 类事件强制写 audit + 调试性事件不入)** ✅ 兼容:4 类新事件归类 4 强制写;调试性事件(LLM raw response / Redis 计数原始操作)仍走 `logs/quantmind.jsonl` 30 天
- **§2 红线 18(凭证类 audit 仅 fingerprint 严禁 plaintext)** ✅ 兼容:成本预算 payload 仅含 spent/budget/pct/by_provider 数值;严禁透出 provider key plaintext 或 fingerprint(by_provider key 用 'deepseek'/'qwen'/'kimi' 字符串字面量,非 API key 值;P1-7 §2 红线 20)
- **§2 红线 19(audit 查询入口仅 CLI + GET API)** ✅ 兼容:4 类新事件可通过 `scripts/query_audit.py --event-type MONTHLY_BUDGET_*` 或 GET `/api/audit/events?event_type=...` 查询;不引入额外查询入口

## 实施期任务

P1-7 实施期 Phase B 任务 G-008 同步落地:

- **G-008**:升级 `backend/audit/models.py::AuditEventType` 新增 4 类;单元测试 G-031 enum 完整性断言 22 → 26 类;集成测试 G-037 4 类新事件端到端写 audit_events 断言

## 不在本 amendment 范围内

- ❌ 不修改 `AuditActor` enum 5 类(继承)
- ❌ 不修改 `AuditOutcome` enum 4 类(继承)
- ❌ 不修改 `AuditEvent` frozen Pydantic schema 字段集(继承 10 字段)
- ❌ 不修改 audit_events collection 索引(5 索引继承)
- ❌ 不修改 TTL 180 天(继承)
- ❌ 不修改 JSONL 双写 30 天(继承)
- ❌ 不引入新 actor 类型(仍 5 类:feishu_user / frontend_user / system / scheduler / cli)
- ❌ 不引入 audit 查询前端页面(继承 P1-6 §2 红线 19;CLI + GET API only;P1-5 11 页名额永锁)

## 与 P1-7 §1 决策协同

- 继承 P1-7 §1.6 月节点 50%/80%/100% 触发动作 → `MONTHLY_BUDGET_*` 3 个枚举值
- 继承 P1-7 §1.5 Kimi daily cap ¥4 → `KIMI_DAILY_CAP_4CNY_BREACHED` 枚举值
- 继承 P1-7 §1.8 派生 P1-6 amendment 完整定义

---

**P1-6 amendment 2026-05-10 锁定 ✅**

`AuditEventType` enum 22 类 → 26 类;均归类 4 异常 + 拦截事件;非破坏式追加。
