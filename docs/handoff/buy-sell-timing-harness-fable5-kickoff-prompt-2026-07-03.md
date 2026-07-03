# FABLE 5 clean-context session kickoff prompt —— 买卖点/仓位管理 agent harness 调研 + 计划书

> **用途**:owner 2026-07-03 要求。新 session 用 **FABLE 5**(`claude-fable-5`)干净上下文执行。**时机门**:本 harness 计划书**在量化选股策略敲定之后**(DS 防御选股线 dev + 近期 holdout 出结论、选股 alpha 锁定)才落入 `docs/plan.html` 作为新 phase 并实施;在此之前,该 session 产出 = 调查研究 + 计划书草案(存 `docs/research/`),owner 批后再定实施时机。
> **复制下面 `====` 之间的全部内容作为新 session 的首条 prompt。**

====================================================================

你是 QuantMind 项目的架构研究员(FABLE 5)。本 session 的唯一任务:**为「买卖点确定 / 仓位管理」层做调查研究 + 撰写计划书**——在选股成功的前提下,设计一套**完整的 agent 系统级 harness 架构**,确定**准确的建仓 / 加仓 / 减仓 / 清仓时机 + 仓位 sizing**,使**选股 + 买卖点整合后整体稳定盈利**(终极目标)。**本 session 不写实现代码,只产出:①调查研究 ②计划书(implementation plan)。**

## 0. 开工协议(先做,别跳)

1. 读 `docs/SESSION-KICKOFF.md`(通用开工协议)+ `CLAUDE.md` 全文(§1 跨 session 协作协议 / §2 核心红线 / §2.0/§2.0b amendment 总览 / §3 工程原则 / §5 操作速查)。
2. 读 SSoT `docs/plan.html`(`#current` 现状 + `#session-log` + `#frozen` 冻结面 + `#legacy` 负结果地图);确认量化选股线(DS 防御选股 D1→D4)当前进度 —— **本 harness 的输入 = 选股线最终锁定的 alpha,故须待其敲定;本 session 先做调研+计划书,不并入实施**。
3. **grep 核实,别假设**;报告用中文,代码/commit 用英文;完成先改 SSoT 再报告。

## 1. 问题定义(要解决的)

选股线(quant first gate)输出「买哪些票」。**本层解决「对每个持仓,何时建仓、何时加仓、何时减仓、何时清仓,以及每步的仓位大小」**,并把它做成一个**agent 系统级 harness**(确定性决策模块 + LLM 建议层 + 风险门 + 执行契约 + 验证 harness 的整体架构),确保在 ≤5 集中 + 现金 buffer + T+1 + 飞书人工执行的真实约束下**稳定盈利、回撤可控**。

## 2. ⚠️ 必读负结果(核心,绝不重蹈;`docs/research/` + `#legacy`)

**本项目已用真数据证明:在反转选股书上,几乎所有『择时/退出 overlay』净有害。你的架构必须正视这些,不能天真地再造一个 EXIT-timing overlay。**

- **C1a 避顶部 EXIT-on-held**(`c1-avoid-top-exit-results-2026-06-27.md`):net **−663k** vs baseline NoOp **+459k**;MDD 推高 54%→**82%**;输两个零信息 placebo;**②错失 588k > ①避损 485k**。机制:EXIT 砍掉刚买入的超卖名 = 反弹前砍仓。
- **B1 regime de-risk**(`b1-regime-derisk-results-2026-06-26.md`):derisk MDD **60.4% > baseline 54.2%**;regime 择时**负技艺**(两 placebo 双双击败);−10% 检测器晚触发穿过 V 型反弹。
- **B2 永久防御 sleeve**(`b2-defensive-sleeve-results-2026-06-27.md`):旧 MDD≤8% gate **FAIL on every arm**;唯一 ≤8% 者=全仓国债=债券基金非选股闸;**红利 ETF 是 equity,broad crash 同崩**。
- **QGR-4 exit-veto**(买入集 veto):MDD 54.2%→**58.3%**(veto 不控回撤反升)。
- **机械 −12% 止损**对反转策略**净有害**(C1a `stop_only` −431k)—— P-B 原则不否定,但**固定阈值实现与反转冲突**=决策边界发现。

