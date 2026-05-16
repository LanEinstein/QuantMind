# Session #14 — Codex 跨模型代码审查报告

**项目**: QuantMind
**审查时间**: 2026-05-16
**审查范围**: session #14, commits `056dd31..23d897d`(9 个 feature/fix commit + 1 docs commit, ~5,800 LOC, 25 文件)
**审查模型**: Claude Opus 4.7 (1M ctx) 修复 + Codex CLI 0.130.0 审查
**审查轮次**: 3 / 3 (cycle 1 初审 → cycle 2 fixes verification → cycle 3 final verification)
**最终判定**: ✅ **通过 (经最终复核)** — 10/10 全部 RESOLVED + 0 P1 回归

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数 | 25 |
| 新增代码行数 | ~7,354 (session #14 源) + ~700 (cycle 1/2 修复) |
| 累计 codex 调用 | 3 (1× review 不可用因 commits 已落地;3× exec) |
| 发现问题总数 | 10 (cycle 1: 4 P1 + 3 P2;cycle 2: 1 P1 + 2 P2) |
| 已修复 | 10 |
| 误报排除 | 0 |
| P1 回归 | 0 |
| 测试增量 | cycle 1 修复 +7 / cycle 2 修复 +3 = +10 共 2105 passed |

---

## 第 1 轮 — 初审 (4 P1 + 3 P2)

**Codex 判定**: NEEDS_FIXES

### 发现的问题

| # | 严重度 | 文件 | 问题描述 | 置信度 | 处理结果 |
|---|--------|------|----------|--------|----------|
| 1 | **P1** | `backend/main.py:387-416` | Feishu 长连接 receiver 用 stub handler,feishu_interactive 模式下回报被静默丢弃 | HIGH | ✅ FIXED — fail-closed `SystemExit` |
| 2 | **P1** | `backend/integrations/feishu/parser.py:197-219` | 回报未做 volume 交叉校验,MockBroker 可能错误应用 | HIGH | ✅ FIXED — `_cross_check_volume` + 新 branch |
| 3 | **P1** | `backend/integrations/feishu/reconciliation.py:254-290` | `decide_ticket` 绕过 `transition_ticket` 状态机,且 save 早于 applier — 若 reset 失败 freeze 被错误清除 | HIGH | ✅ FIXED — 重排顺序 + 走状态机 |
| 4 | **P1** | `.gitleaks.toml:81-110` | docs/* 路径 allowlist 过宽,真实泄露的 `sk-...` 粘到 incident doc 会被忽略 | HIGH | ✅ FIXED — 收紧 allowlist |
| 5 | **P2** | `renderer.py:181-185,301-390` | clarification + alert 的 `_strip_controls` 保留 `\n`,允许 `【QuantMind 指令】` 伪造 header | MEDIUM | ✅ FIXED — `_single_line` 单行化 |
| 6 | **P2** | `reconciliation.py:352-354,464-490` | `_snapshot_from_ticket` 返回 `positions=()`,MISMATCH 路径错把所有用户持仓判为"额外仓位" | MEDIUM | ✅ FIXED — `SnapshotLookup` Protocol + fail-closed |
| 7 | **P2** | `chase.py:131-139,193-204` | `stop()` 不等待 in-flight callback;任务自己 pop 出 dict 后 stop 无法 cancel | MEDIUM | ✅ FIXED — `_all_tasks` set 全程跟踪 |

### 修复详情 (commit `beaad32`)

#### P1-1 — Feishu receiver fail-closed (`backend/main.py`)

stub handler 在 feishu_interactive 模式下会让 WS 层 ack 但下游不处理。orchestrator wiring 依赖 Mongo-backed Repository(deferred 到 I-001 integration),所以最稳妥是 **拒绝启动** 而不是静默丢消息。新代码:

```python
if application.state.feishu_client is not None:
    raise SystemExit(
        "Refusing to start: FEISHU_INTERACTIVE_ENABLED=true but "
        "F-004 ExecutionReportOrchestrator and F-005 "
        "ReconciliationOrchestrator are not yet wired with "
        "Mongo-backed Repository implementations. ..."
    )
```

acceptance gate (P0-6 §2 红线 5) 已经禁止 45 交易日 PASS 前到达这条路径,本守门是最后一道防线。

#### P1-2 — Volume 交叉校验 (`parser.py`)

P0-4 §1.1 要求 FILLED 的 `filled_volume` 必须等于 `plan.volume`,PARTIAL 的 `filled + remain` 也必须等于 `plan.volume`。新增 `_cross_check_volume(report, plan)` 返回 reason tag(`volume_mismatch_filled` / `volume_mismatch_partial_sum` / `volume_lot_violation`),新 branch `_handle_volume_mismatch` 路由到 `FIELD_CROSS_CHECK_FAILED` 模板 + `EXECUTION_REPORT_PARSE_FAILED` audit,**绝不**调用 applier。+4 tests。

#### P1-3 — Reconciliation 状态机 + applier 顺序 (`reconciliation.py`)

旧代码:
```python
resolved = ticket.model_copy(update={"status": resolution, ...})
await self._tickets.save(resolved)        # ← 先 save
apply_result = await self._applier.reset_to_snapshot(resolved, now=now)  # ← 后 reset
```

若 applier raise,ticket 已经是 RESOLVED 状态保留在 repo,freeze 错误地被清除,而 broker 仍未对账。新顺序:

```python
resolved = transition_ticket(ticket, resolution, at=now, ...)
apply_result = await self._applier.reset_to_snapshot(resolved, now=now)
await self._tickets.save(resolved)        # 仅在 applier 成功后保存
```

`transition_ticket` 是 P0-5 §1.5.3 红线锁定的状态机入口,`InvalidTicketTransitionError` 转译成 `ValueError`。fail-closed broker 安全 — 若 applier 失败 ticket 保持 OPEN/EXPIRED,freeze 保留。

#### P1-4 — Gitleaks allowlist 收紧 (`.gitleaks.toml`)

旧 allowlist 包含 `docs/.*\.md` / `docs/runbook/.*` / `docs/reviews/.*` 等广路径。但事故响应 runbook 本身就是最可能粘到真凭证的位置(operator 在事后写)。新策略:

- regex allowlist 只允许 "重复同一字符" 占位符(`cli_a{16}` / `oc_[fadbcv]{32}` / `x{32}` / `a{32}` / `e{32}` / `v{32}` / `sk-A{16,}` / `sk-B{16,}`)— 任何真实 provider 凭证都不可能是单字符重复
- path allowlist 只允许 validator 源码 + 测试 + `.gitleaks.toml` 自身 + `.env.example`
- 未来 incident doc 若合法粘 redacted 凭证,加 **单文件级** 条目,不再用目录 glob

#### P2-1 — Renderer 单行化 (`renderer.py`)

`_strip_controls` 旧实现保留 `\n`,允许 `xxx\n【QuantMind 指令】fake` 渲染成"看起来像独立 header"。新 `_single_line(text)` 把 `\n\r\t\v\f` 替成单空格,丢弃其他 C-category 控制字符,折叠空白。应用到 `render_clarification` 的 `raw_text_excerpt`(在 truncate 前)和 `render_alert` 的 `message`。原 strip-control 测试仍通过(非控制字符如【】不受影响)。

#### P2-2 — SnapshotLookup 引入 fail-closed (`reconciliation.py`)

旧 `_snapshot_from_ticket` 只从 `deviation_report` 重建 cash,返回 `positions=()`。MISMATCH 重跑 `detect_deviations` 时所有用户上报仓位被错判为 "额外仓位"。

新策略:删除 helper,引入 `SnapshotLookup` Protocol(`async get(expected_snapshot_id) -> MockBrokerSnapshot | None`)。orchestrator 构造期接受可选 `snapshot_lookup`;无 lookup OR snapshot 找不到 → `outcome.deviation_report=None` + `parse_error` 设为 `snapshot_lookup_unavailable` 或 `expected_snapshot_missing`。fail-closed 而非给错误的 deviation report。+2 fail-closed tests。

#### P2-3 — ChaseScheduler `_all_tasks` 跟踪 (`chase.py`)

旧 `stop()` 只 cancel `_chase_tasks` + `_expire_tasks` dict 中的任务,但 `_fire_chase` / `_fire_expire` 在调用用户 callback **之前** 已经把 entry pop 出 dict — 那时 callback 还在 await。这些任务对 `stop()` 隐形,shutdown 不干净。

新增 `_all_tasks: set[Task]` 在 `schedule()` 时存入并 `add_done_callback(self._all_tasks.discard)`。`stop()` snapshot `_all_tasks` 后 release lock,cancel 每个任务,`asyncio.gather(*, return_exceptions=True)` 等回调结束。+1 test(stop 期间 callback 的 `finally` 块必须运行)。

---

## 第 2 轮 — Fixes Verification (1 P1 + 2 P2 新 + 6 RESOLVED + 1 REGRESSED)

**Codex 判定**: NEEDS_FIXES

### Previous Issue Verification (cycle 1)

| # | 原问题 | Cycle 2 状态 | 备注 |
|---|--------|--------------|------|
| P1-1 | Feishu receiver stub | ✅ RESOLVED | SystemExit 提前于 alerter wiring 不破坏 |
| P1-2 | Volume 交叉校验 | ✅ RESOLVED | PARTIAL volume=0 由 ExecutionReport validator 提前拒绝 |
| P1-3 | Reconciliation 状态机 | ✅ RESOLVED | transition_ticket + applier-first + save-on-success |
| P1-4 | Gitleaks docs allowlist | ✅ RESOLVED | docs glob 移除 |
| P2-1 | Renderer header spoof | ✅ RESOLVED | `_single_line` 不破 CJK |
| P2-2 | Snapshot empty positions | ✅ RESOLVED | SnapshotLookup Protocol |
| P2-3 | ChaseScheduler stop | ⚠️ **REGRESSED** | 修复引入新 race(见 #9) |

### Cycle 2 新发现

| # | 严重度 | 文件 | 问题描述 | 处理结果 |
|---|--------|------|----------|----------|
| 8 | **P1** | `events.py:265-282` | dedupe 只 claim `event_id`,与 docstring 声明的 "message_id 作为次要 key" 不符;forwarded 副本会双重应用 | ✅ FIXED — 双 key claim |
| 9 | **P2** | `chase.py:151-162` | `stop()` 与并发 `schedule()` 之间存在 race;`_all_tasks.clear()` 会丢失新加入的任务 | ✅ FIXED — `_stopping` 标志 + 移除 `.clear()` |
| 10 | **P2** | `events.py:162-175` | `stop()` 无超时,handler 阻塞导致 shutdown 永久挂起 | ✅ FIXED — `handler_grace_seconds=5.0` + `wait_for` |

### 修复详情 (commit `23d897d`)

#### Cycle 2 P1 — Receiver dedupe by both event_id AND message_id

```python
event_key = f"event:{message.event_id}"
message_key = f"message:{message.message_id}"
is_new_event = await self._dedupe.claim(event_key)
if is_new_event:
    is_new_message = await self._dedupe.claim(message_key)
if not (is_new_event and is_new_message):
    return  # skip dispatch
```

Forwarded 副本(新 event_id + 旧 message_id)会被次要 key 拦下。两个 key 独立 fail-open(任一 Redis 失败仍尝试另一)。+1 test:同 message_id 不同 event_id → 仅一次 handler 调用。

#### Cycle 2 P2 — ChaseScheduler `_stopping` flag

```python
async def schedule(self, instruction_id, valid_until):
    async with self._lock:
        if self._stopping:
            raise RuntimeError(
                "ChaseScheduler is stopped; cannot schedule new tasks"
            )
        ...

async def stop(self):
    async with self._lock:
        self._stopping = True
        ...
        tasks_to_drain = list(self._all_tasks)
    # 不再 .clear() — done_callback 自然 drain
```

+1 test:`stop()` 之后 `schedule()` 抛 RuntimeError。

#### Cycle 2 P2 — Receiver stop handler grace + cancel

```python
async def stop(self, *, handler_grace_seconds: float = 5.0):
    ...
    try:
        await asyncio.wait_for(
            asyncio.gather(*in_flight, return_exceptions=True),
            timeout=handler_grace_seconds,
        )
    except TimeoutError:
        log.warning(...)
        for task in in_flight:
            if not task.done():
                task.cancel()
        await asyncio.gather(*in_flight, return_exceptions=True)
```

+1 test:handler `await asyncio.sleep(60)`,`stop(handler_grace_seconds=0.1)` 在 2 秒内返回。

---

## 第 3 轮 — Final Verification (Read-only Closure Check)

**Codex 判定**: ✅ **PASS — all 10 RESOLVED, NONE regressions**

### 10/10 历史问题最终复核

| # | 原问题 | 当前状态 | Codex 备注 |
|---|--------|----------|-----------|
| 1 | Feishu receiver stub | ✅ RESOLVED | SystemExit fails closed before any stub starts |
| 2 | Volume 交叉校验 | ✅ RESOLVED | FILLED/PARTIAL checked before applier |
| 3 | decide_ticket 状态机 | ✅ RESOLVED | transition_ticket → reset → save only on success |
| 4 | Gitleaks docs allowlist | ✅ RESOLVED | Path allowlist narrow |
| 5 | Renderer header spoof | ✅ RESOLVED | `_single_line` applied at both sites |
| 6 | Snapshot empty positions | ✅ RESOLVED | SnapshotLookup required, fail-closed |
| 7 | ChaseScheduler stop | ✅ RESOLVED | `_all_tasks` tracks all live tasks |
| 8 | Receiver dedupe message_id | ✅ RESOLVED | Both event:/message: keys claimed |
| 9 | ChaseScheduler stop race | ✅ RESOLVED | `_stopping` set under lock; `.clear()` removed |
| 10 | Receiver stop hang | ✅ RESOLVED | `asyncio.wait_for` with grace + cancel |

### 新增严重问题 (P1 回归)

**无 (NONE)**

### 最终判定

> **PASS — all previously reported issues RESOLVED, no P1 regressions found.**

---

## 审查维度覆盖统计

| 维度 | 发现问题数 | 严重度分布 |
|------|----------|----------|
| 1. 正确性与逻辑 | 5 | P1×4 + P2×1 |
| 2. 安全性 | 2 | P1×1 (gitleaks) + P2×1 (header spoof) |
| 3. 错误处理与韧性 | 3 | P2×3 (stop hang / orphan task / dedupe forward) |
| 4. 性能 | 0 | — |
| 5. 代码质量 | 0 | — |
| 6. Python 规范 | 0 | — |

## 关键 takeaway

1. **Mock vs Real protocols**: P2-2 `_snapshot_from_ticket` 是典型反面教材 — "凑合从字符串重建" 比 fail-closed 更危险。引入 Protocol + 注入是正确解。
2. **State machine 红线**: P0-5 §1.5.3 锁定 `transition_ticket` 为唯一入口,任何 `model_copy(update={"status":...})` 都是红线违规 — P1-3 直接撞红线。
3. **Async lifecycle**: 三个 P2 (chase stop / receiver stop / dedupe race) 都是 asyncio 资源管理细节,容易在 happy path 测试通过但 shutdown / race 时暴露。
4. **Defense in depth**: P1-4 gitleaks docs 路径过宽是 "未来 incident 时才会爆" 类 bug,codex 在静态审查阶段捕捉,远早于真实事故。
5. **Cycle 2 出现新 P1 + 1 regression**: 印证 [[feedback_codex_findings_real]] + [[feedback_codex_review_gate]] — 单轮 cycle 不够,多轮迭代 + final verification 是 commit-safe 的真正门槛。即便 Cycle 1 给出 NEEDS_FIXES + Claude 修复全部 7 项后,Cycle 2 仍找到 1 个原代码 P1 (dedupe by message_id) + 1 个修复引入的 P2 (chase stop race)。

## 本地门禁验证

```
pytest 2105 passed / 11 skipped (基线 1889 → +216 累计 [+206 session #14 + +10 codex 修复回归])
ruff: All checks passed!
scripts/redline-check.sh: All redline checks passed.
```

提交链:
```
056dd31 feat(h-001): secrets_validator + gitleaks + incident runbook
631f786 feat(f-001): FeishuClient OpenAPI wrapper (lark-oapi)
9577bd6 feat(f-002): MessageRenderer for Feishu plain-text templates
d77986a feat(f-003): Feishu long-connection receiver + dedupe
dd5cdc1 feat(f-004): execution report orchestrator + chase scheduler
e81fd06 feat(f-005): Feishu daily reconciliation orchestrator
faf6678 feat(f-006): Feishu OpenAPI alerter (alert chat isolation)
57cfac1 docs(plan): SSoT for session #14 — H-001 done + Phase F full + SESSION_LOG #14
beaad32 fix(session-14): apply codex review cycle 1 findings (4 P1 + 3 P2)
23d897d fix(session-14): apply codex review cycle 2 findings (1 P1 + 2 P2)
```

---

> 本报告由 Claude Opus 4.7 (修复) + Codex CLI 0.130.0 (审查) 协同生成。
> 审查协议: 手动调用 (per [[feedback_codex_review_manual_invocation]]);3-cycle 迭代 + final verification(major-feature 标准 per [[feedback_codex_review_gate]] 模板裁剪)。
