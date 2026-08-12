# P1-6 Amendment 2026-05-10 — AuditEventType 26 → 27 类(P2-4 派生:新增 EXECUTION_REPORT_PARSE_FAILED)

## 元数据

| 字段       | 值 |
|-----------|----|
| amendment 编号 | P1-6-amendment-2026-05-10-audit-eventtype-27 |
| 主决策     | `docs/decisions/P1-6-secrets-shell-env-12month-event-driven-rotation-loopback-only-no-local-auth-audit-mongo-jsonl-dual-write.md` |
| 触发决策   | `docs/decisions/P2-decisions-finalization-2026-05-10.md` Q4 P2-4 告警渠道 |
| 前序 amendment | `docs/decisions/P1-6-amendment-2026-05-10-audit-eventtype-26.md`(P1-7 派生;22 → 26 类)|
| 日期       | 2026-05-10 |
| 状态       | ✅ 已锁定 |
| 性质       | **非破坏式扩展**(P1-7 amendment 后 26 类不变;追加 1 类归类 4 异常 + 拦截事件)|

## 触发原因

P2-4 告警渠道决策对齐时核查 P1-6 §1.8 锁定 22 类 + P1-7 amendment 后 26 类 AuditEventType,对照用户原始决策清单 8 类必须告警事件,发现唯一遗漏 — **飞书回报解析失败**(用户列出的"飞书回报解析失败")没有对应独立 `AuditEventType` 事件。当前 P0-4 §1 已锁严格正则失败即 AMBIGUOUS 不更新 MockBroker + 30 分钟超时追问 + 澄清飞书 5 模板预写死,但状态机本身的"parse_ok=False" 节点没有 audit 事件挂载,导致后续 audit 反查"过去 7 天有多少回报解析失败"需要跨多 collection join,效率低且不符合 P1-6 §1.8 "4 类事件强制写 audit + 调试性事件不入"统一原则。

## 变更内容

### `backend/audit/models.py::AuditEventType` enum 扩展

**P1-7 amendment 后状态(26 类)** + **本 amendment 新增 1 类**:

```python
# backend/audit/models.py(P2-4 amendment 后状态;27 类)
class AuditEventType(str, Enum):
    """锁定 27 类 audit event_type — 任何新增必走 P1-6-amendment。"""
    
    # 类 1 — 两唯二写入端点调用(2)
    # ...(同 P1-6 原 2 类 不变)
    
    # 类 2 — 模式切换 + 冻结源 + 生命周期事件(11)
    # ...(同 P1-6 原 11 类 不变)
    
    # 类 3 — 凭证生命周期 + 飞书收发(7)
    # ...(同 P1-6 原 7 类 不变)
    
    # 类 4 — 异常 + 拦截事件(8 + 4 + 1 = 13)
    STATE_MACHINE_ILLEGAL_TRANSITION = "state_machine_illegal_transition"
    RISK_ENGINE_CHECK_REJECTED = "risk_engine_check_rejected"
    BUILDER_EARLY_RETURN = "builder_early_return"
    MOCKBROKER_PRICE_LIMIT_VIOLATION_AT_FILL = "mockbroker_price_limit_violation_at_fill"
    DATA_QUALITY_BREACH = "data_quality_breach"
    RECONCILIATION_TICKET_OPEN_OR_EXPIRED = "reconciliation_ticket_open_or_expired"
    LLM_CALL_TIMEOUT_30S = "llm_call_timeout_30s"
    DAILY_COST_CEILING_20CNY_BREACHED = "daily_cost_ceiling_20cny_breached"
    # P1-7 amendment 新增(4 类):
    MONTHLY_BUDGET_50PCT_REACHED = "monthly_budget_50pct_reached"
    MONTHLY_BUDGET_80PCT_REACHED = "monthly_budget_80pct_reached"
    MONTHLY_BUDGET_100PCT_REACHED = "monthly_budget_100pct_reached"
    KIMI_DAILY_CAP_4CNY_BREACHED = "kimi_daily_cap_4cny_breached"
    # P2-4 amendment 新增(本 amendment;1 类):
    EXECUTION_REPORT_PARSE_FAILED = "execution_report_parse_failed"
```