**教训提炼(你的架构须内化)**:① 这些失败**都在反转书上**(5 日买超卖);新选股线是**防御性**(D1-D4),动态不同 —— 但**绝不可假设 timing 在新策略上就有效,必须在冻结引擎上验证**。② **执行机制**(close-T→T+1、不可成交排队、止损硬触发、做T profit-gate、再入锁)**已冻结为契约**(见 §3 C0b),你设计的是**timing 决策**不是执行机制。③ 结构墙:`rotation-only 冻结竞技场`表达不了组合层控回撤 overlay;真控回撤杠杆 = **现金 buffer**(buf40_5 MDD 56%→31%),不是择时。④ **FAIL 报 FAIL**,绝不移球门/样本内调参凑结果(round-1..4 死法)。

## 3. 必须整合的既有资产(REUSE,别重造;grep 核实)

- **冻结执行契约 C0b**:`scripts/factor_research/e2e_simulator.py`(`run_e2e_backtest` 复用冻结 harness)+ `ExitExecutionContract`(contract_id SHA256,钉死 close-T→T+1 / 不可卖排队被套 / 止损硬触发 / 做T profit-gate / 再入锁)+ 9 tests(byte-exact overlay-disabled≡冻结引擎不变量)。**你的 timing 层挂在这个契约上,契约字节不动。**
- **RiskEngine**:纯函数 14-check,`backend/risk/`,≥95% 覆盖,严禁 `import backend.{llm,agents,mirofish,data}`;仓位三连(单股 ≤15%〔P-E 已改:取消单股 cap + 置信集中~60%〕/ 总仓 ≤70% / 单次 ≤5 万)+ 熔断(≤5 单/日 + 日亏 −5% + 连亏 3 + 60min 冷却,SELL 不熔断)+ ≤5 并发持仓。
- **InstructionPlan 单一构造点**:仅 `instruction_plan_builder` 可构造;`side/volume/limit_price` 确定性派生,**永不**来自 LLM JSON;状态机 `DRAFT→VALIDATED→DISPATCHED→FILLED/EXPIRED/REJECTED/AMBIGUOUS`。
- **双线架构**:Line-1 = LLM 选股路径(4 必经 agent 辩论,fund_manager 唯一方向倡议者);**Line-2 = 确定性零 LLM 持仓监控**(`AnomalyDetector`/`AddPositionEvaluator` 派生 SELL/ADD,不经辩论,builder `assemble_monitoring_plan` 单一构造点)。**你的减仓/清仓决策大概率属 Line-2 确定性路径。**
- **持仓 thesis(Phase W)**:`PositionThesis`(LLM 支柱文本 + **确定性**量化失效阈值,显式落库)+ 阶段2 `THESIS_QUANT_BREAK`(白名单量化模板派生,**只增卖压永不放松现有止损**)。
- **≤5 槽轮动(Phase V)**:`backend/slot_portfolio/`(纯量化、append-only `RotationIntent`、在位『独立够弱』AND 挑战『margin 胜出』双条件、T+1 跨日、到期 fallback)。
- **冻结回测引擎**:`scripts/factor_research/gate_backtest.py`(`run_gate_backtest`)+ `gate_bar_source.PitBarSource` + `honest_gates`(DSR)+ `trial_ledger`(非清零债)+ `regime_detector` + `arena_ablation`(placebo 消融模板)。**timing 层必须在此验证,禁假设。**

## 4. owner 交易原则 P-A..P-E(架构须遵守;`feedback-owner-trading-principles-2026-06-27` + `qgr-confirmation-stop-swing-sizing-amendment-2026-06-27`)

- **P-A 确认门**:建仓等市场反馈,**不追飞刀**(下跌中不接);入场侧确认门未验(C1a 只证 EXIT 侧确认有害),须验证。
- **P-B 强制止损两条腿**:硬触发腿**不等确认**(安全底线);但**固定阈值机械止损在反转上净有害** → 止损须**策略适配 + 验证**,不是照搬 −12%。
- **P-C 做T profit-gate**:**无正浮盈不做T**(T+1 负成本)。
- **P-D 安全底线**:见 §2 红线。
- **P-E 仓位**:**取消单股 15% cap** + **置信集中~60%**(高置信名重仓)+ **≤5 名上限(非必占满)** + **≥40% 现金 buffer,严禁梭哈**。现金 buffer 是唯一被证有效的控回撤杠杆。

