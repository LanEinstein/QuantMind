# Amendment 2026-06-21 — 量化研究判据 + 闸门角色重定(quant-gate rebar)

> **类型**:研究治理 amendment(改"量化选股策略研究专项"的判据 + 量化闸门角色框定)。**先于任何 QGR 代码**(红线:改决策边界先 amendment)。
> **关联**:总纲 R0 + `P0-9`(全市场筛)+ `P0-7`(风险/预算)。**方案** `docs/research/quant-first-gate-rearch-plan-2026-06-21.md`(codex 评审 REVISE→定稿)。**触发**:owner 2026-06-21 识破前四轮框定错 + 4 决策 + 主旋律/底部确认精化。
> **重要边界**:本 amendment 改的是**研究判据 + 闸门优化目标的框定**(离线研究)。它**不**改 live `backend/screening/screener.py` 的运行行为——live screener 的真实 rebar(去 momentum-0.40 等)**由 QGR-6 go-live gate 单独审批**(owner gate + LiveArtifactRegistry + 45 日真管线 shadow replay + 人工 pin + 重启),**当前 sim 暂停、live 一行未改**。

## 1. 背景:前四轮的框定错(owner 2026-06-21 认定)
前四轮(round-1..4 + R5)把量化当成"要跑赢 CSI300 的整体组合策略"优化 → round-2/3/4 一路滑向 benchmark-relative 增强指数(~300 名加权)。但系统里量化**根本不是组合策略**,是面对 ~5000 票的**第一道选股闸门**。round-4 那个 provisional PASS 增强指数 ≤5 持仓 top-N agent 系统**装不下、且仅 provisional**,不能上线。**判据错(逼着走增强指数)+ 角色错(当组合)→ 珍贵历史答了错问题。**

## 2. 本 amendment 锁定的变更(rebar)

**2.1 量化闸门角色(框定)**:量化 = 面对 ~5000 票的**第一道选股精度闸门**(非组合/指数跟踪器)。输出 = 预算覆盖内"优质 + 确定上涨"≤5 名(含 ETF)→ 交 4-agent 辩论二筛。**精度优先于覆盖**。

**2.2 判据(推翻旧"超额≥0"硬门)**:
- **新硬判据 = 绝对净盈 + 控回撤**:判 ≤5 选股篮子**本身**(经真系统机制:≤5 槽/最短持仓 5td/轮动/T+1/分板块滑点/涨停不可成交)的**扣成本绝对净盈利 + 回撤**,要求**跨 CPCV 路径/regime 稳定为正**。
- **CSI300 超额**:降为**补充披露**,**不当硬门**(≤5 名 long-only 短线选股器结构上无法稳定跟踪 cap 加权指数;round-1 已证 benchmark-naive)。
- **防 long-beta 假象(codex P1)**:候选须在绝对净盈 + 风险调整上**稳定击败可部署 baseline 面板**(随机合格 top-5 / 现役 screener momentum-0.40 / 纯流动性筛 / ETF-only 510300 / CSI300-ETF 买入持有),否则牛市高 beta 篮子"看着盈利"却无技艺。

**2.3 信号架构(owner 2026-06-21 精化,客观化"买跌票/避热门")**:
- **(A) 主旋律 tilt(择"场")**:tilt 向国家/时代战略主线(AI/机器人/AIDC/玻璃基板/AI 产业链必要原材料 等),**避开夕阳产业**。**PIT-clean 实现**:① 概念/行业成分 PIT(`ths_index` 同花顺概念 as-of-date + `index_classify`/`index_member_all` 申万行业);② **战略主题映射 = 预注册 + 政策发布日溯源**——每主题挂 `effective_from`=宣示它的政策文件(五年规划/政府工作报告/行业政策)发布日,tilt **只从该日起生效**(**严禁** hindsight:不得用"现在知道 AI 赢了"从 2015 就 tilt);映射冻结进 git(provenance-gated)作披露假设;③ 夕阳产业 = 行业级营收/盈利长期下行 + 政策不利的客观代理。
- **(B) 场内选"高价值尚在低位的优质股"**:主题内 quality(roe/gpm/价值 E-P)高 + **自身仍在低位**(52 周区间低分位 / 筹码成本下方 / 近期回调)。
- **(C) 客观底部确认门(治 A 股"跌了再跌的洗盘",不凭感觉、不接飞刀)**:**多指标综合**判健康筑底 vs 洗盘——缩量 / 站稳筹码成本带上方(`cyq_chips`)/ 资金流企稳 / 无新技术破位 / 无困境(非 ST/无停牌/无退市审计风险)/ 基本面质量地板。**符号/阈值 R 阶段从零验,不假设。**
- **(D)「不追涨」= 高位/过度延展剔除**(高换手/MAX/IVOL/已大涨到高位,**作用于主题内**)。
- **(E) 两条腿**:主旋律 tilt + value-at-low + 质量 + 底部确认 = **较慢"持仓"腿**(随轮动持有数周-数月跟涨复苏);反转 + 1日动量 = **快腿**;同竞技场公平比。

