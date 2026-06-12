# backend/review/ — 子任务上下文(Phase AA 模拟盘自动驾驶)

> 状态:**done(AA-002/AA-003)**。治理:[`P1-2.A-amendment-2026-06-12-sim-autopilot-review-crons.md`](../../docs/decisions/P1-2.A-amendment-2026-06-12-sim-autopilot-review-crons.md) §1.1/§1.3/§1.4 + `P2-2-amendment-2026-06-12` §1.6(policy_hash 字段)。任务:plan.html AA-002/AA-003/AA-005。

## 职责
**盘后归因复盘(facts-first)+ 周末/节假日深复盘**:纯确定性地把当日成交事实(成交价 vs 当日 VWAP、滑点、费用、持有期收益、policy_hash、style)+ 预注册反事实(实际产出过的 HOLD plan / 被拒单)+ 违规计数,落成 append-only `DailyReviewRecord` / `WeeklyReviewRecord` —— 这是 Phase AB 客观晋升引擎的证据地基。

## 模块结构
| 文件 | 内容 |
|------|------|
| `models.py` | `TradeFact` / `CounterfactualEntry` / `DailyReviewRecord` / `WeeklyReviewRecord`(全 frozen strict forbid;**无任何自由文本字段** → LLM 无处可写,4 类写权限不变)。`CounterfactualEntry` 模型层钉死反后见之明红线:`promotable=True` 必须 `pre_registered=True` 且非 HYPOTHETICAL(codex P2-6)。 |
| `attribution.py` | 纯函数日归因:`build_daily_review` / `build_trade_fact` / `derive_vwap_basis`(VWAP 合理性带 [price/3, price*3],单位损坏→null 不毒化聚合)/ `normalize_kline_vwap`(股/手、元/千元单位方言消歧)。无 IO 无时钟。 |
| `weekly.py` | `resolve_review_week`(评审周 = 含「now 之前最后一个交易日」的 ISO 周;周中节假日 `complete=False` 不premature 落档)+ `build_weekly_review` 纯聚合。 |
| `ops_gate.py` | §1.4 ops 门(全过才跑,未知=fail):无 OPEN 票 / snapshot checksum / snapshot 新鲜 / artifact registry / 磁盘 ≥2GiB / LLM 预算余量 ≥¥10(**计同一 `llm:usage:{utc_date}` 计数器,不绕 ¥100 cap**)/ 行情新鲜;`activation_allowed` 独立旗标 = 距下一开盘 ≥2h(AB 消费)。 |
| `store.py` | `MongoReviewRecordStore`(`review_records`)+ `MongoWeeklyReviewStore`(`weekly_review_records`):**append-only,无 update/delete 面**;重跑 = 幂等 skip;weekly 行存在性 = 周六 run 成功标记(节假日补跑 lane 据此 gating)。 |

cron 接线在 `backend/broker/scheduler.py`(18:00 `daily_attribution_review` / 周六 10:00 `weekend_deep_review` / 每日 10:00 自 gating `holiday_catchup_review`)+ `backend/main.py` 回调(采集 trades/kline/thesis/审计计数,模块本身零 IO 依赖注入)。

## 本模块红线
1. **纯确定性零 LLM**:严禁 `import backend.{llm,agents,agents_team,mirofish}`;LLM 复盘散文(如未来有)只在编排层落 `evidence_collection`,绝不进本模块的晋升证据存储。
2. **append-only**:store 无 update/delete;修正 = 追加新 schema_version 记录。
3. **反后见之明**:非预注册反事实永不 `promotable`(模型校验钉死);仅实际产出过的 HOLD plan / 被拒单可作 AB 晋升证据。
4. **失败不冻结交易**:复盘 lane 与交易解耦(X-005 先例);cron 一次重试,二次失败只 DEGRADED audit。
5. **预算不绕行**:周末实验预算检查读同一日计数器;门槛常量 `LLM_BUDGET_MIN_REMAINING_CNY`。
6. **激活黑窗**:距下一开盘 <2h `activation_allowed=False`(AA 阶段无激活动作,AB 强制消费此旗标)。

## import 隔离
严禁 `import backend.{llm,agents,agents_team,mirofish}`(redline-check `[AA-005]` grep + `tests/review/test_module_contract.py` AST 双重守门)。可用:`backend.{utils,models}`(`trading_hours` 静态日历 + reconciliation/equity 模型只读)。本模块**不构造** InstructionPlan(单一构造点不破)。

## 测试
`tests/review/`:models(13)+ attribution(16)+ store(5)+ weekly(10)+ ops_gate(14)+ cron gating(4+8)+ 模块契约(AST)。新模块覆盖率 ≥95%。
