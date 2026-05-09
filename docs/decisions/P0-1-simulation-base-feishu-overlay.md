# P0-1 — 两种运行模式与系统边界

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P0-1 |
| 决策日期   | 2026-05-09 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联 audit | `docs/quantmind_project_audit_2026-05-07.md` §1 / §8.2 / §13 / §15 |
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §0 / P0-1 |
| 替代       | 旧 CLAUDE.md 中"P0-1 = 半自动实盘 live_confirm"作废 |

## 决策摘要

QuantMind 永久不开发真实券商账户的程序化下单适配器。系统采用 **simulation_auto 永远在跑 + feishu_interactive 可叠加切换**的架构:`simulation_auto` 是 always-on 的底座(因子挖掘/趋势推演/选股/分析/复盘/数据采集),它产生的"买卖类 InstructionPlan"经由唯一开关 `FEISHU_INTERACTIVE_ENABLED` 决定走 SimulationExecutor 自动撮合(关)还是发飞书让用户在真实券商手动执行(开)。MockBroker 是唯一的账户镜像,不存在两条平行账本;模式切换是"账户生命周期事件",必须经过强制清空 + 飞书初始化对账。InstructionPlan 必须由角色鲜明的多 Agent 多轮辩论生成,Agent 知识库可演化(P2-2 细化)。

## 1. 决策具体内容

### 1.1 运行模式定义

只允许两种运行模式:

| 维度 | `simulation_auto` (always-on 底座) | `feishu_interactive` (可叠加切换) |
|------|------------------------------------|------------------------------------|
| 是否常开 | 是。所有非买卖动作(数据/分析/复盘/告警)持续运行 | 否。由 `FEISHU_INTERACTIVE_ENABLED` 控制 |
| 真实券商 API | 不接 | 不接 |
| 真实下单 | 不下 | 用户手动 |
| MockBroker 角色 | 虚拟资金的模型能力考场 | 用户真实资金的状态镜像(由飞书回报驱动) |
| 买卖 InstructionPlan 出口 | SimulationExecutor → MockBroker 自动撮合 | FeishuMessenger → 用户手动 → 用户飞书回报 → ExecutionReportParser → MockBroker 应用为成交 |
| 飞书消息 | **仅系统告警**(行情断流 / LLM 不可用 / 模拟账户异常等),**绝不发买卖指令** | 买卖指令 + 系统告警 + 对账请求 + 超时追问 |

### 1.2 架构示意

```
┌────────────────────────────────────────────────────────────┐
│  simulation_auto 底座(永远在跑)                          │
│  ─ 数据采集 / 行情推送                                    │
│  ─ 多 Agent 分析(详见 §1.6)                              │
│  ─ MiroFish 事件演化推断(P2-1 决策)                      │
│  ─ MockBroker mark-to-market / 风险监控 / 盘后复盘       │
│  ─ 系统告警(始终发飞书)                                  │
└────────────────────────────────────────────────────────────┘
                       │
            产生买卖类 InstructionPlan
                       │
              ┌────────┴────────┐
              │                  │
   FEISHU_INTERACTIVE        FEISHU_INTERACTIVE
        =false                    =true
              │                  │
              ▼                  ▼
  ┌─────────────────┐   ┌────────────────────────┐
  │ SimulationExec  │   │ FeishuMessenger         │
  │  自动撮合       │   │  发结构化指令 + 风控摘要 │
  │       │         │   │       │                 │
  │       ▼         │   │   用户在真实券商执行     │
  │   MockBroker    │   │       │                 │
  │  (虚拟资金)     │   │   用户飞书回报实际成交   │
  │  - 可任意重置   │   │       │                 │
  │  - 初始资金随设 │   │  ExecutionReportParser  │
  │  - 可手动埋入   │   │       │                 │
  │    长持持仓     │   │   MockBroker 应用成交    │
  │                 │   │  (真实资金状态镜像)     │
  │                 │   │  - 不可任意重置         │
  │                 │   │  - 初始 = 真实账户余额   │
  └─────────────────┘   └────────────────────────┘
              │                  │
              └────────┬─────────┘
                       ▼
       继续 simulation_auto 实时分析
```

