# 量化第一闸门 · 重做方案与行动计划(Quant First-Gate Re-research, QGR)

> **状态**:codex 评审过(REVISE→定稿 §4.4) · **owner「开」✅(2026-06-21)** · amendment `docs/decisions/quant-gate-rebar-amendment-2026-06-21.md` 已落 · owner 主旋律/底部确认精化已并入(§3.8) · 进 QGR-1 · **日期**:2026-06-21 · **作者**:Claude(Opus 4.8)
> **性质**:这是对「量化选股策略研究专项」的**框架性重做**。它**取代**前四轮(round-1..4 + R5)以「组合 vs CSI300 超额」为判据、逐步滑向 benchmark-relative 增强指数的研究路线。前四轮的历史记录仍**作为诚实证据保留**,但其**问题框定(framing)已被本方案判定为错**,不再作为未来 session 的行动指针。
> **provenance**:本方案的信号设计与评测学结论来自 2026-06-21 三路 provenance-gated 调查(系统真实角色 file:line 级核实 / A 股短线信号文献 / 非消耗型评测方法学),出处见 §8。

---

## 0. owner 锁定的四个决策(2026-06-21,本方案据此构建)

1. **盈利判据 = 绝对净盈 + 控回撤**:判 ≤5 选股篮子**本身**的扣成本净盈利 + 回撤,要求**跨 CPCV 路径 / regime 稳定为正**;CSI300 超额仅作**补充披露**,**不当硬门**。(推翻前四轮「超额≥0」硬门——那正是把量化推向增强指数、偏离选股器角色的根源。)
2. **horizon = 两条腿并进**:先做 **5-10td 选股闸门**(贴系统当前最短持仓 5td + 轮动机制)上线;**同时**在研究侧用同一套评测竞技场**公平对比 5-10td vs 真·超短(T+1/1-2td)机制**,证据够再决定是否改 live 持仓机制。
3. **sim 暂停**,直到 B 层前向确认产出一个可部署、前向过门的闸门策略——**不赶 interim**。
4. **清理旧/错误内容**:确保未来 session 接手时不被旧设定(round-4 provisional PASS / 增强指数 / 「明天启动 sim」/「既有 test 第 N 次评测」)误导。本方案 = 新的权威接手锚点。
5. **2026-06-21 精化(amendment `quant-gate-rebar` §2.3)**:「避热门」=紧跟国家主旋律择"场"(AI/机器人/AIDC/玻璃基板/AI 链材料,**避夕阳**)+ 不追涨已高位名,**非避战略赛道**;「买跌票」=主题内"高价值尚在低位优质股"+ **客观底部确认门**(治"跌了再跌的洗盘"/接飞刀,不凭感觉)。详 §3.8。

---

## 1. 诊断:前四轮到底错在哪(不是因子选得差,是问题框定错)

| 维度 | 前四轮做法 | 为什么错 |
|------|-----------|---------|
| **框定(根因)** | 把量化当成「要跑赢 CSI300 的整体组合策略」优化 → round-2/3/4 一路滑向 benchmark-relative 增强指数(~300 名加权 + box-tilt) | 系统里量化**根本不是组合策略**,是面对 ~5000 票的**第一道选股闸门**。优化了一个系统跑不了的东西(round-4 增强指数与 ≤5 持仓 top-N agent 系统是两个物种),还用错判据(cap 加权指数超额)评它。 |
| **判据** | 四门含「超额 vs CSI300 ≥0」硬门 | ≤5 名 long-only 短线选股篮**结构上**无法稳定跟踪 300 名 cap 加权指数(round-1 takeaway 已点明 benchmark-naive)。逼着判据走,就只能去做增强指数。 |
| **horizon** | round-2/3/4 用月级/5d 再平衡 + 组合构造 | 系统是**每日选股、最短持仓 5td + 轮动**。研究 horizon 与系统执行 horizon 脱节。 |
| **数据使用** | 只用 daily/daily_basic/fina/report_rc | 8000 积分解锁的**短线数据**(moneyflow/limit_list_d/top_list/cyq_chips/margin/forecast/express…)基本没用上(虽然调查证明其中数个是陷阱,见 §3)。珍贵历史没被用来回答「短线选股闸门」这个真问题。 |
| **评测可复用性** | 每轮把锁定 test(2025-06-04..2026-06-12)**一次性烧掉**,累计已评 4 次 | 没有可复用、可公平横向对比多策略的竞技场;test 集越用越稀缺,反而逼出「慎第 5 次」的死结。 |