### P1-6 §2 红线 12 同步更新

> **`AuditEventType` enum 锁定 27 类 event_type**(P2-4 amendment 后;P1-7 amendment 后 26 类 + 1 类):任何新增/删除/重命名必走 `P1-6-amendment-{date}-{原因}.md`;严禁 magic string `event_type` 写入 audit(必须用 enum)。

### 与 P1-6 §1.7 audit schema 兼容性

新增 1 类完全复用 P1-6 §1.7.1 `AuditEvent` frozen Pydantic schema 不变:

| 字段 | EXECUTION_REPORT_PARSE_FAILED 值 |
|------|---------------------------------|
| `event_id` | UUIDv4(默认生成)|
| `timestamp` | UTC ISO8601(默认生成)|
| `event_type` | `AuditEventType.EXECUTION_REPORT_PARSE_FAILED` |
| `actor` | `AuditActor.FEISHU_USER`(用户经飞书发回报)/ `AuditActor.FRONTEND_USER`(用户经前端备用录入页发回报);取决于双路径调用方 |
| `actor_detail` | feishu user_id 或 frontend session_id |
| `resource_type` | `'execution_report'` |
| `resource_id` | `None`(parse 失败时无法生成 instruction_id 关联;严禁猜测,继承 P0-4)|
| `payload` | `{raw_text: str, regex_attempt_results: dict[str, bool], parse_error_kind: str}`(raw_text 完整记录因继承 P0-3 §2.5 LLM 严禁拼接飞书消息文本无 prompt injection 风险)|
| `outcome` | `AuditOutcome.FAILURE`(parse 失败) |
| `correlation_id` | 尝试关联到正在等待回报的 InstructionPlan ID(若可识别);失败则 None |
| `reason_namespace` | `'execution_report_ambiguous'`(继承 P0-4 §1 严格正则失败即 AMBIGUOUS 节奏;与 P1-2.C 命名空间区分原则对齐)|

### 告警通道(继承 P1-7 §1.7)

- **飞书 warning**(继承 P0-4 §1 澄清飞书 5 模板预写死;`Alerter.fire(severity=WARNING, dedup_15min)` + 澄清飞书消息走 P0-2 §1.2 主路径长连接发送;**严禁走 P0-2 §2.5 备用 webhook**因继承"备用 webhook 仅可发系统告警绝不发买卖指令/对账请求/澄清消息"红线)
- **audit 写入** `EXECUTION_REPORT_PARSE_FAILED`(reason_namespace='execution_report_ambiguous';actor=FEISHU_USER 或 FRONTEND_USER;outcome=FAILURE;raw_text 完整记录)

### TTL 与双写继承 P1-6 §2 红线 13+14 不变

- Mongo `audit_events` collection TTL 180 天(继承)
- JSONL `logs/audit.jsonl` 30 天双写(继承)
- Mongo failure 时 fail-open(JSONL 兜底 + warning 不阻主路径;继承 P1-6 §2 红线 15)

## 与 P1-6 + P0-4 + P0-3 + P1-5 红线兼容性核查

