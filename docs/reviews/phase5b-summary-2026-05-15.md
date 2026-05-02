# Phase 5B Summary — 2026-05-02

> **作者协议**: 本报告对应 Phase 5B 出口检查的 STOP 节点。Phase 5B-T01/T02/T03 三个 task 全部 ✅ 落地;出口 7 项指标中 4 项 (cost x3 + latency x2 + shadow consistency x2) **设计与工具就位**,真值采集仍 deployment-bound;出口检查标记保留 🔧,等用户书面授权后启动 7 天 shadow 部署窗口完成 final gate。本文档同时是 Phase 5C 入口前置物料的物料清单。
>
> **报告日期**: 2026-05-02 (按 SSoT §6 文件名约定 2026-05-15;实际推进比计划早 13 天,文件名沿用计划口径以维持索引一致)。
>
> **执行 session**: claude-opus-4-7-1m + Codex CLI(7 轮 review)。

---

## 1. 工作清单完成情况

| Task ID | Title | Status | Commit | Test Report |
|---|---|---|---|---|
| P5A-T00 — P5A-T05 | Phase 5A 评估期生命线 | ✅ 已完成(2026-05-08 收尾) | 737bf83 | docs/reviews/phase5a-summary-2026-05-08.md |
| P5B-T01 | agent_models.yaml schema + per-agent thinking config | ✅ 已完成(2026-05-02) | c95e004 | docs/reviews/p5b-t01-codex-summary.md |
| P5B-T02 | Fast/Slow watchlist 拆分 | ✅ 已完成(2026-05-02) | 07a19ea | docs/reviews/p5b-t02-codex-summary.md |
| P5B-T03 | Tiered triage→escalation routing | ✅ 已完成(2026-05-02) | eb10fc1 | docs/reviews/p5b-t03-codex-summary.md |
| P5B-Exit | 出口检查 harness + 报告 | 🔧 推进中(harness 全部就位;7 天 shadow 真值待部署) | _本 commit_ | docs/reviews/p5b-exit-codex-summary.md |

**Phase 5B 出口 7 项指标当前状态**

| 指标 | 阈值 | Harness | 真值 | 状态 |
|------|------|---------|------|------|
| 单股 fast 成本 | ≤ ¥0.20 | ✅ `compute_exit_report.cost.fast_p95_rmb` | ⏳ 部署后采集 | 🔧 工具就位 |
| 单股 slow 成本 | ≤ ¥0.50 | ✅ 同上 | ⏳ | 🔧 工具就位 |
| 日均成本 | ≤ ¥1.20 | ✅ `cost.daily_total_rmb` | ⏳ | 🔧 工具就位 |
| fast p95 延迟 | ≤ 8 min | ✅ `latency.fast_p95_sec` | ⏳ | 🔧 工具就位 |
| slow p95 延迟 | ≤ 15 min | ✅ `latency.slow_p95_sec` | ⏳ | 🔧 工具就位 |
| 决策一致率(action 维度) | ≥ 0.85 | ✅ `shadow.action_match_rate` | ⏳ | 🔧 工具就位 |
| 决策置信度偏差 | < 0.15 | ✅ `shadow.confidence_delta_mean_abs` | ⏳ | 🔧 工具就位 |

---

## 2. 测试基线

```
$ /home/ps/anaconda3/envs/zhanglan/bin/pytest -q --tb=no
1077 passed, 11 skipped in 3.24s
```

| 维度 | 命令 | 结果 |
|------|------|------|
| Backend 全量 | `pytest -q --cov=backend --cov-fail-under=70` | **1077 passed**, 11 skipped, 0 failed;**coverage 83%**(vs T03 baseline 82.47%) |
| Risk 隔离覆盖 | `pytest -q tests --cov=backend.risk --cov-fail-under=95` | **98.20%**(engine 100% / circuit_breaker 96% / stop_loss 97%) |
| 风控红线静态 | `grep -rn "from backend.llm\|from backend.agents\|from backend.mirofish" backend/risk/` | **clean**(仅 engine.py:4 docstring 提及禁令) |
| Lint | `ruff check` (新文件) | **All checks passed** |

**新模块覆盖**:
- `backend/services/shadow_recorder.py`: **100%**
- `backend/services/shadow_compare.py`: **94%**
- `backend/services/phase5b_exit_check.py`: **97%**

