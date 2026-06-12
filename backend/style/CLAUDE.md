# backend/style/ — 子任务上下文(Phase AC,风格分型)

> 状态:**done(AC-001 StyleClassifier + PositionThesis.style + 持仓铭牌;AC-005 接线 selector 价值槽;AC-006 接线 per-style 软层)**。治理:[P0-8-amendment-2026-06-12](../../docs/decisions/P0-8-amendment-2026-06-12-style-classifier-and-value-line.md) §1.1/§1.5 + R0 §3(PIT)+ §4(单一构造点)。任务:plan.html AC-001 / AC-007。

## 职责
**确定性风格分型**:给每个候选/持仓打 `style ∈ {SHORT_TERM, VALUE}`。`SHORT_TERM` = 现 5 因子动量数学栈;`VALUE` = 过三层价值线筛选(`backend/screening/value_factors` + `value_score`)且有可派生 thesis。标签 **display-only + 仅软层**:贯穿 InstructionPlan reasoning 上下文 / evidence / 前端 / 飞书消息 + 条件化卖出软层(止盈带,AC-006),**永不改任何硬风控数值**(止损/熔断/仓位三连/14-check/可卖量/单一构造点 —— AC-006 对抗测试钉死)。

## 模块结构(已实现)
| 文件 | 内容 | 任务 |
|------|------|------|
| `models.py` | `StyleTag`(SHORT_TERM/VALUE)+ `StyleInputs`(因子谱 + 三层 value_score + thesis 可派生性)+ `StyleClassifierConfig`(value_gate frozen)+ `StyleClassification`(verdict + replay-stable reason) | AC-001 |
| `classifier.py` | `classify_style(inputs, config) -> StyleClassification`:纯总函数,VALUE iff value_score 有限 ≥ gate AND thesis_derivable;否则 SHORT_TERM(AC-003 前 value_score=None → 全 SHORT_TERM,与现状 bit-identical)。`STYLE_FEATURE_CODE_VERSION` pin | AC-001 |

## 本模块红线
1. **纯确定性零 LLM**:同 `StyleInputs` + 同 config → 同 `StyleClassification`(bit-exact 可 replay)。**严禁** `import backend.{llm,agents,agents_team,mirofish}`(redline `[AC-007]` + `tests/style/test_module_contract.py` AST 双守门)。可用:标准库。
2. **永不构造 InstructionPlan**;style 是 advisory 标签,无 side/volume/limit_price。
3. **标签 display-only**:落 `PositionThesis.style`(AC-001)+ 持仓铭牌 `entry_style`(broker 买入时 stamp,AC-001)+ 飞书 `style_badge`(renderer,display-only);**永不改风控数值**(AC-006 对抗:任意 style 组合下保护性 SELL bit-identical)。
4. **fail-closed**:脏 / 缺 value_score → SHORT_TERM(更保守软层),永不臆造 VALUE。
5. **value 软层只增不减**:VALUE 仅放宽止盈带(`value_take_profit_r_mult ≥ 1.0`,让利润奔跑),**永不**收紧任何止损(`backend/monitoring/style_soft`)。

## 接口契约
- `classify_style(StyleInputs(momentum_20d, volatility_20d, ma_ratio_5_20, value_score, thesis_derivable), config?) -> StyleClassification`。
- 落库:Line-1 `_persist_thesis` 算 style → `build_position_thesis(style=)`;铭牌:runner `StyleNameplateSink.set_pending_entry_style(code, style)` → `MockBroker._apply_buy` episode-open stamp `entry_style`。
- 价值槽:`CandidateSelector.select(..., value_scores=, value_gate=)`(AC-005;None → bit-identical)。
- 软层:`evaluate_intraday_sell_intents(..., style_by_code=, style_soft=)`(AC-006;None → v11 bit-for-bit)。
- 飞书:`backend.integrations.feishu.renderer.style_badge(style) -> str`(⚡短线/🏛价值;display-only)。

## 三层价值线(配套,在 backend/screening/)
- `value_factors.py`:中层(event-study CAR / Amihud / 容量,AC-002)+ 底/表层 helper(beta / resonance_count / pit_fundamentals / percentile,AC-003)。纯 PIT。
- `value_score.py`:三层确定性合成 `compute_value_score -> ValueScore ∈ [0,1]`(AC-003)。
- `backend/knowledge_graph/resonance.py`:只读 KG 独立 evidence family 计数(同 run 去重,AC-003)。
- `backend/theme_research/tier_weights.py`:题材四级赋权 1.0/0.75/0.5/0.25 + 序约束 immutable clamp(AC-004)。

## 测试
`tests/style/`:classifier(确定性 + gate + fail-closed)+ position_thesis_style(落库 + 对抗:标签不改数值 + 旧 thesis 兼容)+ broker_entry_style(铭牌 stamp/pop/add-on/复购)+ module_contract(AST 隔离)。`tests/screening/test_value_factors.py` + `test_value_score.py`、`tests/knowledge_graph/test_resonance.py`、`tests/theme_research/test_theme_tier.py`、`tests/candidate_selector/test_value_slots.py`、`tests/monitoring/test_style_soft.py`(对抗:硬层 style-invariant)。