### 1.3 模式切换 = 账户生命周期事件

切换不是简单 flag toggle,必须经过**强制重置 + 重新初始化**。

#### 1.3.1 `feishu_off → feishu_on`(进入真实交易)

按以下步骤执行,失败任一步则回滚到 `feishu_off`:

1. **归档**: 当前 MockBroker 状态(账户/持仓/订单/成交/快照)整体导出到 `mockbroker_archives` collection,key = `simulation_archive_{ISO 时间戳}`
2. **重置**: MockBroker 清空(账户=空、持仓=空、订单/成交=空)
3. **首条飞书**: 平台主动发"账户初始化对账"消息到目标群,要求用户回报:
   ```
   日终对账 {YYYY-MM-DD}
   可用现金 {金额}
   持仓 {code1} {数量}股 成本 {价格}; {code2} ...
   (无持仓时回复:可用现金 {金额}; 持仓 无)
   ```
4. **解析与确认**: ExecutionReportParser 解析回报;歧义则发澄清消息(P0-4 细化);两次澄清失败仍未解析则放弃切换并通知用户
5. **初始化**: 解析成功后将 MockBroker 初始化到该真实状态
6. **状态切换**: `FEISHU_INTERACTIVE_ENABLED=true` 才正式生效;此前的状态写入 `state_transitions` collection 留痕

#### 1.3.2 `feishu_on → feishu_off`(退出真实交易)

允许反向切换,但必须满足以下两条路径之一:

**路径 A — 已清仓退出**:
- 用户在飞书发"退出真实交易 资产已全部提现"指令
- 平台校验当前 MockBroker 持仓是否为空 + 现金是否=0(允许容差)
- 校验通过后归档 `user_reported_archive_{ISO 时间戳}`,MockBroker 重置回虚拟初始资金(默认值或前端设定)

**路径 B — 保留长持监控**:
- 用户明确声明"某些股票要长时间持有,继续在 off 模式监控"
- 平台归档 `user_reported_archive_{ISO 时间戳}`(保留真实状态全量)
- MockBroker 重置;**用户在前端"手动初始状态设定"页面**录入要继续监控的持仓:
  - 股票代码 + 数量 + 买入日期 + 成本价 + 当前估值
  - 现金部分由用户单独设定(虚拟金额)
- 录入完成后切换 `FEISHU_INTERACTIVE_ENABLED=false`,simulation_auto 基于这个"虚拟资金 + 长持持仓"组合继续推演

两条路径都强制归档,以便后续复盘可追溯真实交易段。

### 1.4 切换语义边界

- **切换需要后台显式触发**(API 端点 + 前端按钮),不允许仅靠改 env var 自动生效 — 因为切换附带账户重置等副作用
- **切换期间(初始化对账进行中)**冻结所有买卖类 InstructionPlan 生成,只保留分析/告警类输出
- **切换失败回滚**: 任一步失败,MockBroker 恢复到归档前状态,FEISHU_INTERACTIVE_ENABLED 保持原值
- **切换审计**: 所有切换写入 `state_transitions` collection,字段含 `from_mode`、`to_mode`、`triggered_by`、`archive_id`、`init_recon_message_ids[]`、`status`、`error?`

### 1.5 InstructionPlan 路由规则

- **唯一通路**: 所有买卖类 InstructionPlan 进 ModeRouter
- **off 时**: 全部进 SimulationExecutor → MockBroker 撮合
- **on 时**: 全部进 FeishuMessenger;ExecutionReportParser 收到回报后异步驱动 MockBroker
- **不允许 InstructionPlan 同时进双方**(避免账户被双倍变动)
- **on 时未收到回报**: 超时后(具体时长 P0-4 决策)发飞书追问一次,再超时才标记 expired;expired 不更新 MockBroker
- **用户选择性不执行**: 用户可飞书回复"未执行 QM-... 原因: ..." 或直接不回复;未执行的 InstructionPlan 不变更 MockBroker;下一轮 simulation_auto 研判会重新评估是否重发

### 1.6 多 Agent 辩论 = InstructionPlan 唯一生成路径(架构原则)

