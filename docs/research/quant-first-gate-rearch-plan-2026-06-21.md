# 量化第一闸门 · 重做主文档(Quant First-Gate Re-research, QGR)— 单一自足接手锚点

> **状态**:codex 评审过(REVISE→定稿) · **owner「开」✅(2026-06-21)** · amendment `docs/decisions/quant-gate-rebar-amendment-2026-06-21.md` 已落 · owner 主旋律/底部确认精化已并入(§3.8) · **当前进度 = 基础全锁定,进 QGR-1** · **日期**:2026-06-21 · **作者**:Claude(Opus 4.8)
> **这是什么**:量化选股研究专项的**框架性重做的唯一权威主文档**。它**取代**前四轮(round-1..4 + R5)以「组合 vs CSI300 超额」为判据、滑向增强指数的研究路线(那套**框定已判错**,仅作历史诚实记录)。**新 session 读本文档 + `CLAUDE.md` + `MEMORY.md` 即可零拼凑接手**;本文档已内联 amendment 关键条款 + codex 处置 + 现有基建盘点 + 各阶段执行规格,无需再翻 amendment/summary/memory 才能动手。
> **本地 commit(push 待 owner 授权)**:`ae4bac2`(方案+codex summary+旧内容清理)· `3ef57d3`(amendment+精化+CLAUDE.md)。
> **provenance**:信号设计 + 评测学结论来自 2026-06-21 三路 provenance-gated 调查(系统真实角色 file:line 级 / A 股短线信号文献 / 非数据消耗型评测学),出处见 §10。

---

## 0. owner 决策(2026-06-21,本文档据此)

1. **判据 = 绝对净盈 + 控回撤**:判 ≤5 选股篮子**本身**的扣成本净盈+回撤,跨 CPCV 路径/regime **稳定为正**;CSI300 超额仅补充披露、**不当硬门**(推翻前四轮「超额≥0」硬门——那是把量化推向增强指数、偏离选股器角色的根源)。
2. **horizon = 两条腿并进**:先做 **5-10td 选股闸门**(贴系统最短持仓 5td + 轮动)上线;**同时**用同一竞技场**公平比 5-10td vs 真·超短(T+1/1-2td)机制**,证据够再动 live 持仓机制。
3. **sim 暂停**,直到 B 层前向确认产出可部署、过门的闸门;**不赶 interim、不明天启动**。
4. **清理旧/错误内容**(已执行:本文档 + MEMORY.md banner + 旧 project 文件 SUPERSEDED 标 + CLAUDE.md 状态/原则0 + plan.html SESSION_LOG)。
5. **2026-06-21 精化(§3.8)**:「避热门」=紧跟国家主旋律择"场"(AI/机器人/AIDC/玻璃基板/AI 链材料,**避夕阳**)+ 不追涨已高位名,**非避战略赛道**;「买跌票」=主题内"高价值尚在低位优质股"+ **客观底部确认门**(治"跌了再跌的洗盘"/接飞刀,不凭感觉)。

---

## 1. 诊断:前四轮错在框定(不是因子差)

| 维度 | 前四轮做法 | 为什么错 |
|------|-----------|---------|
| **框定(根因)** | 把量化当「要跑赢 CSI300 的整体组合策略」优化 → round-2/3/4 滑向 benchmark-relative 增强指数(~300 名加权 + box-tilt) | 系统里量化是面对 ~5000 票的**第一道选股闸门**,不是组合。优化了系统跑不了的东西(round-4 增强指数 ≤5 持仓 top-N 装不下),还用错判据(cap 加权指数超额)评它。 |
| **判据** | 四门含「超额 vs CSI300 ≥0」硬门 | ≤5 名 long-only 短线选股篮**结构上**无法稳定跟踪 300 名 cap 加权指数。逼着判据走 → 只能去做增强指数。 |
| **horizon** | round-2/3/4 月级/5d 再平衡 + 组合构造 | 系统是每日选股、最短持仓 5td + 轮动。研究 horizon 与系统执行脱节。 |
| **数据** | 只用 daily/daily_basic/fina/report_rc | 8000 积分解锁的短线数据(资金流/涨跌停/筹码/事件…)基本没用上(虽部分是陷阱,§3.6)。 |
| **评测可复用性** | 每轮把锁定 test 一次性烧掉,累计已评 4 次 | 无可复用、可公平比多策略的竞技场;test 越用越稀缺。 |