**一句话**:round-4 的 +2.68% 超额,R5 体检已证一大半来自「增强指数构造本身(long-only floor + 残留 size tilt)」,真因子边际很薄;而**能映射到 top-N 系统的那部分(因子排名),恰恰是被证明薄、哨兵不过、低 DSR 的部分;产生超额的构造,恰恰是 top-N 装不下的部分**。这就是「框定错 → 珍贵数据答了错问题」的本质。

---

## 2. 纠正后的框架:量化 = 第一道选股精度闸门

### 2.1 闸门在系统中的真实角色(file:line 级核实)
- **节奏**:每日 09:35 在 T-1 EOD 帧上选股一次(`config/universe_policy.yaml` slow bucket)。
- **流水线**:`Screener`(全市场 → top-100 排序,`screener.py`)→ `BudgetTierPolicy` 可负担过滤(`budget_policy/policy.py`)→ `CandidateSelector`(→ 最终 ≤5,保 ≥3 纯量化槽,MiroFish 仅 ±1 百分位有界重排,`candidate_selector/selector.py`)→ 每候选 4 agent 辩论 → 单一构造点 `assemble_plan` → RiskEngine 14-check → 自动撮合。
- **持仓**:≤5 槽(`risk.yaml:max_total_positions=5`),最短持仓 **5td**(`slot_rotation_policy.yaml`),到期/弱势轮动(7 条弱势门 + 挑战者 margin)。
- **宇宙**:沪深主板 + 创业板 + 宽基 ETF;永禁 科创688/北交8/ST/可转债;排除四件套(新股≤30td / 次新≤180td / 20d 额<¥2亿 / 单价>¥500)。
- **预算分层**:Micro<¥2k 仅 ETF / Small ¥2k-10k / Normal≥¥10k(单≤15%/总≤70%/单笔≤¥50k)。

### 2.2 闸门要优化的目标(纠正后)
> **从「全市场 ~5000 票里,每日选出预算覆盖内、未来 ~5-10td(两条腿:亦评估 T+1/1-2td)大概率上涨的 ≤5 名优质票(含 ETF),交 agent 二筛」这件事的精度。**

- **评判对象** = 这份 ≤5 篮子(经真系统机制:≤5 槽/5td 持有/轮动/T+1/成本/涨停不可成交)的**扣成本绝对净盈利 + 回撤**,跨 CPCV 路径/regime **稳定为正**。
- **不是** 组合 vs 指数超额(决策 1)。
- **须稳定击败可部署 baseline 面板(防 long-beta 假象,codex P1)**:随机合格 top-5 / 现役 screener(momentum-0.40)/ 纯流动性筛 / ETF-only(510300)/ CSI300-ETF 买入持有 —— 否则牛市高 beta 篮子"看着盈利"却无选股技艺。详 §4.4。
- **精度优先于覆盖**:预算内宁可输出 2 个高把握可负担名,也不要 5 个边际名。

### 2.3 「确定上涨」必须顺着 A 股证据建(反直觉,关键)
调查铁证(§3):**A 股短线奖励的是「买健康的近期跌票 + 避开热门高换手/高波/彩票票」,不是追涨热门**。一个朴素的「挑明天的明星股」筛子会直接踩进 MAX/IVOL/换手过度定价陷阱(已证亏钱)。所以闸门的「优质 + 确定上涨」= **流动性/涨跌停过滤后的短期反转 + 慢速质量 tilt + 强制彩票剔除**的合成,**逆人性但顺证据**。

> **owner 2026-06-21 精化(amendment `quant-gate-rebar` §2.3 / 本方案 §3.8,已并入)**:① 「避热门」= 紧跟国家/时代主旋律择"场"(AI/机器人/AIDC/玻璃基板/AI 链材料 = 应进的热门赛道,**避夕阳产业**)+ 不追涨**已高位**名,**非避战略赛道**;② 「买跌票」= 主题内"高价值尚在低位的优质股" + **客观底部确认门**(缩量/站稳筹码成本/资金流企稳/无破位/无困境/质量地板,**治 A 股"跌了再跌的洗盘",不接飞刀**)。详 §3.8。

---

## 3. 信号设计(A 股短线,文献驱动,provenance-gated)

> 完整出处见 §8。每个信号标注:A 股方向 / horizon / 可信度(robust / mixed / likely-overfit)。**严禁拍脑袋造因子**(铁律②);符号一律 R 阶段从零验,不假设。