**架构硬约束**:

- 所有买卖类 InstructionPlan **必须由角色鲜明的多 Agent 多轮辩论生成**
- 数据源、资讯、MiroFish 事件演化推断的输出 **作为辩论输入**,不直接产生 InstructionPlan
- 辩论结果 → Pydantic 严格校验 → RiskEngine 验证 → 最终 InstructionPlan
- 任何"绕过辩论 / 单 Agent 直出 / LLM 直接计算仓位"的代码路径都属于红线违规

**Agent 业务能力构成**(原则声明,具体实现 P2-2 决策):

- Agent 能力 = `静态 prompt 模板` + `可演化的专属知识库`
- 知识库进化的潜在数据源(候选,P2-2 决策):
  - 网络可获取的公开知识(研报 / 政策 / 财经分析)
  - simulation_auto 模式下的模拟交易经验与教训
  - feishu_interactive 模式下的真实交易反馈与盈亏归因
- 进化触发机制 / 存储格式 / 版本回滚 / 人工评估门 全部留 P2-2 详细设计

### 1.7 旧授权语义的废止与迁移

旧的 `AUTHORIZATION_MODE × QUANTMIND_PHASE` 矩阵作为本次 P0-1 落地的一部分**一次性破坏式删除**:

| 旧符号 | 处理 |
|--------|------|
| `AUTHORIZATION_MODE` env var | 删除,不再读取 |
| `QUANTMIND_PHASE` env var | 删除,不再读取 |
| `phase5_eval` / `phase6_prep` / `phase6_dryrun` | 删除 |
| `phase7_live` | **永久禁止符号**,任何代码出现 = 红线违规 |
| `suggest` / `confirm` / `auto` 三态 | 删除;前端旧按钮文案同步替换 |
| `live_confirm` | 词汇本身禁用,在新代码中视为非法标识符 |
| `backend/services/authorization.py` | 整文件删除或彻底重写为 `run_mode.py`(命名待 P1 决策),只校验 `FEISHU_INTERACTIVE_ENABLED` 与切换合法性 |
| `tests/test_authorization.py` | 删除,新写 `tests/test_run_mode.py` 覆盖 §1.3 切换流程 |
| `tests/test_risk_api.py` 中 auth-mode 用例 | 删除,改为切换接口测试 |
| `backend/api/risk.py` POST `/api/risk/auth-mode` | 删除或重定向到新切换端点 |

迁移代价:1139 测试中的若干用例必须重写。**该 PR 必须走 codex review 5 轮 hard gate**(major 级)。

### 1.8 系统启动行为

- **启动必读 env**: `FEISHU_INTERACTIVE_ENABLED`(默认 `false`)
- **启动断言**(替代旧 `assert_authorization_mode()`):
  - 校验 `FEISHU_INTERACTIVE_ENABLED ∈ {true, false}`
  - 校验若 `=true`,则 MongoDB `state_transitions` 最近一次 transition 状态必须是 `feishu_on_active`(防止环境变量与持久化状态不一致)
  - 校验 broker 注册表的 active broker 必须是 `mock`(永久红线 — 任何尝试切真实 broker 启动失败)
  - 失败任一项 → `SystemExit`,uvicorn 非零退出码

## 2. 红线 / 边界(立即生效)

P0-1 落地后这些立即成为代码硬约束:

1. **永久禁止真实券商 API 下单/撤单/账户同步**;`backend/broker/` 仅留 `IBroker` interface stub 与 `MockBroker`,不开发 qmt/vnpy/Ptrade 实现
2. **`FEISHU_INTERACTIVE_ENABLED` 是唯一的运行时开关**;`AUTHORIZATION_MODE` / `QUANTMIND_PHASE` 在新代码中查无此 var
3. **MockBroker 是唯一账户镜像**,不存在 simulation 与 user_reported 两条平行账本;两种状态通过归档 collection 区分历史段
4. **模式切换不允许仅改 env var 自动生效**,必须经过 §1.3 的归档 + 重置 + 初始化对账流程
5. **InstructionPlan 必须由多 Agent 多轮辩论生成**;LLM 不允许绕开辩论直接产出股数/价格/有效期
6. **feishu_off 时绝不发买卖指令**;只发系统告警(行情断流/LLM 不可用/模拟账户异常/数据质量降级)
7. **feishu_on 时未回报的 InstructionPlan 不更新 MockBroker**;超时机制由 P0-4 进一步锁定
8. **风控隔离不变**: `backend/risk/` 严禁 `import backend.llm` / `backend.agents` / `backend.mirofish`
9. **`live_confirm` / `phase7_live` / `auto` 三个词在新代码中视为非法标识符**(grep 必须为空,通过 lint rule 持续校验)

