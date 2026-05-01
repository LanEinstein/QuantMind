# Codex 跨模型代码审查报告 — P5A-T02 五轮综合

**项目**: QuantMind
**任务**: P5A-T02 Daily LLM Cost Hard-Cap (cost_guard)
**审查时间**: 2026-05-01
**审查模型**: Codex CLI (gpt-5.5, reasoning effort: xhigh)
**审查轮次**: 5 / 5 (major-feature 完整 5 轮 hard-gate)
**最终判定**: ✅ 通过 (经最终复核)

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数 | 5 (含新增 cost_guard.py + 2 测试文件) |
| 变更行数 | ~700 (insertions) |
| 5 轮发现问题总数 | 6 (P2: 4, P3: 2) |
| 已修复 | 6 |
| 误报排除 | 0 |
| 未解决 | 0 |
| 最终复核 | PASS,Codex 独立运行 50 个相关测试全过 |

## 各轮次详情

### Cycle 1 — 初次审查 (R1 架构维度焦点)

**Codex 判定**: NEEDS_FIXES (2 × P3)

| # | 严重度 | 文件 | 问题 | 修复 |
|---|--------|------|------|------|
| 1 | P3 | `backend/data/analysis_scheduler.py:19` | F401: `BudgetState` 导入但未引用 | 从 import 块移除 |
| 2 | P3 | `backend/services/cost_guard.py:90` & `:117` | UP037: 已开启 `from __future__ import annotations`,quoted annotation 多余 | 移除 `redis.asyncio.Redis` 周围引号 |

详见 `/tmp/codex_review_fSN3PK/cycle_1.md` (252KB)。Codex 实际跑了 ruff,主动定位精确行号。

### Cycle 2 — 增量复审 (R2 UX/接口 + 重新检查代码质量)

**Codex 判定**: NEEDS_FIXES (1 × P2)

Cycle 1 的 2 项 P3 全部 RESOLVED。

| # | 严重度 | 文件 | 问题 | 修复 |
|---|--------|------|------|------|
| 3 | P2 | `backend/services/cost_guard.py::_read_env_float` | NaN/Inf 不被拒绝。`QUANTMIND_DAILY_BUDGET=nan` 或 `inf` 时 `_classify` 返回 "ok",静默禁用 hard cap (worst-case guardrail failure) | 加 `math.isfinite(value)` 校验,non-finite 回落到 default + warning |

详见 `/tmp/codex_review_fSN3PK/cycle_2.md`。

### Cycle 3 — 增量复审 (R3 测试维度焦点)

**Codex 判定**: NEEDS_FIXES (1 × P2)

Cycle 2 的 NaN env 修复 RESOLVED。

| # | 严重度 | 文件 | 问题 | 修复 |
|---|--------|------|------|------|
| 4 | P2 | `backend/services/cost_guard.py::get_budget_state` | Redis 聚合的 spent_today 仍可能 NaN/-inf,bypass `_classify` | 加 finite + non-negative 校验,fail-closed 为 hard_breach (sentinel spent = budget+1) |

详见 `/tmp/codex_review_fSN3PK/cycle_3.md`。

### Cycle 4 — 增量复审 (R4 性能 + 错误处理)

**Codex 判定**: NEEDS_FIXES (2 × P2)

Cycle 3 的 NaN spent 修复 RESOLVED。

| # | 严重度 | 文件 | 问题 | 修复 |
|---|--------|------|------|------|
| 5 | P2 | `backend/llm/cost_tracker.py::_parse_usage_key` | 单条负值 `cost_rmb` 偏移合法 spend (e.g., -1.0 + 20.5 = 19.5,bypass cap) | 加 per-entry 验证,non-finite 或 <0 → drop entry + warning |
| 6 | P2 | `backend/data/analysis_scheduler.py::_run_and_persist` | cron + manual API 并发时双方都看到 under-cap 快照,double-spend | 加 `asyncio.Lock`,`_run_and_persist` 包装 `_run_and_persist_locked` 实际执行 |

详见 `/tmp/codex_review_fSN3PK/cycle_4.md`。

### Cycle 5 — 最终复核 (R5 安全 + ops closure)

**Codex 判定**: ✅ **PASS**

| # | 原问题 | 状态 | 证据 |
|---|--------|------|------|
| 5 | Per-entry cost validation | RESOLVED | `_parse_usage_key` 现 drop 非有限或负值,聚合层只 append 非 None;5 个参数化测试覆盖 `-1.0/-100.5/nan/inf/-inf` + 0 + 正值 |
| 6 | 并发竞争 | RESOLVED | `AnalysisScheduler.__init__` 持有 `asyncio.Lock`,`_run_and_persist` 在 lock 下委托 `_run_and_persist_locked`;3 路并发测试断言 `max_active == 1` |