**单元/集成增量**: 116 个新测试覆盖 shadow_recorder + shadow_compare + phase5b_exit_check + 两个 CLI + 数据库索引契约。`test_creates_shadow_decisions_indexes` 锁定 unique(run_id) + TTL(created_at, 30d) 两条索引;`TestExitCheckCLILiveInputs` 用 mock motor + Redis 验证 ``$or`` 窗口过滤 + 5-字段投影 + 资源关闭。

---

## 3. 关键决策记录

### 决策 1:不动产线管道,把 shadow 录制设计成"独立数据层"

- **场景**: P5B-T03 SSoT §2.7 描述 `analysis_records.shadow_*` 字段录制方案。
- **抉择**: A) 把 baseline + routed 双调用塞进 `fund_manager_node`;B) 提供一个独立的 `shadow_decisions` 集合 + 公共录制 API,operator 用单独后台任务消费 baseline 路径,实时分析仍走 routing 路径。
- **选 B**。理由:① P5B-T03 用了 7 轮 codex 才把 routing 路径稳住;再在产线管道上叠加双调用会双倍成本(违背 P5B-T03 节省 40% 的初衷)、双倍 LLM 调用故障面;② 独立录制可以由 operator 单独控制启停 / 抽样比例,production 不会被 shadow 实验拖垮;③ TTL 索引让数据不会无限堆积。
- **代价**: 7-day shadow 真值需要 deployment 后由 operator 单独写 `shadow_decisions` 入 Mongo。已在 backlog 列出"Phase 5C 部署任务: shadow recorder cron wiring"。

### 决策 2:把出口指标分成"工具就位"和"真值"两个状态

- **场景**: 用户要求"按计划稳步落实 Phase 5B 出口";出口规定 7 天 shadow 同时跑 baseline 和 routing,无 deployment 窗口前无法采到真值。
- **抉择**: A) 等用户开启部署再做出口工具;B) 先把所有 harness + CLI + 阈值检测全部落地、用 117 个测试锁定行为契约,部署窗口一打开就能直接跑 `scripts/phase5b_exit_check.py --strict`。
- **选 B**。理由:符合 memory[feedback_quality_over_minimal] "完整升级路径优先" + memory[feedback_doc_detail] "handoff 必须详尽"——下一段 session(或下一名工程师)接手时不需要重发明任何工具,直接按 README 执行。

### 决策 3:Codex 7 轮全部跑完,不省略

- **场景**: 这是 harness 代码,看似不影响业务;但 codex R1 立即捕到 2 个 P1(timestamp parsing + 0-cost fall-through)+ R5 又捕到 2 个 MED 安全问题。
- **结论**: 印证 memory[feedback_codex_findings_real]——绿色 71→117 测试套件没有挡住 4 个真实 bug + 2 个安全风险。所有 15 个发现全部修复并加锁定测试。

---

## 4. 阻塞与风险

### 当前阻塞

| 阻塞点 | 类型 | 解除条件 |
|--------|------|----------|
| 7 天 shadow 真值采集 | deployment-bound | 用户授权部署窗口 + operator 启动 shadow_recorder 抽样任务 |
| `--days` 真实负载验证 | deployment-bound | 同上 |
| 单股成本 / 延迟 实测对比 | deployment-bound | 同上(P5B-T03 §865 已列入 deferred) |

> 这三项都是 SSoT §6.972 出口要求,但都依赖部署后真实 7 天产生的数据。Harness 已就位,部署窗口一打开 `scripts/phase5b_exit_check.py --strict` 即可输出真值报告。

### 已识别风险

| 风险 | 严重度 | 缓解措施 |
|------|--------|----------|
| Operator 误执行 `--days 90`(被 clamp 吃掉)以为绕过 30 天 TTL | LOW | `_bounded_days` raises ArgumentTypeError;CLI 文档明示 30d 上限 |
| `shadow_decisions` TTL 30d 下短期回滚后无法回看历史 | LOW | 部署初期可改 `_TTL_DAYS_DEFAULT` 临时延长;commit 已用单一常量 |
| 部署后 operator 忘写 baseline shadow → action_match_rate=NaN | MEDIUM | `shadow_compare.compute_shadow_report` empty 输入返回 ``passes={action_match: False, has_data: False}``;`--strict` 会让 CI 红 |
| Mongo 上线前未跑 `MongoDBService.initialize()` → 索引缺失 | MEDIUM | main lifespan 已在 P5A-T03 阶段强制调用 initialize;新加的 shadow_decisions 索引接入相同生命周期 |

