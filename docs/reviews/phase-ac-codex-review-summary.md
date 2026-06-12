# Phase AC(风格分型与价值线)codex review 摘要 — 2026-06-12

> 范围:AC-001..AC-007 全套未提交 diff(新模块 `backend/style/` + `screening/value_factors.py` + `screening/value_score.py` + `knowledge_graph/resonance.py` + `monitoring/style_soft.py` + `theme_research/tier_weights.py` + 模型/选择器/卖出栈/铭牌接线 + 12 测试文件 + v2 prompt + redline `[AC-007]`)。
> 调用:`codex review --uncommitted`(1 cycle)+ `codex exec --sandbox read-only` verify ×4(逐 finding 收敛)。
> 结论:**0 P0 / 0 P1;review 3 P2 + verify 3 P2(同一持仓铭牌机制的逐层深挖)全部修复 + 回归测试。最终 verify = COMMIT-SAFE。**

## review cycle 1 findings(3 P2)

| # | 位置 | 问题 | 修复 |
|---|------|------|------|
| 1 | `line1_runner.py` `_process_candidate` | style 在 route 前注册到 broker pending,非交付路径(HOLD/REJECTED/dry-run/send_failed)留下未消费的 pending → 后续同码买入误盖 | 非交付 route 清 pending(后续 verify 进一步收敛,见下) |
| 2 | `monitoring/intraday_triggers.py` 软层 | VALUE 拓宽止盈带跳过 TP 时,低优先 WEIGHT_TRIM 仍记基准 `eff_r` → PIT replay 误判旧短线 TP 触发 | trim 记 `tp_eff_r`(后续 verify 收敛为仅"TP 真被评估"时,见下) |
| 3 | `knowledge_graph/resonance.py` | Instrument↔Instrument 边(如 CORRELATES_WITH)把对端股票节点 provenance 也计为一个 family → 单条股票关系凑足 ≥2 共振门 | 邻居为 INSTRUMENT 时跳过其节点 provenance,仅计 incident 边 provenance。回归 `test_peer_instrument_provenance_not_counted` |

## verify cycle(4 轮,逐层深挖持仓铭牌机制)

| # | 位置 | 问题 | 最终修复 |
|---|------|------|------|
| A | `intraday_triggers.py` ~1400 | `tp_eff_r` 传给**所有** WEIGHT_TRIM,含 `hard_cap_only=is_long_term` → 长持硬顶 trim 元数据随 style 漂移(应风格不变) | `trim_eff_r = tp_eff_r` **仅当 TP 真被评估**(`not is_long_term and not sealed`),否则基准 `eff_r`。回归 `test_long_term_hard_cap_trim_identical_across_styles` |
| B | `line1_runner.py` + `mock_broker.py` | sim-only 清除 + 口头"同日过期"未落地 → feishu 非交付/sim REJECTED 仍留 stale pending | ① 非交付 route **两模式都清**;② `MockBroker.advance_day()` `_pending_entry_styles.clear()`(**落地同日过期界**);③ episode-open + add-on 都 pop。回归 `test_advance_day_discards_unconsumed_pending` + 两模式清除 |
| B' | `line1_runner.py` `delivered` | `simulation_routed` action 在 sim 冻结/broker 拒单(`final_status=REJECTED`、`trade_ids=()`、零 broker 变更)时仍判 delivered → 既漏清 style 又给非持仓落 thesis(**顺修既存 W-001 bug**) | 新 `_route_produced_holding(ro)`:feishu dispatched/skipped_duplicate→True;`simulation_routed`→需 `final_status is FILLED AND len(trade_ids)>0`。gating 同时管 thesis 持久化 + style 清除。回归 `test_sim_rejected_route_clears_style_and_skips_thesis` + fake 改真实 FILLED |
| C | `simulation_executor.py` + `appliers.py` | 成交事件 payload 写 `entry_policy_hash`/`sell_stack_version` 但**漏 `entry_style`**,而 recovery 读 `payload['entry_style']` → 重启后 style 丢失 | 两写端经 `broker.entry_style_for(code)` 读刚成交持仓的 style 写入 payload(sim + feishu 双路径)。回归 `test_entry_style_persisted_in_filled_payload` + `test_filled_buy_persists_entry_style_in_payload` + `test_seed_from_recovery_carries_entry_style` |

## 门禁状态(修复后)
- 全量 **5508 passed** / 13 skipped(基线 5374 → +134)/ 覆盖率 90.88%(≥70)。新模块 96-100%。
- ruff 全绿 / redline(含新 `[AC-007]`)全过 / 新模块 mypy strict 绿(networkx/yaml 缺 stub 与既有 KG 模块一致,非新增)。
- 安全地基红线全留:LLM 不进运行时数据路径(三层得分纯 PIT + 人工 pin 题材)/ 单一构造点不破 / **硬风控 style-invariant(对抗钉死)**/ 仅 2 写端点不变 / 量化资格权威。
- 风格标签 **display-only**:落 `PositionThesis.style` + 持仓铭牌 `entry_style`(成交时 stamp + 事件 payload 持久 + recovery 重建)+ 飞书 `style_badge`;**永不改任何风控数值**。