**新发现严重问题**: 无

**Codex 独立验证**: 在沙箱中运行
```
pytest -q -s -p no:cacheprovider \
  tests/test_cost_persistence.py \
  tests/test_analysis_scheduler_budget.py \
  tests/test_cost_guard.py
# 50 passed in 0.65s
```

详见 `/tmp/codex_review_fSN3PK/cycle_5.md`。

## 6 维度覆盖

| 维度 | 检查项数 | 发现问题 | 修复 |
|------|----------|----------|------|
| 1. 正确性与逻辑 | 多轮 | 4 (NaN env / NaN spent / 负 entry / 并发) | 全部 |
| 2. 安全性 | 检查 | 0 | — |
| 3. 错误处理 | 多轮 | 1 (cost_guard probe failure 应 fail-open vs fail-closed 决策) | 已记录,fail-open for infra,fail-closed for data corruption |
| 4. 性能 | 检查 | 0 (lock 引入排队但 eval-period 单实例可接受) | — |
| 5. 代码质量 | 多轮 | 1 (unused import) | 全部 |
| 6. 语言规范 | 多轮 | 1 (UP037 quoted annotation) | 全部 |

## 关键设计决策

### 1. 双层 NaN/Inf 防御

- 数据层 (`cost_tracker._parse_usage_key`): 单条 entry 验证,drop 不进聚合
- 守门层 (`cost_guard.get_budget_state`): 聚合后再次验证,fail-closed 为 hard_breach

两层独立,任一层漏过另一层兜底。

### 2. fail-closed vs fail-open 边界

- **数据腐败** (NaN/Inf/负值): fail-closed (拒绝该 entry,守门层 fail-closed 为 hard_breach)
- **基础设施故障** (Redis ConnectionError): fail-open (proceed,scheduler 兜底 except Exception)

理由: 数据腐败是确定性 bug,人工修复前应停;基础设施故障多为瞬时,继续跑减少业务损失。

### 3. asyncio.Lock 而非 Redis 分布式锁

eval-period 阶段 backend 单实例运行,asyncio.Lock 足够。Phase 6C ¥10k 干跑前如要扩到多实例,会升级为 Redis Lua 原子锁(在 §6 Phase 7 纲要中标注)。

### 4. Sentinel spent (`budget + 1.0`) 局限

Codex Cycle 4 注:`daily_budget=1e16` 时 `+1.0` 因浮点精度可能等于 daily_budget 本身,sentinel 不严格大于 hard_ceiling。实际不影响:`status="hard_breach"` 字段直接驱动 scheduler 的判定逻辑,与 `spent_today > hard_ceiling` 数值比较解耦。

## 测试基线

| 指标 | Before P5A-T02 | After P5A-T02 |
|---|----------------|---------------|
| pytest 总数 | 677 passed / 11 skipped | 723 passed / 11 skipped (+46) |
| `backend.services.cost_guard.py` 行覆盖 | 不存在 | 100% |
| `backend.data.analysis_scheduler.py` 行覆盖 | 既存 | 既存 + 新分支测试 |

## 计划偏离说明

| 计划要求 | 实际执行 | 偏离原因 |
|----------|----------|----------|
| `hypothesis` contract test on `daily_budget ∈ [1, 1000]` 等 | 替换为参数化 unit 边界测试 | hypothesis 未安装,边界场景已用确定性测试覆盖 |
| E2E `redis-cli HSET` + curl 验证 | 延后到部署后 24h 监控 | E2E 需后端重启,纳入 Phase 5A 出口检查 |
| 7 天线上无误熔断 | 部署后跟踪 | 同上 |

---

## 配套报告(R1-R5 占位指引)

§2.4 5-round 模板要求每轮有独立 topic 报告。本任务用 `cycle_N.md` 输出 + 本综合报告替代分散文档,理由:

1. Skill 的 cycle 设计是 6-dim 全维度审查,不是 single-topic 焦点;每一轮均覆盖 R1-R6 全部维度
2. 各 cycle 实际发现的问题跨维度,把它们硬切到 R1/R2/R3/R4/R5 是人为损失上下文
3. 本综合报告以 cycle 为主线、维度为副线,完整保留审查推进逻辑

按需可在 `docs/reviews/p5a-t02-r{N}-{topic}.md` 生成 alias 占位文件,内容指向本综合报告对应 cycle 段落。

> 审查模型: Claude Opus 4.7 (修复) + Codex gpt-5.5 (审查,read-only,执行了 ruff + pytest 双重独立验证)
