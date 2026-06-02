# W-002 盘后复盘 EOD cron + LLM advisory — codex review summary

> 任务:W-002(plan.html Phase W)。审查:`codex review --uncommitted`(cycle 1,后台 `</dev/null`)。
> 模型:codex-cli 0.133.0。本地门禁前置全绿(pytest 4548 / ruff / redline)后跑 codex。

## 发现与处置(3 条,全修)

| # | 等级 | 位置 | 问题 | 处置 |
|---|------|------|------|------|
| 1 | P2 | `main.py` evidence 上下文查询 | **advisory 自我强化** —— 取 code 的近 3 条 evidence 时把本特征自己写的 `path=="thesis_review"` 往日 verdict 也喂回当「当前证据」,health 标签自我循环、挤掉新鲜市场/新闻/MiroFish 证据 | ✅ 查询加 `path: {"$ne": "thesis_review"}` 排除自身复盘行 |
| 2 | P3 | `thesis_advisory.py` parse | **解析误判** —— 按枚举序(BROKEN 先)子串匹配整段,「THESIS_INTACT … 未达到 THESIS_BROKEN」被判 BROKEN | ✅ 改为锚定**最早出现**的标签(leading verdict);新增对抗测试钉死 |
| 3 | P3 | `cost_guard.reset_daily_gate_counters` | **reset 漏清** —— 新 `llm:thesis_review:*` count/dedup 是 per-day gate,但 reset 只清 debate/reserved/anomaly;fresh-day dry-run / 同日 rerun 会因残留 dedup 把复盘 spurious 跳过 | ✅ reset keys 元组加 thesis_review count+dedup;新增 reset 测试 |

## 验证
- 修后:pytest **4550 passed, 13 skipped**(+2 codex-fix 测试)/ ruff All checks passed / redline All passed。
- 安全地基红线一条未破:monitoring 仍 0-LLM + import 隔离(`[N-005]` 绿;LLM 调用在 orchestration 层经注入 client)/ advisory verdict 无任何决策字段(side/volume/limit_price/RiskCheckSummary)/ evidence-only(DEBATE- 前缀,无 risk-summary 管线)/ LLM spend 计同一 `llm:usage` 计数器不绕 ¥100 cap / 单一构造点不破(本路径不构造 InstructionPlan)。

## 备注
- 3 条均为 advisory 准确性/可重复性问题(非安全红线),但 P2 自我强化若不修会系统性污染 health 判断,属真 bug,已修。
