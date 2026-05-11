# P0-7 Amendment 2026-05-11 — risk_parameter_proposals 走 P2-2 shadow validate 流程(扩字段 + 合并 P2-2 体系)

## 元数据

| 字段       | 值 |
|-----------|----|
| amendment 编号 | P0-7-amendment-2026-05-11-risk-proposals-shadow-validation |
| 主决策     | `docs/decisions/P0-7-risk-redlines-position-circuit-universe-llm-immutability.md` |
| 触发决策   | `docs/decisions/P2-2-self-evolution-conservative-three-paths-shadow-validate-feishu-notify-file-registry.md` §1.1 + §2 红线 10 |
| 前序 amendment | `docs/decisions/P0-3-amendment-2026-05-09-extend-risk-checks-from-7-to-14.md`(实施期产出;扩 7-check → 14-check)|
| 日期       | 2026-05-11 |
| 状态       | ✅ 已锁定 |
| 性质       | **非破坏式扩展**(P0-7 §1.4 锁定的 RiskConfig 全锁 + LLM 永不持有写引用不变;仅 `risk_parameter_proposals` collection 扩字段 + review 流程升级)|

## 触发原因

P2-2 自进化机制边界 dedicated session 锁定后,用户 Round 3 Q1 选 "合并到 P2-2 体系"(risk_parameter_proposals 通道纳入 P2-2 shadow validation 体系)。原因:

- P0-7 §1.4 锁定的 `risk_parameter_proposals` 通道**周报 review + 人工 amendment + 重启**与 P2-2 锁定的 **shadow validate + 飞书主动通知 + 自动起草 pending_amendment + 人工 review + restart** 功能重叠
- 双轨混乱(同一类型提议两套流程)违反"高内聚低耦合"原则
- 用户希望"人工仅 review + sign-off,不再走 P0-7 §1.4 原周报 review 流程"

合并后 risk_parameter_proposals 与 prompt_version / rag_document / exemplar 同款流程,统一在 P2-2 evolution_shadow_run cron 处理。

## 变更内容

### `backend/risk/proposals.py::RiskParameterProposal` schema 扩字段

**P0-7 §1.4 原 schema**:

```python
# backend/risk/proposals.py(P0-7 §1.4 原锁定状态)
class RiskParameterProposal(BaseModel):
    """LLM 提议的 RiskConfig 参数变更条目(只读 ledger;人工 review 才生效)。"""
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    proposal_id: str = Field(pattern=r"^RPP-\d{8}-\d{6}-\d{6}-\d{3}$")
    """格式 RPP-{YYYYMMDD}-{HHMMSS}-{microseconds}-{seq};单调递增"""

    proposed_by: Literal["fund_manager", "risk_officer"]
    """提议来源 Agent"""

    proposal_text: str = Field(max_length=5000)
    """LLM positive list 字段(P0-10 §1.1);LLM 可写"""

    target_field: str
    """目标字段路径,如 'PositionLimitsConfig.max_single_stock_pct'"""

    proposed_value: float | int | str
    """提议新值"""

    current_value: float | int | str
    """当前生产值(代码自动填,LLM 不写)"""

    evidence_collection_ids: list[str]
    """支撑 evidence_id 列表(代码自动填)"""

    proposed_at: datetime
    """提议时间;tz-aware Asia/Shanghai"""

    accepted: bool = False
    """人工 review 后是否接受;LLM 严禁写(代码守门;P0-10 §1.2 类别 9)"""

    accepted_at: datetime | None = None
    accepted_by: str | None = None
```

**本 amendment 扩字段**(非破坏式追加 4 字段;默认值兼容历史 record):

```python
# backend/risk/proposals.py(本 amendment 后状态)
class RiskParameterProposal(BaseModel):
    """LLM 提议的 RiskConfig 参数变更条目;P2-2 amendment 后走 shadow validate 流程。"""
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    # === P0-7 §1.4 原字段(不变)===
    proposal_id: str = Field(pattern=r"^RPP-\d{8}-\d{6}-\d{6}-\d{3}$")
    proposed_by: Literal["fund_manager", "risk_officer"]
    proposal_text: str = Field(max_length=5000)
    target_field: str
    proposed_value: float | int | str
    current_value: float | int | str
    evidence_collection_ids: list[str]
    proposed_at: datetime
    accepted: bool = False
    accepted_at: datetime | None = None
    accepted_by: str | None = None

    # === P2-2 amendment 新增字段(4 字段)===
    target_artifact_type: Literal[
        "risk_config",         # 默认值;P0-7 §1.4 原语义
        "position_limits",
        "circuit_breaker",
        "watchlist_policy",    # 注:本字段仅锁约束;实际 watchlist 修改仍需 P0-9 amendment
        "broker_config",       # 注:本字段仅锁约束;实际 broker 修改仍需 P1-2.C amendment
    ] = "risk_config"
    """提议目标 artifact 类型 discriminator(便于 P2-2 shadow validate 路由分发)"""

    shadow_validation_status: Literal[
        "pending",             # 默认值;尚未走 shadow validate
        "running",             # evolution_shadow_run cron 正在跑
        "passed",              # shadow validate 通过 + 5+3 硬门槛达标 + challenger 胜
        "failed",              # shadow validate 未通过
        "promoted",            # 人工 review 后已 amendment + restart 生效
        "rejected",            # 人工 review 后拒绝
    ] = "pending"
    """shadow validate 状态;P2-2 evolution_shadow_run cron 自动更新"""

    pending_amendment_id: str | None = None
    """指向 docs/decisions/pending/{artifact_id}.md 草案 ID;shadow validate 通过后由 amendment_drafter 自动填"""

    feishu_notified_at: datetime | None = None
    """飞书主动通知时间;evolution_feishu_notifier 写入"""
```