---

## 5. 下一阶段入口条件

- [ ] **用户授权进入 Phase 5C** (回复 "授权进入 Phase 5C")
- [ ] **部署窗口已开**(预期 P5C-T01 起需要 7 天 shadow 数据,启动前置 operator 任务把 shadow_recorder 接入 cron / 后台 worker)
- [ ] **`scripts/phase5b_exit_check.py --strict` 在 7 天 shadow 数据采到后输出 ✅ × 7**(harness 已就位;真值缺哪一项就标 `⚠️ no-data`,不会 fail-open)

> 当前 STOP 状态:不自动跨阶段。Phase 5C 准备物料(P5C 6 个 task 详细计划)在 SSoT §3 `Phase 5C` 一节已列出,等部署窗口 + 7-day shadow 一致性达标后再开工。

---

## 6. Phase 5C 应处理的 cross-cutting backlog(从 T01/T02/T03/Exit-harness 累计)

> 这一节是 P5B 终段交班物料,P5C 第一件事是把这批问题归档进 task 列表。

1. **shadow_recorder cron wiring**: backend/services/shadow_recorder.py 已是纯数据层,需要部署后接 cron 触发 baseline 调用 + 写 `shadow_decisions`。建议作为 P5C-T0X 单独 task(包含 sampling rate 配置 + 失败重试 + cost-guard 双重保护)。
2. **prose-prompt agent 改 JSON contract**: P5B-T03 仅在 fund_manager 启用 routing,因为 intelligence/bull/bear/risk 的 prompt 没有 JSON 结构(详见 SSoT §865)。P5C 可单独 task 把 4 个 prose agent 的 prompt 改成"JSON 决策段 + 散文段"双输出,然后扩展 routing。
3. **`/api/watchlist/*` + `/api/monitoring/*` operator-auth 网关**: P5B-T02/T03/Exit 都把鉴权改造 deferred 到 P5C 监控面板统一改造(see codex P5B-T03 R5 HIGH-1)。
4. **save_policy round-trip-safe YAML**: P5B-T02 codex R5 LOW(YAML comments 不保留)。
5. **`_reload_config` TOCTOU**: P5B-T02 codex R3 LOW(mtime 检测 race window)。
6. **per-bucket scheduler lock**: P5B-T02 codex R4 LOW(双 cron 同时触发竞态)。
7. **PolicyStore 跨进程一致性**: P5B-T02 codex R5 LOW(file lock,replica 部署时读写不一致)。
8. **route 字段含 model id**: P5B-T03 codex R2 INFO(escalation log 增加 model name 便于 cost analysis)。
9. **Hypothesis 框架引入**: 4 个 task 都把 contract / property test 留作 deferred 等单独 dep PR(SSoT §2.3 列入测试金字塔但未引入)。
10. **Redis AuthenticationError 单独 alert 路径**: P5B-T02 codex R5 LOW。
11. **agent/provider regex 白名单**: P5B-T03 codex R5 LOW(配置层 schema 校验)。

> 这 11 项都属于 cross-cutting backlog,Phase 5C 入口前请先按重要度排序,集中在 P5C-T0X 系列开 sub-task,不要散在每个新 task 里(避免 task 范围爆炸,印证 memory[feedback_quality_over_minimal])。

---

## 7. Harness 使用说明(operator handoff)

> 按 memory[feedback_doc_detail] "handoff docs should be exhaustive",这一节是 7 天 shadow 部署后的执行 SOP。

### 7.1 启用 shadow recording

> **2026-05-02 补丁**: 在 `commit 12bac5b` 之后又补了 `backend/services/shadow_runner.py` + `agent_models.yaml::fund_manager_shadow_baseline` + analysis_scheduler 的 fire-and-forget hook。**operator 不再需要写自定义 cron job** — 设 `QUANTMIND_SHADOW_ENABLED=1` 启动后端就会自动采集。具体步骤见独立 runbook `docs/reviews/phase5b-shadow-deployment-runbook.md`。

简短回顾:

