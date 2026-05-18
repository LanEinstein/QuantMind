# Phase X Pre-flight Rehearsal Report — 2026-05-18 session #21

## 元数据

| 字段 | 值 |
|------|----|
| 日期 | 2026-05-18 |
| Session | #21 phase-x-dedicated-planning |
| 执行人 | Claude Opus 4.7 (1M ctx) |
| 目的 | Phase X 计划文档 session 收尾验证:Phase J substrate 冷启 + 5 类 P0-6 reset 触发器无误触 + 0 真实 LLM ¥ |
| 关联文档 | `docs/decisions/P2-2-implementation-plan-2026-05-18.md`(Phase X 28 任务计划)+ `docs/runbook/i-002-production-runbook.md`(I-002 长跑 runbook)+ `docs/plan.html`(SSoT session #21 SESSION_LOG)|

## 1. smoke_test_cold_start.py — PASS ✅

**命令**:
```bash
QUANTMIND_LLM_STUB=1 QUANTMIND_BROKER_SKIP_RS_GATE=1 \
  /home/ps/anaconda3/envs/zhanglan/bin/python scripts/smoke_test_cold_start.py --json
```

**结果**:
```json
{
  "verdict": "PASS",
  "ok": true,
  "missing_required": [],
  "present_required_count": 24,
  "expected_required_count": 18,
  "llm_router_stubbed": true,
  "lifespan_traceback": null,
  "stub_failure": false
}
```

**通过项**:
- ✅ 18 个核心 app.state slot 全部 populate(实际 24 项,含可选)
- ✅ `llm_router_stubbed: true` — 0 真实 LLM 调用,零 ¥
- ✅ `lifespan_traceback: null` — 启动/关停链无 Python 异常
- ✅ `stub_failure: false` — LLM router 走 stub 路径

**预期 None 项(4 项,非缺陷)**:
1. `reconciliation_orchestrator` — `FEISHU_DECISION_CHAT_ID` 未设(P0-2-amendment-2026-05-16 §4 红线 7;simulation_auto 默认不接飞书决策群)
2. `feishu_client` — `FEISHU_INTERACTIVE_ENABLED=false`(simulation_auto baseline)
3. `feishu_alerter` — Feishu alert chat 未配置(alert 降级为 audit-only)
4. `owner_authorization` — `QUANTMIND_PROD_RUN` 未设(J-007 二级门在 dev mode 是 no-op)

## 2. simulate_n_trading_days.py --days 45 — PASS ✅

**命令**:
```bash
QUANTMIND_LLM_STUB=1 QUANTMIND_BROKER_SKIP_RS_GATE=1 \
  /home/ps/anaconda3/envs/zhanglan/bin/python scripts/simulate_n_trading_days.py --days 45 --json
```

**结果**:
```json
{
  "verdict": "PASS",
  "ok": true,
  "requested_days": 45,
  "trading_days_walked": 45,
  "start_date": "2026-05-18",
  "end_date": "2026-07-20",
  "tick_count": 360,
  "ticks_per_day": [
    "morning_open", "intraday_mtm_sample", "morning_close",
    "afternoon_open", "intraday_mtm_sample", "afternoon_close",
    "eod_pipeline", "advance_day"
  ],
  "llm_router_stubbed": true,
  "real_llm_calls_observed": 0,
  "reset_triggers_fired": [],
  "tick_callback_errors": [],
  "elapsed_seconds": 0.003812
}
```

**通过项**:
- ✅ `trading_days_walked: 45` — 完整覆盖 I-002 acceptance 窗口的全长度
- ✅ `tick_count: 360`(= 45 × 8 ticks/day,8 ticks 锁定 P1-2.B intraday MTM + EOD pipeline 节奏)
- ✅ `real_llm_calls_observed: 0` — **零真实 LLM ¥**;严守 owner directive
- ✅ `reset_triggers_fired: []` — P0-6 §1 5 类 reset(行情断流 30min / LLM 全停 1h / MockBroker 损坏 / 状态机非法迁移 / 长连接 4h)**全部 0 误触**;harness pinned-clock 不会触发任何 reset 是预期行为
- ✅ `tick_callback_errors: []` — 360 ticks 无 callback 异常
- ✅ `elapsed_seconds: 0.003812` — pinned-clock 极快(无真实 sleep,无真实 LLM)

## 3. 综合结论

| 检查项 | 状态 |
|--------|------|
| Phase J substrate(J-001..J-007)落地正确 | ✅ smoke_test 18 slot 全活 + 0 lifespan 异常 |
| QUANTMIND_LLM_STUB=1 防护 | ✅ smoke + 45-day simulator 双验证 0 真实 LLM 调用 |
| 5 类 P0-6 reset 触发器无误触 | ✅ 45 天 360 ticks 0 reset_triggers_fired |
| MockBroker + 关停链完整性 | ✅ 启动/关停顺序无异常 |
| I-002 真实长跑前置条件 | ✅ Phase J 7/7 done + harness 测试通过;**仅缺 owner 直接授权** (`QUANTMIND_OWNER_PROD_AUTHORIZATION` env var) |

## 4. 下一站推荐

按 2026-05-17 session #19 owner 推荐序列已完成 (b) Phase X 计划文档 → 预飞演练。下一站 owner-gated 二选一,**可并行**:

### 选项 A — Phase X 实施期(任意时刻可启;与 I-002 并行)
- 召开 Phase X 实施期 dedicated session(无 owner action 前置门)
- 第 1 session 推 X-A 基础设施 7 任务(X-001..X-007,~5.5d 工作量)
- Phase X 18 模块严禁 import `backend.{api, broker, risk, llm, agents, mirofish, data}`(本计划 X-018 守门)= 与 I-002 主路径零依赖;**可与 I-002 真实长跑并行推进**

### 选项 B — I-002 真实长跑(需 owner 直接授权)
- owner 导出 `QUANTMIND_PROD_RUN=1` + `QUANTMIND_OWNER_PROD_AUTHORIZATION=<owner>:YYYYMMDD`(YYYYMMDD ≤7 日;由 J-007 守门)
- `sudo systemctl start quantmind`(按 `docs/runbook/i-002-production-runbook.md`)
- 45 真实交易日 ≈ 9 周 + ≈ ¥900 LLM 预算(per current LLM pricing)
- 每日 16:30 检视 acceptance_dashboard CLI(J-001);任何 P0 级中断走 incident playbook

**两路径互不依赖,owner 优先级灵活**。本预飞证实 Phase J 与 I-002 启动条件 ✅ + Phase X 实施期启动条件 ✅,**无 blocking 技术债**。
