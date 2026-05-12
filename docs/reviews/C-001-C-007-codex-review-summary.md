# Codex 跨模型代码审查报告 — C-001 + C-007

**项目**: QuantMind
**审查时间**: 2026-05-12
**审查范围**: Phase C-001 (`backend/data/stock_metadata.py`) + C-007 (`config/holidays.yaml` + `backend/data/trading_calendar.py` + `backend/utils/holiday_loader.py` + `backend/utils/trading_hours.py` 升级)
**审查轮次**: 3 / 3
**最终判定**: ✅ **PASS — 经第 3 轮独立复核确认 5/5 问题全部 RESOLVED + 无新增严重回归**
**触发协议**: 2026-05-12 落地的"提交前(强制 / 通用)"协议第一次实战 — `feedback_codex_review_before_every_commit.md` + `docs/plan.html#protocol`

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数 | 7 |
| 变更行数 | 1282 行 (新增 1265 / 修改 17) |
| 发现问题总数 | 5 (3 在 cycle 1 + 1 NOT_RESOLVED 延续 + 1 新 HIGH 在 cycle 2) |
| 已修复 | 5 |
| 误报排除 | 0 |
| 未解决 | 0 |
| 新增严重回归 | 0 |

---

## 各轮次详情

### 第 1 轮 — `codex review --uncommitted` (gpt-5.5, xhigh reasoning)

**Codex 判定**: NEEDS_FIXES (3 issues)

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P1 | `backend/utils/trading_hours.py:70` | `makeup_workdays_2026` 把 State Council 调休补班 周末标成交易日,但 SSE/SZSE 自 2018+ 公示"为周末休市,不补休"——实际交易所 weekend 仍闭市 | FIXED: 清空 `makeup_workdays_2026: []`; 在 YAML 顶部加 SSE-vs-State-Council 区分注释 + 列出 5 个 office-only 调休日做 ops 参考; 在 `is_trading_hours.is_trading_day` docstring 加同样说明; 保留 loader 三段式逻辑以便交易所真发布周末交易日时 ops 可填入 |
| 2 | P2 | `backend/data/stock_metadata.py:68-70` | BJ 前缀枚举遗漏 `88xxxx` / 其他 `4xxxxx`,fall-through 到 `UnknownCodeError` 而非 `ForbiddenCodeError(reason=bj_forbidden)` 与 docstring 承诺不符 | FIXED: BJ 前缀改为广义匹配 `("4", "8", "92")`;新增 `499999` / `880001` / `899999` 测试用例验证 fail-closed |
| 3 | P2 | `backend/data/stock_metadata.py:204-205` | `round()` 用 banker's HALF_EVEN,与 SSE/SZSE 公示的 HALF_UP 涨跌停限价差一分钱 | FIXED: 新增 `_round_half_up` 走 `Decimal(str(value)).quantize(0.01, ROUND_HALF_UP)`;`get_price_limits` 改用之 |

**新增测试**: `test_round_half_up_diverges_from_pythons_banker_round` 直接断言 `round(7.255,2)==7.25` vs `_round_half_up(7.255)==7.26`。

### 第 2 轮 — `codex exec` 增量复核

**Codex 判定**: NEEDS_FIXES (1 NOT_RESOLVED + 1 NEW HIGH)

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 4 | P3 (NOT_RESOLVED 延续 #3) | `backend/data/stock_metadata.py:225` | `_round_half_up` 本身正确,但 `get_price_limits` 仍在 Decimal 转换前做 float 乘法 — 例:`prev_close=1.65` (IEEE 754 = 1.6499…) `* 0.9 = 1.4849…` → `_round_half_up` 返回 `1.48`,数学正确答案 `1.485` HALF_UP `1.49` | FIXED: 重写 `get_price_limits` 端到端走 Decimal:`prev_close` + `pct` 各自经 `Decimal(str(...))` 入域,以 Decimal 算术做乘法,最后 `quantize` HALF_UP 再 `float()`;新增 `test_get_price_limits_uses_decimal_arithmetic_end_to_end` 断言 `(1.49, 1.82)` |
| 5 | HIGH (新发现) | `config/holidays.yaml:54` | `2026-02-24` 标为春节闭市,但 SSE 通告 2025-12-22 + SZSE 通告 2025-12-22 实际明确闭市到 02-23 (Mon) 02-24 (Tue) 起恢复交易 | FIXED: 从 `holidays_2026` 删除 02-24;注释更新 cite SSE/SZSE 2025-12-22 通告;只列 02-16/17/18/19/20 + 02-23 共 6 个 weekday;周末 02-21/22 由默认规则覆盖 |

### 第 3 轮 — `codex exec` 最终复核 (read-only)

**Codex 判定**: ✅ **PASS** (5/5 RESOLVED + NONE regressions)

Codex 主动跑 web search 核对 SSE/SZSE 官方 2026 休市通告 + 跑 inline Python 断言验证 `499999` / `880001` / `899999` / `920099` 全 fail-closed + `(7.255, 0.005, 0.015)` HALF_UP 全正确 + `get_price_limits(SH_MAIN, 1.65) == (1.49, 1.82)` + `2026-02-24` 标为 trading 而非 holiday。

---

## 误报分析

无。3 轮 5 个问题全部判定为真实 issue 并修复。

---

## 最终验证

**状态**: EXECUTED (cycle 3 即 final verification — 因 cycle 2 仍 NEEDS_FIXES,cycle 3 同时承担"修复后复核"和"达到最大轮次后的 closure check"双角色)
**判定**: PASS
**复核证据**: cycle 3 codex 用 web search + inline Python 子进程独立验证,无 P1 regression。

---

## 落地指标

| 指标 | 值 |
|------|-----|
| pytest | 1381 passed, 11 skipped (基线 1376 → +5 新测试) |
| 后端覆盖率 | 85.37% (>70% gate) |
| 风控覆盖率 | 97.60% (>95% gate) |
| ruff | clean (新文件 0 错;`backend/data/scheduler.py` 1 个 I001 是已存在前置债,与本次无关) |
| `scripts/redline-check.sh` | 全绿 |
| 前端 type-check | passed |
| vitest | 80 passed |

---

## 价值小结

- **协议首战即拦截 5 个真实 bug**:其中 1 个 P1 (节假日语义错位) + 1 个 HIGH (官方公告不一致)假如不跑 codex 会直接进入主线,潜伏到 Phase D RiskEngine 14-check 上线后才暴露 (届时已被 5 冻结源 / acceptance 报告 / OpenAPI MTM 引用,修复成本指数级上升)。
- **印证 [[feedback_codex_review_before_every_commit]] 立论**:全量 1376 测试绿 + ruff clean + redline-check 全绿的状态下,codex 仍找出 5 个真实问题。绿测试 ≠ 提交安全。
- **跨模型互补**:codex (gpt-5.5) 主动 web search 核对 SSE/SZSE 官方公告,这是单凭 Claude 静态推理与 grep 难以做到的事实校对维度。
- **Decimal 精度教训**:在 IEEE 754 上做金额算术,即使最终走 Decimal `quantize`,只要中间过 float 乘法就会丢精度。这条结论可以泛化到 Phase E (`MockBroker` 撮合 / `cost_calculator` / `MTM`) — 后续涉及金额的模块务必端到端 Decimal,不要"先 float 算再 Decimal 量化"。

---

> 本报告由 Claude Opus 4.7 (1M context) + Codex CLI 0.130.0 (gpt-5.5 xhigh) 协同生成
> 审查协议:`docs/plan.html#protocol`"提交前(强制 / 通用)"行 + memory `feedback_codex_review_before_every_commit.md`