**2.4 评测学(可复用、非数据消耗型)= 两层**:
- **A 层 = 可复用开发/对比竞技场**:真 CPCV(purging+embargo,路径 φ=(k/N)·C(N,k))+ 选股质量度量(主=事件循环净 P&L/效用+MDD+换手约束;precision@K/rank-IC 仅诊断)+ Hansen SPA/Romano-Wolf 多策略公平对比 + PBO(按真实选择规则)/DSR(ONC 有效 N + HAC 自相关校正)/MinBTL + **累计 trial 账本**。
- **累计 trial 账本 legacy 块(codex P0)**:改判据**不重置** data-mining 债 → 账本预置 legacy 块(R1-R4 名义网格+诊断+消融+符号检验+4 次 test 读),DSR/MinBTL 用 `max(legacy_N, ONC 有效 N)`,绝不从零。
- **B 层 = 稀缺前向确认**:预注册+字节冻结 → 持续累积前向处子窗口(非重叠完整 5td bet 为独立观测+alpha-spending)→ go-live(owner+LiveArtifactRegistry+45 日**真管线 shadow replay**+pin+重启)。
- **量化机制回测 = proxy**(不含 LLM 辩论/全 RiskEngine/Line-2),非全系统验证;go-live 须真管线 shadow replay。
- **诚实边界**:固定历史给不出无限次干净确认(Dwork/Hardt-Ullman);A 层可复用对比,B 层确认须新数据。

## 3. 保留不变(安全地基红线全留)
永禁真实下单(只 MockBroker/SimulationExecutor)/ 离线研究 never touch live path / 仅 Tushare 官方 SDK / PIT 可复现(字节+checksum+coverage)/ LLM 只用于文献、**永不进 PIT/评测/运行时数据路径** / governance `EconomicMechanism` enum 不动(新机制 fail-closed until amendment)/ 127.0.0.1 / 飞书人工 / RiskEngine 纯函数 / 单一构造点 / 四诚实保障(冻结-then-read 防火墙 / 累计-N deflation / 显式披露 / 判据不放宽)。**LLM 主题层(Phase Y `theme_research`)是 live 定性层,与研究闸门的客观 PIT 主题信号互补,LLM 永不进研究 PIT/评测路径。**

## 4. 范围 / 门 / 待办
- **生效范围**:研究专项(QGR);live 行为零改动(sim 暂停)。
- **前置门**:本 amendment(本文件)→ QGR-1 数据摄取(coverage-only)→ QGR-2 评测竞技场 → QGR-3 因子(主旋律/底部确认 PIT-soundness 走**专门 codex 门**)→ QGR-4 搜索+两条腿公平比 → QGR-5 冻结+预注册 → QGR-6 前向确认 → go-live(单独 owner gate)。
- **诚实 caveat(强制)**:战略主题"哪个主题战略"有 hindsight 前视 + 主观映射风险 → 政策发布日 PIT + 预注册冻结 + **必须在 baseline 面板证明主题维度 OVER 非主题 baseline 有增量**(否则 deflation/baseline 揪出"主题 tilt 只是 hindsight")。
- **codex**:本 amendment 为 docs(codex-exempt);QGR 各编码阶段 commit 前各自走 codex 代码门(撞额度→/code-review high)。

## 5. owner 4 决策(2026-06-21,据此)
① 判据=绝对净盈+控回撤(去 CSI300 超额硬门) ② horizon=两条腿并进 ③ sim 暂停直到 B 层前向确认 ④ 清理旧/错误内容(已执行)。+ 2026-06-21 精化:主旋律择场 + 客观底部确认 + 不追涨高位名(§2.3)。