### P0-7 §1.4 流程更新

**原流程**(P0-7 §1.4 锁定):

```
1. fund_manager 或 risk_officer Agent 复盘时写 proposal_text
   ↓
2. 写 risk_parameter_proposals collection(LLM positive)
   ↓
3. 每周一 09:00 项目所有者人工 review proposal list
   ↓
4. 接受的 proposal → 编辑 config/risk.yaml + 起草 P0-7-amendment + 重启
   ↓
5. 拒绝的 proposal → 设 accepted=False(代码守门写,LLM 不写)
```

**本 amendment 后新流程**:

```
1. fund_manager 或 risk_officer Agent 复盘时写 proposal_text + target_field
   ↓ (走 P0-10 LLM positive list 字段权限)
2. 写 risk_parameter_proposals collection(LLM positive;status=pending)
   ↓
3. BrokerScheduler 第五 cron evolution_shadow_run 22:00 mon-fri 触发:
   a. 取 status=pending 的 proposal list
   b. 对每个 proposal:
      - 设 status=running
      - 调 backend/services/shadow_chain.py::run_shadow_validate
        (artifact_type=target_artifact_type, artifact_id=proposal_id)
      - 输出 shadow_acceptance_reports collection
      - 写 audit `shadow_evolution_run_completed`
   c. challenger 胜判定:
      - 通过 + 5+3 硬门槛达标 + challenger 胜 → status=passed
      - 不通过 → status=failed
   ↓
4. status=passed 的 proposal:
   a. amendment_drafter.draft_p0_7 → 起草 docs/decisions/pending/{proposal_id}.md
   b. 填 pending_amendment_id 字段
   c. 写 audit `evolution_amendment_drafted`
   d. EvolutionFeishuNotifier.fire_pending(proposal_id, shadow_metrics)
   e. 填 feishu_notified_at 字段
   f. 写 audit `evolution_feishu_notified`
   ↓
5. 用户 review pending_amendment.md(via 飞书通知触发 → 进 SystemStatus.vue 自进化点 → 走 GET /api/evolution/pending)
   ↓ (人工最终批准)
6. 用户编辑 config/risk.yaml + 移动 pending amendment 到 docs/decisions/ + 重启
   ↓
7. 重启加载新 RiskConfig
8. accepted=True + accepted_at + accepted_by 由代码守门写;status=promoted
9. 拒绝路径:用户在 SystemStatus.vue 标记 rejected → status=rejected
```

### P0-7 §2 红线 14(同步说明)

**原红线 14**:`RiskConfig`/`PositionLimitsConfig`/`CircuitBreakerConfig` runtime 不可改 + hot-reload 禁用 + 修改必走 git diff + amendment + 重启。

**本 amendment 后红线 14 不变**:RiskConfig 等 frozen Pydantic strict + extra='forbid' + runtime 不可改 + hot-reload 禁用**完全保留**;仅 `risk_parameter_proposals` collection 的 LLM 提议路径从"周报 review"升级为"shadow validate + 飞书通知 + 起草 amendment 草案"。**人工 review + 编辑 YAML + amendment + restart 的最后 3 步仍为人工执行,严禁自动化**。

### P0-7 §1.4 LLM 字段权限(同步说明)

**LLM positive list 不变**(继承 P0-10 §1.1):

- `risk_parameter_proposals.proposal_text` 仍是 LLM positive list 字段(LLM 可写)
- `risk_parameter_proposals.target_field` 仍可由 LLM 间接写(通过 proposal_text 中提取,代码自动填字符串)

**LLM negative list 强化**(本 amendment 锁定):

