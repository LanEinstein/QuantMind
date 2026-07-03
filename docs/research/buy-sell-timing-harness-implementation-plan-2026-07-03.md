# 买卖点 / 仓位管理 agent 系统级 harness —— 计划书草案(2026-07-03)

> **状态**:**计划书草案**(配套调研 = `buy-sell-timing-position-management-research-2026-07-03.md`)。**作者**:Claude(Opus 4.8)· owner:dr.zhang
> **⏳ 时机门(硬)**:本计划书**待选股 alpha 敲定后**(DS 防御选股 D1→D4 dev + 近期 holdout 出结论、alpha 锁定)才整合进 `docs/plan.html` 作为新 phase 并实施。在此之前 = 草案,owner 批后再定实施时机。**本 session 不写实现代码**。
> **过门**:本计划书须过 codex ≥1 轮红队(见 §8 记录)。
> **红线**:§CLAUDE.md §2 全留;研究/评测**零 LLM**;冻结引擎 + `ExitExecutionContract` 字节不动;反过拟合四门不放宽;非清零账本不清零;**FAIL 报 FAIL**;push/摄取/live 激活 owner-gated。

---

## 0. 设计公理(从调研 §0 继承,不再 litigate)

1. **sizing 承重,timing 陷阱**:回撤控制的载重杠杆 = 结构性 sizing(硬现金 buffer + 置信集中),非择时 overlay。**架构默认立场**:sizing 层优先且承重;一切 timing 层默认高风险、默认 OFF,须独立跑赢零信息 placebo 才纳入。
2. **执行机制冻结**:`ExitExecutionContract`(close-T→T+1 / 排队被套 / 硬止损不等确认 / 做T profit-gate / 再入锁)+ 冻结引擎字节不动。本 harness 设计 **timing 决策**,挂在契约上。
3. **决策权归属(红线派生,非可选)**:研究/评测侧 **100% 确定性、零 LLM**;live 侧 LLM 仅 **evidence-only advisory**(综合研判文本入 `evidence_collection`,永不写 side/volume/limit_price;单一构造点 `instruction_plan_builder` 派生实际订单)。**本计划书研究侧全确定性;LLM advisory 层 = live-only,单独 owner-gated,不在本研究竞技场。**
4. **减仓/清仓大概率属 Line-2 确定性零 LLM 路径**(`AnomalyDetector`/builder `assemble_monitoring_plan`);建仓属 Line-1;做T 属 value_swing overlay。

---

## 1. 问题分解(四类决策 + sizing)