- **P1-6 §2 红线 11(audit_events append-only insert-only)** ✅ 兼容:新事件同款约束严禁 update/delete/replace
- **P1-6 §2 红线 16(LLM 严禁写 audit_events)** ✅ 兼容:`ExecutionReportParser` 由 backend/execution/parser.py 主导(非 LLM);失败 raise 后调用方挂 audit_store.write 仍是确定性代码非 LLM
- **P1-6 §2 红线 17(4 类事件强制写 audit + 调试性事件不入)** ✅ 兼容:本事件归类 4 强制写
- **P1-6 §2 红线 18(凭证类 audit 仅 fingerprint 严禁 plaintext)** ✅ 兼容:本事件不涉及凭证;raw_text 完整记录(无 prompt injection 风险继承 P0-3 §2.5)
- **P1-6 §2 红线 19(audit 查询入口仅 CLI + GET API)** ✅ 兼容:可通过 `scripts/query_audit.py --event-type EXECUTION_REPORT_PARSE_FAILED` 或 GET `/api/audit/events?event_type=...` 查询
- **P0-4 §1 严格正则 only + LLM 完全不参与回报路径** ✅ 兼容:本 amendment 仅在 ExecutionReportParser 失败 raise 节点挂 audit hook;不引入任何 LLM 参与 parse 路径(防 prompt injection)
- **P0-3 §2.5 LLM 严禁拼接飞书消息文本** ✅ 兼容:raw_text 完整记录无须 LLM 拼接;**澄清飞书发送走 P0-4 §1 五模板预写死**(模板硬编码非 LLM 拼接)
- **P1-5 §2 红线 5 仅 2 写入端点**(execution-reports + reconciliation-tickets/{id}/decide)✅ 兼容:本事件由两写入端点的失败分支挂 audit hook;不引入新写入端点
- **P0-2 §2.5 备用 webhook 仅可发系统告警** ✅ 兼容:澄清飞书走主路径不走备用 webhook

## 实施期任务

P1-7 实施期 Phase B 任务 G-008 同步落地(已挂 P1-7 任务,本 amendment 无需新增 G 任务):

- **G-008 升级**:在 `backend/audit/models.py::AuditEventType` 新增 4 类(P1-7 amendment)+ 1 类(本 amendment;EXECUTION_REPORT_PARSE_FAILED)= 共新增 5 类
- **G-017 升级**(原 P1-7 G-017 是 Kimi cap 集成,这里是新引用):在 `backend/execution/parser.py::ExecutionReportParser` 失败 raise 分支挂 audit_store.write 调用 + 飞书 warning 触发(继承 P0-4 §1 澄清飞书 5 模板)
- **G-031 升级**:单元测试 enum 完整性断言 26 → 27 类
- **G-037 升级**:集成测试 5 类新事件(P1-7 amendment 4 + 本 amendment 1)端到端写 audit_events;特别核 EXECUTION_REPORT_PARSE_FAILED 的 raw_text + regex_attempt_results + reason_namespace='execution_report_ambiguous' 完整性

## 不在本 amendment 范围内

- ❌ 不修改 `AuditActor` enum 5 类(继承)
- ❌ 不修改 `AuditOutcome` enum 4 类(继承)
- ❌ 不修改 `AuditEvent` frozen Pydantic schema 字段集(继承 10 字段)
- ❌ 不修改 audit_events collection 索引(5 索引继承)
- ❌ 不修改 TTL 180 天(继承)
- ❌ 不修改 JSONL 双写 30 天(继承)
- ❌ 不引入 LLM 参与 parse 路径(继承 P0-4 §1 严格正则 only)
- ❌ 不引入新写入端点(继承 P1-5 §2 红线 5 仅 2 写入端点)
- ❌ 不引入 audit 查询前端页面(继承 P1-6 §2 红线 19;CLI + GET API only)
- ❌ 不修改 P1-7 amendment 后 26 类 AuditEventType(本 amendment 仅追加 1 类)

## 与 P1-7 amendment 协同

- 本 amendment 是 P1-6 主决策的**第 2 个 amendment**(第 1 个为 P1-7 派生 22→26 类;本 amendment 26→27 类)
- 累积更新 P1-6 §2 红线 12 锁定为 27 类(P1-6 主文档 + P1-7 amendment + 本 amendment 三者协同)
- 实施期 G-008 任务一次性升级 backend/audit/models.py 含 5 类新增不需多次部署

---

**P1-6 二次 amendment 2026-05-10 锁定 ✅**

`AuditEventType` enum 22 → 26 → 27 类;EXECUTION_REPORT_PARSE_FAILED 归类 4 异常 + 拦截事件;非破坏式追加;P2-4 决策派生。
