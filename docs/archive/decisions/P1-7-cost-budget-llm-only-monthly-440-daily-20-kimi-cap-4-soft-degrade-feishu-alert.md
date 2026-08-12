# P1-7 — 成本预算扩(LLM only / 日 ¥20 hard + 月 ¥440 soft / Kimi 单独 daily cap ¥4 / 软触发关 Kimi escalation / 飞书 hard+月节点告警 + Phase B 成本拆解面板)

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P1-7 |
| 决策日期   | 2026-05-10 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联 audit | `docs/quantmind_project_audit_2026-05-07.md` §3(LLM 子系统现状)+ §6.4(成本守门现状) |
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §P1-7 — 成本预算 |
| 范围说明   | 本决策为 **P1 决策对齐路径 A 第六份**(P1-2.A/B/C 三子全锁 + P1-5 + P1-6 后第三份非 P1-2 决策);**P1 决策对齐收官**;锁定成本预算分类边界 + 月预算维度 + 日/月优先级 + Kimi 单独 cap + 软触发降级矩阵 + 月节点动作 + 告警通道 + Phase B 成本拆解面板挂载 |
| 依赖决策   | `docs/decisions/P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md`(§2.5 备用 webhook 仅可发系统告警绝不发买卖指令)+ `docs/decisions/P0-7-risk-redlines-position-circuit-universe-llm-immutability.md`(§2 红线 14 RiskConfig runtime 不可改 → cost_guard 配置同款)+ `docs/decisions/P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md`(§1.4 ¥20 hard ceiling + LLM 全停 1h 触发 P0-6 中断 + agent_models.yaml tiered routing kimi escalation + 4 必经 Agent + §2 红线 1 LLM 字段权限矩阵 → cost_guard 内部不持 LLM 写引用)+ `docs/decisions/P1-2.B-mtm-30s-equity-points-data-quality-on-demand.md`(§1.4 BrokerScheduler 30s 节奏 → 数据成本节奏对齐免费源不设 ceiling)+ `docs/decisions/P1-5-frontend-workflow-mvp-7-pages-readonly-first-write-strict-bounded.md`(§2 红线 1 MVP 7 + Phase B 4 共 11 页永锁含"成本拆解" + §2 红线 5 仅 2 写入端点其他全 GET)+ `docs/decisions/P1-6-secrets-shell-env-12month-event-driven-rotation-loopback-only-no-local-auth-audit-mongo-jsonl-dual-write.md`(§1.7 audit_events frozen schema + §1.8 AuditEventType 22 类 + §2 红线 16 LLM 严禁写 audit + §2 红线 18 凭证 fingerprint 严禁 plaintext)|
| 派生 amendment | (1) `P0-10-amendment-2026-05-10-cost-budget-extension.md`:¥20 hard ceiling 扩展为 LLM 总 daily ¥20 + Kimi 单独 daily ¥4 + 月 soft ¥440(¥20 hard 不变,扩 monthly 维度)(2) `P1-6-amendment-2026-05-10-audit-eventtype-26.md`:`AuditEventType` enum 22 类 → 26 类(新增 `monthly_budget_50pct_reached` / `monthly_budget_80pct_reached` / `monthly_budget_100pct_reached` / `kimi_daily_cap_4cny_breached`)|
| 替代       | 当前成本守门状态:`backend/services/cost_guard.py` 已有 BudgetState frozen + Redis 聚合 + DailyBudgetExceededError + 70% soft / 100% hard 双阈值 ✅;`backend/llm/cost_tracker.py` 三 provider 单价表完整(deepseek/qwen/kimi)+ `track_usage()` 写 Redis ✅;`backend/monitoring/alerter.py` 已有 `cost_budget_exceeded` 类型 + 15min cooldown + webhook 支持 ✅;P1-6 已铺垫 `daily_cost_ceiling_20cny_breached` audit event_type ✅;但**无月预算维度 / 无 Kimi 单独 cap / 无软触发降级矩阵 / 无月节点动作 / 无 Phase B 成本拆解面板挂载** |

## 决策摘要

QuantMind P1-7 成本预算扩采用 **仅锁 LLM 预算其他不设 + 日 ¥20 hard 严格 + 月 ¥440 soft 警告 + Kimi 单独 daily cap ¥4(防 escalation 单笔吞全天 cap)+ 软触发关闭 Kimi escalation 路径(fund_manager 仅走 triage qwen)+ 月预算 50% audit only / 80% 飞书 warning / 100% 飞书 critical 三阶节点 + 静态固定 ¥440 按自然月不依赖 holidays.yaml + 飞书仅 hard breach + 月节点 + Phase B 成本拆解面板(P1-5 4 Phase B 页之一)全量展示** 架构,完成 P1 决策对齐路径 A 第六份决策(**P1 决策对齐收官**):

1. **预算结构:仅锁 LLM 预算,数据/运维不设 ceiling**:LLM 是唯一可变成本(三 provider 按 token 计费;DeepSeek `¥0.0002/k` + Qwen `¥0.001/k` + Kimi `¥0.0021/k` input + `¥0.0084/k` output)。**数据源 akshare/adata/baostock 全免费**(无 API 额度 / 无计费;30s 节奏继承 P1-2.B 不变);**运维成本** MongoDB/Redis 自托管在 127.0.0.1(继承 P1-6 §1.5)月固定 ¥0(本机)或 ≤¥150(若 P2 升级 Atlas M0+)— **P1-7 仅预留字段不主动监控**(避免引入 cloud billing API 凭证扩展破 P1-6 凭证池仅 LLM 3 + 飞书 6 锁状态)。**LLM 总 daily hard ¥20 + monthly soft ¥440 + Kimi daily cap ¥4**(防 escalation 单笔失控);**deepseek + qwen 不单独 cap**(允许 failover 自由分配;deepseek 是 kimi 的 1/42 价廉做大头)。**不引入三类全分(LLM/数据/运维) ceiling**(数据全免费时 ceiling=0 无意义 + 运维需引入云 billing SDK 过度工程);**不引入单一预算共享所有类目**(未来引入第三方付费数据源时无法快速分配);**不引入仅 escalation 路径单独 cap**(与 Kimi 单独 cap 实质等价因当前 Kimi 仅用于 escalation,Kimi cap 形式更简单)。

2. **月度预算 ¥440 = 22 工作日 × ¥20 静态固定 / 按自然月**:月预算 = 22 工作日 × ¥20 daily hard = ¥440;**所有自然月统一 ¥440 ceiling 不依赖 holidays.yaml 浮动**(月初 cost_guard 启动期 Redis key `llm:budget:monthly:{YYYYMM}` 静态初始化即可)。实际 underspend(某些月 19-20 交易日)作为 buffer 无需特别处理。**不引入动态查 holidays.yaml 当月实际交易日数 × ¥20**(holidays.yaml 微调时 ceiling 随之变动违反 frozen 原则;跨月边界夜间调度复杂);**不引入月初首交易日 09:00 锁定当月交易日数 × ¥20**(需额外 BrokerScheduler cron 增加复杂度且漏执行需 fallback 到 ¥440 与静态等价);**不引入按自然月日均 ¥20 滚动 30 天 = ¥600**(与 22 工作日 × ¥20 = ¥440 不一致 + 与 P0-10 ¥20 daily 节奏的"工作日意涵"不符)。

3. **月预算与日预算关系:日 hard 严格 + 月 soft 警告**:日 ¥20 hard 仍是**唯一停摆触发器**(继承 P0-10 §1.4 不变 + DailyBudgetExceededError 仍 raise + simulation_auto 进入 `paused-budget-breach` 状态);月 ¥440 soft 仅在 50%/80%/100% 节点写 audit + 飞书 info/warning/critical,**月预算 100% 超限不停摆 LLM 不暂停 simulation_auto**(继承 P0-10 LLM 全停 1h 触发 P0-6 中断的语义不应被月度预算意外触发)。**不引入月 hard 严格** + 日 hard ¥20(月度连环触发即停摆当月剩余天 LLM 调用 = 假阳性多 + simulation_auto 实际并未失控只是月节奏波动);**不引入日 soft + 月 hard 严格**(冲突 P0-10 §1.4 ¥20 hard ceiling 锁定需走 P0-10-amendment 但破 P0-10 fail-closed 精神)。日 hard 是熔断器,月 soft 是仪表盘 — 角色清晰不重合。

4. **软触发(日 ¥14 = 70%)优先降级:关闭 Kimi escalation 路径**:软触发即调用 `SoftDegradeManager.activate_kimi_escalation_block()`,fund_manager 走 tiered routing 时不再 escalate 到 kimi-k2.6(`agent_models.yaml::escalation: confidence_lt: 0.6` 暂时改为 `confidence_lt: -1.0` 永不触发);fund_manager 仅走 triage = qwen-3.6-plus(`¥0.001/k` 是 kimi-output `¥0.0084/k` 的 1/8.4)。**4 必经 Agent 完整保留**(fundamental_analyst + technical_analyst + risk_officer + fund_manager;继承 P0-10 §2 红线 2 缺一即降级 HOLD;软触发严禁缩减 4 必经 Agent 任一);**指令完整率不受影响**(fund_manager 仍能产出 BUY/SELL/HOLD)。**不引入软触发关闭事件路径 cap=1**(event_path 是 P0-9 加分非核心 cap=1 已是最低限再降即等于完全关闭事件路径相对意义不大);**不引入软触发降低 fast 频次 09/11/13/15 → 09/13**(fast 是盘中验证关键节奏 4→2 节省 LLM 但 11:00/15:00 突发事件响应延迟到 13:00/次日 09:00 不可接受);**不引入软触发全模型降级 deepseek-only**(中文金融领域 qwen 优势丧失影响信号质量 + 需走 P0-10-amendment 改 tiered routing 锁定;Kimi 关闭已抓最大头 80% LLM 成本来源 — escalation 是最贵触发器);**不引入软触发立即停摆**(软触发是预警节点不是熔断节点 — 否则与 hard 角色重叠)。

