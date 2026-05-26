# U-D1 Codex 跨模型代码审查报告

**任务**: U-D1 — Line-2 生产编排上线(调度接线 + 节假日/盘中 gating + lifespan + 100k;Line-1 拆 U-D1b)
**审查时间**: 2026-05-26
**模型**: Claude Code (实现/修复) + Codex CLI (审查)
**审查轮次**: codex 1 cycle (`codex review --uncommitted`) + codex verify-1 (`codex exec`) + claude `/code-review`(high,3-angle;codex verify-2 遇额度限,owner 指定改用 claude 自审技能)
**最终判定**: ✅ 通过(codex 4 findings + 1 回归 全修;claude /code-review 余项均为已记录 U-D3 接缝,U-D1 baseline 零暴露)

---

## 变更范围

| 文件 | 内容 |
|------|------|
| `backend/broker/scheduler.py` | +2 注入式 cron(`line2_daily_runner` 09:35 + `line2_intraday_runner` 30s);`advance_day` 节假日 gating(`is_trading_day`);`initial_capital` 默认 1M→100k |
| `backend/services/line2_context_providers.py`(新) | 真 Line-2 daily + intraday provider(`Line2RunState`/`Line2CodeContext` + `Line2DailyProvider`/`Line2IntradayProvider` + async 装配 `build_line2_run_state`/`build_line2_code_contexts`);组合 RiskEngine/broker/market_meta/stock_metadata/data_quality |
| `backend/main.py` | `_init_line2_runners`:RouteCoordinator + InstructionDispatcher + 2 runner + SnapshotStore + intraday manifest store + 2 cron callback,挂 app.state;callback 缺 frame 时 fail-open 跳过(U-D3 接真数据) |
| `backend/services/smoke_check.py` | `ORCHESTRATION_REQUIRED_SLOTS` 18→22(+`instruction_dispatcher`/`route_coordinator`/`line2_daily_runner`/`line2_intraday_runner`) |
| `pyproject.toml` / `.gitignore` | provider 模块入 TID251 per-file-ignore;Line-2 运行时 store 目录 gitignore |
| `tests/` | scheduler(+8)+ provider 单测/集成(+14)+ I-001 22 slot + smoke 22 + evolution 7-job 列表 |

## Cycle 1 发现的问题(3 P1 + 1 P2,全部修复)

| # | 严重度 | 文件 | 问题 | 修复 |
|---|--------|------|------|------|
| 1 | P1 | scheduler.py | 日线 cron 09:00 早于 09:30 开盘 → RiskEngine 14-check #7 `is_trading_hours` 拒绝每条 SELL,日线 runner 失效 | `LINE2_DAILY_CRON` + start() 注册改 **09:35**(盘中,gate 通过) |
| 2 | P1 | main.py | 两 callback 调 `build_line2_run_state` 未传 `open_tickets` → builder `ticket_freeze` 早返看不到 OPEN/EXPIRED 对账票 → simulation_auto 下 SimulationExecutor 不查票 → 对账冻结期可自动撮合 | `_open_tickets_or_skip()` 读 `reconciliation_ticket_repository.list_all_open()`,**fail-closed**(查询失败跳过整轮),两 callback 都透传 `open_tickets=` |
| 3 | P1 | main.py | Line-2 builder 用默认失活 `_StaticModeSwitchProbe` → D-005 模式切换期 `mode_switch` 早返不触发 → feishu 派发路径绕过冻结 | builder 注入 `mode_switch_probe=mode_router.mode_state` |
| 4 | P2 | line2_context_providers.py | `data_quality_provider.evaluate` 抛错时回落 `clean_data_quality()`(标记可交易)→ DQ 探针故障期照样路由 | 新增 `blocking_data_quality()`(`quote_unavailable=True`→不可交易),DQ 故障 fail-closed |

## Verify-1(codex read-only)+ 回归修复

codex verify-1:4 项原 finding 全 **RESOLVED**;**新查出 1 P1 回归**(我修复 #2 引入):
`reconciliation_ticket_repository` 挂载到 app.state 晚于 `broker_scheduler.start()` → 启动窗口内 Line-2 cron 触发会看到 None repo → `_open_tickets_or_skip` 返回空票视图 → 绕过对账冻结。

**回归修复**(2 处,fail-closed):(a) `reconciliation_ticket_repository` 挂载提前到 `MongoTicketRepository(db)` 绑定后、`_init_line2_runners` + `broker_scheduler.start()` 之前(去掉晚处重复赋值);(b) `_open_tickets_or_skip()` 在 repo 缺失时返回 `None`(跳过整轮),不再返回空 tuple(继续)。

## claude `/code-review`(high,3-angle,codex 额度耗尽改走)

codex verify-2 遇使用额度限(~15:07 重置),owner 指定改用 claude 自审技能。3 finder angle(逐行/删除行为/跨文件)+ 推理验证:

- **代码修 3 项**(本轮):① FEISHU_INTERACTIVE + 占位 chat 原仅由 lifespan 顺序保证不外发 → 改 `_init_line2_runners` 构造期 `SystemExit` fail-closed;② `_names` 原按全码键(`600000.SH`)而消费方按裸码查 → 改按裸码键(否则映射失效);③ `_line2_daily_job` docstring `09:00`→`09:35`。
- **余项均为已记录 U-D3 接缝**(data_quality_provider 未接 / today_instruction_count / day-open NAV+PnL / index_closes / 日线 suspension 实时盘口 / snapshot_at replay 保真 / Line-2 ADD concentration_exception 保守默认 / RouteCoordinator mode 启动期冻结):**U-D1 baseline 零暴露**(无 frame → Line-2 不路由);已在 plan.html U-D1 notes 标为 U-D3 硬前置(接 frame 前必接 DQ provider 等)。无新 P1 回归。

## 门禁

- ruff:全绿
- redline-check:全绿(X-018 orchestration 隔离 / M-004 单一构造点 / N-005 monitoring 隔离 不破)
- pytest:全量 3714 passed / 11 skipped(基线 3691 → +23)
- 新模块覆盖率:`line2_context_providers` 92% / `scheduler` 86%

## 不变量保持

永禁真实下单 / 飞书人工 gate / 单一构造点 M-004 / RiskEngine 纯函数 / Line-2 零 LLM + monitoring 隔离 / 127.0.0.1 / config runtime 不可改 —— 全不破。不改决策边界 → 无 amendment。

> 本报告由 Claude Code + Codex CLI 协同生成。