### 3.1 核心「上涨」信号(robust)
1. **短期反转(1-5d),流动性 + 涨跌停过滤**(`daily`/`adj_factor`/`daily_basic`)——A 股最强截面效应(Carpenter-Lu-Whitelaw RFS 2021;China reversal 系列)。long-only 形态 = 在**健康、流动、非跌停、非困境**名中选近期超跌反弹者。**robust**,是任何新信号要超越的 baseline。**陷阱**:loser leg 富集跌停/坏消息/停牌风险 → 必须流动性 + 涨跌停 + ST 过滤;用 VWAP/次开成交,不用收-收价(避 bid-ask bounce 虚高)。
2. **1 日日内动量(新投资者驱动的超短续涨)**(`daily`/`daily_basic`/`stk_limit`)——Gao-Jiang-Xiong-Xiong NBER 2023:A 股**无周/月动量,但有显著日动量**,买"强但非涨停"名,T+1 即退,第 2-3 日反转。**robust**,最"上涨"形;但**1 日衰减**,与系统 5td 最短持仓**horizon 不匹配**(→ 两条腿对比的核心待验项)。**陷阱**:最强"今日赢家"恰是涨停名,涨停 BUY 系统禁止且现实不可成交 → 只用"近涨停未封板"切片。

### 3.2 强制负向 overlay(robust,**剔除**用)
3. **彩票/注意力剔除(MAX / IVOL / 异常高换手)**(`daily_basic`/`daily`/`stk_factor_pro`)——Leippold-Wang-Zhou JFE 2022 / Nartea-Wu PBFJ 2018:高 MAX/高 IVOL/高换手在 A 股**稳健跑输**(retail 过度定价)。**这是反转之后最稳健的截面结果,但它告诉你「别买什么」**。→ 任何候选集先过这道剔除/惩罚。**陷阱**:涨跌停截断真实 MAX(PBF 2021"MAX is not the max"),计算时校正。

### 3.3 特征(feature,入排名但不直接当 BUY 触发)
4. **涨停结构**(`limit_list_d`/`stk_limit`/`suspend_d`)——早封/一字/连板 = 动量 tag;盘中破板/高开 = fade tag。**mixed**:现象真但**日频不可成交**(涨停买不到)→ 只作特征/过滤,不作收盘价 BUY 信号。

### 3.4 慢速 tilt(robust 但慢,月级)
5. **分析师修正**(`report_rc`,R4 已验真 alpha)+ **业绩预告/快报事件**(`forecast_vip`/`express_vip`,PEAD)——正向但 horizon **数周-数月** → 作**慢速质量 tilt / 事件 flag**叠在快信号上,**不作短线触发**。`report_rc.tp`=利润总额非目标价(目标价=`min_price`);PIT 闸用 `report_date<d` 非 `create_time`;评级 95% 买入→用 within-stock 修正非 level。

### 3.5 风险/regime overlay(仅风控,**绝不**当 BUY 触发)
6. **筹码分布/获利盘**(`cyq_chips`,资本利得 overhang,Grinblatt-Han JFE 2005)——理论扎实但**模型派生数据(非纯 PIT)**、horizon 偏慢 → 谨慎当 conditioning 特征,字节存档存疑。
7. **聚合北向 + 融资融券**(`moneyflow_hsgt`/`hsgt_top10`;`margin`/`margin_detail`)——froth/fire-sale 风险 flag,**非买入触发**。

### 3.6 ⛔ 明确陷阱清单(花了 8000 积分也别这样用)
| 陷阱 | 真相 |
|------|------|
| **`hk_hold` 北向日频持股** | **2024-08-19 起改季度披露,日频实时北向流停发** → **日频 PIT 已死**。任何 2024-08 后的日频 hk_hold 因子=不存在的数据。**#1 陷阱**。仅季度持股变动可做慢因子。 |
| **`moneyflow`「主力净流入」** | 中国微结构研究证**无稳健日频提前预测力**;撮合拆单破坏「大单=聪明钱」前提。likely-overfit,多半只 fit 当日已发生的价格。可测但别假设是聪明钱。 |
| **龙虎榜 `top_list` 席位当买入信号** | 无干净 OOS 学术证据,**收盘后才出=反应式滞后**,游资席位高次日反转风险。likely-overfit。 |
| **高换手 / MAX / 高量当利好** | 已证 A 股**跑输**(§3.2)。朴素"热门股"筛=踩雷。 |
| **融资余额上升当利好** | 证据是**反向/情绪** flag(更差远期 + fire-sale 下行),符号反了。 |
| **隔夜跳空当动量** | A 股是**反转**(T+1 逆选择),美股符号反过来。 |
| **同日 `limit_list_d` 当特征** | 该榜单当日盘后才齐 → 当日决策用=前视泄漏;只能用 `<d` 历史涨停结构。 |
| **复权因子泄漏** | 比值中用固定 asof 复权抵消;严禁未来复权因子混入 PIT 特征(round-1 守住,续守)。 |
| **停复牌名** | `suspend_d` 停牌不可成交、复牌跳空巨大 → 排除/特殊处理,不当正常票。 |
| **低价/壳彩票暴露** | 低价小盘壳富集彩票/操纵;价格下限 + 底部流动性剔除守住,别让反转 loser leg 滑进壳。 |

