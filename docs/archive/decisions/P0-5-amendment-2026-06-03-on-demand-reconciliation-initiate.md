# P0-5 修订 — 2026-06-03 按需对账发起(on-demand reconciliation initiate ops 工具)

> **修订基准**: [P0-5 对账 / 冻结 / fail-closed ticket 状态机](./P0-5-reconciliation-freeze-failclosed.md)（CLAUDE.md §2.7）
> **关联**: P0-1-amendment-2026-06-03（重启≠switch;本 incident 同源)/ §2.4(全后端仅 2 写端点:本工具是**带外 ops 脚本**,不新增 HTTP 写端点)/ §2.6(飞书人工 gate)
> **修订日期**: 2026-06-03(深夜 #63 ops session 续)
> **决策人**: owner（2026-06-03 同意「正经地建 on-demand 对账工具,amendment-first + codex 审查」)
> **性质**: 接线缺口修复 + ops 工具(amendment-first;代码随后 TDD + codex-review)。**不改对账语义、不改 ticket 状态机、不新增 HTTP 写端点、不绕人工 gate**——只补「如何创建一张 OPEN ticket 并发起对账」这条**生产从未接线**的入口。

## 0. 触发与意图

2026-06-03 排查发现:对账子系统**组件齐全但 initiate/ticket 创建从未接线**——
- `ReconciliationOrchestrator.handle_reply`(飞书回复路由)✅ 接线(main.py lifespan)。
- `decide_ticket`(`POST /api/reconciliation-tickets/{id}/decide`)✅ 接线。
- **`initiate_reconciliation` 无任何调用方**;`run_eod_pipeline` 只写 `BrokerSnapshot` + 跑 acceptance,**不创建 ticket**;全代码库无任何 `ReconciliationTicket(...)` 构造。
- `_handle_mismatch` 要求**先有 OPEN ticket**(`self._tickets.get(...)`,带 `expected_snapshot_id`);无创建路径 → 飞书发"对账差异"得 `unknown_ticket_id`,无操作。

后果:**owner 无法对账**(没有任何路径创建 ticket)。这阻塞了 #63 incident 的镜像复原(seq26 mode-switch-reset 把镜像清到 ¥100k/0,需对账复原真实 5 仓),也意味着对账子系统从未在生产跑过。

## 1. 决策

### 1.1 新增带外 ops 脚本 `scripts/reconcile_now.py`(owner-gated,类比 `run_line1_now.py`)
按需创建一张 OPEN ticket 并发起对账,供 owner 在需要复原/核对镜像时手动跑。**不是 HTTP 端点**(§2.4 仅 2 写端点不破);**不在 cron 自动跑**(避免无人值守自动发起)。门控同 `run_line1_now`:`--preview`(零写、只打印将发的飞书文本 + ticket 计划)/ `--send --confirm`(真创建 + 真发飞书)。须从 go-live shell 跑(`QUANTMIND_PROD_RUN` + owner auth env)。

### 1.2 工具行为(只创建 OPEN ticket + 发起,**不改镜像**)
1. 连 Mongo;`recover_state` 读当前 broker 状态(只读)。
2. 构造并持久化一张当前状态的 `BrokerSnapshot`(复用 `run_eod_pipeline` 同款:`compute_snapshot_checksum` + `BrokerSnapshotStore.append`,append-only;`last_event_sequence = read_latest_sequence()`)→ 得 `snapshot_id`。该 id 可被 `MongoSnapshotLookup`(读 `broker_snapshots`)解析为 `MockBrokerSnapshot`,供 `_handle_mismatch` 重算 deviation。
3. 构造一张 OPEN `ReconciliationTicket`(`ticket_id` 合 `^RECON-\d{8}-\d{3}$`;`expected_snapshot_id` = 上面的 snapshot_id;`deviation_report` = **占位空报告** `overall_passed=True, deviations=()`(真 deviation 在 owner MISMATCH 回复时由 `detect_deviations` 重算,不入 ticket);`actual_reconciliation_id` = 占位;`status=OPEN`)→ `MongoTicketRepository.save`。
4. 经 `MessageRenderer.render_reconciliation_request` + `FeishuClient.send_message` 向**决策群**发起对账提示(等价 `initiate_reconciliation` body;决策群≠告警群 P0-2-amendment 不破)。
5. **到此为止**——镜像改写由 owner 后续两条飞书回复经**已接线的运行实例** orchestrator 完成:`对账差异 <ticket_id> 现金 <cash> 持仓 ...`(MISMATCH → 记 reported)→ `对账采纳：用户回报 <ticket_id>`(RESOLVE_USER → `decide_ticket` → `ReconciliationApplier.reset_to_snapshot`)。

### 1.3b 修复 renderer 回复指令与 parser 漂移(bug,非决策边界变更)
`render_reconciliation_request` 显示的回复指令(`采纳镜像` / `采纳回报 现金…` / `更正 现金…`)与**活跃 parser** `parse_reconciliation_reply` 的语法(`对账无误` / `对账差异 现金 持仓` / `对账采纳：用户回报` / `对账采纳：系统镜像` / `对账更正`)**完全不符** —— 因 initiate 从未接线,此漂移从未被触发。本 amendment 将 renderer 指令对齐到 parser 锁定语法(P0-5 §1.3.1;parser 是单一真相源,renderer 只是显示之前写错了 → bug 修复,非改语法)。新增 round-trip 测试钉死「renderer 指示的格式必能被 parser 解析」。

### 1.3 OPEN ticket 自带冻结(安全增益,非新行为)
OPEN ticket 是 §2.7 五大买卖冻结源之一。创建后运行实例即冻结 BUY/SELL 路由(含 09:35 Line-1 cron),resolve(decide)后自动解冻——天然防"镜像 desync 期间误路由"。本工具不改该语义,仅利用之。

## 2. 红线(保留 / 不变)
- 镜像改写**仍仅经** `ReconciliationApplier.reset_to_snapshot`(经 RESOLVED ticket;工具**不**调 applier、**不**直接 mutate 镜像)。append-only 8 红线不破。
- 仍**仅 2 个 HTTP 写端点**(本工具是带外脚本,非端点)。
- 人工 gate 不破:复原须 owner 亲自在飞书回报真实持仓 + 采纳(工具只发起,不替 owner 决策)。
- ticket 状态机 / `transition_ticket` 单一真相源不变;工具只创建 OPEN(初始态),不做 RESOLVED 转换。
- 决策群≠告警群(P0-2-amendment-2026-05-16)不破;LLM 不进对账路径不破。
- `BrokerSnapshot` 写经 `BrokerSnapshotStore.append`(transactional append-only),checksum 正确计算;不直接写 Mongo。

## 3. 范围限定
- **不**把 initiate 接进 cron(保持手动 ops;若未来要 16:00 自动对账,另写 amendment 接 `run_eod_pipeline`)。
- **不**改 `decide` 端点 / `handle_reply` / `transition_ticket` / applier。
- **不**改对账阈值(cash 1 元 / volume 0% / cost 0.01 元)。

## 4. 验证
- TDD:ticket+snapshot 构造 helper(占位 deviation report 合法 / expected_snapshot_id 指向 append 的 snapshot / ticket_id 合 pattern / OPEN 不带 resolved_at);`--preview` 零写;forbidden/缺 env fail-closed。
- 端到端(dry-run):`--preview` 打印将发文本 + ticket 计划,无 Mongo/飞书写。
- 全量 pytest + ruff + 官方 redline-check 全绿;codex-review 修完 P0/P1/P2 再 commit。