**R5 实证**:round-4 的 +2.68% 超额一大半来自「增强指数构造本身」(long-only floor + 残留 size tilt),真因子边际很薄。**能映射到 top-N 的(因子排名)恰是被证薄的部分;产生超额的(构造)恰是 top-N 装不下的部分。**

---

## 2. 纠正框架:量化 = 第一道选股精度闸门

### 2.1 系统真实角色(file:line 级核实)
- **节奏**:每日 09:35 在 T-1 EOD 帧选股一次(`config/universe_policy.yaml` slow bucket)。
- **流水线**:`Screener`(全市场→top-100 排序,`backend/screening/screener.py`)→ `BudgetTierPolicy` 可负担过滤(`backend/budget_policy/policy.py`)→ `CandidateSelector`(→最终 ≤5,保 ≥3 纯量化槽,MiroFish 仅 ±1 百分位有界重排,`backend/candidate_selector/selector.py`)→ 每候选 4 agent 辩论(`backend/orchestration/line1_runner.py:~486`)→ 单一构造点 `assemble_plan` → RiskEngine 14-check → SimulationExecutor 自动撮合。
- **持仓**:≤5 槽(`config/risk.yaml:max_total_positions=5`),最短持仓 **5td**(`config/slot_rotation_policy.yaml`),到期/弱势轮动(7 条弱势门 + 挑战者 margin,`backend/slot_portfolio/`)。
- **宇宙**:沪深主板+创业板+宽基 ETF(`backend/services/universe_policy.py`);永禁 科创688/北交8/ST/可转债;排除四件套(新股≤30td/次新≤180td/20d 额<¥2亿/单价>¥500)。
- **预算分层**:Micro<¥2k 仅 ETF / Small ¥2k-10k / Normal≥¥10k(单≤15%/总≤70%/单笔≤¥50k)。

### 2.2 闸门要优化的目标
> **从全市场 ~5000 票里,每日选出预算覆盖内、未来 ~5-10td(两条腿:亦评 T+1/1-2td)大概率上涨的 ≤5 名优质票(含 ETF)交 agent 二筛 —— 这件事的精度。**
- **评判对象** = ≤5 篮子(经真系统机制:≤5槽/5td 持有/轮动/T+1/分板块滑点/涨停不可成交)的**扣成本绝对净盈+回撤**,跨 CPCV/regime 稳定为正。
- **不是** 组合 vs 指数超额。**精度优先于覆盖**(宁可 2 个高把握,不要 5 个边际)。
- **须稳定击败可部署 baseline 面板(防 long-beta 假象,codex P1)**:随机合格 top-5 / 现役 screener(momentum-0.40)/ 纯流动性筛 / ETF-only(510300)/ CSI300-ETF 买入持有 —— 否则牛市高 beta 篮子"看着盈利"却无技艺。

---

## 3. 信号设计(A 股短线,文献驱动 provenance-gated;符号 R 阶段从零验)

### 3.1 核心「上涨」信号(robust)
1. **短期反转(1-5d),流动性+涨跌停过滤**(`daily`/`adj_factor`/`daily_basic`)——A 股最强截面效应(Carpenter-Lu-Whitelaw RFS 2021)。long-only = 在健康/流动/非跌停/非困境名中选超跌反弹。**陷阱**:loser leg 富集跌停/坏消息/停牌 → 必须过滤;用 VWAP/次开成交。
2. **1 日日内动量(新投资者驱动)**(`daily`/`daily_basic`/`stk_limit`)——Gao-Jiang-Xiong-Xiong NBER 2023:A 股**无周/月动量,有显著日动量**,买"强但非涨停"名,T+1 退,第 2-3 日反转。**1 日衰减,与系统 5td 最短持仓不匹配 → 快腿,两条腿对比核心待验项。** 涨停名不可成交 → 只用"近涨停未封板"切片。