### 3.7 候选信号 → Tushare 8000 端点映射(R 阶段建因子用)
`daily`+`adj_factor`(反转/动量/MAX/IVOL)· `daily_basic`(换手/量比/市值/PE→EP)· `stk_limit`+`limit_list_d`+`suspend_d`(涨跌停结构/停牌)· `stk_factor_pro`(预算技术因子)· `cyq_chips`(筹码,谨慎)· `report_rc`+`forecast_vip`+`express_vip`(分析师/事件,慢 tilt)· `moneyflow_hsgt`/`hsgt_top10`/`margin`(风险 overlay)。**`moneyflow`/`top_list`/日频`hk_hold` 仅作"可测但默认陷阱"对照,不进核心。**

### 3.8 owner 2026-06-21 精化:紧跟主旋律 + 客观底部确认(amendment `quant-gate-rebar` §2.3 强制)
owner 对「买跌票/避热门」给出关键客观化方向,**重塑信号架构**:

**(A)「避热门」≠ 避战略赛道,而是 ① 紧跟国家/时代主旋律择"场" + ② 不追涨已高位名。**
- **主旋律 tilt(新维度,PIT 客观)**:tilt 向国家战略主线(AI / 机器人 / AIDC / 玻璃基板 / AI 产业链必要原材料 等),**避开夕阳产业**。**PIT-clean 实现**:
  - 概念/行业成分 PIT:`ths_index`(同花顺概念,as-of-date 成分)+ `index_classify`/`index_member_all`(申万行业 PIT)给"某票 as of d 属哪些概念/行业"。
  - **战略主题映射 = 预注册 + 政策发布日溯源(防 hindsight 前视)**:每战略主题挂 `effective_from`=宣示它的政策文件(五年规划/政府工作报告/行业政策)发布日;tilt **只从该日起生效**(**严禁**用"现在知道 AI 赢了"从 2015 就 tilt)。映射冻结进 git(provenance-gated)作披露假设。
  - **夕阳产业 = 客观代理**:行业级营收/盈利长期下行 + 政策不利,PIT 计算。
- **「不追涨」= 高位/过度延展剔除**(= §3.2 彩票剔除 overlay,**作用于主题内**):主题内也避高换手/高 MAX/已大涨到高位的名。

**(B)「买跌票」= 主题内"高价值尚在低位的优质股" + 客观底部确认(不凭感觉、不接飞刀)。**
- **尚在低位 + 高价值**:主题内,quality(roe/gpm/价值 E-P)高 + **自身仍在低位**(52 周区间低分位 / 筹码成本下方 / 近期回调)= quality-value-at-low。
- **客观底部确认门(新,替代朴素反转,治 A 股"跌了再跌的洗盘")**:**多指标综合**判健康筑底 vs 洗盘——① 缩量(成交量/换手收缩)② 站稳筹码成本带上方(`cyq_chips`)③ 资金流企稳 ④ 无新技术破位 ⑤ 无困境(非 ST/无停牌/无退市审计风险)⑥ 基本面质量地板。**符号/阈值 R 阶段从零验,不假设。**

**(C)与既有 LLM 主题层的关系**:live Phase Y `backend/theme_research/`(LLM peer-sourcing)+ 知识图谱产业链是**互补的 live 定性层**;**研究闸门用上面的客观 PIT 主题信号**(LLM 永不进 PIT/评测路径)。

**(D)两条腿归属**:主旋律 tilt + value-at-low + 质量 + 底部确认 = **较慢"持仓"腿**(随轮动持有数周-数月跟涨复苏);反转 + 1日动量 = **快腿**;同竞技场公平比(决策 2)。

**(E)诚实 caveat(强制,见 §7 + QGR-3 codex 门)**:战略主题"哪个主题战略"有 **hindsight 前视 + 主观映射风险**(回测最易自欺一类)→ 强制:政策发布日 PIT + 预注册冻结 + **必须在 baseline 面板证明主题维度 OVER 非主题 baseline 有增量**(否则 deflation/baseline 揪出"主题 tilt 只是 hindsight")。

---

## 4. 评测框架(核心):可复用、非数据消耗、可公平对比 + 诚实边界

> 这是 owner 第 4 条要求(决策书原话:历史数据珍贵、评测不能数据消耗型、要可反复评、可公平对比多策略)的正面解答。**结论(数学已证边界)**:"非数据消耗"能做到**可复用的开发/对比层**,但**最终上线判定只能靠新数据**——固定历史给不出无限次干净"确认"(Dwork 2015 / Hardt-Ullman FOCS 2014 有硬上界)。所以分两层。

