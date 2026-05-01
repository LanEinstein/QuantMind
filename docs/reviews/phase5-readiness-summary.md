# QuantMind Phase 5 Readiness — `/codex-review` 5 轮门禁汇总

| 项 | 值 |
|---|---|
| 任务 ID | `phase5-readiness` |
| 启动 HEAD | `a5f1b9d` (`fix(deploy): forward shell-env LLM keys ...`) |
| 完成日期 | 2026-04-25 |
| 审查者 | Claude Code (Opus 4.7 1M) + Codex CLI 0.124.0 |
| Skill 版本 | `~/.claude/skills/codex-review/SKILL.md` 与 `LanEinstein/CCodexSkill` 一致 |
| 轮次焦点 | 1 架构 / 2 UX / 3 测试 / 4 性能+a11y / 5 安全+ops |

## 各轮判定

| 轮次 | 判定 | CRITICAL | HIGH/WARNING | MEDIUM/INFO | 报告 |
|---|---|---:|---:|---:|---|
| R1 Architecture & data flow | MAJOR_CONCERNS → 修复后通过 | 4 | 4 | 0 | [phase5-r1-architecture.md](phase5-r1-architecture.md) |
| R2 UX / design alignment | MAJOR_CONCERNS → 修复后通过 | 3 | 6 | 1 | [phase5-r2-ux.md](phase5-r2-ux.md) |
| R3 Testing coverage & correctness | NOT_SHIPPABLE → 修复后通过 | 2 | 4 | 1 | [phase5-r3-testing.md](phase5-r3-testing.md) |
| R4 Performance & accessibility | NEEDS_CHANGES → 修复后通过 | 0 | 1 | 7 | [phase5-r4-perf-a11y.md](phase5-r4-perf-a11y.md) |
| R5 Security & ops readiness | NEEDS_CHANGES → 修复后通过 | 2 | 7 | 1 | [phase5-r5-security.md](phase5-r5-security.md) |

5 轮共发现 **11 CRITICAL + 21 HIGH/WARNING + 9 MEDIUM/INFO = 41 处问题**。
所有 CRITICAL + HIGH/WARNING 已修复（含代码改动 + 模板修订）。
2 处 MEDIUM 已记入 [phase5-readiness-backlog.md](phase5-readiness-backlog.md) 并附 owner / 截止日期。

## 修复关键摘要（按文件分组）

### 后端管线 / 持久化
- `backend/agents/graph.py`：暴露公开 `AnalysisRunError`；任何 agent step `failed` → 整体 `status=failed`，不再静默升格为 completed。
- `backend/agents/collector.py`：新增 `on_agent_failed` 与 `has_failed_steps` / `first_failure_summary`。
- `backend/agents/graph.py:_make_node`：节点抛异常时调用 `on_agent_failed` 而非 empty `on_agent_completed`，SSE 事件正确携带 `status="failed"`。
- `backend/api/analysis.py`：`/stock` 与 `_run_job` 显式 catch `AnalysisRunError`，持久化失败 record，error 事件附 `record_id`。
- `backend/data/analysis_scheduler.py`：scheduler caller 同样捕获 `AnalysisRunError` 并持久化，主流程继续跑剩余股票。
- `backend/data/database.py`：去掉 `analysis_records.signal_id` 的 `unique=True`（trading_signals upsert 共享 signal_id）；新增 `trade_date` 单字段索引和 `(stock_code, created_at)` / `(trade_date, created_at)` 复合索引。

### SSE / 实时
- `backend/services/analysis_stream.py`：`subscribe()` 原子返回 `(job, queue, snapshot)`；slow consumer drop 主动 drain + 推 `None` 哨兵；`shutdown()` 用 `asyncio.gather` await 已取消任务；新增 `max_subscribers_per_job` / `max_active_jobs` 容量上限与 `subscriber_count` / `active_job_count` helper。
- `backend/api/analysis.py`：stream consumer 用 snapshot 而非 `job.events[-1]`；遇到 terminal event_type 主动 break；新增 admission cap（429）；DTO mapper `_detail_from_record` 把持久化 shape 翻译成前端 `AnalysisDetail` 契约（debates/risk/decision）。

