# W-004 确定性 THESIS_QUANT_BREAK 触发 — codex review summary

> 任务:W-004(plan.html Phase W)。审查:`codex review --uncommitted`(cycle 1,后台 `</dev/null`)。
> 模型:codex-cli 0.133.0。本地门禁前置全绿(pytest 4574 / ruff / redline N-005+M-004)后跑 codex。

## 发现与处置(2 条 P2,均 only-add-pressure 边界真回归,全修)

| # | 等级 | 位置 | 问题 | 处置 |
|---|------|------|------|------|
| 1 | P2 | `intraday_triggers.py` | **大仓位全退被 check#9 拒 → 反减卖压** —— thesis break 抢占 TAKE_PROFIT/WEIGHT_TRIM 发全量 SELL,若 full-exit > ¥50k 单次上限被 builder 拒,而被抢占的 tranche 本可过 → 净卖压减少(违反 only-add-pressure) | ✅ `_thesis_break_intent` clamp 到单次上限(`min(full, cap_lots)`,必过 check#9);clamp 后为 0(极端 price>cap)则**回退** tp/trim 不抢占;新增 clamp 对抗测试(30000股→clamp 12500=¥50k) |
| 2 | P2 | `main.py` + `line2_intraday_runner.py` | **store 读失败禁用整个 intraday 扫描** —— flag ON 时 `open_theses()` 抛错在 `run()` 前 → 每 30s tick 跳过 ATR/drawdown/ADD | ✅ main.py 回调 + runner `_thesis_breaks` **双层 fail-open** try/except → 降级空 break map 保留既有 Line-2 行为;新增 runner fail-open 单测 |

## 验证
- 修后:pytest **4577 passed, 13 skipped**(+3 codex-fix 测试)/ ruff All checks passed / redline All passed(N-005 monitoring 零 LLM + M-004 单一构造点)。
- only-add-pressure 红线对抗测试全绿:空 break map ⟹ baseline bit-identical / 保护止损(ATR+drawdown)永远胜出 / thesis intact 不放宽止盈带 / clamp 保证全退恒过 check#9 / store 失败不破 tick。
- 安全地基:monitoring 仍零 LLM(thesis_break 纯量化只引 backend.position_thesis 纯模块)/ 单一构造点(经 assemble_monitoring_plan)/ SELL 不熔断 / 生产 env 门控 default-OFF(阶段2 gated+shadow)。

## 备注
- 两条都是「只增卖压」在 RiskEngine 单次上限边界 + store 故障边界处的真回归,codex 抓得精准;clamp + 双层 fail-open 后该红线在边界处仍成立。