## 5. 硬约束 / 红线(违反即停;`CLAUDE.md §2`)

永禁真实下单 · 飞书人工执行(display-only,owner 手动)· 127.0.0.1 only · **LLM 永不写决策字段**(只 4 类可写文本:reasoning / evidence content / debate text / proposal text)· RiskEngine 纯函数 · InstructionPlan 单一构造点(`side/volume/limit_price` 永不来自 LLM)· PIT 字节存档可复现 · fail-closed(数据损坏)/ fail-open(基建抖动)· 反过拟合四门不放宽 · 非清零账本不清零 · governance enum 不动 · **codex 前置门**(有代码任务 commit 前)· push/摄取/live 激活/look-once = owner-gated · FAIL 报 FAIL · 报告中文 / 代码 commit 英文。

## 6. 方法论(强制)

1. **Research & Reuse first**(`~/.claude/rules/development-workflow.md`):`gh search` + 一手文献(仓位管理 / entry-exit timing / pyramiding 加仓 / trailing & chandelier stop / volatility-targeting & Kelly sizing / 分批建仓 scaling / execution 算法),优先复用 battle-tested 方案;**为 A 股 ≤5 人工执行 T+1 场景适配**,provenance 记来源。
2. **正视负结果**(§2):任何 timing/EXIT/sizing 设计都要说明「为何不会重蹈 C1a/B1/B2/QGR-4」+ **如何在冻结引擎上验证**(不假设)。
3. **plan mode**:调研后进 plan mode,拆架构 + 分 phase + 定 gate + TDD;用 `AskUserQuestion` 就关键决策点问 owner(如:减仓/清仓是纯确定性还是 LLM 建议 + 确定性裁决;sizing 是 vol-target 还是置信分层;止损如何策略适配)。
4. **codex 对抗**:计划书须过 codex review(`codex review` 或 `/code-review high`)≥1 轮红队,修完 P0/P1/P2 findings。
5. **产出落盘**:调研 + 计划书草案存 `docs/research/`;**待选股 alpha 敲定后**,再把计划书整合进 `docs/plan.html`(仿 DS phase 的加法:新 phase + 任务卡 + SESSION_LOG + 修订)。

## 7. 产出规格(计划书须含)

1. **问题分解**:建仓 / 加仓 / 减仓 / 清仓 四类决策,各自的**触发信号 + 确定性判据 + sizing 规则 + 与 RiskEngine/契约的接口**;哪些是确定性(权威决策),哪些 LLM 仅 evidence-only 建议。
2. **agent 系统级 harness 架构**:模块图 + 数据流(选股输出 → 建仓 → 持仓监控〔加/减/清〕→ InstructionPlan → RiskEngine → 飞书人工)+ 每个 agent/模块的输入输出 + LLM 权限边界 + 单一构造点如何不破。
3. **与选股线整合**:如何吃 DS 选股 alpha 的输出;≤5 槽 + 现金 buffer + T+1 轮动如何与建仓/清仓协同;整体稳定盈利的闭环。
4. **验证方案**:在冻结引擎 + `e2e_simulator` 契约上**回测整合系统**(选股+timing),regime 分层 + 股灾切片 + 反过拟合门 + placebo 消融(证 timing 层非 size-drift/运气);**FAIL 报 FAIL**。
5. **分 phase 实施路线**:任务卡(TDD / codex 门 / owner gate)+ 依赖 + 风险;明确哪些待 owner 授权(摄取/激活/上线)。
6. **诚实预期 + 决策树**:基于负结果,诚实说明各设计的成功可能性 + 失败分支的预承诺动作。

## 8. 完成标准

调研 doc + 计划书 doc 落 `docs/research/`;计划书过 codex ≥1 轮;末尾一句话指下一步 + 在 SESSION_LOG(若已并入 plan.html)或 handoff 记录 owner 待决点。**记住:本 session 只调研 + 写计划书,不写实现代码;计划书整合进 plan.html 的时机 = 选股 alpha 敲定之后(owner gate)。**

====================================================================
