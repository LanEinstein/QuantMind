# P1-5 — 前端工作流 MVP 7 页 + Phase B 4 页(决策闭环 4 分组 + 写入接口立即下线 + WS 单通道 12 类消息扩展 + 飞书主路径 + 前端备用回报录入 + 5 冻结源全局 StatusBar + 三层 reason 抽屉三 tab)

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P1-5 |
| 决策日期   | 2026-05-10 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联 audit | `docs/quantmind_project_audit_2026-05-07.md` §5(前端实现现状)+ §7(前后端接口断层)|
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §P1-5 — 前端优先工作流 |
| 范围说明   | 本决策为 **P1 决策对齐路径 A 第四份**(P1-2.A/B/C 三子全锁后第一份非 P1-2 决策);锁定前端 MVP 7 页 + Phase B 4 页清单 + 一级菜单分组 + 写入接口收口红线 + 实时通道协议 + 用户回报双路径 + 5 冻结源展示策略 + 三层 reason 展示位置 |
| 依赖决策   | `docs/decisions/P0-1-simulation-auto-base-feishu-interactive-overlay.md`(§1.3 模式切换 = 账户生命周期事件不是 flag toggle + §2 红线 6 旧 AUTHORIZATION_MODE 矩阵实施期一次性破坏式删除)+ `docs/decisions/P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md`(§1.2 永禁 HTTPS 入站 + §1.3 lark-oapi 长连接单实例 + §2.5 备用 webhook 仅告警)+ `docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md`(§1.1 instruction_id 严格正则 + §1.2 BUY/SELL/HOLD 三态 + §2 红线 12 frozen Pydantic strict)+ `docs/decisions/P0-4-execution-report-parser-strict-regex-and-fail-closed-state-machine.md`(§1.4 5 种回报模板 + §1.5 严格正则 only + §3.1 ExecutionReportApplier 单一入口)+ `docs/decisions/P0-5-mockbroker-single-mirror-active-eod-reconciliation-failclosed-ticket.md`(§1.5 reconciliation_ticket 三选一裁定 + §1.6 OPEN/EXPIRED 期间冻结买卖类路由)+ `docs/decisions/P0-6-acceptance-45-day-rolling-stability-and-strategy-gates.md`(§1.3 acceptance_reports collection + §1.4 can_switch_to_feishu_on() 校验)+ `docs/decisions/P0-7-risk-redlines-position-circuit-universe-llm-immutability.md`(§2 红线 14 RiskConfig runtime 不可改 + §2 红线 15 backend/api/*仅 GET)+ `docs/decisions/P0-8-data-strict-primary-secondary-watchlist-snapshot-multi-domain-mirofish-data-quality.md`(§1.4 5 类 evidence_id 前缀 + §2 红线 7 MiroFish 仅入 evidence_collection)+ `docs/decisions/P0-9-watchlist-scope-frequency-traditional-quant-primary-long-only.md`(§2 红线 12 backend/api/watchlist*.py 仅 GET + §2 红线 13 long-only)+ `docs/decisions/P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md`(§2 红线 1 字段权限矩阵 + §2 红线 5 backend/api/{llm,agents}*.py 仅 GET)+ `docs/decisions/P1-2.A-persistence-hybrid-snapshot-and-broker-scheduler.md`(§1.6.2 EOD chain freeze 第五种来源 eod_pipeline_freeze + §1.4 broker_events/broker_snapshots collection)+ `docs/decisions/P1-2.B-mtm-30s-equity-points-data-quality-on-demand.md`(§1.4 EquityPoint per-position MTM + §1.7 DataQualityProvider per-stock evaluate)+ `docs/decisions/P1-2.C-matching-allornone-defensive-limitcheck-tiered-slippage-transfer-fee.md`(§1.1 三层 reason 区分 + §1.4 OrderCostBreakdown 结构)|
| 派生 amendment | (无;本决策不修改既有 P0/P1-2.A/B/C 决策的硬约束;新增 backend/api 路由表为 GET only 端点扩展属非破坏式落地。Simulation.vue 保留为 P1-5 范围外属 P1-5 自身约束,不派生上游 amendment)|
| 替代       | 当前前端 7 大页面(Dashboard / Portfolio / AgentDebate / RiskCenter / Performance / Simulation / Settings)+ 7 红线违规 POST 端点(`/api/risk/config` / `/api/risk/auth-mode` / `/api/settings/llm-config` / `/api/settings/data-sources/test` / `/api/settings/mirofish` / `/api/trading/approve` / `/api/trading/reject` / `/api/trading/cancel`)+ 缺失 4 关键页面(用户回报录入 / 验收报告 / 对账裁定 / 系统状态独立页)+ ApprovalQueue.vue 半自动审批环节(新方向下不存在审批)+ 旧 auth_mode_change WS 消息(模式切换非 flag toggle 已 P0-1 锁定)|

## 决策摘要

QuantMind 第一阶段前端工作流采用 **MVP 7 页(Phase B 落地)+ Phase B 收尾再补 4 页 + 决策闭环 4 一级菜单分组 + 写入接口立即下线 + Phase A 后端一次性破坏式删除 + WS 单通道 12 类消息扩展 + 飞书主路径用户回报 + 前端备用录入页 + 5 冻结源全局 StatusBar + 独立系统状态页 + 三层 reason InstructionPlan 池详情抽屉三 tab + Performance + 验收报告分离 + P1-5 暂不加本机认证(P1-6 处置)+ Simulation.vue 保留为 P1-5 范围外** 架构,完成 P1 决策对齐路径 A 第四份决策:

1. **MVP 7 页(实施期 Phase B 落地)**:① Dashboard(运行总览 + 顶部 StatusBar 5 冻结源)② 系统状态页(5 冻结源独立显示 + 24h 时间轴 + decision metadata)③ InstructionPlan 池(指令池 + 详情抽屉三 tab Builder/Engine/Broker reason)④ Portfolio(只读仓位 + 委托 + 成交)⑤ 用户回报录入页(飞书备用通道 + 5 模板表单 + 实时正则镜像预览)⑥ 对账裁定页(OPEN/EXPIRED ticket 三选一裁定按钮)⑦ 验收报告页(45 交易日滚动窗口表格 + 5 稳定性硬门槛 + 3 策略硬门槛 + can_switch_to_feishu_on() 布尔 + 7 观察指标);MVP 优先级基于"决策闭环最小可验证子集"原则:simulation_auto 跑通 + feishu_interactive 切换前置校验 + 系统状态可观测 + 用户回报降级路径完整。

2. **Phase B 收尾 4 页**:① Agent 辩论可视化(基于 SSE /api/analysis/stream Bull/Bear 双方论证 + 风控 + fund_manager 决策)② 数据质量面板(7 breach 信号 + 3 计数 + per-stock evaluate 历史)③ 飞书消息历史(发送+接收完整记录,审计用)④ 成本拆解面板(每日 LLM/数据/运维分类成本 + ¥20 hard ceiling 监控)。Phase B 收尾意味着 4 页在 P1-2.A/B/C 数据 schema 全量落地 + MVP 7 页验证后再实施,避免无数据可展示。

3. **P1-5 范围外保留**:Simulation.vue(MiroFish 多 Agent 独立思考演化过程可视化)保留代码不重点投入;**用户原话:"应当展现出 mirofish 众多 Agent 独立思考演化的过程,炫酷一些,在后续可能的融资与展示环节或有大用,但这种可视化的展示必须重点突出,后续推进至此时再做细致打算"**;P1-5 阶段不改造不重点投入,后续阶段(P2 或独立"展示"决策)再细致打算;此约束不冲突 P0-9 §2 "MiroFish 加分非核心"红线 — 不夺主路径 cap 但保留可视化展示价值。

4. **写入接口立即下线 + Phase A 一次性破坏式删除**:前端 RiskCenter / Settings / ApprovalQueue 所有 POST/PUT/PATCH/DELETE 调用 + 按钮立即下线(改为只读字段 + "改 YAML+重启"文案);后端 `backend/api/{risk,watchlist,llm,agents}*.py` + `backend/api/settings/*.py` 所有非 GET handler Phase A 一次性破坏式删除(与 P0-1 §2 红线 6 旧 AUTHORIZATION_MODE 矩阵删除节奏对齐);**仅允许 2 个前端写入端点**:① POST `/api/execution-reports`(用户回报录入,前端备用通道)② POST `/api/reconciliation-tickets/{ticket_id}/decide`(对账三选一裁定);其他全部禁写。

5. **WS 单通道扩展 12 类消息**:保留 `/ws/market` 单 WebSocket 长连接,扩展消息类型矩阵:已有(扩展)6 类 = `index_update` / `signal` / `news` / `status` / `position_update` / `circuit_breaker_update`;新增 8 类 = `instruction_plan_update` / `broker_event` / `equity_point_update` / `data_quality_breach` / `freeze_source_update` / `ticket_update` / `acceptance_report_ready` / `feishu_message_received`;删除 2 类 = `auth_mode_change`(模式切换非 flag toggle,通过页面跳转通知)+ `approval_update`(ApprovalQueue 废弃)。SSE `/api/analysis/stream` 仅保留 LLM 流式输出场景(Agent 辩论可视化 Phase B 用)。WS 单通道选择理由:与 lark-oapi 长连接架构同构 + 浏览器并发连接限制规避 + 重连逻辑统一管理。

6. **用户回报飞书主路径 + 前端备用录入页**:主路径(P0-4 §1.5 + §3.1 已锁)= 用户飞书群发文本回报 → 后端 lark-oapi 长连接接收 → ExecutionReportApplier 严格正则解析。备用路径 = 前端"用户回报录入"页提供 5 种回报模板表单(已执行/部分执行/未执行/更正/盘后补录)+ 实时 JS 正则镜像预览(纯展示用)+ 提交走 POST `/api/execution-reports` 后端走同一 ExecutionReportApplier 入口;**前端 JS 正则镜像必须与后端保持一致**(实施期 D 任务约束,单元测试断言两者输出相同)。备用路径设计动机:飞书长连接 4h 故障窗口期(P0-6 §1.5 系统级中断重置触发条件之一)用户仍可通过前端补录 + PC 前操作时机不切手机也可便捷录入;不冲突 P0-2 + P0-3 飞书核心交互闭环定位 — 备用 ≠ 主用。

7. **5 种买卖类路由冻结源全局 StatusBar 顶部常驻 + 独立系统状态页**:全局顶部 StatusBar(每个页面常驻)5 个独立状态点 = `freeze_source_switch` / `freeze_source_ticket_open` / `freeze_source_circuit_breaker_cooldown` / `freeze_source_data_quality` / `freeze_source_eod_pipeline_freeze`;任一为真 → 点变红 + tooltip 展示原因;独立"系统状态"页全量展示 5 冻结源当前状态 + 近 24h 变更时间轴 + 决策 metadata(`eod_pipeline_failed_at` / `circuit_breaker_state.cooldown_until` / `data_quality_state.breach_signals` / `open_tickets[].ticket_id` / `mode_switch_in_progress` 等)+ 熔断状态 + 长连接状态 + LLM 路由健康度 + 数据源状态。多增点设计动机:5 状态独立并行查看 ≠ 聚合为单一 frozen=true(P0-1 + P0-5 + P0-7 + P0-8 + P1-2.A 五种来源在底层独立判断,UI 层不允许聚合掩盖)。

8. **三层决策拦截 reason 在 InstructionPlan 池详情抽屉三 tab**:点击指令详情 → 抽屉 → 3 tab:① Tab "Builder 早返"(InstructionPlanBuilder 五道早返六检查项 = `buy_sell_only` / `strict_id_match` / `valid_until_check` / `new_stock_exclusion` / `liquidity_exclusion` / `single_amount_cap_50000` 等;颜色标 ✅/❌ + 详细 reason 文本)② Tab "RiskEngine 14-check"(14 项 risk_summary 逐项展示;每项含 check_id / passed / reason / threshold / actual_value)③ Tab "MockBroker at-fill"(仅 FILLED/REJECTED 终态显示;含 fill_price / cost_breakdown / `reason='price_limit_violation_at_fill'` 或其他 broker reason 辨别 + 与 RiskEngine pre-route reason='limit_up_block'/'limit_down_block' 区分以便 audit)。三层独立 tab 选择理由:P1-2.C §2 红线 11 "三层 reason 各自独立便于 audit" 强约束;合并平展会丢失拦截层级信息。

9. **Performance + 验收报告页分离**:Performance.vue 保留 → 数据源 `broker_snapshots` + `equity_points`;展示连续 equity_curve 折线图 / 回撤图 / 资金分布饰环图 / 对比 `000300.SH` 基准走势(沪深 300 累计超额);职责:可视化绩效复盘。验收报告页独立新建 → 数据源 `acceptance_reports` collection;展示 45 交易日滚动窗口表格化输出(5 稳定性硬门槛 ± PASS/FAIL + 3 策略硬门槛 ± PASS/FAIL + can_switch_to_feishu_on() 布尔 + 7 项观察指标);职责:决策表格输出。两页职责分离选择理由:可视化与决策表格是两种异构展示需求;合并到单页会形成双职责冲突 + 视觉层级混乱。

10. **一级菜单按决策闭环 4 分组**:① **运行状态**(Dashboard 总览 / 系统状态)② **决策与指令**(InstructionPlan 池 / Agent 辩论[Phase B])③ **账本与成交**(Portfolio 仓位 / 用户回报录入 / 对账裁定)④ **复盘与验收**(Performance 绩效 / 验收报告 / 数据质量[Phase B] / 飞书消息历史[Phase B] / 成本拆解[Phase B]);设置(LLMRouter / DataSources / MiroFishConfig / CostDashboard)折叠为右侧只读下拉(全部移除写入按钮,仅展示当前 YAML 状态 + "改 YAML+重启"文案)。Simulation.vue 不进 4 分组(P1-5 范围外,后续阶段细化);分组动机:与决策闭环(watchlist → 分析 → 指令 → 风控 → 执行 → 回报 → 复盘)同构,用户使用路径 = 菜单路径。

11. **本机访问认证 P1-5 暂不加(P1-6 统一处置)**:P1-5 范围仅 UI 层 + API 协议;认证、密钥轮换、IP 白名单、审计日志归 P1-6;实施期默认 127.0.0.1 只绑(继承 P0-2 §2.5 + 全局 §2.10 红线);**补充红线**:前端不允许存储任何凭证到 `localStorage` / `sessionStorage` / `cookie`(防浏览器截图 + 历史记录 + 第三方扩展泄露);所有敏感配置展示均末四位脱敏 + `webhook_configured` 布尔(继承 P0-2 §2.5 + 全局 §2.10)。

12. **第一阶段排除项**:移动端原生 App / 远程跨网访问(P2-3 范围;P1-5 仅本机内网)/ 多用户权限分级(单用户路线)/ 双语国际化(中文 only)/ 主题切换(默认浅色)/ 客户端缓存大数据(`acceptance_reports` 全部走后端按需 GET)/ 前端原生跑机器学习(LLM 调用全后端中转,前端零模型)/ HTTPS 入站接收飞书事件(继承 P0-2 §2 红线 1)/ webhook 直接发买卖指令(继承 P0-2 §2.5)。

## 1. 决策具体内容

### 1.1 MVP 7 页清单(实施期 Phase B 落地)

| 序号 | 页面 | 数据源 | 关键展示 | 写入端点 |
|------|------|--------|----------|----------|
| ① | Dashboard 总览 | WS index_update + signal + news + 当日 InstructionPlan 摘要 | 沪深三大指数 + 板块热力图 + 资金流向 + 新闻快讯 + 当日 InstructionPlan 摘要(状态分布饼图)+ 顶部 StatusBar 常驻 5 冻结源 | 无 |
| ② | 系统状态 | GET /api/system-status + WS freeze_source_update / circuit_breaker_update | 5 冻结源独立状态点 + 近 24h 变更时间轴 + decision metadata + 熔断状态 + 长连接状态(lark-oapi)+ LLM 路由健康度 + 数据源状态(adata/akshare/baostock) | 无 |
| ③ | InstructionPlan 池 | GET /api/instruction-plans + WS instruction_plan_update | 当日全 InstructionPlan 列表 + 状态过滤(DRAFT/VALIDATED/DISPATCHED/FILLED/EXPIRED/REJECTED/AMBIGUOUS)+ 详情抽屉三 tab(Builder/Engine/Broker reason)+ MIROFISH- 前缀 evidence 折叠 | 无 |
| ④ | Portfolio 仓位 | GET /api/portfolio + WS position_update + broker_event | 当前持仓表(代码/股数/成本/市值/未实现盈亏/price_source/staleness)+ 当日委托 + 成交历史(含 commission/stamp_tax/slippage_cost/transfer_fee 拆解) | 无(原 ApprovalQueue 移除)|
| ⑤ | 用户回报录入 | POST /api/execution-reports | 5 种回报模板表单(已执行/部分执行/未执行/更正/盘后补录)+ 实时 JS 正则镜像预览(展示用)+ 提交走 ExecutionReportApplier 同一入口 | POST /api/execution-reports |
| ⑥ | 对账裁定 | GET /api/reconciliation-tickets + WS ticket_update | OPEN/EXPIRED ticket 列表 + 偏差明细(cash 1 元 / volume 0% / cost 0.01 元三档)+ 三选一裁定按钮(对账采纳:用户回报 / 对账采纳:系统镜像 / 对账更正)| POST /api/reconciliation-tickets/{ticket_id}/decide |
| ⑦ | 验收报告 | GET /api/acceptance-reports + WS acceptance_report_ready | 45 交易日滚动窗口表格 + 5 稳定性硬门槛(指令完整率 ≥95% / 回报解析准确率 ≥99% / 数据缺失率 ≤1% / LLM 超时率 ≤5% / 信号生成成功率 ≥95%)± PASS/FAIL + 3 策略硬门槛(回撤 ≤8% / PnL ≥0 / 沪深 300 超额 ≥0)± PASS/FAIL + can_switch_to_feishu_on() 布尔 + 7 观察指标 | 无 |

### 1.2 Phase B 收尾 4 页清单

| 序号 | 页面 | 数据源 | 关键展示 | 写入端点 |
|------|------|--------|----------|----------|
| ⑧ | Agent 辩论可视化 | GET /api/analyses/{job_id} + SSE /api/analysis/stream/{job_id} | Bull/Bear 双方论证 + 风控评估 + fund_manager 决策(BUY/SELL/HOLD 倡议)+ debate_round_count + LLM thinking 流式输出(Kimi tiered) | 无 |
| ⑨ | 数据质量面板 | GET /api/data-quality + WS data_quality_breach | 7 breach 信号(quote_unavailable/staleness/divergence/minimum_freshness/news_outage/mirofish_unavailable/watchlist_snapshot_outage)+ 3 计数(stale_count/divergence_count/news_failure_count)+ per-stock evaluate 历史 | 无 |
| ⑩ | 飞书消息历史 | GET /api/feishu-messages + WS feishu_message_received | 发送 + 接收完整记录 + tenant_access_token 状态(末四位脱敏)+ 长连接断线重连历史 | 无 |
| ⑪ | 成本拆解面板 | GET /api/cost-breakdown | 每日 LLM/数据/运维分类成本 + ¥20 hard ceiling 监控 + tiered routing 命中分布 + per-Agent 累计成本 | 无 |

### 1.3 P1-5 范围外 — Simulation.vue 保留

- **位置**:`frontend/src/views/Simulation.vue` 及子组件 `InflectionTimeline / ExtremeScenarioPie / HiddenVariableMatrix / ModelContribution`
- **决策**:P1-5 阶段保留代码不改造不投入;后续阶段(P2 或独立"展示价值"决策)再细致打算
- **用户原话(2026-05-10 P1-5 第二轮 Q5 回复)**:"应当展现出 mirofish 众多 Agent 独立思考演化的过程,炫酷一些,在后续可能的融资与展示环节或有大用,但这种可视化的展示必须重点突出,后续推进至此时再做细致打算"
- **不冲突的红线**:P0-9 §2 "MiroFish 加分非核心" — 后续展示页不得占用 traditional 主路径 cap;P0-8 §2 "MiroFish 仅入 evidence_collection 不入 RiskCheckSummary" — 可视化展示不得加入决策路径
- **菜单可见性**:P1-5 阶段菜单暂不展示(避免误导);后续阶段决策决定是否独立"展示"分组

### 1.4 一级菜单分组(决策闭环 4 分组 + 设置折叠)

```
运行状态
  ├ 总览 (Dashboard)              [MVP]
  └ 系统状态 (5 冻结源)            [MVP]

决策与指令
  ├ 指令池 (InstructionPlan)       [MVP]
  └ Agent 辩论                     [Phase B]

账本与成交
  ├ 仓位 (Portfolio)               [MVP]
  ├ 用户回报录入                   [MVP]
  └ 对账裁定                       [MVP]

复盘与验收
  ├ 绩效 (Performance)             [MVP]
  ├ 验收报告                       [MVP]
  ├ 数据质量                       [Phase B]
  ├ 飞书消息历史                   [Phase B]
  └ 成本拆解                       [Phase B]

设置 (只读 折叠)
  ├ LLMRouter                      [全部只读]
  ├ DataSources                    [全部只读]
  ├ MiroFishConfig                 [全部只读]
  └ CostDashboard                  [全部只读]

(P1-5 范围外:Simulation.vue 不出现在菜单中)
```

### 1.5 写入接口红线收口

#### 1.5.1 前端立即下线(实施期 Phase A)

需移除的前端写入按钮 + axios 调用:

| 文件 | 旧调用 | 处置 |
|------|--------|------|
| `frontend/src/views/RiskCenter.vue` | POST /api/risk/config | 删除按钮 + axios 调用,改为只读字段展示 + "改 config/risk.yaml + 进程重启"文案 |
| `frontend/src/views/RiskCenter.vue` | POST /api/risk/auth-mode | 删除切换器(模式切换 = 账户生命周期事件,不是 flag toggle) |
| `frontend/src/views/settings/LLMRouter.vue` | POST /api/settings/llm-config | 删除按钮 + axios 调用,改为只读 + "改 config/agent_models.yaml + 重启"文案 |
| `frontend/src/views/settings/LLMRouter.vue` | POST /api/settings/llm-config/test | 删除测试按钮(LLM 连接性走后端 health-check API,前端不主动测试) |
| `frontend/src/views/settings/DataSources.vue` | POST /api/settings/data-sources/test | 删除测试按钮(数据源健康度走后端 cron + WS status 推送) |
| `frontend/src/views/settings/MiroFishConfig.vue` | POST /api/settings/mirofish | 删除按钮 + axios 调用,改为只读(MiroFish 配置 runtime 不可改) |
| `frontend/src/components/trading/ApprovalQueue.vue` | POST /api/trading/approve/{id} | 整个组件删除(无审批环节 — simulation_auto 自动执行 / feishu_interactive 走飞书) |
| `frontend/src/components/trading/ApprovalQueue.vue` | POST /api/trading/reject/{id} | 整个组件删除(同上) |
| `frontend/src/components/trading/OrderList.vue` | POST /api/trading/cancel/{orderId} | 删除取消按钮(订单生命周期由系统管理 + valid_until 自动 EXPIRED;手动撤销走对账更正路径) |
| `frontend/src/views/AgentDebate.vue` | POST /api/analysis/stock | 改为后端定时触发(slow_pipeline + fast_pipeline) — 前端不再触发分析 |
| `frontend/src/views/AgentDebate.vue` | POST /api/analysis/jobs | 同上,改为后端定时触发 |

#### 1.5.2 后端 Phase A 一次性破坏式删除

需删除的后端 handler:

| 文件 | 删除 handler |
|------|-------------|
| `backend/api/risk*.py` | 所有 POST/PUT/PATCH/DELETE handler(继承 P0-7 §2 红线 15 + P0-10 §2 红线 5)|
| `backend/api/watchlist*.py` | 所有 POST/PUT/PATCH/DELETE handler 包括 deprecated `add_stock` / `remove_stock` / `clear`(继承 P0-9 §2 红线 12)|
| `backend/api/llm*.py` | 所有 POST/PUT/PATCH/DELETE handler(继承 P0-10 §2 红线 5)|
| `backend/api/agents*.py` | 所有 POST/PUT/PATCH/DELETE handler(继承 P0-10 §2 红线 5)|
| `backend/api/settings/*.py` | POST handler `llm-config` / `mirofish` / `data-sources/test`(派生本决策 §2 红线 3)|
| `backend/api/trading/*.py` | POST handler `approve` / `reject` / `cancel`(派生本决策 §2 红线 4)|
| `backend/api/analysis/*.py` | POST handler `stock` / `jobs`(改为后端定时触发,前端不再调用)|

删除节奏与 P0-1 §2 红线 6 旧 AUTHORIZATION_MODE 矩阵删除节奏对齐(实施期 Phase A 一次性破坏式),目标:lint rule grep 全空。

#### 1.5.3 仅允许 2 个前端写入端点

| 端点 | 用途 | 路径 |
|------|------|------|
| POST /api/execution-reports | 用户回报录入(前端备用通道) | `backend/api/execution_reports.py` |
| POST /api/reconciliation-tickets/{ticket_id}/decide | 对账三选一裁定 | `backend/api/reconciliation_tickets.py` |

**绝禁**任何其他写入端点出现在前端;若有新写入需求 → 走 P1-5-amendment + 决策对齐再加。

### 1.6 实时通道协议(WS 单通道扩展 12 类消息 + SSE LLM 流式)

#### 1.6.1 WebSocket 消息矩阵

`/ws/market` 单 WS 长连接(`useWebSocket.ts`),12 类消息:

| 消息类型 | 已有/新增/删除 | 触发源 | 推送内容 |
|---------|---------------|--------|---------|
| `index_update` | 已有 | 后端 30s cron | 沪深三大指数实时行情 |
| `signal` | 已有 | 信号生成 cron | 当日新增 InstructionPlan 摘要 |
| `news` | 已有 | 多域 5 源情报 cron | 财经/时政/全球新闻流 |
| `status` | 已有 | 后端 5s cron | 系统总状态 heartbeat |
| `position_update` | 已有 | broker_event 触发 | 持仓变更 |
| `circuit_breaker_update` | 已有 | RiskEngine 触发 | 熔断状态变更 |
| `instruction_plan_update` | **新增** | InstructionPlanBuilder 触发 | InstructionPlan 状态变更(DRAFT→VALIDATED→DISPATCHED→FILLED/...) |
| `broker_event` | **新增** | MockBroker `_fill_order` 触发 | broker_events collection 新增事件(insert-only)|
| `equity_point_update` | **新增** | BrokerScheduler 30s intraday_mtm 触发 | EquityPoint 含 per-position MTM |
| `data_quality_breach` | **新增** | DataQualityProvider per-stock evaluate 触发 | 4 阻断 breach 信号 + 3 非阻断信号 |
| `freeze_source_update` | **新增** | 5 冻结源任一变更 | 5 状态点独立推送(`source_id` + `frozen` + `reason`)|
| `ticket_update` | **新增** | reconciliation_ticket 创建/状态变更 | OPEN/EXPIRED/RESOLVED |
| `acceptance_report_ready` | **新增** | EOD pipeline acceptance_reports 写入完成 | 当日 acceptance_report 摘要 |
| `feishu_message_received` | **新增** | lark-oapi 接收用户回报 | 飞书消息原文 + 解析结果 |
| `auth_mode_change` | **删除** | — | 模式切换 = 账户生命周期事件,通过页面跳转通知 |
| `approval_update` | **删除** | — | ApprovalQueue 废弃 |

#### 1.6.2 SSE 协议保留

`/api/analysis/stream/{job_id}` SSE 仅保留 LLM 流式输出场景(Agent 辩论可视化 Phase B):

- `agent_started` / `agent_completed` / `pipeline_completed` / `error` 事件
- 流式 token 推送(Kimi thinking + Bull/Bear 论证)

不再扩展 SSE 通道用于业务事件;业务事件全走 WS 单通道。

### 1.7 用户回报录入路径

#### 1.7.1 主路径(P0-4 已锁)

```
用户在飞书群 @机器人 发文本回报
  → 后端 lark-oapi 长连接接收 (im.message.receive_v1)
  → renderer.py 提取文本(防 prompt injection)
  → ExecutionReportApplier 严格正则解析 (P0-4 §1.5)
  → 5 种回报模板任一匹配 → 更新 MockBroker 状态
  → 不匹配 → AMBIGUOUS → 飞书发澄清
```

#### 1.7.2 备用路径(P1-5 新增)

```
用户在前端"用户回报录入"页填表单
  → 5 模板下拉选择 (已执行/部分执行/未执行/更正/盘后补录)
  → 前端 JS 正则镜像实时预览 (纯展示用,不参与决策)
  → 用户提交 → POST /api/execution-reports
  → 后端 ExecutionReportApplier 严格正则解析 (与主路径同一入口)
  → 5 种回报模板任一匹配 → 更新 MockBroker 状态
  → 不匹配 → 返回 422 + 详细错误 + 用户在前端修改重提交
```

#### 1.7.3 镜像一致性约束

- 前端 JS 正则镜像源代码生成自后端 ExecutionReportApplier 相同正则常量(实施期 D 任务 E-018:抽出 `backend/execution/regex_patterns.py` 为单一真相源,前端通过构建时代码生成或运行时 GET /api/execution-reports/regex-patterns 获取)
- 单元测试断言两者输出相同(`test_frontend_backend_regex_consistency.py`)
- 镜像不一致即视为前端 bug,前端 fail-closed(显示"正则模式同步失败"+ 阻止提交,引导走飞书主路径)

### 1.8 五种买卖类路由冻结源前端展示

#### 1.8.1 全局 StatusBar 5 状态点

`frontend/src/components/common/StatusBar.vue`(现有组件扩展)5 个独立状态点:

| 状态点 ID | 来源决策 | 触发条件 | 红色 tooltip 文案 |
|----------|---------|---------|------------------|
| `freeze_source_switch` | P0-1 §1.3 | 模式切换中(simulation_auto ↔ feishu_interactive) | "模式切换中:账户生命周期事件,买卖类路由冻结" |
| `freeze_source_ticket_open` | P0-5 §1.6 | 任一 reconciliation_ticket OPEN/EXPIRED | "对账裁定中:{ticket_id} 等待用户三选一" |
| `freeze_source_circuit_breaker_cooldown` | P0-7 §1.3 | 熔断冷却 60min 期间(`circuit_breaker_state.cooldown_until` 未过)| "熔断冷却中:{cooldown_reason} 至 {cooldown_until}" |
| `freeze_source_data_quality` | P0-8 §1.5 | DataQualityState 4 阻断 breach 任一为真(quote_unavailable / staleness / divergence / minimum_freshness)| "数据质量降级:{breach_signal} 触发买卖类降级 HOLD" |
| `freeze_source_eod_pipeline_freeze` | P1-2.A §1.6 | eod_pipeline 失败 + 1 retry 后 freeze(`circuit_breaker_state.eod_pipeline_failed_at` 非空)| "EOD 流水线失败:{eod_pipeline_failed_at} 冻结次日买卖类" |

任一为真 → StatusBar 显示红色;**永禁聚合为单一 `frozen=true`**(5 状态独立并行查看,UI 层不允许聚合掩盖)。

#### 1.8.2 系统状态独立页

| 区块 | 数据源 | 展示 |
|------|--------|------|
| 5 冻结源详情 | GET /api/system-status/freeze-sources | 5 状态点当前状态 + 近 24h 变更时间轴 + decision metadata |
| 熔断状态 | GET /api/system-status/circuit-breaker | 当日触发记录 + cooldown_until + recent_failures |
| 长连接状态 | GET /api/system-status/longconn | lark-oapi 连接状态 + 心跳时间 + 重连历史 |
| LLM 路由健康度 | GET /api/system-status/llm-health | 4 必经 Agent 调用成功率 + 超时率 + tiered routing 命中分布 |
| 数据源状态 | GET /api/system-status/data-sources | adata / akshare / baostock / news 5 源 + watchlist_snapshot 30s cron 状态 |

### 1.9 三层决策拦截 reason — InstructionPlan 池详情抽屉三 tab

#### 1.9.1 抽屉结构

点击 InstructionPlan 列表行 → 抽屉(`PositionDetailDrawer.vue` 模式复用)→ 3 tab:

##### Tab 1 — Builder 早返(InstructionPlanBuilder 五道早返)

| 检查项 | 来源决策 | 通过条件 | 失败 reason 示例 |
|--------|---------|---------|------------------|
| `buy_sell_only` | P0-3 §1.2 | side ∈ {BUY, SELL, HOLD};HOLD 不路由 | "side=SHORT not in long-only set" |
| `strict_id_match` | P0-3 §1.1 | `instruction_id` 严格正则 33-34 字符 | "instruction_id format violation: missing prefix" |
| `valid_until_check` | P0-3 §1.4 | `valid_until > created_at` + 当日内 + ≤14:55 | "valid_until exceeds 14:55 cutoff" |
| `new_stock_exclusion` | P0-9 §1.3 | 上市 >30 个交易日 | "new stock exclusion: listed 25 trading days" |
| `liquidity_exclusion` | P0-9 §1.3 | 过去 20 交易日日均成交额 ≥2 亿 | "liquidity below 2 yi: 1.5 yi avg ADV" |
| `single_amount_cap_50000` | P0-7 §1.2 | 单笔 ≤5 万元(BUY 路径)| "single amount exceeds 50000: 60000 calculated" |

每检查项颜色标 ✅/❌ + 详细 reason 文本;六检查项独立 + 任一 ❌ 即降级 HOLD 不路由。

##### Tab 2 — RiskEngine 14-check

| Check ID | 名称 | 来源 | 展示字段 |
|---------|------|------|---------|
| 1-14 | (P0-7 §1.5 + 派生 amendment 逐项;含涨跌停 check 12)| RiskEngine | check_id / passed / reason / threshold / actual_value / blocked / soft_warning |

14 项逐项展示;任一 ❌ + REJECTED → 不路由飞书。

##### Tab 3 — MockBroker at-fill(仅 FILLED/REJECTED 终态)

| 字段 | 来源 | 展示 |
|------|------|------|
| 终态 | broker_event | FILLED / REJECTED |
| `fill_price` | P1-2.C §1.1 | 含滑点后实际成交价 |
| `cost_breakdown` | P1-2.C §1.4 | OrderCostBreakdown 拆解(commission / stamp_tax / slippage_cost / transfer_fee / total_cost)|
| `reason`(REJECTED 才有)| P1-2.C §1.1 | `price_limit_violation_at_fill` / `market_meta_unavailable_at_fill:{code}` / `slippage_bps_missing_for_board:{board}` / `unclassifiable_board_at_fill` |

**与 RiskEngine pre-route 的 reason 区分**:`price_limit_violation_at_fill`(MockBroker at-fill 二次防御)≠ `limit_up_block`/`limit_down_block`(RiskEngine pre-route 14-check check 12);两层 reason 不同便于 audit 区分两层拦截点(P1-2.C §2 红线 11)。

#### 1.9.2 跨层 audit 视图

抽屉顶部一行总结:`Builder ✅ / Engine ✅ / Broker ❌ price_limit_violation_at_fill` 直观展示三层拦截层级。

### 1.10 Performance.vue + 验收报告页分离

#### 1.10.1 Performance.vue(保留 + 数据源切换)

- **数据源**:`broker_snapshots` collection(EOD 全量快照)+ `equity_points` collection(30s intraday MTM)
- **API**:GET /api/portfolio/equity-curve(连续 equity_curve 折线 + per-position MTM 详情)+ GET /api/portfolio/drawdown(最大回撤曲线)+ GET /api/portfolio/benchmark-compare(对比 000300.SH)
- **展示**:
  - 折线图:连续 equity_curve(45 交易日窗口)+ 当日 30s 粒度 intraday MTM
  - 回撤图:历史最大回撤 + cooldown_until 标注
  - 资金分布饰环图:cash / 持仓市值占比
  - 对比基准:000300.SH 累计超额走势(P0-6 §1.3 沪深 300 累计超额 ≥0)
- **职责**:可视化绩效复盘

#### 1.10.2 验收报告页(独立新建)

- **数据源**:`acceptance_reports` collection(EOD 16:00:30 cron upsert,P0-6 §1.4)
- **API**:GET /api/acceptance-reports(列表)+ GET /api/acceptance-reports/{report_date}(详情)
- **展示**:
  - 45 交易日滚动窗口表格:per-day 5 稳定性 + 3 策略 + can_switch_to_feishu_on() 布尔
  - 5 项稳定性硬门槛 ± PASS/FAIL:
    - 指令完整率 ≥95%(`instruction_completeness_ratio`)
    - 回报解析准确率 ≥99%(`execution_report_parsing_accuracy`)
    - 数据缺失率 ≤1%(`data_missing_ratio`)
    - LLM 超时率 ≤5%(`llm_timeout_ratio`)
    - 信号生成成功率 ≥95%(`signal_generation_success_ratio`)
  - 3 项策略硬门槛 ± PASS/FAIL:
    - 最大回撤 ≤8%(`max_drawdown`)
    - 累计 PnL ≥0(`cumulative_pnl`)
    - 沪深 300(`000300.SH`)累计超额 ≥0(`benchmark_excess_return`,缺失基准时 =0 等价边界 PASS)
  - can_switch_to_feishu_on() 布尔:全 8 硬门槛 PASS 即 true
  - 7 项观察指标:不参与判断,仅展示参考(累计胜率 / 盈亏比 / 平均持仓周期 / 换手率 / 平均执行延迟 / 平均 LLM 调用成本 / Agent 辩论平均轮数)
- **职责**:决策表格输出 + 切换 feishu_on 前置校验依据

#### 1.10.3 分离动机

- 可视化(折线/回撤/饰环)与决策表格(PASS/FAIL/can_switch)是两种异构展示需求
- 合并到单页会形成双职责冲突 + 视觉层级混乱
- 用户在两个页面的使用场景不同:Performance 偏日常监控 / 验收报告偏切换决策时刻查阅

### 1.11 P1-5 暂不加本机认证(P1-6 处置)

- **P1-5 范围**:仅 UI 层 + API 协议
- **P1-6 范围**:认证 + 密钥轮换 + IP 白名单 + 审计日志
- **当前默认**:127.0.0.1 only(继承 P0-2 §2.5 + 全局 §2.10)
- **补充红线(P1-5 §2 红线 11)**:前端不允许存储任何凭证到 `localStorage` / `sessionStorage` / `cookie`
- **敏感配置展示**:全部末四位脱敏 + `webhook_configured` 布尔(继承 P0-2 §2.5 + 全局 §2.10)
- **Vite 配置**:`vite.config.ts` 默认 `host: '127.0.0.1'`(实施期 D 任务约束;不允许 `host: '0.0.0.0'`)

## 2. 红线(P1-5)

> 以下条款一律以 P1-5 决策为准。**违反即视为红线违规**;实施期 grep / lint rule 应自动检测违规。

1. **MVP 7 页 + Phase B 4 页清单永锁**:Dashboard / 系统状态 / InstructionPlan 池 / Portfolio / 用户回报录入 / 对账裁定 / 验收报告(MVP)+ Agent 辩论 / 数据质量 / 飞书消息历史 / 成本拆解(Phase B);任何新增/删除/合并必走 `P1-5-amendment-{date}-{原因}.md`。Simulation.vue 保留为 P1-5 范围外。

2. **`backend/api/{risk,watchlist,llm,agents}*.py` 仅 GET 永锁**:任何 POST / PUT / PATCH / DELETE handler 红线违规;Phase A 一次性破坏式删除(继承 P0-7 §2 红线 15 + P0-9 §2 红线 12 + P0-10 §2 红线 5)。lint rule grep 应全空:`grep -rnE "@router\.(post|put|patch|delete)" backend/api/{risk,watchlist,llm,agents}*.py`。

3. **`backend/api/settings/*.py` POST 永锁删除**:`llm-config` / `mirofish` / `data-sources/test` POST handler Phase A 删除;agent_models.yaml + DataSources 配置 + MiroFish 配置 runtime 不可改 + 必须改 YAML+重启(继承 P0-10 §1.4 + P0-7 §2 红线 14)。

4. **`backend/api/trading/*.py` 写入 handler 永锁删除**:`approve` / `reject` / `cancel` POST handler Phase A 删除;无审批环节(simulation_auto 自动执行 + feishu_interactive 走飞书);订单生命周期由系统管理 + valid_until 自动 EXPIRED(继承 P0-3 §1.4 + P0-4 §1.5)。

5. **仅允许 2 个前端写入端点**:① POST `/api/execution-reports`(用户回报录入)② POST `/api/reconciliation-tickets/{ticket_id}/decide`(对账三选一裁定);其他全部禁写。任何新写入需求必走 `P1-5-amendment` + 决策对齐再加。

6. **WebSocket 单通道 `/ws/market` 12 类消息永锁**:不引入额外长连接;新增消息类型必走 P1-5-amendment。SSE `/api/analysis/stream` 仅保留 LLM 流式输出场景(Agent 辩论可视化 Phase B);不扩展业务事件 SSE。

7. **WS 删除 `auth_mode_change`(模式切换 ≠ flag toggle,通过页面跳转通知)+ 删除 `approval_update`(ApprovalQueue 废弃)**:对应消息处理代码实施期 Phase A 一并清理。

8. **用户回报双路径同一入口**:飞书主路径 + 前端备用路径必须走同一 ExecutionReportApplier 入口;前端 JS 正则镜像与后端 `backend/execution/regex_patterns.py` 单一真相源保持一致(实施期 D 任务约束 + 单元测试断言);镜像不一致即前端 fail-closed 阻止提交。

9. **5 种买卖类路由冻结源全局 StatusBar 5 独立状态点 + 独立系统状态页**:永禁聚合为单一 `frozen=true`;5 来源(switch / ticket_open / circuit_breaker_cooldown / data_quality / eod_pipeline_freeze)独立并行查看(继承 P0-1 + P0-5 + P0-7 + P0-8 + P1-2.A 五种独立来源底层判断)。

10. **三层决策拦截 reason 在 InstructionPlan 池详情抽屉三 tab 永锁分离**:Tab 1 Builder 早返 / Tab 2 RiskEngine 14-check / Tab 3 MockBroker at-fill;严禁合并平展为单一 reason 列(违反 P1-2.C §2 红线 11)。三层 reason 命名空间不同(`reason='price_limit_violation_at_fill'` ≠ `'limit_up_block'`/`'limit_down_block'`)便于 audit 区分。

11. **P1-5 暂不加本机认证;前端不允许存储任何凭证到 `localStorage` / `sessionStorage` / `cookie`**:防浏览器截图 + 历史记录 + 第三方扩展泄露;敏感配置展示一律末四位脱敏 + `webhook_configured` 布尔(继承 P0-2 §2.5 + 全局 §2.10);Vite 默认 `host: '127.0.0.1'` 不允许 `'0.0.0.0'`(实施期 D 任务约束)。

12. **Performance.vue + 验收报告页分离永锁**:不合并;Performance 数据源 broker_snapshots + equity_points(可视化职责)/ 验收报告数据源 acceptance_reports(决策表格职责);两页职责异构。

13. **Simulation.vue 保留为 P1-5 范围外**:P1-5 阶段不改造不投入;后续阶段(P2 或独立"展示"决策)再细致打算;P1-5 菜单不展示(避免误导);保留代码不冲突 P0-9 §2 "MiroFish 加分非核心" + P0-8 §2 "MiroFish 仅入 evidence_collection"。

14. **前端密钥永远末四位脱敏 + `webhook_configured` 布尔**:LLM key + 飞书凭证(`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_VERIFY_TOKEN` / `FEISHU_ENCRYPT_KEY` / `FEISHU_CUSTOM_BOT_WEBHOOK_URL` / `FEISHU_CUSTOM_BOT_SIGN_SECRET`)前端展示一律末四位脱敏(`****1234`)+ 走 webhook_configured 布尔显示是否配置;严禁前端展示完整密钥(继承 P0-2 §2.5 + 全局 §2.10)。

15. **前端永禁直接调用 LLM**:所有 LLM 调用经后端 API 中转;前端零 LLM SDK / 零 LLM API Key(继承 P0-10 §2 红线 1)。

16. **前端永禁 HTTPS 入站接收飞书事件**:飞书事件订阅走后端 lark-oapi 长连接(单实例,3s 内 ack);备用 webhook 仅可发系统告警绝不发买卖指令(继承 P0-2 §2 红线 1 + §2.5)。

17. **前端 ApprovalQueue.vue + RiskCenter 写入按钮 + Settings 写入按钮 + auth-mode 切换器实施期 Phase A 一次性破坏式删除**:删除节奏与 P0-1 §2 红线 6 旧 AUTHORIZATION_MODE 矩阵删除节奏对齐;实施期 grep 全空:`grep -rn "ApprovalQueue\|auth-mode\|/api/risk/config\|/api/settings/llm-config\|/api/trading/approve\|/api/trading/reject" frontend/src/`。

18. **新增 backend/api 路由表为 GET only 端点扩展**:`backend/api/instruction_plans.py` / `backend/api/acceptance_reports.py` / `backend/api/reconciliation_tickets.py`(GET 列表 + GET 详情)/ `backend/api/system_status.py` / `backend/api/feishu_messages.py` / `backend/api/cost_breakdown.py` / `backend/api/data_quality.py` / `backend/api/equity_curve.py` 仅 GET handler;唯二例外见 §2 红线 5(继承 P0-7 §2 红线 15 + P0-10 §2 红线 5)。

## 3. 实施期任务清单

> P1-5 决策完成不等于实施落地。本节列出从决策锁定到代码合并的具体动作清单,Phase A 与 P0-1 + P0-7 + P0-9 + P0-10 写入接口删除节奏并入,Phase B 落地 MVP 7 页 + 收尾再做 Phase B 4 页。**任何遗漏会让本决策只是文字游戏**。

### Phase A — 写入接口收口(与 P0-1/P0-7/P0-9/P0-10 + 本决策合并执行)

- **E-001** 删除 `backend/api/risk*.py` 所有 POST/PUT/PATCH/DELETE handler;更新 lint rule grep 检查 `grep -rnE "@router\.(post|put|patch|delete)" backend/api/risk*.py` 必空(继承 §2 红线 2)
- **E-002** 删除 `backend/api/watchlist*.py` 所有 POST/PUT/PATCH/DELETE handler 包括 deprecated `add_stock` / `remove_stock` / `clear`;lint rule grep 必空(继承 §2 红线 2)
- **E-003** 删除 `backend/api/llm*.py` 所有 POST/PUT/PATCH/DELETE handler;lint rule grep 必空(继承 §2 红线 2)
- **E-004** 删除 `backend/api/agents*.py` 所有 POST/PUT/PATCH/DELETE handler;lint rule grep 必空(继承 §2 红线 2)
- **E-005** 删除 `backend/api/settings/*.py` POST handler `llm-config` / `mirofish` / `data-sources/test`;保留 GET handler 作只读展示用(§2 红线 3)
- **E-006** 删除 `backend/api/trading/*.py` POST handler `approve` / `reject` / `cancel`;`approve` / `reject` 路由表整个移除(§2 红线 4)
- **E-007** 删除 `backend/api/analysis/*.py` POST handler `stock` / `jobs`;改为后端定时触发(slow_pipeline 09:00 + fast_pipeline 09/11/13/15)+ 前端不再调用
- **E-008** 前端 `RiskCenter.vue` 移除 RiskConfig POST 调用 + auth-mode 切换器 + 切换 axios 调用;改为只读字段展示 + "改 YAML+重启"文案(§1.5.1)
- **E-009** 前端 `frontend/src/views/settings/LLMRouter.vue` + `DataSources.vue` + `MiroFishConfig.vue` 移除所有 POST 按钮 + axios 调用(§1.5.1)
- **E-010** 前端 `frontend/src/components/trading/ApprovalQueue.vue` 整个组件删除 + Portfolio.vue 引用清理(§2 红线 17)
- **E-011** 前端 `frontend/src/components/trading/OrderList.vue` 移除取消按钮 + cancel axios 调用(§1.5.1)
- **E-012** 前端 `frontend/src/views/AgentDebate.vue` 移除 `POST /api/analysis/stock` + `POST /api/analysis/jobs` axios 调用(§1.5.1)
- **E-013** 前端 useWebSocket.ts 移除 `auth_mode_change` + `approval_update` 消息类型处理(§2 红线 7)

### Phase B — MVP 7 页落地(P1-2.A/B/C 数据 schema 全量落地后启动)

- **E-014** 新建 `backend/api/instruction_plans.py`:GET 列表(分页 + 状态过滤)+ GET 详情(含三层 reason)+ WS `instruction_plan_update` 推送(§1.6.1 + §1.9)
- **E-015** 新建 `backend/api/acceptance_reports.py`:GET 列表(45 交易日窗口)+ GET 详情(`/{report_date}`)+ WS `acceptance_report_ready` 推送(§1.10.2)
- **E-016** 新建 `backend/api/reconciliation_tickets.py`:GET 列表 + GET 详情 + POST `/{ticket_id}/decide`(三选一裁定)+ WS `ticket_update` 推送(§1.5.3 + §2 红线 5)
- **E-017** 新建 `backend/api/execution_reports.py`:POST 录入(走 ExecutionReportApplier 同一入口)+ GET 历史(§1.7.2 + §2 红线 5)
- **E-018** 新建 `backend/api/system_status.py`:GET `/freeze-sources` / `/circuit-breaker` / `/longconn` / `/llm-health` / `/data-sources` 子路由 + WS `freeze_source_update` 推送(§1.8)
- **E-019** 新建 `backend/api/portfolio.py`:GET `/equity-curve` / `/drawdown` / `/benchmark-compare` 子路由(数据源 broker_snapshots + equity_points)+ WS `equity_point_update` 推送(§1.10.1)
- **E-020** 抽出 `backend/execution/regex_patterns.py` 为单一真相源:5 种回报正则常量(继承 P0-4);提供 GET `/api/execution-reports/regex-patterns` 让前端运行时获取(避免构建时代码生成耦合)(§1.7.3)
- **E-021** 前端新增 7 MVP 页:`frontend/src/views/InstructionPool.vue` / `SystemStatus.vue` / `ExecutionReportInput.vue` / `ReconciliationDecision.vue` / `AcceptanceReport.vue`(Dashboard.vue + Portfolio.vue 复用扩展)
- **E-022** 前端 `useWebSocket.ts` 扩展 12 类消息类型定义 + 后端 ws_manager.py 推送对接(§1.6.1)
- **E-023** 前端 `frontend/src/components/common/StatusBar.vue` 扩展为 5 冻结源独立状态点(red dot + tooltip)(§1.8.1)
- **E-024** 前端 `frontend/src/components/instruction/ThreeLayerReasonDrawer.vue`:抽屉 3 tab(Builder / Engine / Broker)(§1.9.1)
- **E-025** 前端 `frontend/src/utils/regex_mirror.ts`:JS 正则镜像 + 单元测试断言与后端一致;运行时 GET `/api/execution-reports/regex-patterns` 加载;镜像不一致即 fail-closed(§1.7.3 + §2 红线 8)
- **E-026** 前端 `frontend/src/router/index.ts` 重构一级菜单按决策闭环 4 分组(运行状态 / 决策与指令 / 账本与成交 / 复盘与验收)+ 设置只读折叠;Simulation.vue 不进菜单(§1.4)
- **E-027** 前端 `vite.config.ts` 默认 `host: '127.0.0.1'`(不允许 `'0.0.0.0'`)(§2 红线 11)
- **E-028** 前端 `frontend/src/components/common/StatusBar.vue` + 全 axios 调用清查 + 移除任何 localStorage/sessionStorage/cookie 凭证存储(§2 红线 11)

### Phase B 收尾 — 4 页补建

- **E-029** Agent 辩论可视化页:复用 `frontend/src/views/AgentDebate.vue` + `DebatePanel.vue` + `DebateTimeline.vue`;数据源切换 SSE `/api/analysis/stream` LLM 流式输出
- **E-030** 数据质量面板:新建 `frontend/src/views/DataQualityDashboard.vue` + 后端 `backend/api/data_quality.py`(GET);数据源 DataQualityProvider per-stock evaluate 历史(§1.2 ⑨)
- **E-031** 飞书消息历史:新建 `frontend/src/views/FeishuMessageHistory.vue` + 后端 `backend/api/feishu_messages.py`(GET);数据源 `feishu_messages` collection;tenant_access_token 状态末四位脱敏(§2 红线 14)
- **E-032** 成本拆解面板:新建 `frontend/src/views/CostBreakdown.vue` + 后端 `backend/api/cost_breakdown.py`(GET);数据源每日 LLM/数据/运维分类成本 + ¥20 hard ceiling 监控

### 测试覆盖要求(继承全局 §2.10)

- 后端新增 API:覆盖率 >70%(非 risk 模块);测试断言 GET only(grep `@router.(post|put|patch|delete)` 必空)
- 前端新增组件:Vitest + Vue Test Utils + Playwright E2E;覆盖率 >70%
- 端到端断言:① 模式切换冻结 → StatusBar `freeze_source_switch` 红 ② ticket OPEN → StatusBar `freeze_source_ticket_open` 红 ③ 熔断冷却 → StatusBar `freeze_source_circuit_breaker_cooldown` 红 ④ DataQualityState 阻断 → StatusBar `freeze_source_data_quality` 红 ⑤ EOD 失败 → StatusBar `freeze_source_eod_pipeline_freeze` 红
- 前端 JS 正则镜像与后端一致性测试:`test_frontend_backend_regex_consistency.py`(后端)+ `regex_mirror.spec.ts`(前端);镜像不一致即 fail-closed 阻止提交

### Codex review hard gate(major 5 轮 R1-R5)

P1-5 涉及前端架构 + API 协议 + 红线收口,major 级别,实施期 5 轮 codex review:

- R1 — Architecture review(MVP 7 页 + Phase B 4 页 + 一级菜单分组合理性 + WS 单通道 12 类消息架构)
- R2 — Security review(写入接口收口完备性 + 仅 2 写入端点 + 前端凭证存储红线)
- R3 — Implementation review(E-001~E-032 任务清单与代码 diff 一致性)
- R4 — SDK & dependency review(Vue 3.4 + Pinia 2.1 + Element Plus 2.4 + ECharts 5.4 兼容性 + WS 重连逻辑 + SSE 兼容性)
- R5 — Final review(red lines 18 条全覆盖 + 决策依据完整性)

输出存 `docs/reviews/p1-5-r{N}-{topic}.md`;触发前 `git pull` 同步 `LanEinstein/CCodexSkill`(继承 §2.10)。

## 4. 决策依据

### 4.1 用户对齐(2026-05-10 P1-5 决策对齐 3 轮 10 议题)

第一轮 4 议题(全部对齐推荐):
- Q1 MVP 范围 → MVP 7 页 + Phase B 收尾再补 4 页 ✅
- Q2 写入接口处置 → 前端立即下线 + 后端 Phase A 一次性破坏式删除 ✅
- Q3 实时通道 → WebSocket 单通道扩展消息类型 ✅
- Q4 用户回报路径 → 飞书主路径 + 前端备用录入页 ✅

第二轮 4 议题(3 对齐 + 1 细化):
- Q5 Simulation.vue 处置 → **用户细化**:保留为 MiroFish 多 Agent 演化可视化 P1-5 范围外,后续阶段细化(原始推荐"废弃")
- Q6 5 冻结源展示 → 全局 StatusBar 顶部常驻 + 独立系统状态页详情 ✅
- Q7 三层 reason 展示位置 → InstructionPlan 池详情抽屉三 tab ✅
- Q8 本机认证 → P1-5 先不加,P1-6 统一处置 ✅

第三轮 2 议题(全部对齐推荐):
- Q9 Performance vs 验收报告 → 分离两页职责不同 ✅
- Q10 一级菜单分组 → 决策闭环 4 分组 ✅

### 4.2 关键判断

- **MVP 7 页基于"决策闭环最小可验证子集"原则**:simulation_auto 跑通(Dashboard + InstructionPlan 池 + Portfolio + 验收报告)+ feishu_interactive 切换前置校验(验收报告 + 系统状态)+ 系统状态可观测(系统状态)+ 用户回报降级路径完整(用户回报录入 + 对账裁定);Phase B 4 页是"高级功能",非必经
- **写入接口立即下线 + Phase A 一次性破坏式删除符合 P0-1 旧 AUTHORIZATION_MODE 矩阵删除节奏**:不留兼容期 + lint rule grep 必空 + 一次性收口避免长尾迁移
- **WS 单通道扩展符合 lark-oapi 长连接架构同构**:后端长连接 1 + 前端长连接 1(WS)架构对称;SSE 仅保留 LLM 流式专用;避免 WS+SSE 双通道造成的浏览器并发连接限制 + 重连逻辑重复
- **用户回报飞书主 + 前端备用符合 4h 长连接故障窗口期降级路径**:lark-oapi 长连接断流时(P0-6 §1.5 系统级中断重置触发条件之一)用户仍可通过前端备用通道补录,避免冻结超 4h
- **5 冻结源 StatusBar + 独立页符合多增点设计**:5 状态独立并行查看 ≠ 聚合为单一 frozen=true;UI 层不允许聚合掩盖底层独立判断;独立系统状态页提供 audit 详情
- **三层 reason 抽屉三 tab 符合 audit 低频高准确度需求**:audit 不在主线高频路径,详情抽屉集中入口符合;独立 audit 页会形成多入口冲突
- **本机认证 P1-6 处置符合范围职责划分**:P1-5 仅 UI 层 + API 协议;认证、密钥、IP、审计统一在 P1-6 处理;避免 P1-5 范围越界 + P1-6 重复实施
- **Performance / 验收分离符合可视化 vs 决策表格双职责**:折线/回撤/饰环 vs PASS/FAIL/can_switch 异构展示;合并到单页双职责冲突 + 视觉层级混乱
- **决策闭环 4 分组符合用户使用路径同构**:watchlist → 分析 → 指令 → 风控 → 执行 → 回报 → 复盘 = 运行状态 → 决策与指令 → 账本与成交 → 复盘与验收;菜单路径 = 用户使用路径
- **Simulation.vue 保留为后续阶段(用户细化指令)**:MiroFish 多 Agent 独立思考演化过程可视化在融资展示场景具有价值;P1-5 阶段不重点投入避免分散精力;后续阶段(P2 或独立"展示"决策)再细致打算

### 4.3 排除选项

- **MVP 11 页一次性全上**:Phase B 落地周期翻倍 + 部分页面(Agent 辩论 / 数据质量)在 P1-2.A/B/C 数据 schema 落地前无数据可展示
- **写入接口保留 / 兼容期**:违反 P0-7 §2 红线 15 + P0-9 §2 红线 12 + P0-10 §2 红线 5 多条 GET only 红线;且与 P0-1 删除节奏不一致
- **WS+SSE 分通道(WS 行情 + SSE 业务事件)**:浏览器单域并发连接限制(默认 6)+ 双重连逻辑维护成本 + 协议各司其职意义不大
- **全 SSE / 全轮询**:全 SSE 无双向 ack;全轮询延迟高 + 服务器负载高 + 严重违反实时性预期
- **前端主回报路径**:违反 P0-2 §1.3 + P0-3 §1.5 飞书核心交互闭环定位;PC 前不便切手机时机次于飞书移动场景
- **StatusBar 单点聚合**:掩盖 5 状态独立性;预警都被压成一点;audit 体验下降
- **三层 reason 合并平展**:违反 P1-2.C §2 红线 11 "三层 reason 命名空间不同便于 audit";丢失拦截层级信息
- **P1-5 加本机认证**:范围越界 P1-6;且 P1-6 可能调整认证方案,导致重复实施
- **Performance.vue 废弃合并到验收页**:页面恶心仓量改变 + 单页双职责冲突
- **MVP 4 页极小集**:无验收报告页 simulation_auto → feishu_interactive 切换前置校验在 UI 层缺失,只能命令行查 acceptance_reports collection,体验大幅下降
- **Simulation.vue 完全废弃**:违背用户细化指令(MiroFish 多 Agent 演化可视化在融资展示具有价值)

### 4.4 与 P0/P1-2.A/B/C 红线协同

- 继承 P0-1 §1.3 模式切换 = 账户生命周期事件 → WS 删除 `auth_mode_change`(§2 红线 7)
- 继承 P0-2 §1.2 永禁 HTTPS 入站 + §2.5 备用 webhook 仅告警 → 前端永禁 HTTPS 入站接收飞书事件(§2 红线 16)
- 继承 P0-3 §1.1 instruction_id 严格正则 + §1.4 valid_until → InstructionPlan 池详情抽屉 Tab 1 Builder 早返展示这些检查项(§1.9.1)
- 继承 P0-4 §1.5 严格正则 only + §3.1 ExecutionReportApplier 单一入口 → 用户回报双路径同一入口 + 前端 JS 正则镜像与后端一致(§1.7.3 + §2 红线 8)
- 继承 P0-5 §1.5 reconciliation_ticket 三选一裁定 + §1.6 OPEN/EXPIRED 冻结路由 → 对账裁定页 + StatusBar `freeze_source_ticket_open`(§1.8.1)
- 继承 P0-6 §1.3 acceptance_reports collection + §1.4 can_switch_to_feishu_on() → 验收报告页(§1.10.2)
- 继承 P0-7 §2 红线 14 RiskConfig runtime 不可改 + §2 红线 15 backend/api/* 仅 GET → 写入接口收口(§1.5)
- 继承 P0-8 §1.4 5 类 evidence_id 前缀 + §2 红线 7 MiroFish 仅入 evidence_collection → InstructionPlan 池详情 MIROFISH- 前缀 evidence 折叠面板(§1.1 ③)
- 继承 P0-9 §2 红线 12 backend/api/watchlist*.py 仅 GET + §2 红线 13 long-only → 写入接口收口 + InstructionPlan 池仅 BUY/SELL/HOLD 三态过滤(§1.5.2)
- 继承 P0-10 §2 红线 1 字段权限矩阵 + §2 红线 5 backend/api/{llm,agents}*.py 仅 GET → 写入接口收口(§1.5.2)
- 继承 P1-2.A §1.6.2 EOD chain freeze 第五种来源 eod_pipeline_freeze + §1.4 broker_events / broker_snapshots → StatusBar `freeze_source_eod_pipeline_freeze` + WS `broker_event` 推送(§1.8.1 + §1.6.1)
- 继承 P1-2.B §1.4 EquityPoint per-position MTM + §1.7 DataQualityProvider per-stock evaluate → Performance.vue per-position 展示 + 数据质量面板(§1.10.1 + §1.2 ⑨)
- 继承 P1-2.C §1.1 三层 reason 区分 + §1.4 OrderCostBreakdown 结构 → InstructionPlan 池详情抽屉三 tab + Portfolio 成交历史成本拆解(§1.9 + §1.1 ④)

## 5. 后续动作

### 5.1 SSoT 文档同步

- 更新 `docs/quantmind_owner_decision_points_2026-05-07.md` §P1-5:标 ✅ + 链接本决策文档
- 新建 memory 文件 `/home/ps/.claude/projects/-home-ps-papers-QuantMind/memory/project_p1_5_frontend_workflow.md`
- 更新 `MEMORY.md` 索引:加 P1-5 锁定 entry
- 更新 `CLAUDE.md` §2 加 P1-5 红线节(简化版,详细规约在本决策 §2)

### 5.2 派生 amendment(若有)

无;本决策不修改既有 P0/P1-2.A/B/C 决策的硬约束。新增 backend/api 路由表为 GET only 端点扩展属非破坏式落地。Simulation.vue 保留属 P1-5 自身约束。

### 5.3 下一站

- **P1-6**:secrets 轮换 + IP 白名单 + 审计日志(本决策已铺垫:前端不允许存储任何凭证 + 默认 127.0.0.1 only + 末四位脱敏)
- **P1-7**:预算扩(从单 ¥20/日 hard 到分类 LLM/数据/运维 + 月预算 + 告警阈值;本决策铺垫:成本拆解面板 Phase B 收尾页)
- **P1-3 / P1-4**:已由 P0-3 + P0-4 累积锁定,无独立决策;本决策与之协同(InstructionPlan 池详情 + 用户回报录入路径)

### 5.4 实施期启动条件

- P1 全锁(P1-5 + P1-6 + P1-7 完成)→ 启动实施期 Phase A(代码迁移)+ Phase B(数据 schema 落地)
- Phase A 与 P0-1 旧 AUTHORIZATION_MODE 矩阵删除 + P0-7/9/10 GET only 收口合并执行
- Phase B 在 P1-2.A/B/C 数据 schema 全量落地 + Phase A 代码清理后启动

### 5.5 本决策不做的事

- 不锁定 secrets 轮换机制(P1-6)
- 不锁定 IP 白名单 + 审计日志(P1-6)
- 不锁定预算扩展(P1-7)
- 不锁定 Simulation.vue 重构方案(后续阶段)
- 不锁定移动端原生 App / 远程跨网访问(P2-3)
- 不锁定多用户权限分级(单用户路线)
- 不锁定双语国际化 / 主题切换(默认中文 + 浅色)

---

**P1-5 决策对齐完成 ✅**

P1 决策对齐路径 A 第四份决策锁定;P1-5 = 前端工作流 MVP 7 页 + Phase B 4 页 + 决策闭环 4 分组 + 写入接口立即下线 + Phase A 一次性破坏式删除 + WS 单通道 12 类消息扩展 + 飞书主路径 + 前端备用录入 + 5 冻结源全局 StatusBar + 独立系统状态页 + 三层 reason 抽屉三 tab + Performance + 验收报告分离 + P1-5 暂不加本机认证 + Simulation.vue 保留为 P1-5 范围外。

下一站:P1-6(secrets 轮换 + IP 白名单 + 审计日志)。
