# backend/slot_portfolio/ — 子任务上下文(Phase V)

> 状态:**doing(V-002 scoring/policy;V-003 RotationIntent + churn + 到期 fallback)**。治理:[P0-7-amendment-2026-06-01-five-slot-rotation](../../docs/decisions/P0-7-amendment-2026-06-01-five-slot-rotation.md) + R0 §4(单一构造点)+ R0 §3(PIT)。任务:plan.html V-002 / V-003。

## 职责
**确定性 ≤5 槽组合轮动决策**(纯量化上游层):满 5 持仓且更强候选出现时,判**是否**卖最弱在位换入挑战者。只**提议 + 落 intent**,**不构造 InstructionPlan**(R0 §4),喂 builder。RiskEngine 14-check 仍独立权威 —— 本层在上游,不替代任何 check,check#6 是拒第 6 笔的硬 guard。

## 模块结构
| 文件 | 内容 | 任务 |
|------|------|------|
| `scoring.py` | `IncumbentState`/`ChallengerState` 输入;`evaluate_incumbent_weakness`(7 条)+ `evaluate_challenger_margin`(4 条 margin);纯函数,fail-closed 向**不动作** | V-002 |
| `policy.py` | `RotationPolicyConfig` + `load_rotation_policy_config`(runtime-immutable,fail-closed 校验)+ `propose_rotation`(最弱弱在位 × 最强合格挑战者 → `RotationProposal`) | V-002 |
| `rotation_intent.py` | append-only `RotationIntent`(卖单/双方/分数/expires_at/replay 输入)+ churn 4 闸门 + 到期 3 路 fallback | V-003 |

## 本模块红线
1. **纯函数零 IO**(除一次性 config load);**严禁** `import backend.{llm,agents,mirofish}`(redline `[V-002]` + 模块契约测试 AST 守门)。可用:标准库 + pydantic/structlog/filelock + `backend.slot_portfolio` 内部。
2. **永不构造 InstructionPlan**(`grep "InstructionPlan(" ⊆ {model, builder, tests}` 不破);本层只产 `RotationProposal` / `RotationIntent`,卖/买经 builder 单一构造点。
3. **双条件**(§1.3):在位者**「独立够弱」7 条全真**(无保护止损/无硬退出/龄≥5td/Line-1 分位≤P40/恶化≥20分位/≥1确认/无否决)AND 挑战者**「以绝对 margin 胜出」**(合格 + ≥P75 + 领先≥25分位 + 绝对组合分胜出)。**「独立够弱」是防护核心** —— 健康在位**绝不**为追挑战者卖掉。
4. **确定性 over PIT-pin frame,可 replay**:同输入 + 同 config → 同 `RotationProposal`(bit-exact)。非有限/越界数值 → fail-closed 向不动作(在位「不弱」+ 挑战者「不胜」)。
5. **解耦①②**(§1.6):只用现有 **Line-1 量化分** + **确定性 Line-2 在位健康**(今日可得)。theme conviction(①)/ thesis-health(②)是**可选的** provenance-tagged 组合分组件,各自有独立 PIT replay + shadow + amendment 后并入,**非前置依赖**。扩展位 = `SHIP_FIRST_SCORE_COMPONENTS` + `composite_score`。
6. **churn + 到期 fallback**(V-003):每日 ≤1 轮动 / ≤1 open intent / subcap ≤1 / **让位保护性止损** / 同 incumbent 20td 冷却 / 对 30td 冷却;`expires_at=min(3td,下次再平衡)`;到期 3 路 fallback(原挑战者→最佳≥P75→留现金标 `UNDERINVESTED_ROTATION_EXPIRED` 阻断后续轮动至人工 gate)。

## 输入契约
`line1_percentile`/`entry_percentile` = 今日全市场截面百分位 ∈ [0,1](higher=stronger);`composite_score` = 替换分(ship-first = Line-1 量化 composite,同 screener score 量纲)。编排层(V-004)从 PIT-pin 的 Line-1 screen 结果 + 确定性 Line-2 health 组装这些数值后调本模块,本模块**不取数**。

## 测试
`tests/slot_portfolio/`:incumbent-weak 各条 + 健康在位不卖 + challenger margin 边界 + 对抗(分数任意不破单一构造点)+ replay 可复现 + AST 隔离 + churn/到期 fallback。覆盖率 ≥80%。