## 3. 影响范围(留给 implementation 阶段)

后续实施任务清单(不在 P0-1 决策内,等所有 P0 锁定后由新执行计划编排):

### 3.1 删除项(代码级)

- `backend/services/authorization.py` 整体删除或重写
- `tests/test_authorization.py` 删除
- `backend/api/risk.py` 中 `/api/risk/auth-mode` 端点删除
- `tests/test_risk_api.py` 中 auth-mode 相关用例删除
- 前端 `DecisionCard.vue` 中"建议模式 / 确认模式 / 自动模式"按钮删除
- 所有 `phase7_live` / `live_confirm` / `auto` 字面量出现处清理

### 3.2 新增项(代码级)

- `backend/services/run_mode.py`(命名待 P1):管理 `FEISHU_INTERACTIVE_ENABLED` 状态、切换流程、归档
- `backend/services/mockbroker_archive.py`:MockBroker 状态归档/恢复
- `backend/services/mode_transition.py`:切换状态机(归档 → 重置 → 对账 → 初始化 → 切换)
- `backend/api/run_mode.py`:切换 API 端点(POST `/api/run-mode/transition`、GET `/api/run-mode/state`)
- `backend/integrations/feishu/`(P0-2 锁定后细化):client / events / parser / dedupe
- `backend/services/instruction_plan.py`:InstructionPlan 数据模型(P0-3 决策)
- `backend/services/mode_router.py`:Plan → SimulationExecutor / FeishuMessenger 路由
- `backend/services/execution_report_parser.py`:用户飞书回报解析(P0-4 决策)
- `frontend/src/views/RunModeTransition.vue`:切换前端界面
- `frontend/src/views/ManualPositionSetup.vue`:on→off 路径 B 的长持持仓录入
- 新 collections: `mockbroker_archives` / `state_transitions` / `instruction_plans` / `execution_reports` / `feishu_messages` / `decision_ledger`

### 3.3 配置项

- `.env` 新增: `FEISHU_INTERACTIVE_ENABLED=false`
- `.env` 删除: `AUTHORIZATION_MODE`、`QUANTMIND_PHASE`
- `config/run_mode.yaml`(新):切换超时、重试、归档保留期、初始化对账模板

### 3.4 文档同步

- `CLAUDE.md` §1.3 状态更新(P0-1 ✅)
- `CLAUDE.md` §2.1 P0-1 行更新指向本文件
- `CLAUDE.md` §3.1 红线节同步 §2 内容
- `CLAUDE.md` §3.4 操作速查替换旧 env var
- `MEMORY.md` 新增 `project_run_mode_p0_1.md` 索引项
- 旧 `docs/phase5-eval-and-phase6-prep-master-plan.md` 在执行计划重写时一并退役

## 4. 决策依据

### 4.1 audit 引用

- audit §1 锁定方向:"不再以真实券商账户的程序化下单/半自动下单/全自动下单为目标"
- audit §8.2 暗示 `run_mode × execution_target` 双轴,但本决策最终采用单轴 + 切换器(更简洁,更接近用户本意)
- audit §1.2 关键缺口确认 RiskEngine 未贯穿 + InstructionPlan 缺失 + 没有飞书闭环
- audit §15"当前不建议做的事"清单与本决策红线 §1-§9 一致

### 4.2 代码事实抽检

