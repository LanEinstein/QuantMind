# N-003 Codex 跨模型代码审查报告

**任务**: N-003 — 补仓(Van Tharp + ATR;禁马丁格尔/熊市禁补)→ 飞书 ADD
**审查时间**: 2026-05-25
**审查轮次**: 1 cycle + read-only final verification
**最终判定**: ✅ 通过(经最终复核)
**审查范围**: `backend/monitoring/add_position.py` + `backend/integrations/feishu/renderer.py`(render_add_position)+ `tests/monitoring/test_add_position.py`

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 发现问题总数 | 1(P2;0 P0/P1/P3) |
| 已修复 | 1 |
| 误报排除 | 0 |

## 第 1 轮 — `codex review --uncommitted`

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P2 | add_position.py `parse_held_series` | held 码同时有 valid 行 + 列数错误的 malformed 重复行时,`len(parts)!=5` 的 `continue` 在 `raw_counts` 自增**之前**执行 → valid 行被当唯一返回,可驱动一次 ADD,违反 ambiguous 重复行的 fail-closed 契约。 | ✅ FIXED |

### 修复详情

`parse_held_series` 改为:对每行先取**首字段** code,若属 held 集则**先**自增 `raw_counts`(在列数 / float 解析过滤之前),再做结构过滤;最终仅返回 `raw_counts[code]==1` 的码。valid+malformed 重复 → count≥2 → 整码丢弃(fail-closed)。与 `anomaly._parse`(N-001 同款修复)一致。回归测试 `test_parse_held_series_valid_plus_malformed_duplicate_dropped`。

## 最终验证(read-only closure check)

**复核状态**: EXECUTED · **复核判定**: **PASS** · 原问题 **RESOLVED** · 新增 P1 回归 **无**。
(codex read-only 沙箱无临时目录无法跑 pytest;直接 import-and-assert 探针通过 + 本地 21 测试通过。)

## 门禁

- pytest:`tests/monitoring/test_add_position.py` 21 passed;add_position 模块覆盖率 94%(≥80%);renderer 30 passed 无回归。
- ruff:全绿(`backend.{broker,data,risk}` 经 per-line `# noqa: TID251`)。
- redline-check:全绿(M-004 单一构造点 — ADD 经 builder `assemble_monitoring_plan` BUY 路径)。

## 红线遵守

- **禁马丁格尔**:size 恒为 Van Tharp 固定分数(`risk_fraction × equity / (ATR_mult × ATR)`),永不随回撤放大;且深度水下(回撤超 `max_add_drawdown_pct`)的 ADD 直接 MARTINGALE 拒绝。
- **熊市禁补**:`MarketRegime.BEAR` 阻断所有 ADD。
- 四条件齐(超卖 RSI + 量能企稳 + 无结构性破位 + 仓位余量)缺一不补。
- ADD(BUY)经 builder 全 5 早返(含 watchlist —— 入场须遵守 universe,与 SELL 退出跳过 watchlist 相反)+ RiskEngine 14-check(check 5 独立再校验 15% 仓位 cap)+ 飞书人工。

> 本报告由 Claude Code + Codex CLI 协同生成。
