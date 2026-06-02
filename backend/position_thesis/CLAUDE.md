# backend/position_thesis/ — 子任务上下文(Phase W)

> 状态:**done(W-001 模型+派生+落库+Line-1 接线;evaluation 经 W-004 monitoring/thesis_break 消费)**。治理:[P0-10-amendment-line2-2026-06-01-position-thesis-advisory](../../docs/decisions/P0-10-amendment-line2-2026-06-01-position-thesis-advisory.md) + R0 §3(PIT)+ R0 §4(单一构造点)。任务:plan.html W-001 / W-004。
>
> **消费者**:W-002 LLM advisory 盘后复盘读 `PositionThesisStore.open_theses()`(orchestration 层,monitoring 外);W-004 确定性 `THESIS_QUANT_BREAK`(`backend/monitoring/thesis_break.evaluate_thesis_breaks` 引本模块 `evaluation`,monitoring 仍零 LLM)。**写入点**:Line-1 dispatch(送达 BUY)经 `Line1Runner._persist_thesis`。

## 职责
**买入时显式落库的「为何买」记录**(缺失原语;根除 P-006 反查脆弱性)。买入(Line-1 dispatch)时落 `PositionThesis` = **3–5 支柱文本(LLM,advisory)** + **每条确定性量化失效阈值(无 LLM,机检)** + time-stop/催化窗 + 原始 evidence_ids + 因子锚 + replay 引用。两个下游消费:W-002 LLM 盘后复盘(advisory,只写 evidence)+ W-004 确定性 `THESIS_QUANT_BREAK`(monitoring 零 LLM,经 builder → SELL)。

## 模块结构(已实现)
| 文件 | 内容 | 任务 |
|------|------|------|
| `config.py` | `ThesisDerivationConfig`(runtime-immutable:anchor_drawdown_pct/time_stop_trade_days/score_decay_pct)+ `FEATURE_CODE_VERSION` pin | W-001 |
| `derivation.py` | `derive_invalidation_conditions`(3 白名单模板确定性派生:ANCHOR_DRAWDOWN/TIME_STOP/SCORE_DECAY;**只读 entry_price/score/dates,从不读支柱文本**)+ `build_position_thesis`(支柱文本作不透明 payload);fail-closed:脏 entry_price → raise | W-001 |
| `evaluation.py` | `evaluate_thesis_health`(确定性 broken/intact 滚动;数据缺失的条件**跳过**不算 broken)→ W-004 用 | W-001 |
| `store.py` | append-only JSONL `PositionThesisStore`(open/close 生命周期,同 `slot_portfolio.entry_rank`/`rotation_intent`);买入显式 `open_thesis`(同 instruction_id 幂等)+ `sync_holdings` 退出即 close → 复买拿全新 thesis | W-001 |

## 本模块红线
1. **纯函数零 IO**(除 store 文件 append);**严禁** `import backend.{llm,agents,agents_team,mirofish}`(redline `[W-001]` + `tests/position_thesis/test_module_contract.py` AST 双守门)。可用:标准库 + pydantic/structlog/filelock + `backend.models` + 本子包内部。
2. **永不构造 InstructionPlan**(`grep "InstructionPlan(" ⊆ {model, builder, tests}` 不破);thesis 是 advisory 数据,**非 InstructionPlan**,无 side/volume/limit_price/RiskCheckSummary 字段。它可能催生的 SELL 由 builder 单一构造点确定性派生(W-004)。
3. **支柱文本(LLM)与失效阈值(确定性)解耦**(§1.1 关键张力):支柱 = P0-10 允许的 LLM reasoning 文本;阈值 = 白名单模板从买入快照确定性算出。**LLM 文本永不影响任何指标/比较符/阈值**(codex round-1:LLM 选阈值=偷渡语义进零 LLM SELL)。本模块刻意把确定性集**独立于支柱文本**计算(比"每支柱配模板"映射更强的红线姿态),对抗测试钉死:改支柱文本 → 阈值 bit-exact 不变。
4. **确定性 over PIT,可 replay**:同 entry 快照 + 同 config → 同 conditions(bit-exact)。thesis 携 signal_id/snapshot_id/feature_code_version/evidence_ids = 完整 replay 引用。
5. **显式落库,非 broker_events 反查**(根除 P-006);买入时一次写,消费者只读不重建。

## 输入契约
`build_position_thesis` 由 Line-1 runner(W-001 接线)在 BUY 路由后调用:`entry_price=plan.limit_price`(成交锚,**非订单字段**)/ `entry_score=candidate.score`(Line-1 composite,SCORE_DECAY 锚)/ `pillars`=辩论 state 的 4 agent LLM reasoning(单行截断)/ `snapshot_id=frame.snapshot_id`。失败永不阻断下单(audit 副作用,同 basket digest)。

## 测试
`tests/position_thesis/`:model(校验+无决策字段+is_broken)+ derivation(确定性阈值+**对抗:文本不入阈值**+fail-closed)+ evaluation(broken/intact/不可评估跳过)+ store(往返+open/close+幂等+复买新 thesis+腐坏 fail-closed)+ module_contract(AST 隔离+无 InstructionPlan)。覆盖率 ≥80%。Line-1 接线测试在 `tests/orchestration/test_line1_runner.py`(每 BUY 落 thesis + 写失败不阻断 + 未接线不变)。