5. **Kimi daily cap = ¥4(20% 总日预算)单独锁**:`backend/services/cost_guard.py::BudgetState` 升级新增 `kimi_spent_today` + `kimi_daily_cap = 4.0`;Kimi 调用前必须先调用 `assert_kimi_cap_allows()`;Kimi cap 触发即 raise `KimiDailyCapExceededError`(类似 DailyBudgetExceededError 但仅暂停 Kimi 调用,不暂停 deepseek + qwen)。**理由**:Kimi `¥0.0084/k output` 是 DeepSeek `¥0.0002/k` 的 42 倍 + 8k thinking tokens,单笔 escalation(8k thinking + 2k output)理论可达 8000 × 0.0084 / 1000 + 2000 × 0.0084 / 1000 ≈ ¥0.084/笔 — 但若 escalation 失控连发 100+ 次单 Kimi 一项可达 ¥8.4 接近全天预算的 42%。¥4 cap = 最多 ~47 次 escalation/日(覆盖正常场景;P0-10 已锁 fund_manager.escalation 仅在 triage confidence < 0.6 时触发,正常一天 < 5 次)。**Kimi cap 与软触发关 Kimi escalation 形成二道防线**:soft breach ¥14 前先额度限制(¥4 内自然约束);soft breach ¥14 后完全关闭 escalation。**deepseek + qwen 不单独 cap**:provider failover 时若 deepseek/qwen 任一独立 cap 灭则可能开不出指令(deepseek 灭后 qwen+kimi 总额 ¥10 不够 4 必经 Agent 全跑)— 失去 failover 弹性;允许两者自由分配在 ¥20 - ¥(kimi spent) 内动态调度。

6. **月预算 50%/80%/100% 节点动作锁定:渐进升级**:Redis key `llm:budget:monthly:{YYYYMM}:spent` 累积每日 LLM 总花费;`cost_guard.check_monthly_thresholds()` 在每次 `track_usage()` 后异步检查 — (a) **50% (¥220)** → 仅写 audit_event `monthly_budget_50pct_reached`(actor=system, outcome=success, payload={spent: 220.x, budget: 440.0, pct: 50});不发飞书不打扰用户,作为月度健康度仪表盘进度数据点;(b) **80% (¥352)** → audit_event `monthly_budget_80pct_reached` + `Alerter.fire(severity=warning, dedup_15min, message='LLM 月度预算到 80% 当月剩 N 天')`;飞书 warning 通道(继承 P0-2 §2.5 备用 webhook 仅告警);(c) **100% (¥440)** → audit_event `monthly_budget_100pct_reached` + `Alerter.fire(severity=critical, dedup_15min, message='LLM 月度预算 100% 已超 当月剩 N 天 — 仅 audit 不暂停 LLM 因日 hard 仍唯一熔断器')` + 附 cost_breakdown(provider 拆 + Agent 拆)。**月节点严禁**触发 daily ceiling 动态调整(不引入 100% 后剩余天 daily 缩到 ¥10 — 违反 P0-10 ¥20 daily hard 静态锁定原则 + 增加双轨变量复杂度);**不引入 50%/80%/100% 全发飞书**(50% 是中端状态发送 = 噪声违反 P1-5 低噪声原则);**不引入仅 100% 节点**(80% 时未提前预警冲击到才反应延迟无法计划);**不引入额外 25%/75% 节点**(过度密集打扰)。

7. **告警通道:飞书仅 hard breach + 月节点 + Phase B 成本拆解面板全量展示**:**告警通道分级**:(a) **日 ¥20 hard breach** → 必发飞书 critical(继承 P0-10 §1.4 simulation_auto 暂停所有 LLM 调用必通知用户)+ audit_event `daily_cost_ceiling_20cny_breached`(P1-6 已锁;复用)(b) **日 ¥14 soft breach (70%)** → 仅写 audit + log warning;不发飞书避免高噪声(P1-5 §2 红线"低噪声只发可执行指令/风险变化/对账/澄清"严守)(c) **Kimi ¥4 cap breach** → audit_event `kimi_daily_cap_4cny_breached` + Alerter.fire(severity=warning, dedup_15min)— Kimi cap 是软停 escalation 不是全 LLM 停摆但仍需告警因影响信号质量(d) **月节点 50%/80%/100%** → §1.6 锁定动作。**告警通道仅飞书**:`Alerter.webhook_url = FEISHU_CUSTOM_BOT_WEBHOOK_URL`(继承 P0-2 §2.5 备用 webhook 仅告警);**不引入 SMTP 邮件 / Slack / Discord 第二告警通道**(违反 P1-6 凭证池仅 LLM 3 + 飞书 6 锁状态;增加 SMTP 凭证扩展破 P1-6 §1.1 凭证清单封闭性)。**Phase B 成本拆解面板**(P1-5 §2 红线 1 锁 4 Phase B 页之一)= `backend/api/cost.py` GET `/api/cost/breakdown`(返回 daily_spent + monthly_spent + kimi_spent + by_provider + by_agent + by_path + 月节点状态)+ Vue Phase B 页 `frontend/src/views/CostBreakdown.vue` 实时查询(WS `cost_update` 12 类消息中扩展或 polling 5min);**严禁 POST/PUT/PATCH/DELETE 在 backend/api/cost.py**(继承 P1-5 §2 红线 1+2)。

8. **AuditEventType enum 22 → 26 类(P1-6 amendment)**:本决策派生 `P1-6-amendment-2026-05-10-audit-eventtype-26.md`,在 P1-6 §1.8 锁定的 22 类 `AuditEventType` enum 基础上**新增 4 类**:`MONTHLY_BUDGET_50PCT_REACHED` / `MONTHLY_BUDGET_80PCT_REACHED` / `MONTHLY_BUDGET_100PCT_REACHED` / `KIMI_DAILY_CAP_4CNY_BREACHED`;均归类 4(异常 + 拦截事件)且复用 P1-6 §1.7 audit schema 不变(`actor=AuditActor.SYSTEM`, `resource_type='cost_budget'`, `payload={spent, budget, pct, by_provider}`, `outcome=AuditOutcome.SUCCESS`(节点)/`BLOCKED`(Kimi cap),`reason_namespace='cost_budget_threshold'`)。Mongo TTL 180 天不变,JSONL 30 天双写不变(继承 P1-6 §2 红线 13+14)。

9. **第一阶段排除项**:数据源付费扩展(wind/tushare pro P2-3)/ MongoDB Atlas 月预算 SDK 集成(P2-3)/ 服务器 cloud billing API(P2-3)/ SMTP 邮件告警(P3 团队化)/ Slack/Discord 告警(P3)/ 周预算 / 季度预算(过度细分)/ 节假日按工作日浮动月预算(增加复杂度)/ Kimi 月度 cap(daily cap 已足够)/ deepseek + qwen 单独 cap(失 failover 弹性)/ 月节点 25%/75%(过度密集)/ 月预算 100% 后动态降 daily ceiling(违反 P0-10 静态锁)/ 软触发降级 4 必经 Agent 任一(继承 P0-10 §2 红线 2)/ 软触发降级 fast 频次(响应延迟不可接受)/ 软触发全模型降 deepseek-only(中文金融领域信号质量损失)/ 前端独立"成本预算管理"写入页(冲突 P1-5 §2 红线 1+2)。

## 1. 决策具体内容

### 1.1 Q1 — 预算结构:仅锁 LLM 预算,数据/运维不设 ceiling

#### 1.1.1 三类成本现状对照表

| 类目 | 当前实现 | 月预计成本 | P1-7 是否设 ceiling |
|------|---------|-----------|---------------------|
| **LLM** | `backend/llm/cost_tracker.py::MODEL_PRICING` 三 provider 单价表完整;`track_usage()` 写 Redis `llm:usage:{date}:{agent}:{provider}`;`cost_guard.py::BudgetState` daily 70%/100% 双阈值 | ¥0~¥440(变动) | ✅ 锁(daily ¥20 hard + monthly ¥440 soft + Kimi daily ¥4) |
| **数据源** | akshare(免费)+ adata(免费)+ baostock(免费);`backend/data/scheduler.py` Market 30s + News 300s + Index 15:30 cron(P1-2.B 锁) | ¥0(全免费)| ❌ 不锁(无可变成本)|
| **运维 MongoDB** | docker-compose 127.0.0.1(P1-6 锁) | ¥0(本机)| ❌ 不锁(P1-7 仅预留字段)|
| **运维 Redis** | docker-compose 127.0.0.1(P1-6 锁) | ¥0(本机)| ❌ 不锁(P1-7 仅预留字段)|
| **服务器** | 自托管 dell server | ¥0(已购置)| ❌ 不锁(P1-7 范围外)|

#### 1.1.2 LLM 三 provider 单价对照(继承 `backend/llm/cost_tracker.py::MODEL_PRICING`)

| Provider | Model | Input(¥/k token)| Output(¥/k token)| 倍数(vs deepseek)|
|----------|-------|------------------|---------------------|---------------------|
| DeepSeek | deepseek-v4-pro | 0.0002 | 0.0002 | 1× |
| DashScope | qwen-3.6-plus | 0.001 | 0.001 | 5× |
| Moonshot | kimi-k2.6 | 0.0021 | 0.0084 | 10×~42× |

**单笔成本估算**(继承 `backend/llm/cost_tracker.py::calculate_cost`)= `(prompt_tokens × input_rate + completion_tokens × output_rate) / 1000`。

#### 1.1.3 LLM 内拆 — Kimi 单独 cap 现状 vs 升级

