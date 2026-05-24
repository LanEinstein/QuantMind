# Codex 跨模型代码审查报告 — M-001 CandidateSelector

**项目**: QuantMind
**审查时间**: 2026-05-24
**审查轮次**: 1 / 1 + 最终只读复核
**最终判定**: ✅ 通过 (经最终复核)

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数 | 5(selector.py / __init__.py / config v1.yaml / 2 测试)|
| 发现问题总数 | 2 |
| 已修复 | 2 |
| 误报排除 | 0 |
| 未解决 | 0 |

审查范围:`backend/candidate_selector/selector.py`、`__init__.py`、`config/candidate_weights/v1.yaml`、`tests/candidate_selector/test_selector.py`、`test_module_contract.py`。`docs/plan.html` 不在代码审查范围。

## 第 1 轮(`codex review --uncommitted`)

**Codex 判定**: NEEDS_FIXES(2 × P2,0 × P1)

| # | 严重度 | 文件:行 | 问题 | 处理 |
|---|--------|---------|------|------|
| 1 | P2 | selector.py:223-224 | 有界重排的 `(key, idx)` 二级 tie-break 把量化序固定:`max_shift=1` 时单边 +1 advisory 让第 6 名得 key `5-1=4`,与第 5 名并列后 idx tie-break 保持原序 → 单边 advisory 在常见场景下**无法移动候选/进 shortlist**,有界重排沦为 no-op。 | ✅ FIXED |
| 2 | P2 | selector.py:304-305 | `advisory_weight` 校验放行 `.nan`/`.inf`(NaN 非 `<0`、inf 非负)→ 产生非有限 delta 而非 fail-closed,违背 loader 契约。 | ✅ FIXED |

### 修复详情

**#1 一槽位 advisory 移动失效** — 排序键从 `(idx - delta, idx)` 改为 `(idx - delta, -delta, idx)`:并列时偏向**更大 pull**,让被看多 +1 的候选真正实现允许的单槽位上移;`_apply_bounded_rerank` 末尾的显式位移 post-check(>max_shift 整体丢弃 advisory)保留不变,所以 ≤1 分位边界仍 fail-closed。新增 `test_single_bullish_pull_realizes_one_slot_move` 断言 600005 单 +1 进入 5 名 shortlist 且合格集不变。

**#2 非有限 advisory_weight** — `load_selector_config` 对 `advisory_weight` 增加 `math.isfinite` + `isinstance bool` 守门(并对 `max_percentile_shift` 增 bool 守门)。新增 `.nan` / `.inf` / `bool` 参数化用例,断言 `CandidateSelectorError`。

## 最终验证(read-only 复核)

**复核状态**: EXECUTED
**复核判定**: PASS

| # | 原问题 | 当前状态 |
|---|--------|----------|
| 1 | 有界重排 tie-break no-op | RESOLVED(600005 现移入 shortlist;位移守门保留)|
| 2 | 非有限 advisory_weight | RESOLVED(`.nan`/`.inf`/bool 均拒绝;bool shift 亦拒绝)|

新增严重(P1)回归:**无(NONE)**。

## 红线/不变量覆盖确认

- 资格纯量化:advisory 永不增删合格集成员(`test_advisory_cannot_add_or_remove_a_qualified_member` + 合格集不变断言)。
- 有界重排 ≤1 分位 + over-displacement → fail-closed 丢弃 advisory(`test_bounded_rerank_drops_advisory_on_over_displacement`)。
- ≥`min_quant_slots` 量化名额经截断仍存活(`test_reservation_rescues_evicted_quant_favorites`)。
- 缺席兜底、确定性、tie-break、duplicate/non-finite fail-closed、config 校验全覆盖。
- import 隔离 AST 自检(无 `backend.{llm,agents,mirofish}`)。

**本地门禁**:41 passed,模块覆盖率 100%,ruff(touched files)全绿,redline-check `[L-002]` 全绿。

> 本报告由 Claude Code(修复)+ Codex CLI(审查)协同生成。
