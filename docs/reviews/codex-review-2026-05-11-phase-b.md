# Codex 跨模型代码审查报告 — Phase B (B-001..B-005)

**项目**: QuantMind
**审查时间**: 2026-05-11
**审查轮次**: 3 (1 评审 + 1 修复评审 + 1 最终复核重试)
**最终判定**: ⚠️ **PARTIAL PASS — 复核环节 Codex 不可用,但所有 cycle 1/2 发现的 6 个问题均已修复并由专项回归测试覆盖**

## 1. 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数 | 18 (14 源码 + 7 测试,其中 3 modified + 21 新增) |
| 变更行数 | ~4500 (源码 ~2100 + 测试 ~2400) |
| Cycle 1 发现问题总数 | 5 (P1 ×3 + P2 ×2) |
| Cycle 2 新发现问题 | 1 (CRITICAL — 从 fix #2 引入的回归) |
| 已修复 | 6/6 |
| 误报排除 | 0 |
| 未解决 | 0 |

## 2. 各轮次详情

### Cycle 1 — 初次评审 (`codex review --uncommitted`)

**Codex 判定**: NEEDS_FIXES (3 × P1 + 2 × P2)

#### 发现的问题

| # | 严重度 | 文件 | 问题描述 | 处理结果 |
|---|--------|------|----------|----------|
| 1 | P1 (CRITICAL) | `backend/audit/store.py:115` | `model_dump(mode="json")` 序列化 timestamp 为 ISO 字符串,Mongo TTL 索引只对 BSON Date 生效,180 天 TTL 实际失效 | FIXED |
| 2 | P1 (CRITICAL) | `backend/services/ledger.py:167` | upsert 走 JSON mode,read 走严格 Python mode,任何 get_by_instruction/find_by_correlation 在真实 Mongo 写入后会 ValidationError | FIXED (后又触发 cycle 2 回归) |
| 3 | P1 (CRITICAL) | `backend/models/execution.py:75-77` | FILLED/PARTIAL 回报中 side_zh ↔ instruction_id 不一致 / stock_code 不一致 被静默接受,绕开 P0-4 §1.2.1 AMBIGUOUS 路径 | FIXED |
| 4 | P2 (WARNING) | `backend/services/execution_report_parser.py:123-131` | 正则命中但模型语义失败(如 `0股`)抛 Pydantic ValidationError,跳过 ExecutionReportParseError 的 fail-closed 入口 | FIXED |
| 5 | P2 (WARNING) | `backend/models/instruction.py:271-273` | instruction_id 的 HHMMSS 段未与 created_at 交叉校验,允许时间戳漂移 | FIXED |

#### Cycle 1 修复详情

- **Fix 1** (`backend/audit/store.py`): Mongo 写改为 `event.model_dump()`(Python mode),`timestamp` 以 datetime 落库 → motor 编码为 BSON Date,TTL 索引生效。`event_id` 显式 stringify。JSONL 行仍用 `model_dump_json`。新增断言 `isinstance(coll.documents[0]["timestamp"], datetime)`。
- **Fix 2** (`backend/services/ledger.py`): upsert 切换到 Python mode,`_from_mongo` 用 `strict=False` 验证,容忍 Mongo 的 tuple→list 强制转换。新增 `test_mongo_round_trip_through_repo` 端到端测试。
- **Fix 3** (`backend/models/execution.py`): 新增 `_check_side_and_code_consistency()` 方法,从 `instruction_id` 解析出 code + side,反向校验 `side_zh`("买入"↔BUY / "卖出"↔SELL)与 `stock_code`,不一致抛 ValueError。FILLED/PARTIAL 分支都调用。
- **Fix 4** (`backend/services/execution_report_parser.py`): 将 `_build_report` 包到 try/except ValidationError → 重抛为 `ExecutionReportParseError(reason="field_cross_check_failed")`。新增 3 个回归测试:side 不匹配 / code 不匹配 / `0股`。
- **Fix 5** (`backend/models/instruction.py`): `_hms` 改名为 `hms`,新增 `if hms != created_local.strftime("%H%M%S"): raise ValueError(...)`。新增 `TestInstructionIdHHMMSSCrossCheck`。

### Cycle 2 — 修复后回归评审 (`codex exec` incremental)

**Codex 判定**: NEEDS_FIXES (1 × CRITICAL 新回归;5/5 原问题 RESOLVED)

| # | 原问题 | Cycle 2 状态 | 备注 |
|---|--------|--------------|------|
| 1 | Audit TTL timestamp 序列化为字符串 | RESOLVED | Mongo 文档现在保留 `timestamp` 为 datetime |
| 2 | Ledger Mongo 序列化模式不匹配 | **REGRESSED** | ValidationError 修了,但 Python-mode BSON 往返引入 naive UTC datetime — 见新问题 |
| 3 | Execution report side/code 不匹配被接受 | RESOLVED | FILLED/PARTIAL 都做了 instruction_id 反向校验 |
| 4 | 语义 ValidationError 逃出 parser | RESOLVED | `_build_report` 失败被包成 ExecutionReportParseError |
| 5 | Instruction ID HHMMSS 未校验 | RESOLVED | `hms` 现在与 created_at Asia/Shanghai 时间比对 |

#### Cycle 2 新发现 CRITICAL

> **[CRITICAL] `backend/services/ledger.py:177`** — 默认 motor 客户端从 BSON 解码回 datetime 时返回 naive UTC(无 `tz_aware=True`)。`_from_mongo` 验证 as-is,后续 `append_event` 中 aware `at` 与 naive `entry.updated_at` 比较会 TypeError,真实 Mongo 往返后 ledger 生命周期断裂。回归测试用的 fake Mongo 没 BSON 编解码,所以错过了。

#### Cycle 2 修复详情

- **Fix 6** (`backend/services/ledger.py`): 新增 `_attach_utc(value)` 纯函数,递归遍历 dict/list/tuple,把 naive datetime 用 `value.replace(tzinfo=UTC)` 补成 UTC-aware,不变 input。`_from_mongo` 在 `model_validate(strict=False)` 之前先过 `_attach_utc`。
- **Regression test**: 新增 `_FakeMongoServiceWithBson` 在 upsert 时 strip tzinfo(mimick motor 默认 codec),`test_mongo_round_trip_through_repo` 断言 (a) `fetched.created_at.tzinfo is not None`,(b) 往返后的 entry 上 `append_event` 不 TypeError。

### Cycle 3 — 最终复核

**Codex 判定**: ❓ **UNKNOWN — Codex CLI 1000s 超时 ×2**

Codex `codex exec --ephemeral -s read-only` 调用两次都在 1000 秒后被 timeout SIGTERM。两次提示词不同(详细版与精简版均超时),指向上游 Codex API 不可用而非提示词问题。Per skill phase 5: 两次连续 UNKNOWN → EXIT_REASON=`codex_unavailable`,跳过 phase 6 最终验证,并在报告中显著标注。

**降级判断的本地证据(替代 cycle 3 外部验证)**:

1. **专项回归测试通过**:`test_mongo_round_trip_through_repo` 在新增的 `_FakeMongoServiceWithBson` 上断言 cycle 2 回归不可重现 — fake 在 upsert 时主动 strip tzinfo,模拟 motor 默认 codec 的精确行为。
2. **`_attach_utc` 是纯函数**:不可变递归遍历,16 行代码,显式覆盖 datetime/dict/list/tuple 4 种类型;aware datetime 路径(`value.tzinfo is None` 检查)走 no-op。
3. **全量门禁绿**:`pytest -q` 1282 passed + ruff clean(所有 Phase B 文件) + `scripts/redline-check.sh` 全绿。

## 3. 修复后门禁汇总

| 门禁 | 结果 |
|------|------|
| `/home/ps/anaconda3/envs/zhanglan/bin/pytest -q` | **1282 passed**, 11 skipped(基线 1140 → +142 新测试) |
| Phase B 文件 ruff check | **All checks passed** |
| `scripts/redline-check.sh` | **All redline checks passed**(包含新增 P0-8 evidence_id 前缀扫描) |
| `cd frontend && npm run type-check` | **clean** |
| `cd frontend && npm run test -- --run` | **80 passed** |

## 4. 审查维度覆盖

| 维度 | 关注点 | Cycle 1 发现 | Cycle 2 新发现 |
|------|--------|--------------|----------------|
| 正确性与逻辑 | Mongo 序列化 / 状态机 / 跨字段校验 / 时间戳 | 4 | 1 |
| 安全性 | LLM 隔离 / 凭证 fingerprint / extra='forbid' | 0 | 0 |
| 错误处理 | fail-closed 路径完整性 | 1 (P2) | 0 |
| 性能 | TTL 索引可用性 | 1 (P1, 与正确性相关) | 0 |
| 代码质量 | 可读性 / 重复 | 0 | 0 |
| 语言规范 | Pydantic strict / Python idiom | 0 | 0 |

## 5. Phase B 实施范围

| 任务 | 新增文件 | 主要模型/服务 | 测试数 |
|------|----------|---------------|--------|
| B-001 | `backend/models/evidence.py`、`backend/models/instruction.py`、`backend/services/instruction_plan.py` | InstructionPlan / InstructionSide / InstructionStatus / DataSnapshot / PositionSummary / RiskCheckSummary / EvidencePrefix(5 类锁定) | 102 |
| B-002 | `backend/models/ledger.py`、`backend/services/ledger.py`、DB indexes | DecisionLedgerEntry / LedgerEvent(12 kinds) / DecisionLedgerService / MongoLedgerRepository / `_attach_utc` | 20 |
| B-003 | `backend/execution/regex_patterns.py`、`backend/models/execution.py`、`backend/services/execution_report_parser.py`、`backend/services/instruction_state_machine.py` | 9 正则 + ExecutionReport(FILLED/PARTIAL/UNFILLED × NONE/AMEND/POST_CLOSE) + 17 ALLOWED_TRANSITIONS + 16:00 freeze + 跨字段校验 | 45 |
| B-004 | `backend/models/reconciliation.py`、`backend/services/reconciliation_{threshold,parser,state_machine}.py` | 5 form parser(全角冒号 U+FF1A) + threshold(cash 1元 / volume 0% / cost 0.01元) + 7 ALLOWED_TICKET_TRANSITIONS | 40 |
| B-005 | `backend/audit/{__init__,models,store}.py` + Mongo indexes(TTL 180d) | AuditEventType(40 enum 值 / 5 类 / 评注 "34 类" 系 doc 用语) + AuditActor ×5 + AuditOutcome ×4 + AuditStore JSONL-first dual-write + 凭证 plaintext 黑名单 | 36 |

## 6. 决策文档对齐

| 决策 | 主要落点 |
|------|----------|
| P0-3 | InstructionPlan schema + state machine + valid_until 三连约束 + evidence_id 前缀 |
| P0-4 | ExecutionReport 5 形态严格正则 + 状态机 + 16:00 freeze + 跨字段校验 |
| P0-5 | DailyReconciliation + DeviationReport + ReconciliationTicket + 3 选 1 裁定 + OPEN/EXPIRED freeze |
| P0-7 amendment | risk_summary length=14 + passed 类型 bool \| None |
| P0-8 §1.6.2 | evidence_id 5 前缀 + redline scan |
| P1-6 + 3 amendments | AuditEvent 40 enum + Mongo TTL 180d + JSONL 30d 双写 + 评注修正 doc "22→34" 算术 |
| P2-2 | EVOLUTION_EVENT_TYPES(7 类)+ SYSTEM_ONLY_ACTORS 校验 |

## 7. 已知未尽事项 / 后续接驳点

| 项 | 何时落地 |
|----|----------|
| InstructionPlanBuilder(TradingSignal → InstructionPlan)装配 | Phase D(D-001~D-005) |
| RiskEngine 7-check → 14-check 扩展 | Phase D |
| 真实 MongoDB 集成测试 | Phase E(E-001~E-008)接 MockBroker 时一起做 |
| ExecutionReport orchestrator(parse → state_machine.transition → ledger.append_event) | Phase E / Phase F |
| Reconciliation parser → ticket lifecycle orchestrator | Phase E |
| AuditStore wiring 到 P1-5 两写入端点 + 全 4 类事件触发点 | Phase F / Phase H |
| `scripts/query_audit.py` CLI + `backend/api/audit.py` GET | Phase H(H-001~H-004) |
| Frontend regex mirror 与后端 PATTERNS_AS_DICT 等价性测试 | Phase G(G-008 用户回报录入页) |

## 8. 最终判定与签注

> **PARTIAL PASS — 修复 6/6,门禁 1282/1282 + ruff + redline 全绿;cycle 3 外部复核因 Codex CLI 不可用降级到本地回归测试 + 纯函数复审。建议落入主分支(commit hash 落地后回填 SSoT B-001..B-005 + SESSION_LOG)。**

签注:
- 所有 P1/P2 issue 均有专项 regression test 覆盖,后续若 codex 复核恢复可补跑 cycle 3。
- `_attach_utc` 是稳态修复(只需 datetime 转 aware,无副作用);若后续接入真实 motor 客户端,可优先尝试 `tz_aware=True` codec 配置,届时 `_attach_utc` 仍然安全(aware 输入走 no-op)。
- "AuditEventType 34 类" 的 doc 用语已在 backend/audit/models.py 内联注释 + 测试名修正为"matches_amendment",物理 enum 实际 40 个值。

---

> 本报告由 Claude Code + Codex CLI 协同生成
> 审查模型: Claude Opus 4.7 (修复) + OpenAI Codex CLI (cycle 1+2 review,cycle 3 timeout 降级)