| 决策 | 触发信号 | 确定性判据 | sizing 规则 | 与契约/RiskEngine 接口 | 权威 |
|---|---|---|---|---|---|
| **建仓 ENTRY** | DS 选股分数入合格集 + (可选)P-A 确认门 | score 分位过阈 + (可选)趋势门/收复确认 | S1-S4 置信 softmax → 目标权重 → DiscreteAllocation 整手 | close-T 决策→T+1 成交(契约§1);RiskEngine 14-check(单一构造点) | 确定性 |
| **加仓 ADD** | **默认不做**(pyramiding AVOID,调研 §2.1#6)| —(仅 S5 分批建仓平滑预定目标,非加码赢家)| 预定目标权重的剩余 tranche | 同 ENTRY | 确定性 |
| **减仓 TRIM** | E2 profit-ratchet(仅盈利仓)/ E4 做T(仅正浮盈)| 宽 ATR give-back / 做T profit-gate | 部分卖(base_floor 保护,守 T+1)| ExitOverlay SELL→T+1;Line-2 `assemble_monitoring_plan` | 确定性(**默认 OFF,gated**)|
| **清仓 EXIT** | ★E1 事件安全(ST/退市/停牌/爆雷)/ E3 因子秩衰减/离开合格集 | 非回复状态变化(布尔)/ 秩掉出阈 | 全平(整手)| ExitOverlay SELL→T+1;硬止损不等确认(契约)| 确定性 |
| **SIZING**(贯穿)| 每再平衡日 | 定比 60% 已投 + 置信 softmax(τ 卡顶名 60% of invested)+ ≥40% 现金地板 | S1(基线)/S2(+inverse-vol)/S3(分数 Kelly)/S4(少名浮现金)| 新确定性 sizing provider(§2.1);byte-anchor eq_5 as plumbing 参照 | 确定性 |

**LLM 边界**:以上**全部确定性**。LLM 仅在 live 侧对「何时卖/做T 扩大战果」出**综合研判 evidence-only 文本**(owner 判据重定 §3 允许 advisory),经飞书 display-only 给 owner 人工执行;确定性规则派生实际订单字段。研究竞技场**无 LLM**。

---

## 2. agent 系统级 harness 架构

### 2.1 模块图 + 数据流

```
DS 选股线(锁定 alpha)
  └─ PanelScoreProvider({day:[(code, defensive_score)]})   ← 选股输出,ranker-agnostic
        │
        ▼
[SIZING 层] ★主载重                                        ← 缺口 §3.1;⚠ 见 §2.4 引擎路径(P0-1);owner Q1
  SizingProvider(scores, view) → per-name target_weights(≤5 名, softmax τ, ≥40% cash floor)
        → DiscreteAllocation(greedy) → 整手 BUY(不等权,含预算目标权重)
        │
        ▼
NEW run_sizing_backtest(仿 run_e2e_backtest 复用冻结内件)   ← 非 run_gate_backtest(它只吃标量等权);见 §2.4
        │  不变量 = sizing-disabled(等权)≡ run_gate_backtest eq_5(byte-exact)
        │
        ▼
[建仓 ENTRY] (可选 P-A 确认门, gated)                      ← 默认直接建;门须胜 same-delay 随机入 placebo
        │  close-T→T+1;分板块滑点;涨停不可成交(契约内件复用,字节不动)
        │  ├─ [清仓 EXIT] ExitOverlay: ★E1 事件安全(非价格) + E3 因子秩衰减(价格盲)
        │  └─ [减仓 TRIM] ExitOverlay: E2 profit-ratchet(盈利仓, gated) + E4 做T(正浮盈, gated 默认OFF)
        ▼
E2ERunResult(backtest_result + overlay_orders + contract_id)
        │
        ▼
[验证 harness] regime 分层 + 6 股灾切片 + 反过拟合四门 + placebo 消融(含暴露匹配对照)+ P&L 四分解 + 非清零账本
        │
        ▼
诚实结果表(中文)→ owner 判(present 结果,不替选)
```

**live 侧(本研究不实现,标注 owner-gated 对应)**:建仓→Line-1(4 agent 辩论,fund_manager 唯一方向);减仓/清仓→Line-2 确定性(`AnomalyDetector`/`assemble_monitoring_plan` 单一构造点)+ RiskEngine 14-check + 飞书人工;做T→value_swing;LLM 综合研判→`evidence_collection` advisory。

### 2.2 每模块 I/O + LLM 权限 + 单一构造点如何不破

| 模块 | 输入 | 输出 | LLM | 单一构造点 |
|---|---|---|---|---|
| SizingProvider | DS 分数 panel + view(equity/cash)| target_weights + 整手 BUY intents | **零** | 研究侧 OrderIntent;live 侧 side/volume/limit_price 仍经 `instruction_plan_builder` 确定性派生 |
| EntryGate(P-A)| 候选 + 趋势/收复/量能 bar 特征 | 通过/延迟布尔 | **零** | 不构造订单,仅门控 |
| ExitOverlay(E1/E2/E3/E4)| ExitOverlayContext(close-T held + bars + 事件表)| 追加 SELL/做T intents | **零** | 走 `_merge_pending`;live 侧 Line-2 `assemble_monitoring_plan` |
| 验证 harness | E2ERunResult 序列 | 结果表 + 门裁决 | **零** | N/A |

> **单一构造点不破证明**:研究侧全在 `scripts/factor_research/`(隔离,`grep "import backend.{llm,agents,mirofish,risk}"` 必空);live 侧订单字段永经 `instruction_plan_builder`,sizing/overlay 只产 evidence/advisory 与确定性触发,不写 side/volume/limit_price。

### 2.3 P-E 置信集中的三条候选表达(owner Q1)
- **A(推荐)**:新 `SizingProvider` 产 per-name target weight(softmax)→ DiscreteAllocation 整手,经**新引擎入口 `run_sizing_backtest`**(§2.4)跑,作新容器 `conf60`;对照 eq_5(科学门)/ buf40_5(部署门)/ **eq_5@60% 暴露匹配等权**(§4)。诚实代价 = 新 sizing 引擎路径 + 账本债。
- **B**:研究侧只用 eq_5/buf40_5 等权+现金 buffer;P-E 置信集中留 live RiskEngine amendment(单独 owner-gated),不在研究竞技场。
- **C**:两者都做(先 B 基线,再 A 增量对照,证置信集中相对等权是否真加值)。
- **Claude 推荐 = A**;但 **conf60 的判据 = 严格胜 buf40_5 与 eq_5@60%(净四门)**,否则如实报「harness 无独立 sizing 边,回退 DS 线 buf40_5」(§6;P1-2/P1-3 修)。**待 owner 定**。

### 2.4 ⚠ sizing 引擎路径(P0-1 修:conf60 表达不了冻结引擎 seam,须新入口)
**冲突(codex R1-P0-1)**:冻结引擎的下单量**不由 provider 决定** —— `run_gate_backtest`/`run_e2e_backtest` 调 `decide_day(signals=provider.signals_asof(day), …)`(`e2e_simulator.py:330`),`ScoreProvider` **只产分数、无 volume 权限**;`decide_day` 用标量 `equal_weight = total_equity // max_total_positions` + `single_stock_cap_percent` 派生**等权**买量(`slot_frontier.py:20` 自承置信集中「a documented gap」);overlay 产的 BUY 又被 `_merge_pending` 对 rotation 已 claim 的 code **丢弃**(`e2e_simulator.py:216-233`)。→ 「新 sizing 层 + 冻结引擎字节不动 + conf60 byte-anchor eq_5」**三者不可同时成立**(照旧等权覆盖则 conf60≡eq_5=no-op;改 decide_day 则破字节)。
**成交层可注入(已核,codex R2 同意)**:`_fill_pending`(`harness.py:271-359`)按 `order.volume` **逐单成交、不重算权重**(line 296)→ 不等权整手 BUY volume **可注入**,成交内件是 volume-agnostic plumbing。

**但更深的墙(codex R2 NEW-P0-A,决策层)**:真正卡住置信集中的**不是**成交层,是**决策核 `decide_day` = rotation-only**(≤1 轮入/日、需 challenger、被保护名不可卖;`harness.py:10` docstring)——它每日只给 ~1 个轮入名派 `equal_weight = total_equity // max_total_positions` 的量,**从不整簿再配**。conf60 需要的是**每再平衡日整簿重配到顶名 36-60%、其余递减的常驻目标权重簿** → **无法用 e2e overlay 的加法手法表达**(exit 是追加 SELL=加法;sizing 是**改写** BUY 权重 + 整簿再平衡)。**这正是 B1/B2 撞的同一堵结构墙(「满仓/rotation-only by construction」),这次出现在 sizing/entry 侧。** 后果:① **「复用非重建」失真**——`run_sizing_backtest` 必须**重建**一个再平衡决策核,不是复用 `_fill_pending` 那几个成交内件能解决;② **byte-exact 锚不可达**——eq_5 就是 rotation 引擎,一个等权再平衡器即便权重相同,其成交流(每日多名再平衡 vs ≤1 轮动、无 challenger/保护名约束、换手不同)与 rotation 引擎**逐字段不同** → 不变量**永不成立**(硬追求它 = 悄悄弱化门 = round-1..4 弱门死法)。

**BT-0 必须二选一并认代价(owner 待决 Q1 的子决策;计划书不默认定死)**:
- **读法 A(可 byte-exact,但不实现 P-E)**:复用 `decide_day` 拿「买谁/卖谁」,**只把当日那 1 个轮入名的 volume 按置信 re-size**,存量名不动。退化等权 ≡ eq_5 成立 ✓,但这只是「按入场置信定量」,权重路径依赖、永不整簿再配 → **根本不是 P-E 的『顶名 60% 常驻集中』**。
- **读法 B(实现 P-E,但是重建)**:自建目标权重再平衡决策核,**放弃**对 `run_gate_backtest` 的 byte-exact 锚,改用**共享成交契约 + 自证守恒不变量**(现金守恒 + 整手 + 成交经同一 `_fill_pending`/滑点/涨停/T+1 契约),并**如实声明这是新决策核、非冻结引擎路径**。

> **诚实推论(codex R2 + slot_frontier)**:slot_frontier 已证容器 DSR-invariant(绑定约束=alpha 非容器)→ 为测低 P(success) 的 conf60 而走读法 B(重建 + 弱化验证锚)是**高成本、低回报**。**更务实**:先用冻结引擎**原生可表达**的容器把「暴露/集中」问题探到底(eq_5 / buf40_5 / **buf60_5 暴露匹配**,全是 slot_frontier 现成 cap 变体),真·P-E 常驻 60% 集中作 **live 侧 RiskEngine amendment** 单独 owner-gated 验证——即 **Q1 倾向 B/C 而非纯 A**(见 §7 更新推荐)。

---

## 3. 与选股线整合

1. **吃 DS 选股 alpha 输出**:harness ranker-agnostic,只需 DS 幸存候选(D1 红利低波 / 或 D2-D4)的每日 `{code: defensive_score}` panel → `PanelScoreProvider`。**选股线锁定哪个候选,harness 就吃哪个**;无 harness 侧重训。
2. **≤5 槽 + 现金 buffer + T+1 轮动协同**:DS 线的 buf40_5 容器(40% gross/60% 现金)= sizing 层的 ≥40% 地板起点;harness 在其上加置信集中(conf60)+ 事件安全清仓 + 因子秩衰减退出。轮动 T+1 跨日语义沿用冻结引擎。
3. **整体稳定盈利闭环**:选股(WHAT,防御 alpha 砍左尾/降回撤)× sizing(HOW MUCH,现金 buffer + 置信集中控回撤)× 清仓(WHEN-OUT,事件安全砍永久减值尾)= 三层皆**结构性/确定性**,无一依赖市场择时 → 契合「稳定盈利、回撤可控」终极目标。timing 实验层(P-A/做T)仅在证明跑赢 placebo 后**增量**纳入。

---

## 4. 验证方案(冻结引擎 + 契约上回测整合系统;FAIL 报 FAIL)

**装置**:`run_gate_backtest`(sizing/建仓)+ `run_e2e_backtest`(退出/做T overlay,byte-exact overlay-disabled≡冻结引擎不变量)+ `PitBarSource`(PIT 字节,qfq as-of pin,真 stk_limit)。**窗口 = train_val only**(`20150209→20250425` 老牛熊 6 股灾;近期 holdout 永不碰,真 OOS = owner-gated look-once)。

**判据(owner 2026-06-27 重定,继承)**:① 规则驱动持有期稳定绝对净盈;② **「不被挂山顶」硬门**(避永久套牢);③ MDD/缩量回调仅披露 + 现金 buffer 控;④ 做T 增益(如启)。**熊市/股灾累计不亏 = 防御选股线硬要求(继承 DS synthesis §4)**。

**反过拟合(不放宽)**:size+行业中性化删最小 30%(`neutralize_panel`)· `deflated_sharpe_hac` 对**非清零账本**(每层 append kind=ablation,债只增)· SPA/Romano-Wolf 对 placebo · CPCV(按日期分组)· regime 分层(牛/熊/震荡)+ 6 股灾切片。

**每层专属 placebo(防伪装择时 / size-drift / disposition / 纯暴露削减)**:
- **sizing**:conf60 vs eq_5 vs **eq_5@60% 暴露匹配等权(同 ≥40% 现金地板、等权)** vs size-matched random top-5 vs 纯选股 A0 baseline。**关键(P1-3 修)**:`slot-frontier-results` 已证 buffer 是 risk-scaler(MDD/净盈 ~正比暴露、Sharpe/DSR ~恒定)→ conf60 相对 eq_5 的任何 MDD 改善可能 100% 来自「60%<100% 暴露」(已被 buf 系列证过)。**唯一干净剥离「置信集中」相对「≥40% buffer」增量的对照 = eq_5@60% 暴露匹配**;conf60 须在**同暴露**下严格胜等权,才算集中真加值。size-matched random 仅控 size-drift,不控暴露差,不充分。
- **建仓 P-A**:+确认门 vs 立即入 vs **same-delay 随机入 placebo**(须严格胜随机延迟 + 净 trade-off + 给闲置现金计收益 + regime 分层;防「晚入=少吃下跌暴露」机械效应)。
- **退出 E1/E2/E3**:vs **calendar-random-sell + rate-matched-random-sell** 两 placebo + **P&L 四分解**(避损 vs 错失,复用 C1a 装置)+ 平衡诊断(退出名持龄/log-mv/未实现盈亏,防砍年轻超卖名)。★E1 事件退出须**大幅**胜随机卖(fire on real events);胜不了 = secretly noise stop。
- **做T E4**:profit-gated scale-out vs rate-matched 随机部分卖;胜不了 = disposition 偏差,**报 FAIL 保持 OFF**。

**预承诺 FAIL 判据(写死,评测前)**:任一层若扣账本债后 DSR 不过 / 输 placebo / P&L 四分解「错失>避损」/ MDD 反升 → **报 FAIL,不纳入,不移球门**。整合系统若仅 sizing 层过、timing 层全 FAIL → **交付 sizing-only harness**(诚实且已是主载重)。

---

## 5. 分 phase 实施路线(待 alpha 锁定后并入 plan.html;仿 DS phase 加法)

> 每个编码任务:TDD 先行 · `pytest tests/factor_research` 只增不减 · 逐文件 ruff+mypy · 研究域红线扫描 · **codex commit 前置门** · 一任务一 feature commit · 完成回填 SSoT。**owner gate 点显式标注**。

| 任务 | 内容 | 依赖 | gate |
|---|---|---|---|
| **BT-0** | spec 冻结(committed sizing **S1 唯一候选**、S2-S4 仅 robustness 诊断不得晋级〔P1-6〕 + 块权重 + 容器 conf60 + 事件安全触发白名单 + **各源 PIT as-of 字段**〔P1-5〕 + 各层 placebo 含 eq_5@60% 暴露匹配〔P1-3〕 + **sizing 过门=反过拟合四门同一 DSR≥0.95 + 严格胜 buf40_5**〔P1-2〕 + FAIL 判据;spec_hash,评测前定死)| DS alpha 锁定 | docs/ledger,豁免 codex |
| **BT-0.5a** | 防御持仓短期自相关符号前测(调研 §2.3 硬要求:定「防御=趋势 or 反转」→ 决定价格类退出是否值得建)| BT-0 | 门禁绿 + codex |
| **BT-0.5b(P1-4)** | **事件计数 power precheck**(仿 AP-0.5):数 E1 触发事件数 + placebo 可匹配退出数 + 最小可检测效应;`<30`/功率不足 → **预承诺降纯诊断披露、不进四门裁决**(非报 FAIL);E2/E4 同办 | BT-0 | 门禁绿 + codex |
| **BT-1** | 新因子:beta/tail-beta 滚动 OLS(DS synthesis 唯一新代码)+ 可选 ATR 接线 | BT-0 | 门禁绿 + codex + PIT leak_probe |
| **BT-2 ★** | **先按 BT-0 裁定的读法 A/B 建**(§2.4;A=re-size 轮入名 volume〔可 byte-exact 但非 P-E〕/ B=重建目标权重再平衡核〔实现 P-E 但放弃 byte-exact 锚,改守恒不变量〕)+ **SizingProvider**(S1 定比+softmax;S2-S4 诊断)+ DiscreteAllocation 整手 + conf60 容器;**显式设定并披露 `single_stock_cap_percent`**(conf60 顶名 36-60% 会触 `check_invariants` cap DIVERGENT,须提 cap 且披露此时 cap 不变量让位 sizing 层,对齐 live P-E 取消 15% cap owner-gated amendment)〔NEW-P1-B〕 | BT-1,BT-0(读法裁定)| 门禁绿 + codex + **读法 A:sizing-disabled≡run_gate_backtest eq_5 byte-exact / 读法 B:共享成交契约 + 守恒不变量**(P0-1/NEW-P0-A) |
| **BT-3 ★** | **★E1 事件安全清仓 overlay**(namechange/suspend_d/delisted/forecast/express;**逐源 PIT 公告日 as-of,非生效/退市日**〔P1-5〕;非价格)+ E3 因子秩衰减退出 | BT-2,BT-0.5b | 门禁绿 + codex + overlay-disabled byte-exact + **逐源 leak_probe(事件日 vs 公告日 gap)** |
| **BT-4** | sizing + 清仓整合竞技场:conf60 × 事件安全 × 反过拟合四门 × regime × 6 股灾 × placebo(含暴露匹配)× P&L 四分解;**universe PIT-inclusive 与否显式裁定,current-roster 则 E1 避退市收益仅披露不计入判据**〔P1-5〕 | BT-2,BT-3 | 门禁绿 + codex + **FAIL 报 FAIL** |
| **BT-5(gated 实验)** | E2 profit-ratchet(盈利仓)+ E4 做T(正浮盈,默认 OFF)进竞技场,须胜 placebo 才纳入 | BT-4 | 门禁绿 + codex + placebo 门 |
| **BT-6(gated 实验)** | P-A 建仓确认门(C-E2 趋势门 + C-E1 收复)进竞技场,须胜 same-delay 随机入 placebo + 分解暴露效应 | BT-4 | 门禁绿 + codex + placebo 门 |
| **BT-7** | 整合结果 doc(诚实分级 + 决策树落点)+ 交付形态裁决(full or 「conf60 增量 vs buf40_5」如实)| BT-4..6 | docs;owner 判 |
| **FW'**(owner gate)| **DS-alpha + harness 打包在同一 forward pass 单次 look-once**(严禁两次 OOS;与 DS holdout 协调)〔P1-6〕 | BT-7 | **owner gate** |
| **SIM'**(owner gate)| live 激活:Line-1/Line-2 接线 + LLM advisory + value_swing + ¥1万执行可行性 + RiskEngine P-E amendment | FW' | **owner gate** + amendment |

**待 owner 授权项**:数据摄取(本 harness **无需摄取**,全在库)· push · look-once 前向 · live P-E RiskEngine amendment(取消单股 15% cap → 置信集中须独立 owner-gated amendment + 对抗测试 + 重启)· live 激活。

---

## 6. 诚实预期 + 决策树(基于负结果的预承诺)

> **过门数值定死(P1-2 修,防拿 provisional 当交付)**:sizing 层过门 = **反过拟合四门同一 DSR≥0.95**(不为 sizing 另设软判据);且 **conf60 须严格胜 buf40_5 与 eq_5@60% 暴露匹配(净四门)** 才算 harness 有独立 sizing 增量。**背景**:`slot-frontier-results` 已证全容器配置 DSR 0.003-0.006 ≪ 0.95、绑定约束是 ranker alpha 弱非容器;B2 perm_cash2 DSR 0.044 = provisional 不过门。**buf40_5 是 DS 线 slot_frontier 的容器,不是本 harness 造的** → harness 唯一新 sizing 贡献 = conf60;conf60 打不过 buf40_5 则 harness sizing 增量 = 0。

```
BT-4 sizing+清仓整合结果?
├─ conf60 严格胜 buf40_5 + eq_5@60% 暴露匹配(净四门 DSR≥0.95)+ 胜 placebo
│    └─ ✅ 置信集中真加值 → BT-5/6 gated 实验(增量)→ BT-7 → owner 判 look-once
├─ conf60 过新主判据(净盈+熊市不亏)但 DSR<0.95 / 不胜暴露匹配
│    └─ **provisional,非交付**(round-1..4 死法):如实报「无过反过拟合门的 sizing 边」+ 数据上报 owner,冻结等前向(与 ffc1db3 同队列),不当达标
├─ sizing 过但 timing 实验(BT-5/6)全 FAIL
│    └─ 诚实表述:harness 载重 sizing(现金 buffer)本就来自 DS 线 buf40_5;harness 仅测 conf60 增量 → 若 conf60 无增量,harness 无独立 sizing 边(**不冒充「已是主载重」**)→ owner 判
├─ ★E1 事件安全退出胜随机卖(若 BT-0.5b 功率足)
│    └─ 纳入(控永久减值尾);功率不足 → 纯诊断披露不裁决;胜不了随机 → secretly noise,报 FAIL 剔除
└─ conf60 亦不胜 eq_5@60%/random(DSR 不过)
     └─ **报 FAIL**:置信集中相对等权无增量 → 回退纯选股 + buf40_5(DS 线已有);
        harness 层无独立可部署边 → 如实上报 owner,不移球门
```

**诚实成功概率(先验,基于调研分级)**:
- **现金 buffer**(容器层):**中-高**——但 codex R2 澄清:这条载重杠杆 **slot_frontier 已证属容器层、DSR-invariant、本就来自 DS 线 buf40_5/buf60_5**,**不是本 harness 的新贡献**。
- **conf60 置信集中(harness 唯一新 sizing 贡献)**:**低**——slot_frontier 证全容器 DSR≪0.95(绑定约束=alpha 非容器);conf60 只是同弱 Sharpe 线上又一容器点,过四门 + 严格胜 buf60_5 暴露匹配的先验**低**。且走读法 B(实现 P-E)= 高成本重建 + 弱化验证锚。**owner 批 BT-2 前须知:这是高成本、低 P(success) 的增量验证,大概率 FAIL**(诚实,不是劝退,是不许拿 provisional 冒充成功)。
- ★E1 事件安全清仓:**中**(结构豁免 Kaminski-Lo;预期低频有效)。
- E2/E3/C-E2:**低**(大概率纯暴露削减 / 防御书 placebo 门槛易 FAIL)。
- E4 做T / C-E1 / C-E4:**很低**(disposition + T+1 税 / A 股符号反转)。**预承诺:不过 placebo 即报 FAIL 保持 OFF**。

**决策边界发现(预留)**:若防御持仓 BT-0.5 前测显示**弱反转**(非趋势),则价格类退出(E2)先验进一步降,应更靠事件安全(E1)+ 因子秩衰减(E3)+ 现金 buffer;若显示**趋势**,E2/trailing 先验略升。

---

## 7. owner 待决点(2026-07-03 AskUserQuestion 发出,owner away 未答;下方 = Claude 推荐,待 owner 确认)

| # | 决策 | Claude 推荐 | 备选 |
|---|---|---|---|
| **Q1** | P-E 置信集中如何上冻结引擎 | **推荐随 codex R2 下调 → 倾向 C/B**:先用冻结引擎原生容器(eq_5/buf40_5/**buf60_5 暴露匹配**)把暴露/集中探到底,真·P-E 常驻 60% 集中作 live RiskEngine amendment 单独 owner-gated;**若仍要研究侧验 conf60,须先 BT-0 裁定读法 A(可 byte-exact 但非 P-E)vs B(实现 P-E 但重建+放弃锚)**(§2.4)| A 纯研究侧新核 / B live-only / C 两者 |
| **Q2** | harness 建设范围与优先级 | **A Sizing-first 分层**(sizing 主载重 → 两低风险退出 → P-A/做T 作 gated 实验默认 OFF)| B 全量并行 / C 极简 |
| **Q3** | P-B 强制止损实现口径 | **A 事件驱动异质安全退出**(ST/退市/停牌/爆雷,非价格%)+ 宽 ATR profit-ratchet 作**独立** gated 实验(非 P-B)| B 两条腿 / C 保留价格% |
| **Q4(codex P2-7 新增)** | 置信集中上限 X% **of total**(非 of invested)| 待 owner 定 X | owner 拍 |

**Q4 说明(P-E 三约束数学互斥,须 owner 拍)**:owner P-E 原文「单名可达 ~60% **of total**」,但「60%-of-total **且** ≥40% 现金 **且** ≤5 名」仅在**持 1 名**时自洽(60%+40%=100%);调研 S1 的 τ 卡「顶名≈60% **of invested**≈36% of total」是对 P-E 的**重新诠释**,不该由 harness 默默定死。owner 须在「集中度 vs 分散到 ≤5 名」间显式选 X(X 越高实际持名越少;X=60%-of-total→基本单票)。

**其余已由红线/证据锁定(不问)**:研究侧零 LLM + live LLM advisory evidence-only(红线派生);pyramiding 加仓不做(调研 §2.1#6);固定百分比价格止损不建(Kaminski-Lo + −431k)。

---

## 8. codex 红队记录

### 8.1 R1(2026-07-03,codex-oracle adversarial red-team;7 findings 全处置)

| # | 严重度 | 缺陷 | 处置 |
|---|---|---|---|
| P0-1 | CRITICAL | conf60 置信集中在冻结引擎 seam 上表达不了(ScoreProvider 无 volume 权限 / decide_day 标量等权 / overlay BUY 被 `_merge_pending` 丢弃);「新 sizing 层 + 字节不动 + byte-anchor eq_5」三者互斥 | ✅ 修:新 §2.4 + 改 §2.1 图 + BT-2 gate → 交付新引擎入口 `run_sizing_backtest`(仿 e2e_simulator 复用冻结内件),不变量改为「sizing-disabled(等权)≡ run_gate_backtest eq_5」,非 conf60 anchor eq_5 |
| P1-2 | HIGH | 「DSR 站得住」未定义;slot_frontier 已证全容器 DSR≪0.95、绑定约束是 ranker;「sizing-only 已是主载重」冒充 DS 线 buf40_5 | ✅ 修:§6 定死过门=四门同一 DSR≥0.95 + conf60 严格胜 buf40_5;删「已是主载重」话术;加 provisional-非交付分支 |
| P1-3 | HIGH | conf60 vs eq_5 混淆集中度与暴露水平(buffer 是 risk-scaler,MDD 改善可能纯降暴露)| ✅ 修:§4 + §2.3 加 **eq_5@60% 暴露匹配等权**对照,conf60 须同暴露下胜等权 |
| P1-4 | HIGH | 无 power precheck;防御书 placebo 易 FAIL;E1 事件在红利低波书极稀,可能测不出功率 | ✅ 修:新 BT-0.5b 事件计数 power precheck(仿 AP-0.5);功率不足预承诺降纯诊断不裁决 |
| P1-5 | HIGH | E1 事件退出 PIT/look-ahead 逐源未钉死(delist_date 提前已知 / ann_date vs end_date)+ 幸存者偏差 | ✅ 修:BT-0/BT-1/BT-3 逐源公告日 as-of + 逐源 leak_probe;BT-4 current-roster 则 E1 避退市收益仅披露不计入判据 |
| P1-6 | MEDIUM | S1-S4 best-of 多重比较;DS-alpha 与 harness 各自 look-once = OOS 复用 | ✅ 修:BT-0 S1 唯一候选(S2-S4 诊断不晋级);FW' 改 DS-alpha+harness 同一 forward pass 单次 look-once |
| P2-7 | MEDIUM | 「60%」实为 36%-of-total,悄悄重解 P-E;P-E 三约束数学互斥 | ✅ 修:§7 新增 owner Q4(置信集中上限 X%-of-total,须 owner 拍) |

**codex 肯定面**:§6 决策树最深 FAIL 分支诚实无后门;timing 层先验压得低(内化 C1a/B1/B2/QGR-4);E1 结构豁免论证机制上站得住(前提 P1-4/P1-5 守住);exit overlay seam 忠实度意识正确(问题在 sizing 那条路无对应 seam=P0-1,已修)。

**结论**:R1 的 P0/P1/P2 全 7 项已在计划书内处置(本 session 为计划书草案,「修复」= 修订计划书对应章节)。

### 8.2 R2(2026-07-03,codex-oracle verify pass;复核 R1 修复 + 查新洞)

**复核**:6/7 真闭合(P1-3/P1-4/P1-5/P1-6/P2-7 CLOSED;P1-2 PARTIAL→已补 P2-C 修)。**P0-1 = PARTIALLY-CLOSED**:修复解了成交层(`_fill_pending` 确接不等权 volume ✓)但暴露更深的**决策层结构墙**。

| 新 finding | 严重度 | 内容 | 处置 |
|---|---|---|---|
| NEW-P0-A | CRITICAL | `decide_day` = rotation-only(≤1 轮入/日,从不整簿再配),conf60 常驻目标权重簿**无法加法表达** → 「复用非重建」失真 + byte-exact 锚不可达(=B1/B2 同结构墙,现于 sizing 侧)| ✅ 修:§2.4 重写 = 呈读法 A(可锚非 P-E)/B(P-E 但重建放弃锚)二选一 BT-0 裁定 + 删不可达锚 + 诚实推论(倾向冻结引擎原生容器 + live 侧 P-E);BT-2 gate 随读法分叉;§7 Q1 推荐下调 |
| NEW-P1-B | HIGH | `check_invariants` single-stock-cap 会绊 conf60(顶名 36-60%→DIVERGENT);提 cap 则 cap 不变量失守 | ✅ 修:BT-2 显式设定并披露 `single_stock_cap_percent`,声明 cap 不变量让位 sizing 层(对齐 live P-E amendment)|
| NEW-P2-C | MEDIUM | §6 sizing 先验「中-高、最可能兑现」与 DSR≥0.95 近不可达门自相矛盾 | ✅ 修:§6 先验拆分——现金 buffer(容器层,属 DS 线非 harness 贡献)中-高;conf60(harness 唯一新贡献)低 P(success),owner 批 BT-2 前须知大概率 FAIL |

**R2 总评**:结构墙(rotation-only 无法表达常驻目标权重簿)是本计划书最深的真相,已如实纳入;它把「sizing 主载重」诚实收窄为「现金 buffer(容器层,DS 线已有)承重 + conf60(harness 新贡献)高成本低 P(success) 增量」。**BT-2 开工前须 owner 在读法 A/B 显式裁决,且 Q1 推荐已随之下调。此即 codex 2 轮收敛(R1→R2 CLOSED,结构墙已暴露并诚实处置)。** 真实施每编码任务仍须独立过 codex commit 前置门。
