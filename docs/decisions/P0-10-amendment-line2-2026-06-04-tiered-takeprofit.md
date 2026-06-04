# P0-10-Line2 修订 — 2026-06-04 分级止盈(D1-d:+1R 半仓 → +2R 再一档 → 余仓骑移动止损)

> **修订基准**: [P0-10-amendment-line2-2026-05-30(TAKE_PROFIT/WEIGHT_TRIM 引入)] + [P0-10-amendment-line2-2026-05-31-no-cross-day-take-profit-gate](./P0-10-amendment-line2-2026-05-31-no-cross-day-take-profit-gate.md)(**部分推翻,见 §2**)
> **关联**: [P0-7-amendment-2026-06-04-regime-conditioned-takeprofit](./P0-7-amendment-2026-06-04-regime-conditioned-takeprofit.md)(D1-c,分级与 regime 复合)/ V-003 `rotation_intent.py` + V-004 `entry_rank.py`(append-only ledger + fold 先例)
> **修订日期**: 2026-06-04(#68 session 续;handoff 任务 B.2 待 owner 决策点)
> **决策人**: owner(本 session AskUserQuestion 拍板「现在就做分级」)
> **性质**: 决策边界(止盈从单档改可分级)+ 部分推翻 2026-05-31 WON'T-DO。env 门控默认 OFF + shadow + 人工 gate;安全地基红线全留。

## 0. 意图与现状

- **现状(P-005 + 2026-05-31 WON'T-DO)**:TAKE_PROFIT 单档——现价 ≥ `成本 + eff_r×R` 即卖 `tranche_fraction`(0.5);**跨日无档位 gate**(owner 当时接受「跨日分批 scale-out」:只要仍 ≥+1R,每天再卖余下一半)。当时拒做的原因 = episode 状态须从 `broker_events` 反查重建,过脆。
- **意图(owner 2026-06-04)**:**价格阶梯式**分级落袋——+1R 卖 50% → 涨到 +2R 再卖一档(余下的 50%)→ 余仓骑 ATR 移动止损;而非「每天 +1R 就再砍半」。

## 1. 决策

### 1.1 档位阶梯(`TieredTakeProfitConfig`,frozen,与 D1-c 复合)

- `tiers: tuple[float, ...] = (1.0, 2.0)`——档位目标 = `成本 + tiers[k] × eff_r × R`(`eff_r` = D1-c regime 条件化倍数或静态 1.0;**分级与 regime 自然复合**:熊市整条阶梯前移)。
- 每档卖当前已结算量的 `tranche_fraction`(0.5)→ 实际序列 = 50% → 余下的 50%(原 25%)→ 余 25% 骑移动止损,与 owner 表述一致。
- 校验 fail-closed:tiers 非空、全有限正数、严格递增。
- 阶梯走完(`tiers_taken ≥ len(tiers)`)→ 不再 TAKE_PROFIT,余仓只受保护性止损/硬顶约束。

### 1.2 档位状态 = 自带 append-only ledger(根除 2026-05-31 的「脆」,非推翻其理由)

- 新 `backend/orchestration/takeprofit_ledger.py`:append-only JSONL(`FileLock`,V-003/`entry_rank` 同款模式)。事件:`TIER_TAKEN{code, tier, trade_date, signal_id}` / `EPISODE_CLOSED{code, trade_date}`;fold → `tiers_taken: code → int`。
- **生命周期**:runner 每 tick `sync_episodes(held_codes)`——有 open 档位状态但已不在持 → 追加 CLOSED(全退后重新建仓 = 新 episode,从 tier-1 重新数)。
- **记账时点 = ROUTED**(VALIDATED 且已派发):与既有当日去重同语义。**已知 caveat**:飞书人工 gate 下 owner 未执行的派发也推进阶梯(少卖方向,保守;ledger/audit 可见,owner 可人工裁量)。REJECTED 不记账(次日同档重试)。
- **ledger 读取异常 = fail-closed 抑制 TAKE_PROFIT**(当 tick 把全部在持码并入 `take_profit_already_taken`):错过一次落袋安全,重复砍仓不安全;保护性止损不受影响。
- **2026-05-31 WON'T-DO 的关系**:当时拒的是 `broker_events` 反查重建(脆);本 amendment 用 V-003 确立的自带 event-log 模式(显式落库、fold 重放 bit-exact),理由不冲突、结论更新——owner 拍板。

### 1.3 env 门控 + bit-for-bit

- 新 env `QUANTMIND_LINE2_TIERED_TAKEPROFIT_ENABLED`(默认 OFF);开时 main.py 传 `TieredTakeProfitConfig()` + `TakeProfitLedgerStore`(路径 `QUANTMIND_TAKEPROFIT_LEDGER_ROOT`,默认 `data/takeprofit_ledger/`,gitignore)。
- `tiered_takeprofit=None` → 单档跨日 scale-out 现行为(**v7 bit-for-bit**)。

### 1.4 PIT 三件套

- `IntradaySellIntent.take_profit_tier: int | None`(本次触发的档位序号,1-based);`_sell_record.threshold_params` 写 `take_profit_tier` + 既有 `r_multiple`。
- runner `_compute_config_hash` 纳 `tiered_takeprofit {version, config}`(含派生 maths 版本)。
- `FEATURE_CODE_VERSION`:triggers v7→v8;calibration v3→v4(新 config 定义于该模块)。ledger 本身 append-only + fold = 可重放。

## 2. 红线影响

- **保护性止损零接触**:分级只动 TAKE_PROFIT;ATR/DRAWDOWN/THESIS_QUANT_BREAK/硬顶优先级与 maths 不变;D2 长持豁免正交(豁免者本就跳过 TP)。
- 零 LLM(档位纯价格阶梯);config runtime 不可改;单一构造点(ledger 只记档位,不构造 InstructionPlan);熔断 ≤5 单/日 + 单次 ¥50k + 单股 15% 不变。
- 2026-05-31 amendment 的「当日去重键 (code, kind)」保留(同日至多一档)。

## 3. 测试锚点

- ledger:fold(TIER_TAKEN 累计/CLOSED 重置)/ sync_episodes(退出关闭、再买新 episode)/ 损坏行 fail-closed。
- 触发:tiers_taken=0 在 +1R 触发(tier=1);=1 时 +1R 不触发、+2R 触发(tier=2);=2 阶梯走完不触发;`tiered=None` = v7 bit-for-bit;与 D1-c BEAR 复合(阶梯整体前移)。
- runner:config_hash 含 tiered;ROUTED 才记账;ledger 读取异常 → 当 tick 抑制 TP(其他触发不受影响)。
