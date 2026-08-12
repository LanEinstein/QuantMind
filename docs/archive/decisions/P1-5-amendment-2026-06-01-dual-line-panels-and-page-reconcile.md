# P1-5 修订 — 2026-06-01 双线可视化(现有页内 panel)+ 如实承认页锁/WS 漂移(方向④)

> **修订基准**: [P1-5 前端工作流](./P1-5-frontend-workflow-mvp-7-pages-readonly-first-write-strict-bounded.md)
> **关联**: CLAUDE.md §2.11(前端红线)/ R0(双线)/ P0-8-amendment-2026-06-01(主题层 viz)/ P0-10-amendment-line2-2026-06-01(thesis viz)/ P0-7-amendment-2026-06-01(5 槽轮动 viz)
> **修订日期**: 2026-06-01(规划 session #60)
> **决策人**: owner(AskUserQuestion 2026-06-01:方向④ 答「panel 内嵌 + 如实修订页锁」)
> **性质**: 决策边界锁定;本 session 不写代码,代码在 plan.html Phase Z 实施。

## 0. 触发与意图
Owner 强调双线系统前端「也很重要」:需产业链倒推推理可视化、持仓 thesis 追踪 + 长持 vs 止盈面板、5 槽组合 + 换仓视图、双线每日并行运行态、三层 reason 抽屉延展。

**漂移事实**(grounding 核实):P1-5/CLAUDE.md §2.11 写「MVP 7 + Phase B 4 = 11 页」+「WS 12 类」;**实建 = 13 页**(4 决策闭环分组内多出 `RiskCenter` 等,均为正当只读页)+ **14 WS 类**(原 6 保留 + 新 8 = 14;删的 2 个 `auth_mode_change`/`approval_update` 是**永禁**非「12」的减项,doc「8−2」措辞混淆了并集大小)。

## 1. 决策

### 1.1 如实承认页锁/WS 漂移(owner 选 A:不收敛回 11,如实入账)
- **页锁 11 → 实际 13**:逐页枚举入账为正当锁定集(运行状态 2:Dashboard / SystemStatus;决策与指令 1:InstructionPlans;账本与成交 3:Portfolio / ExecutionReportEntry / ReconciliationCenter;复盘与验收 7:Performance / AcceptanceReports / RiskCenter / AgentDebate / DataQuality / FeishuMessages / CostBreakdown)。`Simulation.vue` 仍 `meta.hidden`(不进菜单,P1-5 范围外不变)。Settings 4 只读子页不计入决策闭环页锁。**本 amendment 后页锁基线 = 实际枚举集**;再加**顶级页**仍需新 amendment(纪律不松)。
- **WS 12 → 实际 14**:枚举锁定 14 类(6 保留 `index_update/signal/news/status/position_update/circuit_breaker_update` + 8 新 `instruction_plan_update/broker_event/equity_point_update/data_quality_breach/freeze_source_update/ticket_update/acceptance_report_ready/feishu_message_received`);`auth_mode_change`/`approval_update` **永禁**不变。前端/后端 `FORBIDDEN_WS_*` 不变。

### 1.2 双线新可视化 = 现有页内 panel/tab/抽屉(不开顶级页)
- **产业链倒推可解释链路**(趋势→板块→链→卡脖子环节→标的;方向①):进 `InstructionPlans` 详情抽屉新 tab 或现有页内 panel;读 pin 的主题候选 artifact + `THEME-` evidence(display-only)。
- **持仓 thesis 追踪 + 长持 vs 止盈面板**(方向②):进 `Portfolio` 页 panel;读 PositionThesis 读模型 + advisory 复盘 evidence(display-only,不可解析)。
- **5 槽组合 + 换仓视图**(方向③):进 `Portfolio` 页 panel;读 RotationIntent 读模型 + 5 槽占用 + 在位/挑战分。
- **双线每日并行运行态**(编排):进 `Dashboard` / `SystemStatus` panel。
- **三层 reason 抽屉延展**:沿用 Builder/Engine/Broker 三 tab 命名空间区分,不新增写交互。

### 1.3 WS 新类 / 推送
thesis-health / rotation 等新推送**优先复用现有 14 类或用轮询(polling)**;**新增 WS 类须本 amendment 之外再写 amendment**(或在本 amendment §1.1 枚举集内显式扩并说明,实施期定)。MVP 阶段倾向轮询,避免无谓扩 WS 协议。

## 2. 落地(plan.html Phase Z;实施前本 amendment 是门)
- 先做 doc/代码漂移 reconcile(plan.html + CLAUDE.md §2.11 页锁/WS 数字如实更新到枚举集)——**轻量、早做**。
- 4 组 panel/tab 实现(全只读;读各方向后端稳定读模型后做)+ ECharts 可视化(产业链有向图 / thesis 健康度时间线 / 5 槽轮动)+ vitest + type-check + build 绿。
- 建议调 `frontend-design` skill 出页面/组件级现代金融投研设计。

## 3. 不变量(本 amendment 不触碰)
- **仅 2 写端点**(`POST /api/execution-reports` + `POST /api/reconciliation-tickets/{id}/decide`)不变;新 viz **全只读**,**严禁**加写端点;`backend/api/{risk,watchlist,llm,agents,cost}*.py` 仍仅 GET。
- 前端**不存任何凭证**到 localStorage/sessionStorage/cookie(UI 偏好不违规)。
- 全层 **127.0.0.1 only** + Vite host 127.0.0.1 不变。
- 前端回报解析正则**镜像** `backend/execution/regex_patterns.py` 单一真相源 + fail-closed 不变(新 viz 不碰回报解析)。
- 5 冻结源 StatusBar 独立不聚合 + 三层 reason 抽屉命名空间区分不变。
- SSE 仅 LLM 流式不变;display-only 飞书/前端文本对抗 `parse_execution_report` 必 `no_pattern_match`。