```python
# backend/services/cost_guard.py(升级后)
"""WHY: BudgetState 升级 daily + monthly + kimi 三维;严禁 plaintext provider key 写 audit。"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

# 配置常量(runtime 不可改 + 严禁 hot-reload;继承 P0-7 §2 红线 14 + P0-10 §1.4)
_DEFAULT_DAILY_BUDGET_RMB: float = 20.0
_DEFAULT_SOFT_CEIL_PCT: float = 0.7
_DEFAULT_MONTHLY_BUDGET_RMB: float = 440.0  # P1-7 新增:22 工作日 × 20
_DEFAULT_KIMI_DAILY_CAP_RMB: float = 4.0    # P1-7 新增:防 escalation 单笔吞全天 cap


@dataclass(frozen=True)  # frozen 永锁;继承 P0-3 §2 红线 12
class BudgetState:
    """LLM 成本守门状态 — daily + monthly + kimi 三维。"""
    
    # 日维度(继承 P0-10 §1.4 不变)
    spent_today: Decimal
    daily_budget: Decimal
    daily_soft_ceiling: Decimal
    daily_hard_ceiling: Decimal
    daily_remaining: Decimal
    daily_status: str  # 'ok' / 'soft_breach' / 'hard_breach'
    
    # 月维度(P1-7 新增)
    spent_this_month: Decimal
    monthly_budget: Decimal
    monthly_pct: float  # 0.0~1.5+(允许超 100% 仅警告)
    monthly_50pct_reached: bool
    monthly_80pct_reached: bool
    monthly_100pct_reached: bool
    
    # Kimi 单独维度(P1-7 新增)
    kimi_spent_today: Decimal
    kimi_daily_cap: Decimal
    kimi_status: str  # 'ok' / 'cap_breached'
    
    # Soft degrade flag(P1-7 新增)
    kimi_escalation_blocked: bool  # True = 软触发后 SoftDegradeManager 关闭 escalation
```

### 1.2 Q2 — 月度预算 ¥440 = 22 工作日 × ¥20 静态固定

#### 1.2.1 静态 ¥440 按自然月初始化逻辑

```python
# backend/services/cost_guard.py(月预算初始化)

async def get_monthly_spent(redis_client, year: int, month: int) -> Decimal:
    """读月度累积花费;Redis key llm:budget:monthly:{YYYYMM}:spent。"""
    key = f"llm:budget:monthly:{year:04d}{month:02d}:spent"
    raw = await redis_client.get(key)
    return Decimal(raw or "0")


async def add_monthly_spent(redis_client, amount_rmb: Decimal, year: int, month: int) -> Decimal:
    """累积月度花费;Redis INCRBYFLOAT 原子操作。
    每月初(自然月切换)旧 key 自然过期(TTL 35 天 = 留 5 天回查 buffer)。"""
    key = f"llm:budget:monthly:{year:04d}{month:02d}:spent"
    new_total_str = await redis_client.incrbyfloat(key, float(amount_rmb))
    if not await redis_client.ttl(key) > 0:
        await redis_client.expire(key, 35 * 24 * 3600)  # 35 天 TTL
    return Decimal(new_total_str)
```

#### 1.2.2 排除选项

- **动态查 holidays.yaml × ¥20**:holidays.yaml 微调时 ceiling 随之变动违反 frozen 原则;跨月边界夜间调度复杂
- **月初首交易日 09:00 锁定 × ¥20**:需额外 BrokerScheduler cron 增加复杂度;漏执行需 fallback 到 ¥440 与静态等价
- **自然月日均 ¥20 滚动 30 天 = ¥600**:与 22 工作日 × ¥20 = ¥440 不一致;与 P0-10 ¥20 daily 工作日意涵不符

### 1.3 Q3 — 月预算与日预算关系:日 hard 严格 + 月 soft 警告

#### 1.3.1 角色矩阵

| 维度 | 阈值 | 角色 | 触发动作 | LLM 是否暂停 |
|------|------|------|----------|--------------|
| 日 | ¥14 (70%) | 软警告 | log warning + audit | ❌ 不暂停 |
| 日 | ¥20 (100%) | **硬熔断**(继承 P0-10 §1.4)| raise DailyBudgetExceededError + 飞书 critical + audit `daily_cost_ceiling_20cny_breached` | ✅ **暂停** |
| 月 | ¥220 (50%) | 仪表盘节点 | audit `monthly_budget_50pct_reached` only | ❌ 不暂停 |
| 月 | ¥352 (80%) | 软警告 | audit + 飞书 warning | ❌ 不暂停 |
| 月 | ¥440 (100%) | 软警告(非硬熔断) | audit + 飞书 critical + cost_breakdown 附件 | ❌ 不暂停 |
| Kimi 日 | ¥4 (20% of daily) | **硬阻 Kimi only** | raise KimiDailyCapExceededError(仅暂停 Kimi 调用,不暂停 deepseek+qwen)+ audit + 飞书 warning | 仅 Kimi 暂停 |

**关键:日 hard ¥20 是唯一全 LLM 熔断触发器**(继承 P0-10 §1.4 不变);月 soft 是仪表盘观察;Kimi cap 是子熔断仅暂停 Kimi。

### 1.4 Q4 — 软触发(日 ¥14 = 70%)优先降级:关闭 Kimi escalation 路径

#### 1.4.1 SoftDegradeManager 抽象

```python
# backend/services/soft_degrade_manager.py(P1-7 新增)
"""WHY: 软触发降级矩阵集中管理;严禁 LLM 写引用(继承 P0-10 §2 红线 1)。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DegradeFlags:
    """软触发降级标志位 — 当前仅 Kimi escalation;未来扩展走 P1-7-amendment。"""
    kimi_escalation_blocked: bool = False


class SoftDegradeManager:
    """软触发降级中心 — 仅 cost_guard 在 soft breach 时调用;严禁其他模块自主调用。"""
    
    def __init__(self):
        self._flags = DegradeFlags()
    
    def activate_kimi_escalation_block(self) -> DegradeFlags:
        """软触发触发:关闭 Kimi escalation;fund_manager 仅走 triage qwen。"""
        from dataclasses import replace
        new_flags = replace(self._flags, kimi_escalation_blocked=True)
        self._flags = new_flags
        return new_flags
    
    def reset_at_day_rollover(self) -> DegradeFlags:
        """每日 00:00 重置(BrokerScheduler 1st cron);新一日重新计算 soft breach 节点。"""
        new_flags = DegradeFlags()
        self._flags = new_flags
        return new_flags
    
    def current_flags(self) -> DegradeFlags:
        return self._flags
    
    def should_block_kimi_escalation(self) -> bool:
        return self._flags.kimi_escalation_blocked
```

#### 1.4.2 fund_manager tiered routing 集成

```python
# backend/llm/router.py(fund_manager tiered routing 升级 — 软触发感知)

async def fund_manager_route(
    confidence: float,
    soft_degrade_flags: "DegradeFlags",  # 由调用方注入(继承 P0-10 §1.4 LLM 隔离)
) -> str:
    """fund_manager 路由:triage qwen(默认)+ escalation kimi(条件)。
    
    P1-7 新增:soft_degrade_flags.kimi_escalation_blocked=True 时永不 escalate 仅走 triage。
    """
    if soft_degrade_flags.kimi_escalation_blocked:
        return "qwen"  # 强制 triage 不 escalate
    
    if confidence < 0.6:
        return "kimi"  # escalation
    return "qwen"  # triage
```

#### 1.4.3 排除选项

- **软触发关闭事件路径 cap=1**:event_path 是 P0-9 加分非核心 cap=1 已是最低再降相对意义不大
- **软触发降低 fast 频次 09/11/13/15 → 09/13**:11:00/15:00 突发响应延迟到 13:00/次日 09:00 不可接受
- **软触发全模型降 deepseek-only**:中文金融领域 qwen 优势丧失影响信号质量 + 需走 P0-10-amendment
- **软触发立即停摆**:角色与 hard 重叠;软触发是预警节点不是熔断节点
- **软触发降级 4 必经 Agent 任一**:违反 P0-10 §2 红线 2 缺一即降级 HOLD

### 1.5 Q5 — Kimi daily cap = ¥4 单独锁

#### 1.5.1 KimiDailyCapExceededError 与 assert 函数

```python
# backend/services/cost_guard.py(Kimi cap 守门)

class KimiDailyCapExceededError(Exception):
    """Kimi 日 cap 触发;仅暂停 Kimi 调用,不暂停 deepseek + qwen。"""
    pass


async def get_kimi_spent_today(redis_client, today: date) -> Decimal:
    """读 Kimi 当日花费;Redis key llm:usage:{date}:kimi(累积 INCRBYFLOAT)。"""
    key = f"llm:usage:{today.isoformat()}:kimi"
    raw = await redis_client.get(key)
    return Decimal(raw or "0")


async def assert_kimi_cap_allows(redis_client, today: date) -> None:
    """Kimi 调用前必须先调用;cap 触发即 raise(继承 P0-10 fail-closed 原则)。"""
    spent = await get_kimi_spent_today(redis_client, today)
    if spent >= Decimal(str(_DEFAULT_KIMI_DAILY_CAP_RMB)):
        raise KimiDailyCapExceededError(
            f"Kimi daily cap reached: spent={spent:.4f} RMB / cap={_DEFAULT_KIMI_DAILY_CAP_RMB} RMB"
        )
```

#### 1.5.2 Kimi cap 数学

- Kimi `¥0.0084/k output` × 8k thinking_tokens + 2k output ≈ ¥0.084/笔 escalation
- ¥4 cap = 最多 ~47 笔 escalation/日(覆盖正常 < 5 次场景 + buffer)
- **二道防线**:soft breach ¥14 前 Kimi cap ¥4 自然约束;soft breach ¥14 后 SoftDegradeManager 完全关闭 escalation

#### 1.5.3 deepseek + qwen 不单独 cap 理由

- provider failover 时若 deepseek/qwen 任一独立 cap 灭则可能开不出指令(deepseek 灭后 qwen + kimi 总额 ¥10 不够 4 必经 Agent 全跑)
- 失去 failover 弹性
- 允许两者在 ¥20 - ¥(kimi spent) 内动态分配(deepseek 是 kimi 1/42 价廉做大头)

### 1.6 Q6 — 月预算 50%/80%/100% 节点动作