### 前端 / UX / a11y
- `frontend/src/types/agent.ts`：`SSEErrorEvent.record_id` 可选。
- `frontend/src/composables/useSSE.ts`：错误信息中文化；新增 `errorRecordId` 暴露；`humanizeErrorMessage` 防止泄漏服务器英文堆栈。
- `frontend/src/stores/agent.ts`：`historyLoading` / `historyError` 状态；`beginStreamingRun` 注入 provisional detail 让 SSE 事件不丢；`fetchHistory` 仅在 6 位代码精确匹配时下发到后端，否则走客户端模糊；`DebateArgument.model` 用真实 `model_label`。
- `frontend/src/views/AgentDebate.vue`：`isStarting/activeJobId` 守护重复点击；失败状态用 `el-result` + 重试按钮；history 列表 listbox 语义只覆盖真正 option，placeholder 移到外层；focus 管理（开始 → streaming，失败 → failed region）；keyboard Enter/Space 导航；search input debounce 250ms。
- `frontend/e2e/agent-debate.spec.ts`：新增 `live SSE debate content surfaces before final detail fetch` —— 通过门控 detail fetch 强制断言 SSE 内容先到达，避免回归。

### 监控 / 风控 / 告警
- `backend/api/monitoring.py`：`_risk_summary` 改为读 `app.state.circuit_breaker`，附 `halted` 与 `consecutive_losses`。
- `backend/api/trading.py`：`/approve` 显式 catch `CircuitBreakerHaltedError`，返回 409 而非 500，并触发告警。
- `backend/monitoring/alerter.py`：webhook 失败仅记录 exception class + http status，不写 URL。

### 部署 / 脚本 / Ops
- `docker-compose.yml`：mongodb / redis 端口绑定 `127.0.0.1`。
- `deploy/quantmind-backend.service`：移除指向不存在文件的 `--log-config`；`ReadWritePaths` 加入 `~/.local/state/quantmind`。
- `deploy/nginx-quantmind.conf`：HSTS / X-Content-Type-Options / X-Frame-Options / Referrer-Policy 全局 always；`/api/analysis/jobs` 6r/m + burst=2；`/api/analysis/stream/` `limit_conn 3`；429 超限。
- `scripts/backup.sh`：默认目录改 `~/.local/state/quantmind/backups`；`umask 077` + `chmod 600`。
- `.gitignore`：忽略 `backups/`。
- `deploy/README.md`：增加 backup 路径与文件权限步骤；安全红线扩充端口绑定/.env 0600 等条目。

### 测试新增
- `tests/test_analysis_stream_hub.py`：subscribe 原子性、drop sentinel、shutdown await。
- `tests/test_analysis_run_failures.py`：collector helpers + /stock + /jobs error path persistence。
- `tests/test_analysis_detail_mapping.py`：DTO mapping 覆盖 debates/risk/decision/empty cases。
- `tests/test_agents_graph.py`：`test_agent_failure_raises_analysis_run_error` 与 `test_emitter_marks_failed_agent_step` —— 真正驱动 graph 失败而非 mock API 边界。
- `tests/test_analysis_stream.py`：integration `/jobs → SSE → /history`、terminal-race、admission cap、subscriber cap。
- `tests/test_analysis_scheduler.py`：record persistence 断言与 `AnalysisRunError` 失败录入。
- `tests/test_api_analysis.py` / `tests/test_llm_preflight.py`：fixture teardown，`preflight()` 用 `MagicMock` 而非 `AsyncMock`，避免静默 bypass 503 guard。

## 测试基线

| 套件 | 数量 | 状态 |
|---|---:|---|
| `pytest -q` | 669 passed / 11 skipped / 1 warning | ✅ |
| frontend `npm run type-check` | 0 errors | ✅ |
| frontend `npm run test` (vitest) | 81 / 81 | ✅ |
| `playwright test e2e/agent-debate.spec.ts` | 4 / 4 | ✅ |
| `playwright test` (full) | 66 / 69 (3 portfolio 失败为评估前已存在，与本次改动无关) | ⚠️ 非回归 |

5 轮 `/codex-review` 门禁通过。Phase 5 代码进入"等待用户部署冒烟 + V.5 引导"阶段。