- `backend/services/authorization.py:45-50` ALLOWED_MODES_BY_PHASE 旧矩阵 — 本决策直接删除
- `backend/main.py:244` lifespan 调用 `assert_authorization_mode()` — 替换为新启动断言
- `backend/api/risk.py` POST `/api/risk/auth-mode` — 删除
- `backend/broker/` 现状仅 MockBroker 内存实现 — 持久化在 P1-2 决策

### 4.3 用户选择记录(2026-05-09 决策对话)

| 问题 | 选择 |
|------|------|
| 两种模式如何共存? | 接近"simulation_auto 永远开 + feishu_interactive 叠加",但有重要细化:模式切换 = 账户生命周期事件 + 多 Agent 辩论是 InstructionPlan 唯一通路 |
| 旧 phase × mode 矩阵处理? | 一次性破坏式删除 |
| 是否需要 investigation 第三模式? | 不需要,就两种模式 |
| InstructionPlan 分发方式? | 用户主导设计:MockBroker 单一账户镜像,InstructionPlan 单目的地路由,模式切换的本质是 MockBroker 的"驱动源"切换 |
| off→on 切换如何重置? | 强制清空 + 飞书初始化对账 |
| on→off 切换如何处理? | 允许,但要求清仓退出或显式声明长持(后者通过前端手动初始状态设定) |
| Agent 进化机制? | P0-1 只锁架构原则,进化细节留 P2-2 |
| feishu_off 时是否发飞书? | 只发系统告警,不发买卖指令 |
| 未回报超时? | 超时后追问一次,再超时才失效 |

## 5. 后续动作 (checklist)

> 本决策本身定稿不触发实施工作。以下条目仅记录"P0-1 锁定后下一步要做什么",真实落地排期等所有 P0 全部锁定后由新执行计划统一编排。

### 5.1 立刻完成的状态同步

- [ ] 更新 `CLAUDE.md` §1.3:P0-1 状态从 ⏳ 改为 ✅,链接本文件
- [ ] 更新 `CLAUDE.md` §2.1:P0-1 行 决策文档列填本文件路径
- [ ] 更新 `CLAUDE.md` §3.1 红线节:把本文件 §2 红线 1-9 同步进去(若有重叠则保留更严格的)
- [ ] 更新 `MEMORY.md` 索引:新增 `project_run_mode_p0_1.md` 条目
- [ ] commit 本决策文档 + CLAUDE.md/MEMORY.md 同步更新(单 PR)
- [ ] 不要立即实施代码删除/迁移 — 等其他 P0 决策也锁定后再统一进入实施期

### 5.2 依赖本决策的下游 P0 决策

- **P0-2 飞书接入形态**: 决定 §1.3 飞书初始化对账消息的承载方式(自定义机器人 / 自建应用 / 长连接 / 卡片)
- **P0-3 InstructionPlan 字段集**: 决定 §1.5 路由的对象结构 + §1.6 多 Agent 辩论产出的字段约束
- **P0-4 飞书回报语法**: 决定 §1.3 #3 的回报模板 + §1.5 超时机制 + ExecutionReportParser 范围
- **P0-5 账户对账机制**: 决定 §1.3 #4 解析失败时的处理 + 容差/异常路径
- **P0-6 simulation_auto 验收标准**: 决定何时 §1.3.1 切换被允许触发(必须达到验收门槛才能切换到 feishu_on)
- **P0-7 风险红线**: 决定 §1.5 InstructionPlan 进 RiskEngine 时的硬限制
- **P0-10 LLM 角色边界**: 决定 §1.6 多 Agent 辩论中 LLM 可做/不可做的分工

### 5.3 实施期(所有 P0 锁定后)

- [ ] 按 §3.1-§3.3 编写 implementation 任务列表
- [ ] 该 PR 走 codex review 5 轮 hard gate(major 级,涉及红线删除)
- [ ] 测试覆盖:run_mode 切换流程 100%(归档/重置/对账/初始化/回滚 5 阶段独立测试)
- [ ] 静态检查:lint rule 阻止 `phase7_live` / `live_confirm` / `AUTHORIZATION_MODE` / `QUANTMIND_PHASE` 重新出现

---

_本文件定稿,不再就地修改。如需调整,新建 `P0-1-amendment-{日期}-{原因}.md`。_