### 4.1 A 层 = 可复用的开发 / 对比竞技场(可反复评、可公平比)
**目的**:对**任意独立挖掘的多个候选闸门策略**反复评测 + 公平横向对比,且 deflation 让"复用"保持诚实。

| 组件 | 做法 | 复用/新建 |
|------|------|----------|
| **共享冻结 PIT 数据集** | 8000 积分摄取的全市场短线数据(§5),survivorship-free,所有策略同一份(公平前提) | 扩 `ingest_round2_data.py` |
| **CPCV(组合净化交叉验证)** | N 组、k 测、路径 φ=(k/N)·C(N,k);purging + embargo(embargo≥标签 horizon)→ 每策略得 **OOS 路径分布**(非单点) | 复用 `walk_forward_eval.py`(已有 CPCV);校验路径数公式 |
| **选股 shortlist 质量度量(纠正口径)** | top-N 篮子扣成本前向净收益 + precision@K + rank-IC,**+ 真系统机制回测**(≤5槽/5td 持有/轮动/T+1/分板块滑点/涨停不可成交) | **新建**:用 `backend/backtest/` 事件循环(`event_loop.py`/`friction.py`/`portfolio.py`/`harness.py`)接一个"闸门选股 → ≤5 槽轮动"策略;**弃用** `benchmark_relative.py`/`benchmark_weights.py`/`exposure_constraints.py`/`long_short.py`(增强指数那套) |
| **多策略公平对比** | Hansen SPA(最优是否真,robust to junk)+ Romano-Wolf StepM(哪些真,强 FWER)/ BH-FDR(海量筛) | `stats_disclosure.py` 有 SPA;**补 Romano-Wolf + BH/BY** |
| **诚实门** | PBO<0.05(CSCV)+ DSR>0.95 用**有效 N**(ONC 聚类去相关 trial,非名义 N)+ MinBTL | `disclosure_stats.py`/`anti_overfit.py` 有 DSR/PBO;**补有效-N 聚类(ONC)** |
| **累计 trial 账本(关键)** | append-only 记录**所有策略所有轮**在共享数据上评过的配置;**累计有效 N** 喂进 DSR 的 SR0 与 MinBTL → 显著性随研究推进**deflate**。这正是"复用诚实"的运作核心:**bar 随每个 trial 上升,竞技场才可反复用** | **新建** ledger(可借 `experiment_registry.py`);**开放问题**:前四轮在同一价格史上的 mining 是否计入(见 §7) |
| **铁律** | 诚实门(PBO/DSR)**永不**用来指导搜索(Goodhart);窗口/超参搜索前committed | 协议固化 |

> **A 层买到什么**:**有限、记账、对自适应复用计惩罚**的开发/对比竞技场(**非**无限免费复用——DSR/SPA/PBO 只惩罚复用、不让它免费,codex P1)。**买不到**:干净的上线判定。A 层"过门"但低 DSR/账本将尽 = **provisional**,非确认。

### 4.2 B 层 = 稀缺的前向确认(上线 gate)
| 组件 | 做法 | 复用/新建 |
|------|------|----------|
| **预注册 + 冻结** | 把选定闸门策略(因子/权重/宇宙/成本/sizing/决策规则)**字节冻结进 git** + 写死成功判据,**在前向数据存在之前** → 把下一步从探索变**确认** | 仿 round-4 freeze 模式 |
| **持续累积的前向处子窗口** | 仅在**冻结之后新增**的真数据上评(post-freeze);**期定义须预声明**(codex P2):verdict 用**非重叠完整 5td 持仓 bet** 为独立观测 + 预注册最小有效观测数 + interim 窥视 alpha-spending;ACCRUING(不足)绝不在噪声带出 verdict | 复用 `round4_forward_test.py`(Layer-B 雏形:fail-closed、ACCRUING 不出 verdict) |
| **多次窥视则 Thresholdout/Ladder 纪律** | 若前向窗随累积反复查,只在"显著偏离开发预期"时报变化,且每次记入固定预算(数学上界:quadratic-in-n) | **新建**(轻量) |
| **go-live gate** | 前向 PASS + 真成本/容量/regime sanity + **owner gate + LiveArtifactRegistry + 45 日 shadow + 人工 pin + 重启** | 复用 `live_artifact_registry.py`/`forward_shadow_mandate.py` |