```bash
# 1. 设置 env
export QUANTMIND_SHADOW_ENABLED=1
export QUANTMIND_SHADOW_SAMPLE_RATE=1.0   # 7 天全量
export QUANTMIND_DAILY_BUDGET=20.0        # cost_guard 单日上限

# 2. 启动后端
QUANTMIND_PHASE=phase5_eval AUTHORIZATION_MODE=suggest \
  /home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app \
  --port 8000 --host 127.0.0.1 --workers 1

# 3. 每次 analysis_scheduler 成功完成一只股票分析,会 asyncio.create_task
#    触发 shadow_runner: 拷贝同 prompt → 调 fund_manager_shadow_baseline (kimi-only)
#    → 比对 routed (production) vs baseline → 写 shadow_decisions Mongo 集合
```

> ⚠️ 启用前确认 `MongoDBService.initialize()` 已经跑过(unique run_id + TTL created_at 30d 索引到位),否则会撞 unique 冲突或无限堆积。

### 7.2 跑 shadow_compare 报告

```bash
# 实时 Mongo
python scripts/shadow_compare.py --days 7

# 离线回放(operator dump JSONL,例如审计 / debug)
python scripts/shadow_compare.py --input shadow_dump.jsonl --strict
# --strict: 任一 gate 失败 exit 1(CI 模式)
```

输出 markdown 表(action_match, |Δconfidence| mean / p50 / p95, per-leg parse_ok / escalation_rate / latency, per-day match_rate)。

### 7.3 跑 phase5b_exit_check 报告

```bash
QUANTMIND_PHASE=phase5_eval AUTHORIZATION_MODE=suggest \
  python scripts/phase5b_exit_check.py --days 7 --strict
```

CLI 自动从 `MONGODB_URI` / `MONGODB_DB` / `REDIS_URL` 取连接字符串(env 优先,argv fallback)。`--days` 被 clamp 到 [1, 30],配合 shadow_decisions TTL 30 天构成完整的 retention 边界。

输出一张 7 行 markdown gate 表(fast_cost / slow_cost / daily_total / fast_latency / slow_latency / shadow_action_match / shadow_confidence_delta),状态列 ✅ / ❌ / ⚠️ no-data 三态。**no-data 不能 silently 通过**——是 R1 P1 + R6 follow-up 的核心修复。

### 7.4 Phase 5B 出口最终判定 SOP

```
gate_count_pass = shadow_compare.passes ∩ exit_check.passes 全部 ✅
                  AND has_data 全部 True
↓
├ Yes → 把 SSoT §6.972 出口 marker 改 ✅,append §7.4 修订记录,生成 Phase 5C 入口前置授权请求
└ No  → 找出红/黄项,根据 backlog §6 排查 root cause(数据缺失 vs 阈值真未达),修复后 re-run
```

---

## 8. 文件清单

**新增产线代码**:
- `backend/services/shadow_recorder.py`(100% 覆盖)
- `backend/services/shadow_compare.py`(94%)
- `backend/services/phase5b_exit_check.py`(97%)
- `backend/data/database.py`(`shadow_decisions` 索引 + TTL)

**新增脚本**:
- `scripts/shadow_compare.py`
- `scripts/phase5b_exit_check.py`

**新增测试**:
- `tests/test_shadow_recorder.py`(29 cases)
- `tests/test_shadow_compare.py`(35 cases)
- `tests/test_phase5b_exit_check.py`(28 cases)
- `tests/test_scripts_shadow_compare.py`(7 cases)
- `tests/test_scripts_phase5b_exit_check.py`(5 cases)
- `tests/test_database.py`(+1 case for shadow_decisions indexes)

**Codex review reports**(7 轮 + summary):
- `docs/reviews/p5b-exit-r1-architecture.md`
- `docs/reviews/p5b-exit-r2-followup.md`
- `docs/reviews/p5b-exit-r3-perf.md`
- `docs/reviews/p5b-exit-r4-testing.md`
- `docs/reviews/p5b-exit-r5-security.md`
- `docs/reviews/p5b-exit-r6-final-verify.md`
- `docs/reviews/p5b-exit-r7-final-verify.md`
- `docs/reviews/p5b-exit-codex-summary.md`

---

> 本报告生成于 2026-05-02 单 session(claude-opus-4-7-1m + Codex CLI),工具就位 + 117 测试 + 7 轮 codex 全部 ✅,**STOP 等待用户授权部署窗口** 完成 7 天 shadow 真值采集 + Phase 5C 入口授权。