#### 1.6.1 check_monthly_thresholds 实现

```python
# backend/services/cost_guard.py(月节点检查)

from backend.audit.models import AuditEventType, AuditActor, AuditOutcome
from backend.monitoring.alerter import Alerter, AlertSeverity


async def check_monthly_thresholds(
    redis_client,
    audit_store,           # AuditStore;严禁 LLM 写引用(P1-6 §2 红线 16)
    alerter: Alerter,
    state: BudgetState,
) -> None:
    """每次 track_usage 后异步调用;50%/80%/100% 节点幂等触发(Redis SET NX 防重)。
    
    严禁触发 daily ceiling 动态调整(违反 P0-10 静态锁)。
    """
    pct = state.monthly_pct
    today = date.today()
    yyyymm = f"{today.year:04d}{today.month:02d}"
    
    # 50%(audit only)
    if pct >= 0.50 and not state.monthly_50pct_reached:
        guard_key = f"llm:budget:monthly:{yyyymm}:50pct_fired"
        if await redis_client.set(guard_key, "1", nx=True, ex=35 * 24 * 3600):
            await audit_store.write(
                event_type=AuditEventType.MONTHLY_BUDGET_50PCT_REACHED,
                actor=AuditActor.SYSTEM,
                resource_type="cost_budget",
                payload={"spent": float(state.spent_this_month), "budget": float(state.monthly_budget), "pct": pct},
                outcome=AuditOutcome.SUCCESS,
                reason_namespace="cost_budget_threshold",
            )
            # 不发飞书(P1-5 低噪声原则)
    
    # 80%(audit + 飞书 warning)
    if pct >= 0.80 and not state.monthly_80pct_reached:
        guard_key = f"llm:budget:monthly:{yyyymm}:80pct_fired"
        if await redis_client.set(guard_key, "1", nx=True, ex=35 * 24 * 3600):
            await audit_store.write(
                event_type=AuditEventType.MONTHLY_BUDGET_80PCT_REACHED,
                actor=AuditActor.SYSTEM,
                resource_type="cost_budget",
                payload={"spent": float(state.spent_this_month), "budget": float(state.monthly_budget), "pct": pct},
                outcome=AuditOutcome.SUCCESS,
                reason_namespace="cost_budget_threshold",
            )
            await alerter.fire(
                alert_type="cost_budget_exceeded",
                severity=AlertSeverity.WARNING,
                message=f"LLM 月度预算到 80%: ¥{state.spent_this_month:.2f} / ¥{state.monthly_budget:.2f}",
            )
    
    # 100%(audit + 飞书 critical + cost_breakdown 附件)
    if pct >= 1.00 and not state.monthly_100pct_reached:
        guard_key = f"llm:budget:monthly:{yyyymm}:100pct_fired"
        if await redis_client.set(guard_key, "1", nx=True, ex=35 * 24 * 3600):
            breakdown = await fetch_cost_breakdown(redis_client, today.year, today.month)
            await audit_store.write(
                event_type=AuditEventType.MONTHLY_BUDGET_100PCT_REACHED,
                actor=AuditActor.SYSTEM,
                resource_type="cost_budget",
                payload={
                    "spent": float(state.spent_this_month),
                    "budget": float(state.monthly_budget),
                    "pct": pct,
                    "breakdown": breakdown,
                },
                outcome=AuditOutcome.SUCCESS,
                reason_namespace="cost_budget_threshold",
            )
            await alerter.fire(
                alert_type="cost_budget_exceeded",
                severity=AlertSeverity.CRITICAL,
                message=(
                    f"LLM 月度预算 100% 已超: ¥{state.spent_this_month:.2f} / ¥{state.monthly_budget:.2f}\n"
                    f"当月剩 N 天 — 仅 audit 不暂停 LLM(日 hard ¥20 仍唯一熔断器)\n"
                    f"By provider: {breakdown.get('by_provider')}"
                ),
            )
```

### 1.7 Q7 — 告警通道:飞书仅 hard breach + 月节点 + Phase B 成本拆解面板

#### 1.7.1 告警通道分级表

| 触发条件 | 告警通道 | 严重度 | 备注 |
|---------|---------|--------|------|
| 日 ¥14 soft breach | audit + log only | — | 不发飞书避免噪声 |
| 日 ¥20 hard breach | audit + 飞书 | CRITICAL | 必发(继承 P0-10 simulation_auto 暂停必通知)|
| Kimi ¥4 cap breach | audit + 飞书 | WARNING | 仅暂停 Kimi 不暂停全 LLM |
| 月 50% (¥220) | audit only | — | 仪表盘节点 |
| 月 80% (¥352) | audit + 飞书 | WARNING | 提前预警 |
| 月 100% (¥440) | audit + 飞书 | CRITICAL | 附 cost_breakdown |

#### 1.7.2 Phase B 成本拆解面板(P1-5 §2 红线 1 锁 4 Phase B 页之一)

```python
# backend/api/cost.py(P1-7 新增;严禁 POST/PUT/PATCH/DELETE — 继承 P1-5 §2 红线 1+2)
"""WHY: Phase B 成本拆解面板 — GET only;前端实时查询。"""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends

from backend.services.cost_guard import get_budget_state, BudgetState

router = APIRouter(prefix="/api/cost", tags=["cost"])


@router.get("/breakdown")
async def get_cost_breakdown(redis_client = Depends(get_redis)) -> dict:
    """成本拆解 — daily + monthly + kimi + by_provider + by_agent + by_path。"""
    state: BudgetState = await get_budget_state(redis_client)
    today = date.today()
    
    by_provider = {
        "deepseek": float(await get_provider_spent_today(redis_client, today, "deepseek")),
        "qwen": float(await get_provider_spent_today(redis_client, today, "qwen")),
        "kimi": float(state.kimi_spent_today),
    }
    
    by_agent = await get_agent_spent_today(redis_client, today)
    by_path = {
        "slow_morning": float(await get_path_spent_today(redis_client, today, "slow_morning")),
        "fast_intraday": float(await get_path_spent_today(redis_client, today, "fast_intraday")),
        "event_path": float(await get_path_spent_today(redis_client, today, "event_path")),
    }
    
    return {
        "daily": {
            "spent": float(state.spent_today),
            "budget": float(state.daily_budget),
            "soft_ceiling": float(state.daily_soft_ceiling),
            "hard_ceiling": float(state.daily_hard_ceiling),
            "remaining": float(state.daily_remaining),
            "status": state.daily_status,
        },
        "monthly": {
            "spent": float(state.spent_this_month),
            "budget": float(state.monthly_budget),
            "pct": state.monthly_pct,
            "thresholds": {
                "50pct": state.monthly_50pct_reached,
                "80pct": state.monthly_80pct_reached,
                "100pct": state.monthly_100pct_reached,
            },
        },
        "kimi": {
            "spent": float(state.kimi_spent_today),
            "cap": float(state.kimi_daily_cap),
            "status": state.kimi_status,
        },
        "soft_degrade": {
            "kimi_escalation_blocked": state.kimi_escalation_blocked,
        },
        "by_provider": by_provider,
        "by_agent": by_agent,
        "by_path": by_path,
    }
```

```vue
<!-- frontend/src/views/CostBreakdown.vue(P1-7 新增 Phase B 页 — P1-5 §2 红线 1 锁 4 Phase B 之一)-->
<template>
  <div class="cost-breakdown">
    <h1>成本拆解</h1>
    <!-- 日预算卡片 -->
    <BudgetCard title="日 LLM 预算" :spent="data.daily.spent" :budget="data.daily.budget" :status="data.daily.status" />
    <!-- 月预算卡片 + 50%/80%/100% 节点点亮 -->
    <BudgetCard title="月 LLM 预算" :spent="data.monthly.spent" :budget="data.monthly.budget" :thresholds="data.monthly.thresholds" />
    <!-- Kimi cap 卡片 -->
    <BudgetCard title="Kimi 日 Cap" :spent="data.kimi.spent" :budget="data.kimi.cap" :status="data.kimi.status" />
    <!-- 软触发降级状态 -->
    <DegradePanel :flags="data.soft_degrade" />
    <!-- 拆解柱状图 by_provider / by_agent / by_path -->
    <BreakdownCharts :by_provider="data.by_provider" :by_agent="data.by_agent" :by_path="data.by_path" />
  </div>
</template>
```

#### 1.7.3 排除选项

