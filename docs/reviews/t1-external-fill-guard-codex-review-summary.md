# T+1 外部回报守卫 — 审查摘要(2026-06-04,#68 session 续)

> 范围:`P0-4-amendment-2026-06-04-external-fill-sell-t1-guard` 全链(mock_broker / trade_dates 新模块 / recovery / snapshots v2 / checksum / scheduler / main seed)。
> 流程:codex `review --uncommitted` 7 轮(cycle-8 撞额度)→ 按既定 fallback 改 `/code-review high`(7 角度 finder + 验证)收尾。

## codex 7 轮(每轮 P1 全修)

| 轮 | finding | 修复 |
|---|---|---|
| 1 | counter 守卫被 16:30 `advance_day` 清零绕过(迟到同日回报) | date-keyed 买入记录(只被 BUY 变更) |
| 2 | `traded_at`=parsed_at,次日补录绕过 | 交易日 = instruction_id 内嵌 `QM-YYYYMMDD`(`trade_dates.instruction_trade_date`) |
| 3 | 单记录被后续买入覆盖 + 重启丢失 | `bought_by_date` map(prune 5)+ recovery replay 重建 + seed 传 recovery 对象 |
| 4 | 快照游标内的同日买入恢复后失明 | v1 快照回退重种 `{snapshot_day: today_bought}`(EOD 快照先于 advance_day) |
| 5 | scheduler 写快照 `today_bought` 恒 0(公开 Position 无此属性,**既有潜在 bug**) | `volume − available_volume` + `export_bought_by_date()` |
| 6 | ① 倒填 SELL 借后日仓位放行;② 补录 BUY 错锁当日 | ① sellable(D)=`volume−Σ(d≥D)`(保守下界);② `lock_today=False`(指令日<今天) |
| 7 | 多日买入日期跨快照丢失 | **BrokerSnapshot schema v1→v2**(owner 拍板):positions 携带 map;读兼容(拒未来版本);checksum 空 map 字节级兼容 v1 |

## /code-review high(cycle-8 替代)

- 7 角度 ~25 候选 → **3 CONFIRMED 全修**:
  1. `to_snapshot_positions()` 丢 map(`scripts/reconcile_now.py` 用它**持久化**检查点 → 重启重开 cycle-7 缝)→ 携带 ISO 键 map。
  2. `seed_from_recovery` 未归一化键类型(快照载体 ISO 字符串键 → 守卫日期比较永不命中)→ str/date 归一化。
  3. 快照校验器只查正则不查真日期(`2026-02-30` 会深崩在 recovery)→ strptime 真解析,读时 fail-closed。
- 其余 REFUTED:误读版本校验方向(`>` 即拒未来)/ 对账复原空 map = amendment 已写明设计 / 已结算股份语义误解 / 5+ 日倒填超 prune 窗(文档化残余,16:00 对账兜底)。

## 文档化残余(均保守方向)

- 对账复原(`reset_to_snapshot`)后记录为空 → 守卫退化为超持仓检查(对账即校准)。
- 倒填日期早于 prune 窗(>5 个买入日期)→ 守卫下界趋松,16:00 对账兜底。
- sellable(D) 为保守下界:极端复杂跨日补录序列可能误拒 → 人工澄清(fail-closed,永不误收)。

门禁:4701 passed / cov 90.72% / ruff / redline 全绿。