### 4.3 与旧"锁定 test 一次性"的关系(重要)
- 旧 `config/research/test_set_lock.json`(2025-06-04..2026-06-12,"touch once")的**一次性语义被取代**:该窗口现在只是 **CPCV 全history 池(2015..2026-06-12)的一部分**,作开发/对比用(诚实靠 deflation);**唯一稀缺确认资源 = post-2026-06-12 的前向窗口**(B 层)。
- 这比"逐轮烧 test"干净得多,且天然支持 owner 要的"可反复评 + 公平对比"。

### 4.4 codex 评审处置 → 方法学定稿(2026-06-21;**本节为准,覆盖前文草案口径**)
codex(gpt-5,只读 sandbox;外部核验 NBER 日动量 / Dwork reusable holdout / Hardt-Ullman 自适应上界 / 2024 北向披露变更)裁定 **REVISE**:reframe 方向正确(量化=第一闸门、去 CSI300 硬门对 ≤5 long-only 选股器成立),但下列须在 owner 签字前钉死:

- **[P0] legacy mining 债不可清零**:改判据(指数超额→绝对净盈)**不重置** data-mining 债。累计 trial 账本**预置 legacy 块** = R1-R4 名义网格 + 诊断 + 消融 + 符号检验 + **4 次锁定-test 读**;DSR/MinBTL 用 `max(legacy_N, 新 ONC 有效 N)`,**绝不从零**。
- **[P1] "可复用"措辞收紧**:A 层 = **有限、记账、对自适应复用计惩罚**的竞技场,**非无限免费**(DSR/SPA/PBO 只惩罚复用)。
- **[P1] 量化 proxy ≠ 全系统验证**:`backend/backtest` 事件循环回测**不含** LLM 辩论 / 全 RiskEngine / Line-2 盘中风控(`strategy.py`)→ 它是**量化机制 proxy**;go-live 仍须**真管线 shadow replay**(45 日)。
- **[P1] CPCV 真路径**:现 `walk_forward_eval.py` 报 held-out combinations 非 stitched CPCV paths → QGR-2 实现**真路径拼接**或改名;**重叠路径绝不当独立样本喂 DSR**。
- **[P1] PBO/DSR/SPA protocol**:PBO = 针对**真实选择规则**的 search-overfit 诊断(非盲目 p<0.05 硬门);DSR 的 SR 方差须**自相关校正(HAC/Newey-West)**(重叠持仓收益,现 `stats_disclosure.py` 是简单方差近似);SPA/Romano-Wolf 须**预声明 benchmark family + 时间序列 block bootstrap**。
- **[P1] 防 long-beta 假象 → baseline 面板**:候选须在绝对净盈 + 风险调整上**稳定击败** {随机合格 top-5 / 现役 screener momentum-0.40 / 纯流动性筛 / ETF-only 510300 / CSI300-ETF 买入持有};否则牛市高 beta 篮子"看着盈利"却无技艺。(= 把"相对"检验从 cap 加权指数换成**可部署 baseline**,既甄别技艺又不逼回增强指数。)
- **[P1] 主指标已冻**:**事件循环净 P&L/效用 + MDD + 换手约束**为主;precision@K / rank-IC **仅诊断**(QGR-3/4 前冻结)。
- **[P1] ETF 分轨**:ETF **不混入个股截面排名** → 单独 lane(beta/趋势)或仅预算 fallback。
- **[P2] 陷阱补全**:+ 同日 `limit_list_d` 前视 / 复权因子泄漏 / 停复牌(`suspend_d`)/ 低价壳彩票暴露(§3.6 已补)。
- **[P2] 前向期定义**:verdict 用**非重叠完整 5td 持仓 bet** 为独立观测,预注册最小有效观测 + interim alpha-spending(§4.2 已补)。
- **[P2] QGR-1 coverage-only**:摄取期严禁看任何因子结果(metrics + 账本冻结前)。

codex summary 落 `docs/reviews/qgr-plan-codex-review-summary.md`。

---

## 5. 数据计划(8000 积分,全面了解恰当使用)

