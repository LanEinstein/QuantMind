# U-D1b 代码审查总结 — Line-1 生产 provider + Line-1 runner cron 接线

> 任务:U-D1b(Line-1 全市场 BUY 选股 4-agent 辩论生产 provider + BrokerScheduler 第 8 cron + main.py 接线)。
> 审查工具:**codex 撞使用额度**(`codex review --uncommitted` 返回 "You've hit your usage limit … try again at 3:07 PM"),按 owner 既定回退([[feedback_codex_rate_limit_fallback]])改跑 **Claude 自带 `/code-review`(high,3 angle × 6 candidate × 1-vote verify)**。
> 日期:2026-05-26。

## 审查范围
`git diff HEAD`(经 `git add -N` 让新文件入 diff):
- `backend/services/line1_context_provider.py`(新,385 行)
- `backend/broker/scheduler.py`(第 8 cron `line1_runner`)
- `backend/main.py`(`_init_line2_runners` 内建 Line-1 + 3-tuple 回调 + 调用点)
- `backend/services/smoke_check.py`(22→23 slot)
- `pyproject.toml`(TID251 per-file-ignore)
- `tests/services/test_line1_context_provider.py`(新)+ 3 个既有测试同步

## 三 angle 发现(去重后 6 类,verify 后保留)

| # | 严重度 | 文件 | 问题 | 处置 |
|---|--------|------|------|------|
| 1 | **P1/HIGH** | line1_context_provider.py | `max_compliant_buy_volume` 的 `by_cash = available_cash / last_price` **漏了 RiskEngine check 4 的 0.1% 手续费缓冲**(`estimated_cost = price × volume × 1.001`);现金为绑定档时按现金切出的整手真实成本 > available_cash → check 4 REJECT。helper "VALIDATES" 契约被破,且少路由一笔本可成交的合规单。 | **已修**:新增 `_FUND_SUFFICIENCY_BUFFER=1.001`,`by_cash = available_cash / (last_price × buffer)`。+ 单测 `test_sizing_cash_cap_honours_fee_buffer`。 |
| 2 | **P2/MEDIUM** | line1_context_provider.py | sizing **忽略 RiskEngine check 8(总仓 ≤70% 总资产)**;在已加载持仓的账户上把单股切到 15% 上限会撞 70% 总仓 → REJECT(同 #1 类:本可路由更小合规单)。 | **已修**:helper 新增 `max_total_position_pct` + `other_positions_value` 参数,`by_total = (70%×total_assets − other_value)/price − existing_shares` 作第 4 道 cap;`build_lead_context` 按 **engine 同款精确码比较**(`p.code == lead.code`)算 existing/other。+ 单测 `test_sizing_capped_by_total_position_pct`。 |
| 3 | **P1/HIGH** | line1_context_provider.py | `_prev_close_from_frame` 返回 `closes[-2]`,而 `parse_held_series` 只拒非有限值(非拒 0/负);`DataSnapshot.prev_close` 有 `gt=0.0` 约束 → 0/负的前一根 bar(数据 glitch)触发 ValidationError,**中断整轮日线 BUY**(screener 只校验最后一根 close,不校验前一根)。 | **已修**:`if len(closes) >= 2 and closes[-2] > 0` 才返回,否则回退 last_price(0% 涨幅永远 price-reasonable)。+ 单测 `test_build_lead_context_non_positive_prev_close_falls_back`。 |
| 4/5 | P2/MEDIUM(latent) | line1_context_provider.py | `concentration_exception` 分支原只按 `max_lots×lot_size` 切,**跳过 check 4/8/9 clamp**;现 `max_lots=1` 锁定故安全,但若 config 调大 max_lots 会切出 guaranteed-reject(或超额)单。 | **已修**:重构后例外分支也过 `by_instruction`/`by_cash`/`by_total`,只豁免 check 5(例外的本意)。 |
| — | P3/文档漂移 | smoke_check.py / scheduler.py | 模块头 docstring 还写 "18 slots" / "Five cron jobs … the fifth"(U-D1 后已 stale)。 | **已修**:smoke 头改 "23 (18+4+1)";scheduler 头改 "Eight cron jobs"。 |

## verify 判定为 REFUTED / 可接受(未改)
- **`open_tickets: tuple[Any, ...]` 类型擦除**:与 Line-2 `build_line2_run_state` 完全同款契约,生产唯一来源 `_open_tickets_or_skip`,非真 bug。
- **`DataSnapshot.is_trading_hours/is_trading_day` 硬编码 True**:RiskEngine check 7 自行用 `now` 派生 trading-time(09:35 cron 必在盘中),非门绕过;仅 audit/replay 字段,已记 U-D3 接真盘口 seam。蓝图 `test_mvp_e2e` Line-1 链同款。
- **`test_start_registers_five_jobs_when_no_evolution_callback` 命名 stale**:U-D1 起即漂移(非 U-D1b 引入),仅成员断言不算 count,留待后续重命名。

## 门禁结果(修复后)
- `pytest -q` 全量 **3731 passed** / 11 skipped(基线 3714 → +17 新测试)。
- 新模块 `line1_context_provider.py` 覆盖率 **97%**(剩 3 行为 parse 失败的日志兜底分支)。
- `ruff check` 触及文件全绿。
- `bash scripts/redline-check.sh` 全绿(M-004 单一构造点 / X-018 orchestration 隔离 / N-005 Line-2 隔离均未破)。

## 结论
P1×2 + P2×3(含 latent)+ 文档漂移全部修复;无 P0。所有发现集中在 sizing helper 与 prev_close 派生,修复后 `max_compliant_buy_volume` 真正镜像 RiskEngine 四道价值/数量 BUY check(4/5/8/9),"VALIDATES" 契约成立。