- **SMTP 邮件告警**:违反 P1-6 §1.1 凭证池仅 LLM 3 + 飞书 6 锁状态(增加 SMTP 凭证扩展)
- **Slack/Discord 告警**:同上凭证扩展
- **前端独立"成本预算管理"写入页**:冲突 P1-5 §2 红线 1+2(backend/api/* 仅 GET 除两唯二例外)
- **soft breach 必发飞书**:违反 P1-5 低噪声原则(只发可执行指令/风险变化/对账/澄清)
- **月 100% 后动态降 daily ceiling**:违反 P0-10 ¥20 daily 静态锁定 + 引入双轨变量复杂度

### 1.8 Q8 — AuditEventType 22 → 26 类(P1-6 amendment)

#### 1.8.1 P1-6 amendment 文件

新建 `docs/decisions/P1-6-amendment-2026-05-10-audit-eventtype-26.md` 内容:

```markdown
# P1-6 Amendment 2026-05-10 — AuditEventType 22 → 26 类(新增 4 类成本预算事件)

## 触发原因

P1-7 锁定月预算 50%/80%/100% 节点 + Kimi daily cap;需新增 4 类 AuditEventType 携带成本预算节点事件入审计。

## 变更内容

`backend/audit/models.py::AuditEventType` enum 新增 4 类(均归类 4 异常 + 拦截事件):

```python
# 类 4 — 异常 + 拦截事件(原 8 类 + 新增 4 类 = 12 类)
# 原:STATE_MACHINE_ILLEGAL_TRANSITION / RISK_ENGINE_CHECK_REJECTED / BUILDER_EARLY_RETURN /
#     MOCKBROKER_PRICE_LIMIT_VIOLATION_AT_FILL / DATA_QUALITY_BREACH /
#     RECONCILIATION_TICKET_OPEN_OR_EXPIRED / LLM_CALL_TIMEOUT_30S /
#     DAILY_COST_CEILING_20CNY_BREACHED
# 新增(P1-7):
MONTHLY_BUDGET_50PCT_REACHED = "monthly_budget_50pct_reached"
MONTHLY_BUDGET_80PCT_REACHED = "monthly_budget_80pct_reached"
MONTHLY_BUDGET_100PCT_REACHED = "monthly_budget_100pct_reached"
KIMI_DAILY_CAP_4CNY_BREACHED = "kimi_daily_cap_4cny_breached"
```

## P1-6 §2 红线 12 同步更新

`AuditEventType` enum 锁定 22 类 → 26 类;任何新增/删除/重命名仍须走 P1-6-amendment。

## 与 P1-6 §1.7 audit schema 兼容性

- `actor=AuditActor.SYSTEM`(成本守门由 cost_guard 调用 SCHEDULER 触发)
- `resource_type='cost_budget'`(扩展 P1-6 §1.7 resource_type 列表)
- `payload={spent, budget, pct, by_provider}`(节点)/ `{spent, cap, calls_count}`(Kimi cap)
- `outcome=AuditOutcome.SUCCESS`(节点) / `BLOCKED`(Kimi cap)
- `reason_namespace='cost_budget_threshold'`
- TTL 180 天不变;JSONL 30 天双写不变

## 实施期任务

P1-7 实施期 G-001 同步落地(`backend/audit/models.py` enum 升级)。
```

#### 1.8.2 派生 P0-10 amendment

新建 `docs/decisions/P0-10-amendment-2026-05-10-cost-budget-extension.md` 内容:

```markdown
# P0-10 Amendment 2026-05-10 — ¥20 hard ceiling 扩展为 LLM 总 daily ¥20 + Kimi 单独 daily ¥4 + 月 soft ¥440

## 触发原因

P1-7 决策需要在 P0-10 §1.4 单一 ¥20 daily hard ceiling 基础上扩展月度维度 + Kimi 单独 cap;¥20 daily hard 锁定不变,仅扩。

## 变更内容

P0-10 §1.4 第 X 段(¥20 hard ceiling 节)扩展为:

> LLM 单调用 30s 硬超时 + 0 重试;失败即 Agent 返 None 不重试。LLM 全停 ≥1h 触发系统级中断(P0-6)。**LLM 总成本守门**:
> - **日 hard ceiling = ¥20**(原锁定不变;唯一全 LLM 暂停触发器;simulation_auto 进入 paused-budget-breach 状态)
> - **日 soft ceiling = ¥14 = 70% × ¥20**(原锁定不变;关闭 Kimi escalation 路径;不暂停 LLM)
> - **月 soft ceiling = ¥440 = 22 工作日 × ¥20**(P1-7 新增;50%/80%/100% 三阶节点 audit + 飞书;不暂停 LLM)
> - **Kimi 子 cap = ¥4/日 = 20% × daily ceiling**(P1-7 新增;Kimi 单独熔断仅暂停 Kimi 调用不暂停 deepseek + qwen)
>
> 4 阈值修改 = `git diff config/*.yaml` + `docs/decisions/P0-10-amendment-{date}-{原因}.md` + 进程重启三步;严禁 hot-reload(继承 P0-7 §2 红线 14)。

## 与 P0-10 §2 红线 1(LLM 字段权限矩阵)兼容性

- cost_guard.py 内部不持 LLM 写引用(继承)
- SoftDegradeManager 不持 LLM 写引用(继承)
- AuditStore 严禁被 backend/llm/ 任何模块导入(继承 P1-6 §2 红线 16)

## 实施期任务

P1-7 实施期 G-002 同步落地(`backend/services/cost_guard.py` BudgetState 三维升级 + KimiDailyCapExceededError + assert_kimi_cap_allows + check_monthly_thresholds)。
```

### 1.9 P1-7 第一阶段排除项汇总

- 数据源付费扩展(wind/tushare pro)— P2-3
- MongoDB Atlas 月预算 SDK 集成 — P2-3
- 服务器 cloud billing API — P2-3
- SMTP 邮件告警 — P3 团队化
- Slack/Discord 告警 — P3
- 周预算 / 季度预算 — 过度细分
- 节假日按工作日浮动月预算 — 增加复杂度
- Kimi 月度 cap — daily cap 已足够
- deepseek + qwen 单独 cap — 失 failover 弹性
- 月节点 25%/75% — 过度密集
- 月预算 100% 后动态降 daily ceiling — 违反 P0-10 静态锁
- 软触发降级 4 必经 Agent 任一 — 继承 P0-10 §2 红线 2
- 软触发降级 fast 频次 — 响应延迟不可接受
- 软触发全模型降 deepseek-only — 中文金融领域信号质量损失
- 前端独立"成本预算管理"写入页 — 冲突 P1-5 §2 红线 1+2
- 飞书 soft breach 告警 — 违反 P1-5 低噪声原则
- 跨自然月动态调度月初锁定逻辑 — 静态 ¥440 已足够
- 凭证池扩展(SMTP/Slack/Discord 等)— 违反 P1-6 §1.1 凭证清单封闭性

## 2. 红线(P1-7)

> 以下条款一律以 P1-7 决策为准。**违反即视为红线违规**;实施期 grep / lint rule 应自动检测违规。

1. **LLM 总日预算 ¥20 hard + 月预算 ¥440 soft 永锁**;数据源 / 运维成本不设 ceiling(akshare/adata/baostock 全免费 + MongoDB/Redis 自托管 127.0.0.1)。修改阈值 = `git diff config/*` + `docs/decisions/P0-10-amendment-{date}.md` + 进程重启三步;严禁 hot-reload(继承 P0-7 §2 红线 14 + P0-10 §1.4)。

2. **月预算静态固定 ¥440 按自然月永锁**:严禁动态查 holidays.yaml × ¥20 / 月初锁定 × ¥20 / 自然月日均 ¥20 滚动 30 天 任一变体;Redis key `llm:budget:monthly:{YYYYMM}:spent` TTL 35 天(留 5 天回查 buffer);所有自然月统一 ¥440 ceiling。

3. **月节点 50%/80%/100% 动作锁定**:50% audit only(`monthly_budget_50pct_reached`)+ 80% audit + 飞书 warning(`monthly_budget_80pct_reached`)+ 100% audit + 飞书 critical + cost_breakdown 附件(`monthly_budget_100pct_reached`);严禁额外节点(25%/75%/120%);严禁全节点发飞书(50% 不发);严禁仅 100% 节点(80% 提前预警必须保留)。

4. **Kimi daily cap = ¥4 单独锁(20% 总日预算)**:`backend/services/cost_guard.py::assert_kimi_cap_allows()` 在 Kimi 调用前必经;触发即 raise `KimiDailyCapExceededError` 仅暂停 Kimi 调用(不暂停 deepseek + qwen);Redis key `llm:usage:{date}:kimi` 累积 INCRBYFLOAT;**deepseek + qwen 严禁单独 cap**(失 failover 弹性)。

5. **软触发(日 ¥14 = 70%)优先降级:关闭 Kimi escalation 路径**:`backend/services/soft_degrade_manager.py::SoftDegradeManager.activate_kimi_escalation_block()` 由 cost_guard 在 soft breach 时调用;fund_manager tiered routing 强制走 triage qwen;**严禁同时关闭 4 必经 Agent 任一**(继承 P0-10 §2 红线 2);**严禁降级 fast 频次**(11/15 突发响应延迟不可接受);**严禁全模型降级 deepseek-only**(违反 P0-10 tiered routing 锁定);每日 00:00 BrokerScheduler reset 软触发标志位。

6. **日 hard ¥20 唯一全 LLM 停摆触发器永锁**(继承 P0-10 §1.4 不变);月 soft ¥440 100% 严禁触发停摆 + 严禁动态调 daily ceiling;Kimi cap ¥4 仅暂停 Kimi 不暂停 deepseek + qwen。

7. **数据源 / 运维成本 P1-7 不设 ceiling 永锁**:akshare/adata/baostock 全免费;MongoDB/Redis 127.0.0.1 自托管;**严禁引入 cloud billing API SDK 监控**(违反 P1-6 凭证池仅 LLM 3 + 飞书 6 锁状态)。30s watchlist_market_snapshots 节奏(继承 P1-2.B §1.4)不变。

8. **告警通道仅飞书 + audit + Phase B 成本拆解面板永锁**:`Alerter.webhook_url = FEISHU_CUSTOM_BOT_WEBHOOK_URL`(继承 P0-2 §2.5 备用 webhook 仅告警);**严禁 SMTP/Slack/Discord 第二告警通道**(违反 P1-6 §1.1 凭证池封闭性);Phase B 成本拆解面板 = `backend/api/cost.py` GET `/api/cost/breakdown` + `frontend/src/views/CostBreakdown.vue`(P1-5 §2 红线 1 锁 4 Phase B 页之一);**严禁 POST/PUT/PATCH/DELETE 在 backend/api/cost.py**(继承 P1-5 §2 红线 1+2)。

9. **`AuditEventType` enum 22 → 26 类**(P1-7 派生 P1-6 amendment):新增 `MONTHLY_BUDGET_50PCT_REACHED` / `MONTHLY_BUDGET_80PCT_REACHED` / `MONTHLY_BUDGET_100PCT_REACHED` / `KIMI_DAILY_CAP_4CNY_BREACHED`;均归类 4(异常 + 拦截事件)`reason_namespace='cost_budget_threshold'`;任何新增/删除/重命名仍须走 P1-6-amendment;严禁 magic string 写入 audit。

10. **`BudgetState` frozen Pydantic v2 strict + extra="forbid" 三层守门永锁**(继承 P0-3 §2 红线 12):升级后字段集 = (daily_*) 6 + (monthly_*) 5 + (kimi_*) 3 + (kimi_escalation_blocked) 1 = 15 字段;严禁就地 mutation;严禁 `extra="allow"`/`"ignore"`;hot-reload 禁用。

11. **cost_guard.py 配置 runtime 不可改 + hot-reload 禁用永锁**(继承 P0-7 §2 红线 14):`_DEFAULT_DAILY_BUDGET_RMB` / `_DEFAULT_SOFT_CEIL_PCT` / `_DEFAULT_MONTHLY_BUDGET_RMB` / `_DEFAULT_KIMI_DAILY_CAP_RMB` 4 常量启动期固定;严禁 setattr/monkey-patch;严禁通过 API 修改;`backend/api/cost.py` 仅 GET。

12. **`cost_guard.py` 严禁 import `backend.{llm,agents,mirofish,data}` 永锁**(继承 P0-10 §2 红线 1 LLM 字段权限矩阵):cost_guard 是基础设施层 LLM 是上层调用方;LLM 写引用反向依赖即破隔离;`SoftDegradeManager` 同款;lint rule grep 必空:`grep -rn "from backend.\(llm\|agents\|mirofish\|data\)" backend/services/cost_guard.py backend/services/soft_degrade_manager.py`。

13. **soft breach `SoftDegradeManager` 标志位每日 00:00 BrokerScheduler reset 永锁**:新一日重新计算 soft breach 节点;严禁手动 reset API(违反 P1-5 §2 红线 1+2);严禁跨日持续 block(若用户当日真高频可能误伤次日)。

14. **`SoftDegradeManager.activate_kimi_escalation_block()` 仅由 cost_guard 在 soft breach 时调用永锁**:严禁其他模块自主调用(防 LLM/agents 模块绕过预算守门主动触发降级);严禁 frontend 暴露写入端点;lint rule grep `grep -rn "activate_kimi_escalation_block" backend/llm/ backend/agents/` 必空。

15. **fund_manager tiered routing 集成 `soft_degrade_flags.kimi_escalation_blocked` 检查永锁**:`backend/llm/router.py::fund_manager_route` 必须接收 `soft_degrade_flags` 参数 + 优先检查 `kimi_escalation_blocked` 短路返回 `qwen`;严禁忽略软触发标志直接走 `confidence < 0.6` escalation 旧逻辑。

16. **`DailyBudgetExceededError` raise + 飞书 critical 必发永锁**(继承 P0-10 §1.4):`assert_budget_allows()` 失败即 raise + `Alerter.fire(severity=CRITICAL, alert_type='cost_budget_exceeded')` + audit_event `daily_cost_ceiling_20cny_breached`(P1-6 已锁;复用);严禁吞 raise;严禁 fail-open。

17. **`KimiDailyCapExceededError` raise + 飞书 warning 必发永锁**:Kimi cap 触发即 raise + `Alerter.fire(severity=WARNING)` + audit_event `kimi_daily_cap_4cny_breached`;严禁不发飞书(影响信号质量必通知);严禁 fail-open。

18. **`Alerter.fire` 月节点 80%/100% + Kimi cap 必走 dedup_15min cooldown 永锁**:防同一 LLM 调用窗口内连环触发飞书轰炸;cooldown key Redis SET NX EX 900;`alert_type='cost_budget_exceeded'` + severity 区分 dedup 路径。

19. **`backend/api/cost.py` 严禁 LLM 写 audit_events 永锁**(继承 P1-6 §2 红线 16):cost_guard 调用 audit_store.write 仅以 SYSTEM/SCHEDULER actor;严禁 frontend_user/feishu_user actor 写 cost_budget event(防伪造);Phase B Vue 页 `CostBreakdown.vue` 仅 GET 不写。

20. **cost_breakdown 响应严禁包含 plaintext API key fingerprint 永锁**(继承 P1-6 §2 红线 18):`/api/cost/breakdown` 响应仅含 spent / budget / pct / by_provider / by_agent / by_path 数值;严禁透出 provider key plaintext 或 fingerprint(by_provider key 用 'deepseek' / 'qwen' / 'kimi' 字符串字面量,非 API key 值)。

## 3. 实施期任务清单

> P1-7 决策完成不等于实施落地。本节列出从决策锁定到代码合并的具体动作清单。**任何遗漏会让本决策只是文字游戏**。Phase A 与 P1-5 + P1-6 实施期 Phase A 合并执行(P1-7 任务量较小,主要落在 Phase B 与 Phase B 收尾)。

### Phase A — 配置常量 + amendment 文档落地(与 P1-5 + P1-6 Phase A 合并)

- **G-001** 新建 `docs/decisions/P0-10-amendment-2026-05-10-cost-budget-extension.md` 完整文件(§1.8.2 完整内容)
- **G-002** 新建 `docs/decisions/P1-6-amendment-2026-05-10-audit-eventtype-26.md` 完整文件(§1.8.1 完整内容)
- **G-003** 同步更新 `docs/decisions/P0-10-llm-role-boundary-...md` §1.4 + §2 红线节(amendment 链接)
- **G-004** 同步更新 `docs/decisions/P1-6-secrets-shell-env-...md` §1.8 + §2 红线 12(amendment 链接 + AuditEventType 22 → 26)
- **G-005** 更新 `MEMORY.md` 索引 + 新建 `project_p1_7_cost_budget.md` memory
- **G-006** 更新 `CLAUDE.md` §2 加 §2.13 P1-7 红线节(简化版,详细规约在本决策 §2)+ §5 操作速查增补 P1-7 grep 4 条
- **G-007** 更新 `docs/quantmind_owner_decision_points_2026-05-07.md` §P1-7 标 ✅ + 链接本决策

### Phase B — cost_guard 升级 + Kimi cap + SoftDegradeManager + 月节点(与 P1-5 + P1-6 Phase B 合并)

- **G-008** 升级 `backend/audit/models.py::AuditEventType` 新增 4 类(§1.8.1 + §2 红线 9)
- **G-009** 升级 `backend/services/cost_guard.py::BudgetState` frozen 三维(daily + monthly + kimi)+ 4 常量定义(§1.1.3 + §2 红线 10+11)
- **G-010** 新增 `backend/services/cost_guard.py::KimiDailyCapExceededError` + `assert_kimi_cap_allows()` + `get_kimi_spent_today()`(§1.5.1 + §2 红线 4+17)
- **G-011** 新增 `backend/services/cost_guard.py::get_monthly_spent()` + `add_monthly_spent()` + `check_monthly_thresholds()`(§1.2.1 + §1.6.1 + §2 红线 2+3+18)
- **G-012** 升级 `backend/services/cost_guard.py::get_budget_state()` 聚合三维 + Soft degrade 标志位读取(§1.1.3)
- **G-013** 升级 `backend/services/cost_guard.py::assert_budget_allows()` 集成 soft breach 时触发 `SoftDegradeManager.activate_kimi_escalation_block()`(§1.4.1)
- **G-014** 新建 `backend/services/soft_degrade_manager.py::SoftDegradeManager` + `DegradeFlags` frozen dataclass(§1.4.1 + §2 红线 12+13+14)
- **G-015** 升级 `backend/llm/router.py::fund_manager_route` 接收 `soft_degrade_flags` 参数 + 优先检查 `kimi_escalation_blocked`(§1.4.2 + §2 红线 15)
- **G-016** 升级 `backend/llm/fallback.py::track_usage` 在每次调用后异步调 `add_monthly_spent` + `check_monthly_thresholds`(§1.6.1)
- **G-017** 升级 `backend/llm/router.py` 在 Kimi 调用前必先调 `assert_kimi_cap_allows`(§1.5.1)
- **G-018** 新建 `backend/api/cost.py` GET `/api/cost/breakdown`(§1.7.2 + §2 红线 8+19+20);路由挂载到 `backend/main.py`
- **G-019** 升级 `backend/broker/scheduler.py` 1st cron(每日 00:00)新增 `SoftDegradeManager.reset_at_day_rollover()` 调用(§2 红线 13)

### Phase B 收尾 — 前端 CostBreakdown.vue 页(与 P1-5 Phase B 收尾合并)

- **G-020** 新建 `frontend/src/views/CostBreakdown.vue`(§1.7.2 完整模板 + §2 红线 8+19);路由挂载到 `frontend/src/router/index.ts`(P1-5 §2 红线 1 锁 4 Phase B 页之一)
- **G-021** 新建 `frontend/src/components/cost/BudgetCard.vue`(daily/monthly/kimi 通用卡片组件)
- **G-022** 新建 `frontend/src/components/cost/DegradePanel.vue`(SoftDegradeFlags 状态展示)
- **G-023** 新建 `frontend/src/components/cost/BreakdownCharts.vue`(by_provider/by_agent/by_path 柱状图;复用 P1-5 现有 chart 库)
- **G-024** 新建 `frontend/src/composables/useCostBreakdown.ts`(5min polling + WS `cost_update` 监听 — WS 12 类已锁不扩;此处 polling)
- **G-025** P1-5 一级菜单"复盘与验收"分组下新增"成本拆解"二级菜单项(P1-5 §2 红线 1 锁 4 Phase B 页之一不超额)

### Lint rule + 静态检查(实施期持续生效)

- **G-026** Lint rule grep `grep -rn "from backend.\(llm\|agents\|mirofish\|data\)" backend/services/cost_guard.py backend/services/soft_degrade_manager.py` 必空(§2 红线 12)
- **G-027** Lint rule grep `grep -rn "activate_kimi_escalation_block" backend/llm/ backend/agents/` 必空(§2 红线 14)
- **G-028** Lint rule grep `grep -rnE "@router\.(post|put|patch|delete)" backend/api/cost.py` 必空(§2 红线 8)
- **G-029** Lint rule grep `grep -rnE "_DEFAULT_(DAILY_BUDGET_RMB|SOFT_CEIL_PCT|MONTHLY_BUDGET_RMB|KIMI_DAILY_CAP_RMB)\s*=\s*[0-9]" backend/services/cost_guard.py | wc -l` 必为 4(§2 红线 11)
- **G-030** CLAUDE.md §5 操作速查节增补本决策 4 条 grep 静态检查命令(P1-7 红线检查)

### 测试覆盖要求(继承全局 §2.10)

- **G-031** 单元测试:`BudgetState` schema 完整性(frozen + strict + extra='forbid' + 15 字段断言);月度 / Kimi 维度新字段断言
- **G-032** 单元测试:`assert_kimi_cap_allows()` 全场景:Kimi spent < 4 → 通过 / Kimi spent >= 4 → raise `KimiDailyCapExceededError`;deepseek + qwen 调用不受 Kimi cap 影响断言
- **G-033** 单元测试:`check_monthly_thresholds()` 全场景:< 50% → 0 触发 / >= 50% → 仅 audit / >= 80% → audit + 飞书 warning / >= 100% → audit + 飞书 critical + cost_breakdown;Redis SET NX 幂等断言(同一节点重复调用仅触发 1 次)
- **G-034** 单元测试:`SoftDegradeManager.activate_kimi_escalation_block()` 标志位变更 + `should_block_kimi_escalation()` 返值 + `reset_at_day_rollover()` 重置;严禁通过 LLM 模块导入(import 断言)
- **G-035** 单元测试:`fund_manager_route` 全场景:`kimi_escalation_blocked=True` 强制 qwen / `kimi_escalation_blocked=False` + `confidence<0.6` → kimi / `confidence>=0.6` → qwen
- **G-036** 集成测试:`get_cost_breakdown` GET `/api/cost/breakdown` 端到端响应 schema 断言;严禁 plaintext API key fingerprint;严禁 POST/PUT/PATCH/DELETE 路由存在
- **G-037** 集成测试:`AuditEventType` 4 新增枚举值写 audit_events 端到端;`actor=SYSTEM` + `resource_type='cost_budget'` + `reason_namespace='cost_budget_threshold'` 断言
- **G-038** E2E 测试:① 触发日 ¥14 soft breach → SoftDegradeManager 激活 + 后续 fund_manager 路由强制 qwen ② 触发 Kimi ¥4 cap → KimiDailyCapExceededError + 飞书 warning + audit;后续 deepseek + qwen 调用不受影响 ③ 触发月 50% → 仅 audit ④ 触发月 80% → audit + 飞书 warning ⑤ 触发月 100% → audit + 飞书 critical + cost_breakdown 附件 ⑥ 跨自然月 BrokerScheduler 1st cron reset 软触发标志位 ⑦ `GET /api/cost/breakdown` 前端 5min polling 实时更新

### Codex review hard gate(major 5 轮 R1-R5)

P1-7 涉及成本守门关键路径 + 软触发降级矩阵 + 多 enum 扩展 + 派生 2 个 amendment + 跨多模块集成,major 级别,实施期 5 轮 codex review:

- **R1 — Architecture review**(月预算 ¥440 静态固定 vs 动态 / Kimi 单独 cap vs 三 provider 全拆 / 软触发 SoftDegradeManager 集中管理 vs 分散触发 / 告警通道仅飞书 + audit 边界 vs 多通道 / Phase B 成本拆解面板挂载 P1-5 11 页框架决策合理性)
- **R2 — Cost & threshold review**(¥20 daily / ¥4 Kimi / ¥440 monthly 三阈值数学一致性;月节点 50%/80%/100% 渐进升级合理性;Kimi cap 47 笔 escalation/日 buffer 充足性;cost_breakdown 三维度拆解 by_provider/agent/path 完整性;Decimal 精度 vs float 在 Redis 存储 + 累积运算保证)
- **R3 — Implementation review**(G-001~G-038 任务清单与代码 diff 一致性;特别核 BudgetState frozen 15 字段 strict + extra='forbid';KimiDailyCapExceededError raise 不被吞;`SoftDegradeManager.activate_kimi_escalation_block` 仅由 cost_guard 调用 lint rule 必空;`add_monthly_spent` Redis INCRBYFLOAT 原子性)
- **R4 — Integration & boundary review**(`backend/llm/router.py` 注入 `soft_degrade_flags` 参数完备性 + 不破 P0-10 §2 红线 1 LLM 字段权限矩阵;`fallback.py::track_usage` 异步调 `check_monthly_thresholds` 不阻 LLM 主路径;`backend/api/cost.py` 仅 GET 严守 P1-5 §2 红线 1+2;`AuditEventType` 4 新增不破 P1-6 §2 红线 12 enum 锁定走 amendment;Alerter.fire dedup_15min 防轰炸)
- **R5 — Final review**(red lines 20 条全覆盖 + 决策依据完整性 + 与 P0-1~P0-10 + P1-2.A/B/C + P1-5 + P1-6 累积红线兼容性 + 派生 P0-10/P1-6 amendment 文档完备性 + Phase B 4 页名额不超额)

输出存 `docs/reviews/p1-7-r{N}-{topic}.md`;触发前 `git pull` 同步 `LanEinstein/CCodexSkill`(继承 §2.10)。

## 4. 决策依据

### 4.1 用户对齐(2026-05-10 P1-7 决策对齐 2 轮 8 议题)

第一轮 4 议题(全部对齐推荐):
- Q1 月预算总额 → ¥440/月 (22 工作日 × ¥20) ✅
- Q2 分类拆分 → 仅锁 LLM 预算,数据/运维不设 ✅
- Q3 月vs日优先级 → 日 hard 严格 + 月 soft 警告 ✅
- Q4 软触发降级 → 关闭 Kimi escalation 路径 ✅

第二轮 4 议题(全部对齐推荐):
- Q5 工作日口径 → 静态固定 ¥440/月 按自然月 ✅
- Q6 月节点动作 → 50% audit only / 80% 飞书 warning / 100% 飞书 critical ✅
- Q7 Provider 内拆 → 仅单独锁 kimi daily cap = ¥4 ✅
- Q8 告警通道 → 飞书仅 hard breach + 月节点 + Phase B 成本拆解面板全量展示 ✅

### 4.2 关键判断

- **仅锁 LLM 预算符合 QuantMind 实际可变成本结构**:数据源 akshare/adata/baostock 全免费 + MongoDB/Redis 自托管 = 无可变;LLM 是唯一按 token 计费可变;不引入数据/运维 ceiling 避免 cloud billing API SDK 凭证扩展破 P1-6 凭证池封闭性
- **¥440/月 = 22 工作日 × ¥20 静态固定符合 QuantMind 简洁原则**:Redis key 月初静态初始化即可不依赖 holidays.yaml;underspend 自动 buffer;不引入动态查 holidays / 月初锁定 cron 增加复杂度
- **日 hard 严格 + 月 soft 警告分层符合 fail-closed 原则**:日 hard 是熔断器(P0-10 锁定不变);月 soft 是仪表盘观察(月度波动属正常不应误熔断);角色清晰不重合
- **Kimi 单独 cap ¥4 符合"按风险等级分级守门"原则**:Kimi `¥0.0084/k output` 是 deepseek 42 倍单笔 escalation 失控可单卡 LLM 总预算;cap ¥4 = 47 笔 escalation buffer 覆盖正常 < 5 次场景;不破 deepseek + qwen failover 弹性
- **软触发关 Kimi escalation 符合"先关最贵后关大头"原则**:Kimi escalation 是 80% 成本来源关掉立省;4 必经 Agent 完整保留 + fast 频次完整保留 + 全 provider 完整保留 = 信号质量损失最小化
- **月节点渐进升级符合 P1-5 低噪声原则**:50% 仅 audit(进度数据点)+ 80% 飞书 warning(提前预警)+ 100% 飞书 critical(必通知);不引入 25%/75% 过度密集打扰用户
- **告警通道仅飞书符合 P1-6 凭证池封闭性**:Alerter 复用 FEISHU_CUSTOM_BOT_WEBHOOK_URL(P0-2 备用 webhook 仅告警);不引入 SMTP/Slack/Discord 第二通道破 P1-6 凭证清单
- **Phase B 成本拆解面板挂载 P1-5 4 Phase B 页符合 P1-5 §2 红线 1 11 页名额永锁**:不超额新增页;不引入前端写入端点(GET only);WS 12 类不扩(用 5min polling)

### 4.3 排除选项

- **三类全分(LLM/数据/运维) ceiling**:数据全免费时 ceiling=0 无意义;运维需引入云 billing SDK 过度工程
- **单一预算共享所有类目**:未来引入第三方付费数据源时无法快速分配;失去未来扩展弹性
- **仅 escalation 路径单独 cap**:与 Kimi cap 实质等价(当前 Kimi 仅用于 escalation),Kimi cap 形式更简单
- **动态查 holidays.yaml × ¥20 月预算**:holidays.yaml 微调时 ceiling 随之变动违反 frozen 原则;跨月边界夜间调度复杂
- **月初首交易日 09:00 锁定 × ¥20**:需额外 BrokerScheduler cron 增加复杂度;漏执行需 fallback 与静态等价
- **自然月日均 ¥20 滚动 30 天 = ¥600**:与 22 工作日 × ¥20 = ¥440 不一致;与 P0-10 ¥20 daily 工作日意涵不符
- **月 hard 严格 + 日 hard ¥20**:月度连环触发即停摆当月剩余天 LLM 调用 = 假阳性多;simulation_auto 实际并未失控只是月节奏波动
- **日 soft + 月 hard 严格**:冲突 P0-10 §1.4 ¥20 hard ceiling 锁定;破 P0-10 fail-closed 精神
- **deepseek + qwen 单独 cap**:provider failover 时某 cap 灭可能开不出指令;失 failover 弹性
- **三 provider 全拆 daily cap**:provider 间无法动态调度;某 cap 灭即开不出指令
- **月节点 25%/75% 额外节点**:过度密集打扰用户;违反 P1-5 低噪声原则
- **月 100% 后动态降 daily ceiling 到 ¥10**:违反 P0-10 ¥20 daily 静态锁定;引入双轨变量复杂度
- **软触发关闭事件路径 cap=1**:event_path cap=1 已最低再降相对意义不大
- **软触发降低 fast 频次 09/11/13/15 → 09/13**:11/15 突发响应延迟到 13/次日 09 不可接受
- **软触发全模型降 deepseek-only**:中文金融领域 qwen 优势丧失;需走 P0-10-amendment
- **软触发立即停摆**:角色与 hard 重叠
- **软触发降级 4 必经 Agent 任一**:违反 P0-10 §2 红线 2
- **SMTP 邮件告警**:违反 P1-6 §1.1 凭证池仅 LLM 3 + 飞书 6 锁状态
- **Slack/Discord 告警**:同上凭证池扩展
- **soft breach 必发飞书**:违反 P1-5 低噪声原则
- **前端独立"成本预算管理"写入页**:冲突 P1-5 §2 红线 1+2

### 4.4 与 P0/P1-2.A/B/C/P1-5/P1-6 红线协同

- 继承 P0-2 §2.5 备用 webhook 仅告警 → §1.7 告警通道 `Alerter.webhook_url = FEISHU_CUSTOM_BOT_WEBHOOK_URL`(月节点 + Kimi cap + daily hard)
- 继承 P0-3 §2 红线 12 frozen Pydantic strict + extra="forbid" → §2 红线 10 BudgetState frozen + strict + extra='forbid' 15 字段
- 继承 P0-7 §2 红线 14 RiskConfig runtime 不可改 + hot-reload 禁用 → §2 红线 11 cost_guard.py 4 常量 runtime 不可改 + hot-reload 禁用
- 继承 P0-9 §2 红线 7 5 单 cap 分配(traditional 4 + event 1) → §1.4 软触发不降 event_path(已最低)+ 不降 fast 频次(响应延迟不可接受)
- 继承 P0-10 §1.4 ¥20 hard ceiling + LLM 全停 1h 触发 P0-6 中断 + agent_models.yaml tiered routing kimi escalation + 4 必经 Agent → §1.3 日 hard 严格不变 + §1.4 软触发关 Kimi escalation 完整保留 4 必经 Agent + §1.5 Kimi 单独 cap;派生 P0-10-amendment(¥20 hard 扩 + Kimi ¥4 cap + 月 ¥440 soft)
- 继承 P0-10 §2 红线 1 LLM 字段权限矩阵 → §2 红线 12 cost_guard.py + soft_degrade_manager.py 严禁 import backend.llm/agents/mirofish/data + §2 红线 14 SoftDegradeManager 仅由 cost_guard 调用
- 继承 P1-2.B §1.4 BrokerScheduler 30s 节奏 watchlist_market_snapshots → §1.1 数据源 30s 节奏不变(不设 ceiling 因免费)
- 继承 P1-5 §2 红线 1 MVP 7 + Phase B 4 共 11 页永锁含"成本拆解" → §1.7.2 + §3 G-020~G-025 Phase B 成本拆解面板挂载;严禁超额新增前端页
- 继承 P1-5 §2 红线 1+2 backend/api/* 仅 GET 除两唯二例外 → §2 红线 8+19 backend/api/cost.py 严禁 POST/PUT/PATCH/DELETE
- 继承 P1-5 低噪声原则(只发可执行指令/风险变化/对账/澄清) → §1.6 月 50% 不发飞书 + §1.7 soft breach 不发飞书
- 继承 P1-6 §1.1 凭证池仅 LLM 3 + 飞书 6 → §2 红线 8 严禁 SMTP/Slack/Discord 第二告警通道破凭证池封闭性
- 继承 P1-6 §1.7 audit_events frozen schema + §1.8 AuditEventType 22 类 → §1.6 + §1.8 月节点 + Kimi cap 写 audit_events;派生 P1-6-amendment(22 → 26 类)
- 继承 P1-6 §2 红线 16 LLM 严禁写 audit → §2 红线 19 cost_guard 写 audit 仅 SYSTEM/SCHEDULER actor;严禁 frontend_user/feishu_user actor 写 cost_budget event
- 继承 P1-6 §2 红线 18 凭证 fingerprint 严禁 plaintext → §2 红线 20 cost_breakdown 响应严禁包含 plaintext API key fingerprint
- 继承 P1-2.A + P1-2.B + P1-2.C broker_events / broker_snapshots / equity_points append-only insert-only → audit_events 同款(P1-6 已锁;P1-7 不破)

## 5. 后续动作

### 5.1 SSoT 文档同步

- 更新 `docs/quantmind_owner_decision_points_2026-05-07.md` §P1-7:标 ✅ + 链接本决策文档
- 新建 memory 文件 `/home/ps/.claude/projects/-home-ps-papers-QuantMind/memory/project_p1_7_cost_budget.md`
- 更新 `MEMORY.md` 索引:加 P1-7 锁定 entry
- 更新 `CLAUDE.md` §2 加 §2.13 P1-7 红线节(简化版,详细规约在本决策 §2);§5 操作速查增补 P1-7 grep 4 条
- 新建 `docs/decisions/P0-10-amendment-2026-05-10-cost-budget-extension.md` + 同步更新 P0-10 主文档 §1.4 链接
- 新建 `docs/decisions/P1-6-amendment-2026-05-10-audit-eventtype-26.md` + 同步更新 P1-6 主文档 §1.8 + §2 红线 12 链接

### 5.2 派生 amendment

- **P0-10 amendment**:¥20 hard ceiling 扩展为 LLM 总 daily ¥20 + Kimi 单独 daily ¥4 + 月 soft ¥440(¥20 hard 不变,扩 monthly + Kimi 子维度)
- **P1-6 amendment**:`AuditEventType` enum 22 类 → 26 类(新增 4 类成本预算事件)

### 5.3 下一站

- **P1 决策对齐收官 ✅**(P1-2.A/B/C + P1-5 + P1-6 + P1-7 全锁;P1-1/P1-3/P1-4/P1-8 由 P0 + P1 累积锁定)
- **启动实施期 Phase A**(代码迁移)+ **Phase B**(数据 schema + cost_guard 升级)
- Phase A 合并 = P1-5 E-001~E-013 写入接口收口 + P1-6 F-001~F-007 secrets/IP/应急 + P1-7 G-001~G-007 amendment + memory + CLAUDE.md + P0-1 旧 AUTHORIZATION_MODE 矩阵删除
- Phase B 合并 = P1-5 E-014~E-028 MVP 7 页落地 + P1-6 F-008~F-020 audit infra + P1-7 G-008~G-019 cost_guard 升级 + P1-2.A/B/C 数据 schema 全量落地
- Phase B 收尾 = P1-5 E-029~E-032 Phase B 4 页 + P1-7 G-020~G-025 CostBreakdown.vue + 其他 3 个 Phase B 页(Agent 辩论 / 数据质量 / 飞书消息历史)

### 5.4 实施期启动条件

- **P1 全锁达成 ✅**(本决策完成即满足);可立即启动实施期 Phase A
- Phase A 与 P1-5 + P1-6 同窗口执行(G-001~G-007 与 E-001~E-013 + F-001~F-007 合并)
- Phase B 在 P1-2.A/B/C 数据 schema 全量落地 + Phase A 代码清理后启动(G-008~G-019 与 F-008~F-020 同窗口)
- Phase B 收尾在 Phase B 主体完成后启动(G-020~G-025 与 E-029~E-032 + F-021~F-027 同窗口)

### 5.5 本决策不做的事

- 不锁定数据源付费扩展(wind/tushare pro)— P2-3
- 不锁定 MongoDB Atlas 月预算 SDK 集成 — P2-3
- 不锁定服务器 cloud billing API — P2-3
- 不锁定 SMTP/Slack/Discord 第二告警通道 — P3 团队化
- 不锁定周/季度预算维度 — 过度细分
- 不锁定节假日按工作日浮动月预算 — 增加复杂度
- 不锁定 Kimi 月度 cap — daily cap 已足够
- 不锁定 deepseek + qwen 单独 cap — 失 failover 弹性
- 不锁定月节点 25%/75% — 过度密集
- 不锁定月 100% 后动态降 daily ceiling — 违反 P0-10 静态锁
- 不锁定软触发降级 4 必经 Agent / fast 频次 / 全模型 deepseek-only — 各自违反 P0-10 / 响应延迟 / 信号质量
- 不锁定前端独立"成本预算管理"写入页 — 冲突 P1-5 §2 红线 1+2
- 不锁定凭证池扩展(SMTP/Slack/Discord 等)— 违反 P1-6 §1.1 凭证清单封闭性
- 不锁定 LLM 模型升级路径 / 价格调整路径 — 价格变化由 amendment 处理(P0-10 §1.4 4 阈值修改三步)

---

**P1-7 决策对齐完成 ✅**

**P1 决策对齐收官 ✅**(P0-1 ~ P0-10 + P1-2.A/B/C + P1-5 + P1-6 + P1-7 全锁)

P1 决策对齐路径 A 第六份决策锁定;P1-7 = 仅锁 LLM 预算 + 日 ¥20 hard 严格 + 月 ¥440 soft 警告 + Kimi 单独 daily cap ¥4 + 软触发关 Kimi escalation + 月 50% audit only / 80% 飞书 warning / 100% 飞书 critical + 静态固定 ¥440 按自然月 + 飞书仅 hard breach + 月节点 + Phase B 成本拆解面板全量展示 + 派生 P0-10 amendment + P1-6 amendment + 20 红线 + 38 实施期任务(G-001~G-038 Phase A 7 + Phase B 12 + Phase B 收尾 6 + Lint 5 + 测试 8)。

**下一站**:实施期 Phase A 启动(代码迁移合并 P1-5 + P1-6 + P1-7 + P0-1 旧矩阵删除);具体执行待用户授权。