- **摄取(owner-gated,离线,字节存档 + checksum + coverage,仅 Tushare 官方 SDK)**:核心短线集 `daily`/`daily_basic`/`adj_factor`(已有)+ **新增** `stk_limit`/`limit_list_d`/`suspend_d`(涨跌停/停牌)/`stk_factor_pro`(技术因子)/`cyq_chips`(筹码,谨慎)/`forecast_vip`/`express_vip`(事件)+ 已有 `report_rc`(分析师);**风险 overlay** `moneyflow_hsgt`/`hsgt_top10`/`margin`/`margin_detail`。
- **主旋律维度数据(新,§3.8)**:`ths_index`(同花顺概念 PIT 成分)+ `index_classify`/`index_member_all`(申万行业 PIT,已有部分)+ **预注册「政策→主题」映射**(政策文件发布日溯源,git 冻结,人工 provenance-gated,**非 Tushare**)。
- **陷阱标注(写进 coverage manifest)**:`hk_hold` 日频 2024-08-19 后失效(只季度);`moneyflow`/`top_list` 列为"可测对照非核心"。
- **分页铁律**:`*_vip` 及多行/票端点**必 limit+offset 分页**(round-3 截断教训 [[reference-tushare-statement-vip-row-cap]]);稀疏流(report_rc/事件)按 round-4 月范围分页。
- **PIT 红线全留**:幸存无偏宇宙 / `report_date<d` / 复权 pin / 离线 replay / fail-closed coverage。

---

## 6. 分阶段行动计划(QGR-0 .. QGR-6,每阶段 owner gate;sim 暂停贯穿)

| Phase | 内容 | 产出 | gate |
|-------|------|------|------|
| **QGR-0** | 本方案 + **codex 评审**(方法学,非代码)→ 修订 → owner go | 本 doc(评审过)+ codex summary | **owner「开」** |
| **QGR-1** | 8000 短线数据 PIT 摄取(§5),字节 + checksum + coverage + 陷阱标注 | 扩 `ingest_round2_data.py` `--phase qgr` + 真摄取(owner-gated) | owner「开」摄取 |
| **QGR-2** | **评测竞技场(A+B 层骨架)**:`backend/backtest` 接"闸门选股→≤5槽轮动"策略(真系统机制)+ CPCV 多路径 + 选股质量度量 + SPA/Romano-Wolf 公平对比 + 有效-N + 累计 trial 账本;B 层前向 runner | 新模块 + 测试 + codex | 门禁全绿 + codex P0/P1/P2 修净 |
| **QGR-3** | **短线因子库**(§3):反转/1d动量/彩票剔除/涨停结构特征/分析师事件慢tilt;CPCV IC 诊断从零验符号;陷阱信号建"对照不入核心" | 扩 `factor_lib.py` + 诊断 doc | 同上 |
| **QGR-4** | **策略搜索/选择(可复用)**:在竞技场搜候选闸门策略(信号组合 + ≤5 槽规则)→ SPA/Romano-Wolf 公平排名 → PBO/DSR 门;**两条腿对比**(5-10td vs T+1/1-2td 机制,同一竞技场公平比)→ 产出一个或多个候选 + provisional 披露 | 搜索 result + 对比 doc | owner 看披露拍板进 QGR-5 |
| **QGR-5** | **冻结 + 预注册**选定候选(字节冻结 git)+ 启动前向累积时钟 | freeze commit + 预注册 doc | owner「开」冻结 |
| **QGR-6** | **前向确认**(累积数月,≥20-40 期出 verdict)→ 过则升 go-live gate(owner+LiveArtifactRegistry+45日shadow+pin+重启)→ **届时才议恢复 sim/上线** | 前向 verdict + go-live 决策 | owner go-live |

- **两条腿(决策 2)**:QGR-4 用**同一竞技场**公平对比"5-10td 选股闸门"(贴现状机制,先上)与"真·超短机制"(需配套改 ≤5槽/5td最短持仓/轮动 → 独立 amendment + 重测);证据够 owner 再决定动 live 机制。
- **sim(决策 3)**:QGR-0..6 全程 sim 暂停;只有 QGR-6 前向过门 + go-live gate 后才议恢复。
- **codex 处置并入(§4.4)**:QGR-1 摄取 **coverage-only**(冻结 metrics/账本前不看因子结果);QGR-2 实现**真 CPCV 路径** + 冻结主指标 + **baseline 面板** + 预置 **legacy trial 块**;QGR-6 go-live 须**真管线 shadow replay**(量化 proxy 回测 ≠ 全系统验证)。

---

## 7. 红线 / 边界 / 开放问题

**安全地基红线全留**:永禁真实下单(只 MockBroker/SimulationExecutor)· 离线研究 never touch live path · 仅 Tushare 官方 SDK · PIT 可复现(字节+checksum+coverage)· LLM 只用于文献不进 PIT/评测路径 · governance `EconomicMechanism` enum 不动(新机制 fail-closed until amendment)· 改决策边界(如真改 live screener/持仓机制)**必 amendment 先行 + codex 前置门 + 重启** · 四诚实保障(冻结-then-read 防火墙 / 累计-N deflation / 显式披露 / 判据不放宽)。

**改判据 = amendment**:决策 1(判据从"超额≥0"改"绝对净盈+控回撤")**本身就是改研究判据**,QGR-0 owner go 后须落一个 `docs/decisions/*-amendment-2026-06-21-quant-gate-rebar.md` 固化(判据 + 闸门角色 + 两层评测)。

