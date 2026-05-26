# U-C3 Codex Review Summary — Line-2 30s 盘中确定性触发 runner

> 任务:U-C3(`docs/plan.html` phase="U 生产编排上线")。被审文件:
> `backend/monitoring/intraday_triggers.py` + `backend/orchestration/intraday_manifest.py`
> + `backend/orchestration/line2_intraday_runner.py` + 两个测试文件。
> 调用:`codex review --uncommitted`(cycle 1)+ `codex exec --sandbox read-only`(4 轮 verify),
> 全程 `</dev/null` 前台。结论:**最终扫描无任何 P0/P1/P2 残留**,累计修 **3 P1 + 5 P2**,全部根因修复 + 回归测试。

## 评审轮次与发现

### Cycle 1(`codex review --uncommitted`)— 2 P1
- **P1-1 快照版本冲突**:`_persist_tick` 用固定 `endpoint="line2_intraday_quotes"` + `version=1` + 当日 `trade_date`;`SnapshotStore.put` 拒绝重复 `(vendor,endpoint,trade_date,version)`,故**同一交易日第二个触发 tick 在路由前必抛 `SnapshotOverwriteError`**。
- **P1-2 同码 SELL+ADD 矛盾路由**:一个持仓码同 tick 可同时满足风险退出 SELL(回撤>5%)与超卖 ADD(略低于成本);dedup 按 `(code, side)` 不去重跨方向 → 同码既卖又买。

**修复**:
- P1-1 → 每 tick quote 快照 `endpoint=f"line2_intraday_quotes-{now:%H%M%S}"`(每 tick 唯一键,`version` 语义不滥用;in-flight 守门 + 30s 节奏保证当日 HHMMSS 唯一)。回归 `test_second_triggered_tick_same_day_persists`。
- P1-2 → `_run_locked` 计算 `sell_codes` 后,过滤掉同码的 ADD 意图(风险退出优先于补仓)。回归 `test_sell_suppresses_add_on_same_code`。

### Verify 1 — 确认 P1-1/P1-2 + 新 1 P1
- **P1-3 实时盘口时间戳契约漏洞**:`_route_one` 用 `spot.snapshot_at` 作 `DataSnapshot.snapshot_at`,builder 盖 `InstructionPlan.created_at=now`,而 `InstructionPlan` 要求 `snapshot_at` **严格早于** `created_at`;`snapshot_at == now`(或时钟漂移略晚)→ **持久化后路由抛 ValueError 崩 tick**(codex 实测复现)。

**修复**:`filter_fresh_quotes` 的新鲜判定从 `age < 0` 收紧为 `age <= 0`(quote 须严格早于 `now`,与 InstructionPlan 不变量一致),非严格早于即 fail-closed,杜绝持久化后崩溃;`fetch_spots` Protocol 文档新增 `now` 在取数之后的契约。回归 `test_filter_fresh_quotes_same_instant_is_stale`(单元)+ `test_invariant3_same_instant_quote_fails_closed`(runner:不触发、不持久化、不崩)。

### Verify 2 — 确认 P1-3 + 2 P2
- **P2-A dedup-skip tick 不持久化与不变量 7 措辞冲突**。
- **P2-B ADD 触发记录缺判据**:`IntradayTriggerRecord` 仅存 live_price/prev_close/atr/stop/add_volume + 部分 config,缺 cost/account/regime/ma_long → 不能从 manifest 复算 ADD 判决。

**修复**:
- P2-A → 精确化不变量 7 措辞(去重 = 同信号首次已 durable 持久化,无新信号可记;`fired_today` 仅在 ROUTED/REJECTED 即首次经 `_persist_tick` 后写入 → 无血缘缺口)。回归断言 `second.quote_snapshot_id is None`。
- P2-B → `IntradayTriggerRecord` 增 `cost_price/position_volume/total_assets/regime/ma_long`;ADD 记录补 `breakdown_tolerance/ma_long_window`。回归 `test_add_manifest_records_decision_inputs`。

### Verify 3 — P2-A 解决,P2-B 部分 + 3 新 P2
- **P2-1 provider 多次读取不一致**:`account/index_closes/daily_frame` 在 eval 与 manifest-record 处分别读取 → 若 provider 非每-tick 不可变,记录可能 ≠ 门用的输入。
- **P2-2 manifest 不对 schema 漂移 fail-closed**:`IntradayTriggerManifest` 仅 `ge=1`,`schema_version=2` 被接受(模块 0 模型均有 `_check_schema_version`)。
- **P2-3 ATR 止损在 recent-high 窗不足时仍触发**:`recent_high` 仅要求 `len>=1`,但窗=20;15 根日线即可触发不完整窗的 ATR 止损。

**修复**:
- P2-1 → `_run_locked` 把 `daily_frame/account/index_closes/name_by_code` 各读**一次**入局部变量,eval + 记录构建 + `_persist_tick` 全用同一局部 → manifest 必记门用的输入(codex 用 mutating-provider 直接断言验证)。
- P2-2 → `IntradayTriggerManifest` 加 `model_validator` `_check_schema_version`(对齐 `MarketDataSnapshot`)。回归 `test_intraday_manifest_fails_closed_on_schema_drift`。
- P2-3 → `recent_high` 仅当 `len(closes) >= recent_high_window` 才计算(整窗自门控,精度优先);回撤触发仍不需历史。回归 `test_sell_atr_requires_full_recent_high_window`。

### Verify 4(最终)— 无残留
> "No P0/P1/P2 findings in the final scan of the three U-C3 files." 三个 P2 均经直接断言确认修复。
> mypy 报 2 处 low-priority typing nit(codex 明确**不**评为 P0/P1/P2;mypy 不在本项目 commit 门禁内)。

## 本地门禁(commit 前)
- `pytest -q --cov=backend --cov-fail-under=70`:**3691 passed** / 11 skipped(基线 3649 → +42 新测试),全仓覆盖率 90.78%。
- 新模块覆盖率:`intraday_triggers.py` 93% / `intraday_manifest.py` 95% / `line2_intraday_runner.py` 98%。
- `ruff check`(触及文件):全绿。
- `bash scripts/redline-check.sh`:全绿(N-005 monitoring 隔离含新 `intraday_triggers.py` + M-004 单一构造点 AST + X-018 Phase X 不受影响,全不破)。

## 红线守恒
永禁真实下单(全 fake 注入,0 真实网络/LLM/飞书);InstructionPlan 单一构造点(仅 `assemble_monitoring_plan`,新文件不构造 InstructionPlan,M-004 AST 守);Line-2 零 LLM 决策 + `backend/{orchestration,monitoring}` 禁 `backend.{llm,agents,agents_team,mirofish}`(orchestration 亦禁 `backend.data`,AST 自检守);RiskEngine 纯函数 14-check;config runtime 不可改(`IntradayTriggerConfig`/`AddConfig` frozen);PIT 可 replay(原始字节+checksum + 消费行血缘 + 触发判据,不用跨 tick 内存高水位);无决策边界变更 → 无 amendment。
