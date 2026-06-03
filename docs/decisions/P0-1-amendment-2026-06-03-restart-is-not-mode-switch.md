# P0-1 修订 — 2026-06-03 进程重启 ≠ 模式切换(ModeRouter 持久化 current_mode)

> **修订基准**: [P0-1 双模式 + 账户生命周期](./P0-1-dual-mode-and-account-lifecycle.md)（CLAUDE.md §2.1）
> **关联**: §2.7（broker_events append-only + MODE_SWITCH_RESET 归档）/ R0 §3（PIT 可复现：mode 从 append-only 事件派生，非新可变状态）/ P0-6 §2 红线 5（acceptance gate 每次启动仍校验，本修订不放松）
> **修订日期**: 2026-06-03（深夜 #63 ops session 真启暴露）
> **决策人**: owner（2026-06-03：『现在就修』ModeRouter 根因 bug）+ 2 轮 codex 对抗确认（landmine-hunt + 修复路径判定）
> **性质**: 决策边界澄清 + bug 修复（amendment-first，代码随后 TDD + codex-review）。**不新增可变 mode 状态**——current_mode 由既有 append-only `MODE_SWITCH_RESET` 事件派生（单一真相源、可 replay、PIT 一致）。

## 0. 触发与意图

**真启暴露的 bug**：`ModeRouter.__init__` 每次都硬编码 `initial_mode=SIMULATION_AUTO`（`main.py` 构造处），不读持久层。于是 lifespan 启动块 `if mode_router.current_mode != "feishu_interactive": switch_mode(...)` 在**每一次进程重启**都判定为「切入 feishu_interactive」→ 触发 `MODE_SWITCH_RESET` + MockBroker 重置到初始资金 0 仓。

后果：一个**已持续处于 feishu_interactive 的账户**,**任何进程重启都会清空 broker 镜像**（2026-06-02 真启:`broker_events` 已积 18 次 mode_switch_reset;`recover_state` 先正确恢复 5 仓、随即被启动 switch reset 抹掉）。这把「进程重启」误当成「账户生命周期切换」,与 §2.1 原意不符——§2.1 的「切换 = 账户生命周期事件」指的是**模式的真实转变**（simulation_auto ↔ feishu_interactive）,**不是**同一模式下的进程重启。

## 1. 决策

### 1.1 进程重启 ≠ 模式切换
**MODE_SWITCH_RESET + MockBroker 重置 仅在「真实模式转变」时触发**,即 `持久 mode != 本次解析出的目标 mode`。同一模式下的进程重启**不得**触发 lifecycle reset,recover_state 恢复出的持仓**必须保留**。

### 1.2 current_mode 由 append-only 事件派生（不新增可变状态）
`ModeRouter` 启动时的 `current_mode` **从持久层派生**:读取 `broker_events` 中**最近一条 `MODE_SWITCH_RESET` 事件**的 `payload.to_mode`;无任何此类事件（全新账户）则默认 `SIMULATION_AUTO`。
- 派生源 = 既有 append-only 事件,**非新增可变 mode 状态**（R0 §3 PIT 一致:mode 可由事件流 bit-exact 重建、可 replay）。
- 实现:`BrokerEventStore.read_last_event_of_type(MODE_SWITCH_RESET)` + `mode_router.resolve_durable_mode(event_store)`;`main.py` 以其结果作 `initial_mode` 传入 `ModeRouter`,既有 `if current_mode != feishu_interactive` 守卫即自然跳过重启误重置。
- `RECONCILIATION_RESET` 等其他事件**不改变 mode**（只查 `MODE_SWITCH_RESET` 的 `to_mode`）。

### 1.3 真实转变仍走完整 lifecycle（不放松）
- 全新账户首次 simulation_auto → feishu_interactive:无前置 MODE_SWITCH_RESET → 派生 SIMULATION_AUTO → 守卫触发 switch（真实首切,重置空账户,无害)。
- feishu_interactive → simulation_auto 回滚、或回滚后再切回:`to_mode` 不同 → 守卫正确触发完整 lifecycle（归档 + reset + audit 对 + freeze）。
- **acceptance gate（P0-6 §2 红线 5)每次启动仍 fail-closed 校验**（`can_switch_to_feishu_on`):本修订只改「是否再触发 reset」,**不改**「feishu 模式是否需 acceptance 通过」——acceptance 不通过仍 SystemExit 拒启。

### 1.4 不复原既有已 reset 状态
本修复只阻止**未来**重启误重置;它**不会**复原已落账的历史 reset（如 2026-06-02 seq 26）——append-only 不可改写,recover_state 必重放 seq 26。已被 reset 的镜像须由 owner 走正式对账（`initiate_reconciliation`→`decide` RESOLVED→append `RECONCILIATION_RESET` seq>26)复原真实持仓。修复 + 对账后,后续重启即正确保留持仓。

## 2. 红线（保留 / 变更)

**保留不变**:
- §2.1 真实模式切换仍是账户生命周期事件（归档 MODE_SWITCH_RESET + reset + audit MODE_SWITCH_INITIATED/COMPLETED + MOCKBROKER_RESET + in-progress freeze)。
- P0-6 §2 红线 5:feishu_interactive 每次启动仍须 acceptance gate ALLOW,env 不可绕过。
- §2.7:broker_events append-only + 8 红线 + checksum fail-closed;mode 派生只读不写。
- ModeRouter 仍禁 `import backend.{llm,agents,mirofish}`。

**变更**:
- `ModeRouter.current_mode` 启动值从「硬编码 SIMULATION_AUTO」改为「派生自最近 MODE_SWITCH_RESET.to_mode」。
- lifecycle reset 触发条件明确为「持久 mode != 目标 mode」,进程重启（mode 未变）不再触发。

## 3. 验证

- TDD:① restart-while-feishu（已有 MODE_SWITCH_RESET to_mode=feishu）→ ModeRouter 启动 current_mode=feishu_interactive → 守卫不再 switch,恢复的持仓保留;② 全新账户首切 simulation→feishu 仍 switch;③ resolve_durable_mode 无事件默认 SIMULATION_AUTO;④ 只认 MODE_SWITCH_RESET(RECONCILIATION_RESET 不改 mode)。
- 全量 pytest + ruff + redline 全绿;codex-review 修完 P0/P1/P2 再 commit。