### 3.2 强制负向 overlay(robust,**剔除**用)
3. **彩票/注意力剔除(MAX/IVOL/异常高换手)**(`daily_basic`/`daily`/`stk_factor_pro`)——Leippold-Wang-Zhou JFE 2022 / Nartea-Wu PBFJ 2018:高 MAX/IVOL/换手在 A 股稳健跑输(retail 过度定价)。**反转之后最稳健的截面结果,但告诉你「别买什么」。** 涨跌停截断真实 MAX → 计算校正。

### 3.3 特征(入排名不直接当 BUY 触发)
4. **涨停结构**(`limit_list_d`/`stk_limit`/`suspend_d`)——早封/一字/连板=动量 tag;破板/高开=fade tag。**日频不可成交 → 只作特征/过滤。** ⚠️ **同日 `limit_list_d` 盘后才齐 → 当日决策用=前视;只用 `<d`。**

### 3.4 慢速 tilt(robust 但慢,月级)
5. **分析师修正**(`report_rc`,R4 已验真 alpha)+ **业绩预告/快报事件**(`forecast_vip`/`express_vip`,PEAD)——正向但 horizon 数周-数月 → 慢速质量 tilt/事件 flag。`report_rc.tp`=利润总额非目标价(目标价=`min_price`);PIT 闸 `report_date<d` 非 `create_time`;评级 95% 买入→用 within-stock 修正非 level。

### 3.5 风险/regime overlay(仅风控,**绝不**当 BUY 触发)
6. **筹码分布**(`cyq_chips`,Grinblatt-Han JFE 2005)——模型派生(非纯 PIT,谨慎,字节存档存疑)。7. **聚合北向+融资融券**(`moneyflow_hsgt`/`hsgt_top10`/`margin`)——froth/fire-sale 风险 flag。