**codex 评审(2026-06-21,REVISE → 已据此定稿于 §4.4)已处置**:① 累计账本 legacy 不清零(原 P0)② 主指标已冻 ③ ETF 分轨 ④ baseline 面板 / CPCV 真路径 / DSR HAC / PBO 按真实选择规则 / SPA 预声明 family / 量化 proxy≠全系统验证(go-live 须 shadow replay)/ 前向期定义 + alpha-spending —— 全见 §4.4。

**仍开放(留给执行期 + owner)**:
1. **两条腿的真·超短机制净成本**:T+1/1-2td 翻的高换手/成本是否被超短 alpha 覆盖,QGR-4 净成本回测见真章(会不会扣成本后 edge 消失)。
2. **战略主题的 hindsight 前视风险(owner 2026-06-21 精化引入,§3.8E)**:"哪个主题战略"是回测最易自欺一类 → 强制政策发布日 PIT + 预注册冻结 + baseline 上证明主题维度有增量;**QGR-3 走专门 codex 门审 PIT-soundness**。(「彩票剔除/确定上涨张力」已由 §3.8 主旋律择场 + 客观底部确认门客观化解决。)

---

## 8. 关键出处(provenance-gated)

**A 股短线信号**:Carpenter, Lu & Whitelaw, "The Real Value of China's Stock Market," *RFS* 2021;Gao, Jiang, Xiong & Xiong, "Daily Momentum and New Investors in an Emerging Stock Market," NBER WP 31839 / *JF* forthcoming;Leippold, Wang & Zhou, "Machine learning in the Chinese stock market," *JFE* 2022;Hou, Qiao & Zhang, "Finding Anomalies in China";Nartea/Wu, MAX/IVOL, *PBFJ* 2018;"MAX is not the max under daily price limits," *PBFJ* 2021;"Price overreaction to up-limit events," *Economic Modelling* 2022;Grinblatt & Han, *JFE* 2005;**HKEX/SSE/SZSE 2024-04-12 公告(2024-08-19 生效:北向实时流停发 + 北向逐股持股改季度)**;"The overnight return puzzle and T+1," *JBF* 2020;Lin et al. margin, *Accounting & Finance* 2025;Bian et al. "Leverage-Induced Fire Sales," NBER WP 25040;Jung-Keeley-Ronen analyst revisions 2019。

**评测方法学**:López de Prado, *Advances in Financial Machine Learning*, Wiley 2018(CPCV/purging/embargo);Joubert et al. "Enhanced Backtesting for Practitioners," *JPM* 2024;Bailey, Borwein, López de Prado, Zhu, "The Probability of Backtest Overfitting," *J. Comp. Finance* 2017;Bailey & López de Prado, "The Deflated Sharpe Ratio," *JPM* 2014 + "The Sharpe Ratio Efficient Frontier," *J. of Risk* 2012;López de Prado & Lewis, effective-N/ONC, *Quant. Finance* 2019;White, "A Reality Check," *Econometrica* 2000;Hansen, "SPA," *JBES* 2005;Romano & Wolf, StepM, *Econometrica* 2005;Benjamini-Hochberg *JRSS-B* 1995 / Benjamini-Yekutieli *Ann. Stat.* 2001;Harvey, Liu & Zhu, "...and the Cross-Section of Expected Returns," *RFS* 2016;Harvey & Liu, "Backtesting," *JPM* 2015;Dwork et al. "The reusable holdout," *Science* 2015 + STOC 2015;Hardt & Ullman, FOCS 2014;Blum & Hardt, "The Ladder," ICML 2015;Nosek et al. pre-registration, *PNAS* 2018。

> CPCV 载荷公式核验:路径数 φ=(k/N)·C(N,k)(**非** C(N,k));N=6,k=2 → 15 组合 / 5 路径。DSR/PSR/SR0/MinBTL 公式见调查记录,实现时 effective-N 用 ONC 聚类、kurtosis 用 full(非 excess)。

---

## 9. 接手指针(给未来 session)

- **本 doc = 量化研究的新权威框架**,取代 round-1..4/R5 的逐轮路线(那些**框定已判错**,仅作历史诚实记录)。
- 旧 memory/CLAUDE.md/plan.html 中关于「round-4 provisional PASS / 增强指数 / 明天启动 sim / 既有 test 第 N 次评测 / forward accrual 续跑」的"下一步"指针**已废**;新下一步 = **QGR-0 codex 评审 → owner go → QGR-1**。
- sim **暂停**直到 QGR-6 前向过门 + go-live gate。
