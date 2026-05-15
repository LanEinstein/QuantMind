# Phase G Codex 跨模型代码审查报告

**项目**: QuantMind
**审查时间**: 2026-05-15 ~ 2026-05-16
**审查轮次**: 3 / 3 (cycle-1 review + cycle-2 re-review + cycle-3 final verification)
**审查范围**: Phase G commits 3fba551..f9c8c76 + 修复 commits c9d83c3 + e304c2f
**最终判定**: ✅ 通过 (经最终复核)

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数 | 37 |
| 变更行数 | ~4250 insertions / ~80 deletions |
| 发现问题总数 | 6 (5 cycle-1 + 1 cycle-2) |
| 已修复 | 4 (cycle-1: P1×1 + P2×2; cycle-2: P2×1) |
| 误报排除 | 2 (cycle-1: P1×1 limit_up_down_block 命名;P2×1 plan.html committed HEAD) |
| 未解决 | 0 |

## 各轮次详情

### 第 1 轮 (cycle 1: 初次审查)

**Codex 判定**: NEEDS_FIXES

#### 发现的问题

| # | 严重度 | 文件 | 问题描述 | 处理结果 |
|---|--------|------|----------|----------|
| 1 | P1 | backend/api/system_status.py:182 | DataQualityState 的 `is_acceptable_for_buy_sell` 和 `degradation_reason` 是 @property,探针却作为方法 `()` 调用;真实状态会抛 `'bool' object is not callable` 并静默降级 data_quality 源为 unavailable。 | FIXED — 新增 `_resolve(obj, attr, default)` helper 同时兼容 property + method 形态;新增使用真实 `DataQualityState` frozen dataclass 的回归测试。 |
| 2 | P1 | backend/api/instruction_plans.py:121 | 测试 fixture 使用 `rule_name="limit_up_down_block"`,误认为引擎命名空间被折叠。 | DISMISSED — 生产 `RiskEngine._check_limit_up_down_block` 发出的 `ValidationResult.rule_name` 即为 `"limit_up_down_block"` (单一规则);P1-5 §1.5 命名空间区分在 rejection_reason 层 (broker `price_limit_violation_at_fill` vs engine 的 limit-up/down message)。fixture 即为生产真值,不需要改。 |
| 3 | P2 | backend/api/instruction_plans.py:230 | Builder / Broker 子探针异常被静默吞掉,无 `log.warning`,无法区分 "无数据" 与 "探针损坏"。 | FIXED — 加上 `log.warning("instruction_plan_builder_rows_probe_failed", error=..., instruction_id=...)` + 等价的 broker 版本。 |
| 4 | P2 | frontend/src/stores/systemStatus.ts:49 | fetch 失败时保留上一次的 sources,可能在后端故障时仍显示全绿状态。 | FIXED — 失败路径重置 sources 为 `_defaultSources()` (5 个 unavailable),`anyActive=false`,`anyUnavailable=true`;新增 vitest 回归 "on fetch failure resets sources to unavailable"。 |
| 5 | P2 | docs/plan.html (已提交 HEAD) | G-007 `commit: ""` 缺失。 | DISMISSED — 工作树已有修复 `commit: "f9c8c76"`,会在 session-log docs-only 提交中落地;不是代码问题。 |

#### 修复 commit:c9d83c3

### 第 2 轮 (cycle 2: 复审 c9d83c3)

**Codex 判定**: NEEDS_FIXES (1 P2 regression)

#### 发现的问题

| # | 严重度 | 文件 | 问题描述 | 处理结果 |
|---|--------|------|----------|----------|
| 6 | P2 | backend/api/system_status.py:57 (`_resolve`) | cycle-1 引入的 `_resolve` helper 在 method-style 路径捕获异常并返回默认值,使损坏的探针看起来 "健康" (active=False) 而非 unavailable。原 try/except 在探针外层,_resolve 的内层 try/except 反而把异常吞掉。 | FIXED — 移除 `_resolve` 内层 try/except,method 异常透传到外层 probe-level try/except;source 正确降级为 `status="unavailable"`。新增回归测试 `TestProbeFailureIsolation::test_method_style_attribute_exception_marks_unavailable`。 |

#### 修复 commit: e304c2f

### 第 3 轮 (cycle 3: 最终验证)

**Codex 判定**: PASS — all 6 issues RESOLVED, NONE regressions.

#### 验证表

| # | 原问题 | 当前状态 | 备注 |
|---|--------|----------|------|
| 1 | DataQualityState 属性误为方法 | RESOLVED | `_resolve()` 同时处理 property/method;真实 `DataQualityState` 回归测试通过。 |
| 2 | rule_name 命名空间疑虑 | RESOLVED | Dismissal 站得住脚;`limit_up_down_block` 仍为引擎规则名,broker `price_limit_violation_at_fill` 独立。 |
| 3 | Builder/Broker 子探针异常静默 | RESOLVED | 双路径都 `log.warning(...)` 后再返回 fallback。 |
| 4 | 前端 fetch 失败保留全绿 | RESOLVED | catch 路径重置 sources/anyActive/anyUnavailable 为 fail-closed unavailable。 |
| 5 | plan.html 缺 commit hash | RESOLVED | 当前 G-007 含 `commit: "f9c8c76"`。 |
| 6 | _resolve 吞 method 异常 | RESOLVED | 内层 catch 已删,method/property 失败传到 probe-level catch,正确标 unavailable。 |

#### 新增严重问题

NONE

---

## 审查维度覆盖

| 维度 | 发现问题 |
|------|----------|
| 正确性与逻辑 | 2 (P1 #1 property dispatch + P2 #6 _resolve regression) |
| 安全性 | 0 |
| 错误处理 | 2 (P2 #3 instruction_plans 异常吞掉 + P2 #4 frontend fetch 失败保留 stale 数据) |
| 性能 | 0 |
| 代码质量 | 0 |
| 语言规范 | 0 |

---

## 关键修复时序

```
3fba551 feat(g-001) ──┐
5246d88 feat(g-002)  │
790fccb feat(g-003)  ├── Phase G feature commits
e98534f feat(g-004)  │
f9c8c76 feat(g-007) ─┘
        ↓
   codex cycle 1 → 5 findings (P1×1 + P2×3 + 2 dismissed)
        ↓
c9d83c3 fix(phase-g): apply codex cycle-1 review findings
        ↓
   codex cycle 2 → 1 regression (P2 #6)
        ↓
e304c2f fix(phase-g): apply codex cycle-2 review finding
        ↓
   codex cycle 3 → PASS (all RESOLVED, NONE regressions)
```

## 本地 gate 通过状态

* pytest: 1889 passed / 11 skipped (baseline 1854 → +35 新测试)
* vitest: 100 passed / 13 test files (baseline 80 → +20 新测试)
* type-check (vue-tsc): clean
* build: ✓ built in 4.22s
* ruff: All checks passed
* scripts/redline-check.sh: All redline checks passed

> 本报告由 Claude Code + Codex CLI 协同生成
> 审查模型: Claude Code (修复) + Codex CLI (审查) | 3 轮迭代闭环
