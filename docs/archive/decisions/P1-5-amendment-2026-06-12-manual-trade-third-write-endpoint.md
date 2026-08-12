# P1-5 amendment(2026-06-12):第 3 写端点 — 用户自主操作记录(manual trades)

> 状态:**已锁定**(owner 2026-06-12 AskUserQuestion 拍板:"新增第 3 写端点")。
> 推翻对象:P1-5"全后端**仅 2 写端点**"→ **仅 3 写端点(枚举锁定)**:`POST /api/execution-reports` + `POST /api/reconciliation-tickets/{id}/decide` + **`POST /api/manual-trades`(新)**。再加写端点仍需 amendment。
> 不动:前端其余全只读;127.0.0.1;飞书消息必经 renderer;LLM 不写决策;MockBroker 单一镜像;append-only。
> 设计依据:dossier 2026-06-12 §3.2 + codex P0-6/P0-7/P1-7。

## 1. 决策(锁定)

### 1.1 域模型:ExternalExecutionEvent(独立域,绝不伪造 InstructionPlan)
- 字段:`external_trade_id`(`UT-{YYYYMMDD}-{seq}` 独立前缀,与 `QM-` 正则空间不相交)、code、side(BUY/SELL)、volume(100 整手)、price、executed_at、`reason` 枚举(USER_TAKE_PROFIT / USER_STOP_LOSS / USER_ADD / USER_OTHER)、note(自由文本,display-only)、`origin=USER_DISCRETIONARY`、可选 `related_instruction_id`(若是对系统建议的偏离执行)。
- **严禁**进 instruction_plans 集合 / InstructionPlan 状态机 / 决策账本;不计入指令完整率等 acceptance 稳定性指标分母。

### 1.2 端点与 applier
- `POST /api/manual-trades`:Pydantic strict + `extra="forbid"`;服务端校验 = 100 整手 + SELL 可卖量 clamp(T+1 available_volume)+ BUY 现金充足 + 交易日/时段合理性;**仅 feishu_interactive 模式接受**(纯 sim 模式 403——模拟盘全自动无人工操作)。
- 经独立 `ManualTradeApplier` 进 MockBroker 镜像(复用 `apply_external_fill` 语义,append-only broker_event,幂等键 external_trade_id);对账三方可见。

### 1.3 飞书同步("已记录"语义;codex P1-7)
- applier 成功后经 renderer 渲染"**【QuantMind 已记录-用户自主操作】**"消息发决策群:无 QM- 指令号、无成交指令动词、对抗 `parse_execution_report` 必 no_pattern_match;幂等 outbox;失败 fail-open 不回滚已落账(沿用 U-D12 ack 先例)。

### 1.4 绩效三分流(codex P0-7,硬要求)
- 全部成交按 `origin` 分流:`system_suggested` / `user_discretionary` / `reconciliation_reset`;
- **实盘就绪度、acceptance 策略 3 门、自进化评分只读 system_suggested 段**——用户 alpha 永不计入系统能力证据;
- 前端绩效页提供合并/分流双视图。

### 1.5 前端表单与按钮逻辑
- 仅 feishu_interactive 模式渲染入口;持仓行内"记录卖出"按钮预填 code/可卖量;建议卡"已按建议执行"(走既有 execution-report 路径)与"已自主调整"(走 manual-trades,带 related_instruction_id)两路分明;两步确认;偏离建议数量落 deviation 日志(沿用 P0-4 report-is-truth 先例)。

## 2. 实施
Phase AD(AD-005);前端 JS 校验与后端 schema 镜像测试(仿 regex 镜像先例)。redline 扫描 `@router.(post|put|patch|delete)` 允许集 2→3。