### 3.6 ⛔ 陷阱清单(花 8000 积分也别这样用)
| 陷阱 | 真相 |
|------|------|
| 日频 `hk_hold` 北向 | **2024-08-19 改季度披露=日频 PIT 已死**(#1 陷阱);仅季度持股可做慢因子。 |
| `moneyflow`「主力净流入」 | 无稳健日频预测力(撮合拆单破坏「大单=聪明钱」);多半 fit 当日已发生价格。 |
| 龙虎榜 `top_list` 买入信号 | 无干净 OOS 证据,**收盘后才出=反应式滞后**,游资高反转。 |
| 高换手/MAX/高量当利好 | 已证 A 股**跑输**(§3.2)。 |
| 融资余额上升当利好 | 反向/情绪 flag(更差远期+fire-sale 下行),符号反。 |
| 隔夜跳空当动量 | A 股是**反转**(T+1 逆选择)。 |
| 同日 `limit_list_d` 当特征 | 当日盘后才齐=前视;只用 `<d`。 |
| 复权因子泄漏 | 比值固定 asof 复权抵消;严禁未来复权因子混入 PIT 特征。 |
| 停复牌名 | `suspend_d` 不可成交、复牌跳空 → 排除/特殊处理。 |
| 低价/壳彩票暴露 | 价格下限+底部流动性剔除,别让反转 loser leg 滑进壳。 |

### 3.7 候选信号 → Tushare 8000 端点映射
`daily`+`adj_factor`(反转/动量/MAX/IVOL)· `daily_basic`(换手/量比/市值/PE→EP)· `stk_limit`+`limit_list_d`+`suspend_d`(涨跌停/停牌)· `stk_factor_pro`(技术因子)· `cyq_chips`(筹码)· `report_rc`+`forecast_vip`+`express_vip`(分析师/事件)· `moneyflow_hsgt`/`hsgt_top10`/`margin`(风险 overlay)。**`moneyflow`/`top_list`/日频`hk_hold` 仅"可测但默认陷阱"对照,不进核心。**

### 3.8 owner 2026-06-21 精化:紧跟主旋律 + 客观底部确认(amendment `quant-gate-rebar` §2.3 强制)
owner 对「买跌票/避热门」给出客观化方向,**重塑信号架构**:

**(A)「避热门」≠ 避战略赛道,而是 ① 紧跟主旋律择"场" + ② 不追涨已高位名。**
- **主旋律 tilt(新维度,PIT 客观)**:tilt 向国家战略主线(AI/机器人/AIDC/玻璃基板/AI 产业链必要原材料 等),**避夕阳产业**。**PIT-clean 实现**:① 概念/行业成分 PIT(`ths_index` as-of-date 成分 + `index_classify`/`index_member_all` 申万行业);② **战略主题映射=预注册+政策发布日溯源(防 hindsight)**——每主题挂 `effective_from`=宣示它的政策文件(五年规划/政府工作报告/行业政策)发布日,tilt **只从该日起生效**(**严禁**用"现在知道 AI 赢了"从 2015 就 tilt);映射冻结进 git 作披露假设;③ 夕阳=行业级营收/盈利长期下行+政策不利的客观代理。
- **「不追涨」= 高位/过度延展剔除**(=§3.2 overlay,**作用于主题内**)。

**(B)「买跌票」= 主题内"高价值尚在低位优质股" + 客观底部确认(不接飞刀)。**
- **尚在低位+高价值**:主题内 quality(roe/gpm/E-P)高 + 自身仍在低位(52周低分位/筹码成本下方/近期回调)。
- **客观底部确认门(新,替代朴素反转,治"跌了再跌的洗盘")**:多指标综合判健康筑底 vs 洗盘——① 缩量 ② 站稳筹码成本带上方(`cyq_chips`)③ 资金流企稳 ④ 无新技术破位 ⑤ 无困境(非 ST/无停牌/无退市审计风险)⑥ 基本面质量地板。**符号/阈值 R 阶段从零验。**

**(C)与 live LLM 主题层关系**:live Phase Y `backend/theme_research/`(LLM peer-sourcing)+知识图谱产业链=互补 live 定性层;**研究闸门用客观 PIT 主题信号,LLM 永不进 PIT/评测路径。**

**(D)两条腿归属**:主旋律+value-at-low+质量+底部确认=**慢"持仓"腿**(随轮动持有数周-数月跟涨复苏);反转+1日动量=**快腿**;同竞技场公平比。

**(E)诚实 caveat(强制)**:战略主题"哪个主题战略"有 hindsight 前视+主观映射风险(回测最易自欺一类)→ 政策发布日 PIT + 预注册冻结 + **必须在 baseline 面板证明主题维度 OVER 非主题 baseline 有增量**;QGR-3 走专门 codex 门审 PIT-soundness。

---

## 4. 评测框架(核心):可复用、非数据消耗、可公平对比 + 诚实边界

> owner 第 4 要求的正解。**结论(数学已证边界)**:"非数据消耗"能做到**可复用的开发/对比层**,但**最终上线判定只能靠新数据**(Dwork 2015 / Hardt-Ullman 有硬上界)。分两层。

### 4.1 A 层 = 可复用开发/对比竞技场(可反复评、可公平比多策略)
| 组件 | 做法 | 复用/新建 |
|------|------|----------|
| 共享冻结 PIT 数据集 | 8000 短线+主旋律数据(§5),survivorship-free,所有策略同一份 | 扩 `ingest_round2_data.py` |
| **真 CPCV** | N 组、k 测、路径 φ=(k/N)·C(N,k);purging+embargo(≥标签 horizon)→ OOS 路径分布 | **复用但须修** `walk_forward_eval.py`:现报 held-out combinations 非 stitched paths(codex P1)→ QGR-2 实现真路径拼接;**重叠路径绝不当独立样本喂 DSR** |
| **选股质量度量(主指标已冻)** | **主=事件循环净 P&L/效用+MDD+换手约束**;precision@K/rank-IC 仅诊断。真系统机制回测经 `backend/backtest/` 事件循环接"闸门选股→≤5槽轮动"策略 | **新建**;**弃用** `benchmark_relative`/`benchmark_weights`/`exposure_constraints`/`long_short`(增强指数那套) |
| 多策略公平对比 | Hansen SPA(最优是否真,robust to junk)+ Romano-Wolf StepM(哪些真,强 FWER)/BH-FDR(海量) | `stats_disclosure.py` 有 SPA;**补 Romano-Wolf + BH/BY** |
| 诚实门(protocol 收紧) | PBO=**针对真实选择规则**的诊断(非盲目 p<0.05)+ DSR 用**ONC 有效 N** 且 SR 方差**HAC 自相关校正**(重叠持仓)+ MinBTL;SPA/Romano-Wolf 须**预声明 family + 时序 block bootstrap** | `disclosure_stats.py`/`anti_overfit.py` 有 DSR/PBO;**补 ONC + HAC + Romano-Wolf + block bootstrap** |
| **累计 trial 账本(关键)** | append-only 记所有策略所有轮在共享数据评过的配置;**累计有效 N** 喂 DSR 的 SR0 + MinBTL → 显著性随研究 deflate。**legacy 债不清零(codex P0)**:账本**预置 legacy 块**(R1-R4 名义网格+诊断+消融+符号检验+4 次 test 读),DSR/MinBTL 用 `max(legacy_N, 新 ONC 有效 N)`,绝不从零 | **新建**(借 `experiment_registry.py`)+ 预置 legacy 块 |
| 铁律 | 诚实门**永不**指导搜索(Goodhart);窗口/超参 committed 在前 | 协议固化 |

> **A 层买到什么**:**有限、记账、对自适应复用计惩罚**的对比竞技场(**非**无限免费,codex P1)。**买不到**:干净上线判定。A 层"过门"但低 DSR/账本将尽 = **provisional**。

### 4.2 B 层 = 稀缺前向确认(上线 gate)
| 组件 | 做法 | 复用/新建 |
|------|------|----------|
| 预注册+冻结 | 选定闸门策略字节冻结进 git + 写死成功判据,在前向数据存在之前 | 仿 round-4 freeze |
| 持续累积前向处子窗口 | 仅在冻结后新增真数据评(post-freeze);**期定义须预声明**:verdict 用**非重叠完整 5td 持仓 bet** 为独立观测 + 最小有效观测 + interim alpha-spending;ACCRUING 绝不在噪声出 verdict | 复用 `round4_forward_test.py`(Layer-B 雏形) |
| 多次窥视纪律 | Thresholdout/Ladder:只在显著偏离开发预期时报变化,每次记入固定预算 | 新建(轻量) |
| go-live gate | 前向 PASS + 真成本/容量/regime sanity + **owner gate + LiveArtifactRegistry + 45 日真管线 shadow replay + 人工 pin + 重启** | 复用 `live_artifact_registry.py`/`forward_shadow_mandate.py` |

### 4.3 与旧"锁定 test 一次性"的关系
旧 `config/research/test_set_lock.json`(2025-06-04..2026-06-12,"touch once")**一次性语义被取代**:该窗口现在只是 CPCV 全history 池(2015..2026-06-12)的一部分(开发用,诚实靠 deflation);**唯一稀缺确认资源 = post-2026-06-12 前向窗口**。比"逐轮烧 test"干净,天然支持"可反复评+公平对比"。

### 4.4 量化 proxy ≠ 全系统验证(codex P1)
`backend/backtest` 事件循环回测**不含** LLM 辩论 / 全 RiskEngine / Line-2 盘中风控(`backend/backtest/strategy.py`)→ 它是**量化机制 proxy**;go-live 仍须**真管线 shadow replay**(45 日)。

---

## 5. 数据计划(8000 积分,QGR-1 摄取)

- **摄取(owner-gated,离线,字节存档+checksum+coverage,仅 Tushare 官方 SDK,IPv4-only 出站)**:核心短线 `daily`/`daily_basic`/`adj_factor`(已有)+ **新增** `stk_limit`/`limit_list_d`/`suspend_d`/`stk_factor_pro`/`cyq_chips`/`forecast_vip`/`express_vip` + 已有 `report_rc`;**风险 overlay** `moneyflow_hsgt`/`hsgt_top10`/`margin`/`margin_detail`。
- **主旋律数据(新,§3.8)**:`ths_index`(同花顺概念 PIT 成分)+ `index_classify`/`index_member_all`(申万行业 PIT,已有部分)+ **预注册「政策→主题」映射**(政策发布日溯源,git 冻结,人工 provenance-gated,**非 Tushare**;QGR-3 冻结)。
- **陷阱标注(写进 coverage manifest)**:日频 `hk_hold` 2024-08-19 后失效;`moneyflow`/`top_list` 列"可测对照非核心"。
- **分页铁律**:`*_vip` 及多行/票端点**必 limit+offset 分页**([[reference-tushare-statement-vip-row-cap]]);稀疏流(report_rc/事件)按 round-4 月范围分页;`cyq_chips` 单股逐日(注意调用量)。
- **PIT 红线全留**:幸存无偏宇宙 / `report_date<d` / 复权 pin / 离线 replay / fail-closed coverage。

---

## 6. 现有基建盘点(reuse / extend / build-new / deprecate)

> 重做**不是从零**——下面是 file 级 reuse 映射。新 session 据此动手,别重造已有件。

### 6.1 `backend/backtest/`(确定性事件循环引擎)= **REUSE**(QGR-2 量化机制回测的底座)
`event_loop.py`(单调 clock 前视即抛 + 涨跌停门)/`friction.py`(Lean 单工厂整数分滑点)/`portfolio.py`(MTM)/`invariants.py`(守恒+cap)/`harness.py`(→BacktestResult)/`strategy.py`(策略接口)/`golden_replay.py`+`golden_vector.py`/`pit_export.py`/`rqalpha_protocol.py`+`rqalpha_entry`(rqalpha 子进程 oracle,可选交叉验证)。

### 6.2 `backend/strategy_evolution/`(评测/晋升基建)= **REUSE 概念 + EXTEND**
`disclosure_stats.py`(DSR/PBO/SPA + MinBTL veto)→ **补 Romano-Wolf + ONC 有效N + HAC**;`anti_overfit.py`(purged-CV/DSR)→ 复用;`forward_shadow_mandate.py`(前向 shadow PENDING 永不 auto-promote)→ Layer-B;`live_artifact_registry.py`(go-live 批准闸,5 类 SHA256 deny-all bootstrap)→ go-live;`sentinel.py`(零-edge 对照)→ 复用;`experiment_registry.py`(实验登记)→ 累计 trial 账本借鉴;`quant_param_search.py`(Sobol 固定 N)→ 搜索复用;`mechanism_registry.py`(经济机制 enum,**governance 不动**)。

### 6.3 `scripts/factor_research/`(研究侧)
- **REUSE/EXTEND**:`ingest_round2_data.py`(摄取编排,扩新端点)/`tushare_client.py`(加 `ths_index`/`stk_limit`/`limit_list_d`/`suspend_d`/`cyq_chips`/`stk_factor_pro`/`forecast_vip`/`express_vip` Protocol)/`build_factor_panel.py`(扩短线+主题因子+前向窗)/`factor_lib.py`(扩短线因子 registry)/`factor_ic_study.py`(IC 诊断)/`neutralize.py`/`fundamentals_pit.py`/`industry_pit.py`/`statements_pit.py`/`namechange_pit.py`/`analyst_revision_pit.py`(PIT builders)/`locked_split.py`(split,reframe 成 CPCV 池+前向窗)/`walk_forward_eval.py`(CPCV,**修真路径**)/`round2_search.py`(搜索 scaffolding,**re-target 到选股质量度量+top-N 选择**)/`portfolio_backtest.py`(简单 top-N 篮回测,辅)/`round4_forward_test.py`(Layer-B 前向 runner,**re-target**)/`stats_disclosure.py`。
- **BUILD-NEW**:① 系统机制回测适配器(接 `backend/backtest` 事件循环 → "闸门选股→≤5槽轮动"策略 → 绝对净 P&L+MDD)② 真 CPCV 路径拼接 ③ 多策略 SPA/Romano-Wolf 公平对比 harness ④ ONC 有效N + HAC SR 方差 ⑤ 累计 trial 账本(含 legacy 块)⑥ baseline 面板(随机top5/现役screener/流动性/ETF/CSI300-ETF)⑦ 短线因子库(反转/1d动量/MAX-IVOL-换手剔除/涨停结构/**底部确认指标**)⑧ 主旋律维度(`ths_index`/申万 PIT 成分 + 政策→主题映射 + 夕阳代理 + 主题内 value-at-low)⑨ Layer-B 预注册+前向 runner。
- **DEPRECATE(增强指数那套,框定已废;保留历史不删)**:`benchmark_relative.py`/`benchmark_weights.py`/`exposure_constraints.py`/`long_short.py` + 一次性 locked-test runners `round{2,3,4}_locked_test.py`/`phase4_locked_test.py`(被两层评测取代)。

---

## 7. 分阶段行动计划(QGR-0..6;每阶段 owner/codex gate;sim 暂停贯穿)

> 每阶段编码任务 commit 前走 codex 代码门(撞额度→`/code-review high`,[[feedback_codex_rate_limit_fallback]]);docs-only 豁免。重 ingest/重启 owner-gated。

| Phase | 内容 | 关键交付/目标文件 | 命令 / 预期(可知处) | gate |
|-------|------|------------------|---------------------|------|
| **QGR-0** ✅ | 方案+codex 评审+amendment+清理 | 本文档 + `quant-gate-rebar` amendment + codex summary | — | **owner「开」✅** |
| **QGR-1** ⏳ | 8000 短线+主旋律数据 PIT 摄取 | 扩 `ingest_round2_data.py` `--phase qgr` + `tushare_client` 加端点;coverage manifest + 陷阱标注 | `$PY -m scripts.factor_research.ingest_round2_data --phase qgr --dry-run`(先验快照数/调用数)→ owner「开」真摄取(exit 0,字节+checksum,gitignored `data/marketdata_pit`) | **coverage-only**(冻评测口径前不看因子结果);门禁绿+codex;重摄 owner-gated |
| **QGR-2** | 评测竞技场(A+B 骨架) | build-new ①-⑥ + ⑨ 骨架;**真 CPCV 路径** + 冻结主指标 + baseline 面板 + 累计账本(legacy 块) | 单元/集成测试;CPCV 路径数自检(N=6,k=2→5 路径) | 门禁绿 + codex P0/P1/P2 修净;**先冻评测口径再进 QGR-3** |
| **QGR-3** | 短线 + 主旋律因子库 | build-new ⑦⑧;CPCV IC 从零验符号;陷阱信号"对照不入核心";**政策→主题映射预注册冻结** | IC 诊断 doc;**带"建议主题清单+政策发布日依据"给 owner 确认再冻** | 门禁绿 + **专门 codex 门审主题 PIT-soundness** + owner 确认主题映射 |
| **QGR-4** | 策略搜索/选择(可复用)+ 两条腿对比 | re-target `round2_search`;在竞技场搜候选闸门(信号组合+≤5槽规则)→ SPA/Romano-Wolf 公平排名 → PBO/DSR 门;**5-10td vs 真超短 同竞技场公平比** | 搜索 result + 对比 doc + provisional 披露 | owner 看披露拍板进 QGR-5 |
| **QGR-5** | 冻结 + 预注册 | 选定候选字节冻结 git + 预注册 doc + 启动前向时钟 | freeze commit | owner「开」冻结 |
| **QGR-6** | 前向确认 → go-live | 前向累积(数月,非重叠 5td bet,≥够期+alpha-spending)→ 过则 go-live(**真管线 shadow replay 45 日**+owner+pin+重启)→ 届时才议恢复 sim/上线 | `$PY -m scripts.factor_research.<forward_runner>`;ACCRUING 不出 verdict | owner go-live |

**两条腿(决策 2)**:QGR-4 用同一竞技场公平比"5-10td 选股闸门"(贴现状机制,先上)与"真超短机制"(需配套改 ≤5槽/5td最短持仓/轮动=独立 amendment+重测);证据够 owner 再决定动 live 机制。

---

## 8. 红线 / 边界(全留)
永禁真实下单(只 MockBroker/SimulationExecutor)· 离线研究 never touch live path · 仅 Tushare 官方 SDK · PIT 可复现(字节+checksum+coverage)· LLM 只用于文献、**永不进 PIT/评测/运行时数据路径** · governance `EconomicMechanism` enum 不动(新机制 fail-closed until amendment)· 改决策边界(真改 live screener/持仓机制)**必 amendment 先行 + codex 前置门 + 重启** · 127.0.0.1 · 飞书人工 · RiskEngine 纯函数 · 单一构造点 · 四诚实保障(冻结-then-read 防火墙 / 累计-N deflation / 显式披露 / 判据不放宽)。**live `screener.py` 当前一行未改(sim 暂停);真 rebar 由 QGR-6 go-live 单独审批。**

---

## 9. 开放问题(执行期 + owner)
1. **两条腿真超短净成本**:T+1/1-2td 翻的高换手/成本是否被超短 alpha 覆盖,QGR-4 净成本回测见真章(扣成本后会不会 edge 消失)。
2. **战略主题 hindsight 前视风险(§3.8E)**:"哪个主题战略"最易自欺 → 政策发布日 PIT + 预注册冻结 + baseline 上证明主题维度有增量;**QGR-3 专门 codex 门**。(「彩票剔除/确定上涨张力」已由 §3.8 主旋律择场+客观底部确认门解决。)
3. **选股质量主指标细化**:事件循环净 P&L 为主已定;precision@K/rank-IC 诊断权重 QGR-2 冻结时定。
4. **ETF 分轨**:ETF 不混入个股截面排名 → 单独 lane(beta/趋势)或仅预算 fallback(QGR-3 定)。

---

## 10. 关键出处(provenance-gated)
**A 股短线信号**:Carpenter-Lu-Whitelaw, *RFS* 2021;Gao-Jiang-Xiong-Xiong, NBER 31839 / *JF* forthcoming;Leippold-Wang-Zhou, *JFE* 2022;Hou-Qiao-Zhang "Finding Anomalies in China";Nartea/Wu, *PBFJ* 2018;"MAX is not the max under daily price limits," *PBFJ* 2021;"Price overreaction to up-limit events," *Economic Modelling* 2022;Grinblatt-Han, *JFE* 2005;**HKEX/SSE/SZSE 2024-04-12 公告(2024-08-19:北向实时流停发+逐股持股改季度)**;"overnight return puzzle and T+1," *JBF* 2020;Lin et al. margin, *Accounting & Finance* 2025;Jung-Keeley-Ronen analyst revisions 2019。
**评测方法学**:López de Prado *AFML* 2018(CPCV/purging/embargo);Bailey-Borwein-LdP-Zhu "PBO," *J. Comp. Finance* 2017;Bailey-LdP "DSR," *JPM* 2014;LdP-Lewis effective-N/ONC, *Quant. Finance* 2019;White *Econometrica* 2000;Hansen SPA *JBES* 2005;Romano-Wolf *Econometrica* 2005;Benjamini-Hochberg/Yekutieli;Harvey-Liu-Zhu *RFS* 2016;Dwork et al. "reusable holdout," *Science* 2015;Hardt-Ullman FOCS 2014;Blum-Hardt "Ladder" ICML 2015;Nosek et al. pre-registration *PNAS* 2018。
> CPCV 路径数 φ=(k/N)·C(N,k)(**非** C(N,k));N=6,k=2→15 组合/5 路径。effective-N 用 ONC;kurtosis 用 full(非 excess)。

---

## 11. 新 session 接手协议
- **本文档 = 唯一权威接手锚点**(已自足:含 amendment 关键条款+codex 处置+基建盘点+各阶段规格)。配套 `CLAUDE.md`(§2 红线+原则0)+ `MEMORY.md`(顶部 QGR banner+[[project-quant-first-gate-rearch-2026-06-21]])。
- **当前状态**:基础全锁定(amendment+清理 committed `ae4bac2`/`3ef57d3`,push 待 owner);**下一步 = QGR-1**(§7)。
- **旧路线全废**:round-1..4/R5 逐轮 + 「明天启动 sim」+ 「前向 accrual 续跑」+ 「既有 test 第 5 次评测」均仅历史。**sim 暂停**直到 QGR-6 go-live。
- **每阶段**:TDD(非 risk>70%/risk>95%)+ 本地门禁全绿(pytest+ruff+mypy strict+`scripts/redline-check.sh`)+ commit 前 codex 代码门 + 一任务一 feature commit + 回填 SSoT;docs-only 豁免 codex。push 待 owner 授权。