- `risk_parameter_proposals.target_artifact_type` LLM **严禁写**(discriminator 由代码守门写;防 LLM 自我升级提议跨类别)
- `risk_parameter_proposals.shadow_validation_status` LLM **严禁写**(P2-2 cron 守门写;防 LLM 伪造 passed 状态)
- `risk_parameter_proposals.pending_amendment_id` LLM **严禁写**(amendment_drafter 写)
- `risk_parameter_proposals.feishu_notified_at` LLM **严禁写**(evolution_feishu_notifier 写)
- `risk_parameter_proposals.accepted` / `accepted_at` / `accepted_by` LLM **严禁写**(继承 P0-10 §1.2 类别 9)

## 与 P0-7 主决策红线兼容性核查

- **P0-7 §1.4(RiskConfig runtime 不可改 + LLM 永不持有写引用)** ✅ 兼容:本 amendment 仅扩展 `risk_parameter_proposals` collection 字段;**不**修改 RiskConfig schema;**不**给 LLM 任何 runtime write 路径
- **P0-7 §2 红线 11(LLM 严禁产出 RiskCheckSummary)** ✅ 兼容:本 amendment 不引入 LLM 产出 RiskCheckSummary;shadow validate 由代码确定性计算
- **P0-7 §2 红线 14(hot-reload 禁用)** ✅ 兼容:本 amendment 不引入 hot-reload;promote 必经 restart
- **P0-7 §1.6 DailyTradingState 注入路径** ✅ 兼容:本 amendment 不修改 DailyTradingState
- **P0-7 §1.7 circuit_breaker_state collection** ✅ 兼容:本 amendment 不修改 circuit_breaker_state
- **P0-7 §1.8 stock_metadata 板块识别** ✅ 兼容:不冲突

## 与 P0-10 + P1-2.A + P1-6 + P2-2 协同

- **P0-10 §1.1 LLM positive list 4 类不变** ✅:本 amendment 不扩展 LLM positive list;`proposal_text` 仍为现有字段
- **P0-10 §2 红线 1(LLM 字段权限矩阵)** ✅ 兼容:新增 4 字段全部 LLM negative
- **P1-2.A BrokerScheduler 5th cron** ✅ 协同:派生 P1-2.A-amendment-2026-05-11-evolution-shadow-cron-5th;新 evolution_shadow_run cron 处理 risk_parameter_proposals
- **P1-6 §1.7 audit schema + AuditEventType 27 → 34 类** ✅ 协同:派生 P1-6-amendment-2026-05-11-audit-eventtype-34;新 7 类 audit 事件覆盖 risk proposal 生命周期
- **P2-2 §1.1 ~ §1.6 完整定义** ✅:本 amendment 是 P2-2 决策的具象实现

## 实施期任务

P2-2 §3 实施期 Phase X-B 任务 H-012 落地本 amendment:

- **H-012**: P0-7 amendment 落地 — `risk_parameter_proposals` collection 扩字段(`target_artifact_type` discriminator + `shadow_validation_status` + `pending_amendment_id` + `feishu_notified_at`);Mongo `risk_parameter_proposals.{field}` 默认值兼容历史 record(MongoDB 字段不存在等价于 None / "pending" / "risk_config");单元测试 frozen + strict + extra='forbid' 约束覆盖新 4 字段

## 不在本 amendment 范围内

- ❌ 不修改 RiskConfig / PositionLimitsConfig / CircuitBreakerConfig schema(继承 P0-7 §1.4 锁定)
- ❌ 不引入 hot-reload(继承 P0-7 §2 红线 14)
- ❌ 不引入新 LLM positive 字段(继承 P0-10 §1.1)
- ❌ 不引入新写入端点(继承 P1-5 §2 红线 5 仅 2 写入端点)
- ❌ 不引入 RiskConfig runtime mutation 路径(继承 P0-7 §1.4)
- ❌ 不引入 backend/risk/ → backend/llm/ 或 backend/agents/ import(继承 P0-1 §2 红线 8)

## 与 P0-3 amendment(7 → 14 check)关系

- 本 amendment 是 P0-7 主决策的**第 2 个 amendment**(第 1 个为 P0-3-amendment-2026-05-09;实施期产出 14-check)
- 不冲突:14-check 仍由 RiskEngine 纯函数确定性产出;本 amendment 仅升级 LLM 提议 review 流程

---

**P0-7 amendment 2026-05-11 锁定 ✅**

risk_parameter_proposals collection 扩 4 字段(target_artifact_type / shadow_validation_status / pending_amendment_id / feishu_notified_at)+ review 流程从周报升级为 P2-2 shadow validate + 飞书主动通知 + 自动起草 pending_amendment;非破坏式扩展;RiskConfig 全锁 + LLM 永不持有写引用红线**完全保留**;P2-2 §1.1 + §2 红线 10 派生。
