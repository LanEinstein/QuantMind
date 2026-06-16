# 因子理论 + 诚实评估方法论调研(Phase 1 交付)

> **日期**:2026-06-16
> **作者**:Claude Opus 4.8 (1M context)
> **专项**:量化选股策略研究(`docs/research/factor-strategy-research-brief-2026-06-16.md`)
> **方法**:4 路 provenance-gated 并行 agent(横截面因子前沿 / 中短线 alpha / A 股特异性 / 诚实评估方法论),每条主张钉到同行评审期刊 / NBER·SSRN 工作论文(具名机构)/ arXiv q-fin / 官方文档;博客农场·营销稿一律仅用于定位原始出处,绝不作为证据。每族附来源可信度评级。
> **用途**:Phase 3 因子库设计与权重搜索的**权威依据**;不替代锁定测试集上的诚实 OOS 验证。

---

## 0. 三条治理性先验(选任何因子前先读)

A 股单因子回测**极易假阳性** —— 这三条 tier-1 结论是约束性先验,非可选阅读:

1. **多重检验门槛(Harvey-Liu-Zhu 2016, RFS 29(1):5-68)**:316 个已发表"因子"语境下,常规 t>1.96 无意义;新因子应过 **t>~3.0**(Bonferroni/Holm/BHY 随检验数升高)。→ 任何自研因子 t∈(2,3) 默认假。([RFS](https://academic.oup.com/rfs/article-abstract/29/1/5/1843824) · [NBER w20592](https://www.nber.org/papers/w20592))
2. **复制失败(Hou-Xue-Zhang 2020, RFS "Replicating Anomalies")**:452 异象用 **NYSE 断点 + 市值加权**,**65% 过不了 t>1.96,82% 过不了 t>2.78**;"trading frictions"类 96% 失败。→ 多数异象是**微盘伪影**,市值加权 + 正确断点后消失。([NBER w23394](https://www.nber.org/system/files/working_papers/w23394/w23394.pdf))
3. **发表后衰减(McLean-Pontiff 2016, JF 71(1))**:97 预测因子样本外低 **26%**,发表后低 **58%**。→ 发表回测夏普是上限,预算"上线后只剩一半"。([Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365))

**净先验**:下表是**稳健性排序的 shortlist,不是菜单**。要求市值加权(或至少流动性筛)、排除微盘/壳、发表效应打 ~40-50% 折。

---

## 1. A 股构造红线(非可选,全部出自同行评审)

这些是 A 股特异性 agent + 横截面 agent **独立交叉确认**的强制构造规则:

1. **剔除最小 ~30% 市值**(壳价值污染):IPO 配额制使"上市资格"本身成稀缺可交易资产,微盘作为借壳标的被高估,价格反映壳期权非基本面。Lee-Qu-Shen 估单壳 2007-2015 值 3-4 亿,**加入壳概率因子后 size 溢价消失**;Liu-Stambaugh-Yuan CH-3 size 因子只用最大 70%(壳价值占市值均值 29.5%)。([Lee-Qu-Shen SSRN 3038446](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3038446) · [LSY JFE 2019](https://faculty.wharton.upenn.edu/wp-content/uploads/2018/03/Size-and-Value-in-China.pdf))
2. **价值用 E/P 不用 B/M**:LSY 发现 **E/P 吸收 B/M** 捕获全部中国价值效应;naive FF B/M 复制留 **17%/年 E/P alpha** 未解释。CH-3 用 E/P 基础的 VMG。
3. **不要照搬美式动量;预期短期反转 + 换手/流动性主导**:12-1 动量在 A 股弱到几乎无;CH-4 第四因子是**换手 PMO 非动量**。
4. **T+1 + 涨跌停 = 可交易约束,回测 oracle 必守**:**涨停剔出 BUY 腿、跌停剔出 SELL 腿**(均不可执行);**反转建在 ≥2 日 close-to-close**(T+1 当日不可回);**绝不**在涨跌停锁价成交。已对齐现有 `harsh_fill` + RiskEngine at-fill recheck。
5. **排除 ST/*ST、退市整理、近端 IPO**;用主板断点;**IVOL/反转的估计窗剔除涨跌停日**(截断收益低估真实波动)。

> 现有系统的 universe 红线(排除 ST/科创/北交/可转债 + 流动性 + 单价上限)**已与文献吻合**,是要保留的真实优势。

---

## 2. 候选因子族(每条:机制 + 出处 + 预期 IC + A 股适配 + 失效条件)

**预期 IC 方向 = 因子值与未来收益的截面相关号。**

| # | 因子族 | 预期 IC | A 股适配 | 优先级 |
|---|---|---|---|---|
| 1 | **短期反转**(≥2~20日) | **负**(买跌买输家) | **本土最强异象**(T+1+散户) | **核心** |
| 2 | **换手/PMO 情绪** | **负**(高换手→低收益) | **中国原生,无美式对应** | **核心** |
| 3 | **价值 E/P**(非 B/M) | **正** | **强**(E/P 吸收 B/M) | **核心** |
| 4 | **IVOL 低波** | **负**(高 IVOL→低收益) | 好(散户彩票偏好) | 核心 |
| 5 | **MAX 彩票** | **负** | 强,但与反转/换手机械重叠 | 负向筛 |
| 6 | **流动性/Amihud** | 正(毛),净后多反转 | **ML 头号预测因子**但成本脆弱 | **筛/风控,非收割** |
| 7 | **质量/盈利**(毛利率) | 正(弱/条件) | 弱,非 SOE 后 2007 较清 | 次级 |
| 8 | **规模**(剔底30%后) | 负(不稳) | 大半是壳伪影 | 仅质量条件化 |
| 9 | **动量 12-1** | ≈0 | **弱/缺失** | **不要照搬** |
| 10 | **北向 smart money** | 正(短半衰) | 仅 Connect 标的;滞后披露当心前视 | tilt 非 alpha |

### 详解(load-bearing 项)

**1. 短期反转** — 机制:流动性提供补偿 + 散户过度反应 + **T+1 "可售权"**(当日买不可卖→隔夜系统性负收益、过冲后修正)。出处:Jegadeesh 1990 JF;Lehmann 1990 QJE;China T+1:[IREF 2024](https://www.sciencedirect.com/science/article/abs/pii/S1059056024006452) / [Qiao-Dam JBF 2020](https://www.sciencedirect.com/science/article/abs/pii/S1386418120300033)。IC:**负**,日/周/月皆显著(中国比美式更短更强)。适配:**主场异象**,但 T+1 使单日腿不可日内回收,建 ≥2 日;集中在低换手名。失效:**最贵成本族**(高换手 1-5 日,Novy-Marx-Velikov RFS 2016:>50%/月换手很难过成本);1 日horizon 因涨跌停磁吸可能翻成动量(符号翻转风险);容量低。

**2. 换手/PMO 情绪** — 机制:散户主导使换手=实时情绪;高异常换手=过度乐观/过度定价→后续低收益。`PMO = 过去月换手 / 过去年换手`。出处:LSY CH-4 PMO;[China 异象 PBFJ 2021](https://ideas.repec.org/a/eee/pacfin/v68y2021ics0927538x21001141.html)。IC:**负**。适配:中国尤强(散户~80%量);需在流动性桶内归一,剔涨跌停日(锁板近零换手)。失效:制度化/北向上升后减弱(后 2017);2014-15 杠杆牛极端,勿外推。

**3. 价值 E/P** — 机制:风险(困境/经营杠杆)+ 行为(过度外推)。出处:LSY JFE 2019(E/P 吸收 B/M);FF 2015。IC:**正**(高 E/P→高未来收益)。适配:**强**,用 E/P(可加 EBIT/EV、CF/P),**勿用 B/M**;仍守剔底30%/ST。失效:2007-2020 全球"价值寒冬"regime 依赖;E/P 分子受盈余管理噪声。

**4. IVOL 低波 / 5. MAX 彩票** — 机制:套利不对称 + 卖空约束 + 散户彩票偏好→高波/高 MAX 过度定价。出处:Ang-Hodrick-Xing-Zhang 2006 JF;Bali-Cakici-Whitelaw 2011 JFE;China:[彩票 A&F 2025](https://onlinelibrary.wiley.com/doi/10.1111/acfi.13354)。IC:**负**,月频。适配:**中国 IVOL 与 MAX 独立共存**(不像美国 MAX 吸收 IVOL)→**两者都留勿合并**;IVOL 用 CH-3/CH-4 残差(非 FF-3,否则误设基准);MAX 受 ±10%/±20% 涨停截断;alpha 在**高波短腿(A 股不可空)**→作**多头规避筛**最划算。失效:稳健(风险类两大复制都过);2020-08 创业板/科创 ±10%→±20% 改 MAX 分布,分段重估。

**6. 流动性/Amihud** — 机制:非流动性溢价 vs 流动性风险。出处:Amihud 2002 JFM;**China ML 头号预测因子**:Leippold-Wang-Zhou 2022 JFE。IC:毛正,但**净后常反转/消失**(活在微盘不可交易角)。适配:用作**筛/风控,非在非流动尾收割**;最高假象风险(HXZ frictions 类 96% 失败)。

**7. 质量/盈利** — 机制:市场低估盈利持续性。出处:Novy-Marx 2013 JFE(毛利率);AFP QMJ 2019;LSY 称 CH-3 解释中国盈利异象。IC:正但**弱/条件**。适配:**争议**(Jansen-Swinkels-Zhou 称中国弱;Li-Liu-Liu-Wei Mgmt Sci 2023:q 因子仅非 SOE 后 2007 清样本好);用毛利率(抗盈余管理),限非 SOE。

**9. 动量** — 机制(为何失败):极高换手压缩"过度反应-修正"窗,中期延续窗打不开。出处:[China 动量 PBFJ 2021](https://www.sciencedirect.com/science/article/abs/pii/S0927538X20306703);LSY CH-4 故意不含动量。IC:**≈0**;残差动量弱存(~0.6%/月)。→ **不要照搬美式 12-1 正动量**(这正是现役 momentum 0.40 权重的最大疑点)。

**工程因子库(特征空间非经济理论)**:qlib **Alpha158/Alpha360**(MS 官方,为 CSII300/500 调,label=前 2 日收益短horizon)+ WorldQuant **101 Alphas**(arXiv 1601.00991,持有 0.6-6.4 日、强相关波动、成本敏感)。→ 仅作**正则化多信号模型的特征空间**,**绝不**手挑单 alpha(t-hacking 重灾)。

**ML 证据**:Gu-Kelly-Xiu 2020 RFS(树/NN 主导,捕非线性交互,OOS 多空 NN 年化夏普 1.35 毛、主导信号=动量/流动性/波动);**Leippold-Wang-Zhou 2022 JFE(中国):流动性=头号预测因子(非美式动量),散户主导提升小盘短期可预测性,OOS 过成本但被显著侵蚀。** → 净后 A 股夏普实质更低。

---

## 3. 推荐评估协议(数值默认;对齐既有 `strategy_evolution` 基建)

为本设定定制(A 股、2015-present、锁定测试集=最近 ~250 交易日、诚实 OOS):

| 环节 | 默认 | 出处 |
|---|---|---|
| **锁定测试集 L** | 最近 250 交易日,开工即封,**仅 1 策略 × 1 次** | OOS prime directive(owner 已锁) |
| **train+val D** | 2015 → L 起点,D↔L 间 purge+embargo 隔断 | |
| **预声明 N** | 搜索前冻结试验数(含失败),DSR/MinBTL 用 | Bailey-LdP 2014 |
| **MinBTL 准入** | 搜索前解 2·ln(N)/E[max_N]² ≤ years(D)(~10.5y→至多数百独立试验) | Bailey et al 2014 |
| **purged k-fold / CPCV** | CPCV **N_groups=10, k=2**(→45 split / 9 path),每界 purge | LdP 2018 Ch7/12 |
| **embargo** | **max(1% of D, ≥最长 label horizon≈20d)** | LdP 2018 Ch7 |
| **净收益优先** | 每候选 PnL 扣 A 股摩擦(佣金+卖出印花税+分板块滑点+过户费+换手×冲击)后才入门;**绝不门控毛夏普** | Novy-Marx-Velikov 2016 |
| **DSR 主统计门** | `deflated_sharpe_ratio` ≥ **0.95**(喂全搜索 N + V[{SR}]) | Bailey-LdP 2014 |
| **PBO(CSCV)披露** | S=16 子矩阵,**≤0.5 硬,≤0.2 目标**;含全搜索矩阵 | Bailey et al 2017 |
| **SPA 披露**(基准=被动 HS300 / 现役权重) | stationary bootstrap,均块~5-10d | Hansen 2005 |
| **哨兵对照** | shuffle 信号掺入每批;放行=门坏了 | 既有 `sentinel.py` |
| **机制门** | 每晋升候选附经济机制;无机制纯数据胜出=默认过拟合拒 | 既有 `mechanism_registry` |

**既有基建直接映射**:`anti_overfit.purged_kfold_splits/deflated_sharpe_ratio` + `disclosure_stats.{minimum_backtest_length,pbo_cscv,spa_disclosure}` + `quant_param_search.ParamExperimentProducer`(预声明 N Sobol)+ `sentinel.make_sentinels` —— 全纯函数 import-isolated,研究脚本直调。

---

## 4. 对现役 FACTOR_WEIGHTS 的直接含义

现役 `{momentum 0.40 / ma_ratio 0.25 / volatility 0.20 反向 / avg_amount 0.15}`(人工拍)与文献的张力:

1. **momentum 0.40 是最大疑点**:A 股动量弱/缺失,短期反转才是主场。给 20 日动量**正**权重 0.40,可能在系统性押反方向。Phase 3 须检验 momentum_20d 的真实 IC 号(预期 ≤0 或不显著)。
2. **volatility 反向 0.20 方向对**(低波好,符 IVOL 异象),但应剔涨跌停日估计、与 MAX 区分。
3. **avg_amount(流动性)0.15**:方向需查 —— 文献是高换手→负;但 avg_amount 是水平非异常换手,且流动性高=可交易性好(风控价值)。须区分"流动性筛"vs"流动性收割"。
4. **缺失的核心族**:E/P 价值、换手 PMO 情绪、IVOL/MAX 显式项 —— 现役因子集无基本面/情绪维度。Phase 3 扩库重点。
5. **时变权重**(Carpenter-Lu-Whitelaw JFE 2021:2004 后 A 股价格信息化趋近美国 + Jansen-Swinkels-Zhou regime):基本面因子近样本/非 SOE 该加权,情绪因子随制度化减权 —— 但**本专项先做静态权重的诚实 OOS 验证**(时变是后续方向)。

> **核心假设(Phase 3 检验)**:把"押正动量"的现役权重换成"短期反转 + 换手/情绪规避 + E/P 价值 + 低波"的 A 股对齐组合,能在锁定测试集上扣成本真盈利 + 跑赢 HS300。**待数据证。**

---

## 5. 来源可信度审计(摘要)

- **Tier 1(完全可信,锚定全部 load-bearing 主张)**:Harvey-Liu-Zhu 2016 RFS;Hou-Xue-Zhang 2020 RFS;McLean-Pontiff 2016 JF;Gu-Kelly-Xiu 2020 RFS;Fama-French 2015 JFE;Jegadeesh-Titman 1993 JF;Novy-Marx 2013 JFE;Frazzini-Pedersen 2014 JFE;Amihud 2002 JFM;**Liu-Stambaugh-Yuan 2019 JFE(中国基准)**;**Leippold-Wang-Zhou 2022 JFE(中国 ML)**;Bailey-LdP 2014 JPM(DSR);Bailey et al 2017 JCF(PBO);Hansen 2005 JBES(SPA);White 2000 Econometrica;Politis-Romano 1994 JASA;Carpenter-Lu-Whitelaw 2021 JFE;Li-Liu-Liu-Wei 2023 Mgmt Sci。
- **Tier 2(强工作论文/实务,温和利益保留)**:Lee-Qu-Shen(Stanford GSB,壳价值);Jansen-Swinkels-Zhou 2021 PBFJ(Robeco);Hou-Qiao-Zhang SSRN 4322815(具名 q 因子作者,**PDF 403→"38 survive/none after risk-adj"待原文核**)。
- **Tier 3(窄域同行评审,仅作机制/方向不作量级)**:各 China IVOL/MAX/反转/北向/涨跌停单异象论文 —— **个体中国异象论文尤暴露假阳性**(用 Tier-1 复制研究校准)。
- **拒用**:alphaarchitect/longbridge/grokipedia/researchgate 镜像等仅用于定位原文。
- **诚实缺口**:多个 ScienceDirect/SSRN 原 PDF 对抓取器 403,引用基于出版商元数据页+摘要(≥2 独立源交叉);LdP 2018 书页未直读(purged-CV/CPCV/embargo 经百科+DSR 论文转述,**编码进 `backend/backtest`/`anti_overfit` 前须对书核**)。

---

## 6. 待办 / 验证缺口(进 Phase 3 前留意)

1. **LdP 2018 Ch7/12 原书**核 CPCV path 公式 φ[N,k]=(k/N)·C(N,k) + embargo 默认比例(现 `anti_overfit` 已实现,Phase 3 复用前抽验)。
2. **基本面数据**:E/P / 毛利率 / PMO 需 `daily_basic`(已摄)+ 可能 `fina_indicator`(财报)—— 摄取 scope 含 `daily_basic`,基本面深度因子的财报数据可能需补端点(Phase 2 评估)。
3. **北向数据**:Tushare 北向需额外端点/权限,且滞后披露前视风险高 —— 本专项**暂不纳北向**(tilt 非核心 alpha),如纳须 PIT 严格。
4. **现役 momentum_20d IC 号**:Phase 3 第一个诊断 —— 若实测中性/负,直接印证现役权重错配。
