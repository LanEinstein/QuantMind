# R4-1 文献调研:分析师修正动量(report_rc)— provenance-gated(2026-06-20)

> 第 4 轮 R4-1 交付之二(文献调研,铁律①「不闭门造车」)。配套数据/权限文档
> `round4-r4-1-data-entitlements-2026-06-20.md`。**本轮头号 alpha 源 = 分析师盈利预测修正动量。**
>
> **调研状态**:3 个 provenance-gated agent **全部交付**(PIT/完整性 + 学术证据 + 卖方金工因子
> 公式;第 2/3 个首跑撞 session 限额→13:20 重置后收窄重跑成功)。所有引文带 URL,未直取核实的
> 量级显式标 UNVERIFIED;数据语义经真探针实证。

---

## 0. 一句话

分析师**盈利预测修正(estimate revision)的"变化"**(非 level、非评级 level)是全球最稳健的
正交 alpha 之一,A 股实测与量价因子相关 ~0.03。**四轮以来最有希望:两项 A 股证据显示分析师价值
集中在大盘段**(Lv 2025:大盘超额 ~4.14% vs 小盘 ~1.74%)—— **恰好补三轮 FAIL 缺的"与 cap 加权
CSI300 对齐"的料**(价值/反转/低波恰在大盘段失效)。**但非稳赢**:净超额取决于大盘 revision drift
能否扛住覆盖衰减 + 乐观偏差 + 成本;DSR/PBO/SPA 仍是约束门,须为"小正但不显著"留余地。

---

## 1. 已验证学术证据(美股/国际锚;agent 核验引文 + URL)

> 引文已核验存在;⚠️ 标注处为 paywall 未直取、量级 UNVERIFIED(机制/符号/出处可信)。
> **纠错**:Stickel "Reputation and Performance" = **JF 47(5) 1992**(非 FAJ 1991);revision-momentum
> 经典 Stickel = **1991, The Accounting Review 66(2):402-416**;JKKL 2004 全名 = "Analyzing the
> Analysts: When Do Recommendations Add Value?"。

| 因子 | 引文(作者 年,期刊) | 符号 | 量级(多空) | 衰减 | 关键 |
|---|---|---|---|---|---|
| **盈利预测修正动量** | Chan, Jegadeesh & Lakonishok 1996, *JF* 51(5) | **+** | ~**0.7%/月**(并价格动量) | 6 月 drift,无反转 | underreaction |
| 修正动量(早期) | Stickel 1991, *Accounting Review* 66(2) | **+** | 大修正前后显著超额 | 天-周 | ⚠️量级 UNVERIFIED |
| 声誉×修正 | Stickel 1992, *JF* 47(5) | **+** | All-Star 上修推动更大;下修弱 | 即时 | 非对称 |
| 修正 drift/价格发现 | Gleason & Lee 2003, *Accounting Review* 78(1) | **+** | high-innovation 修正 drift 更大 | **高覆盖股 drift 更小/更快** | **大盘逆风** |
| 预测**分歧度** | Diether, Malloy & Scherbina 2002, *JF* 57(5) | **−** | high−low ≈ **−9.5%/年** | 持续 | 小盘/输家最强 |
| **目标价**隐含收益 | Brav & Lehavy 2003, *JF* 58(5) | **+** | ΔTP/P 排序 **+7.17%**;level TP/P +2.69% | 短期+1年 | |
| **推荐/评级修正** | Womack 1996, *JF* 51(1) | **+** | 买 +2.4%(短);**卖 −9.1%(6月)** | 非对称 | |
| 评级**变化非 level** | Jegadeesh-Kim-Krische-Lee 2004, *JF* 59(3) | **+**(Δrec);level≈0/反常 | Δrec 稳健、**与其它预测因子正交** | 季度 | **决定性:用变化** |

**美股主题**:修正/推荐的**变化**预测,**level 不预测**(JKKL);drift 在**高覆盖股更弱**(Gleason-Lee)
= cap 加权大盘基准(覆盖最密)的逆风。URL 见下文 §10。

## 2. A 股证据(决定性 —— 为何这轮可能不同 + 符号须从零验)

1. **Liu, Zhang et al. 2023, "Expectation disarray: Analysts' growth forecast anomaly in China,"
   *Pacific-Basin Finance Journal* v82**:A 股**符号与美股有别** —— 高分析师增长/盈利预测**正向**
   预测(多高空低 ~**20%/年, t>3**),长期限预测更强;投资者**低反应**。
   → **铁律:report_rc 因子符号必须在既有 split 上从零验证,绝不照搬美股 revision-momentum 符号。**
   (https://www.sciencedirect.com/science/article/abs/pii/S0927538X23002639)
2. **Lv 2025, "Do Sell-side Analyst Reports Have Investment Value?" arXiv:2502.20489**:⭐**对基准
   决定性** —— 分析师报告超额在**大盘段更大(~4.14% vs 小盘 ~1.74%)**,卖方在大盘真正增值。
   = 补三轮 FAIL 缺的"大盘 alpha"。(⚠️量级 paywall 未直取,UNVERIFIED,作指示性。)
   (https://arxiv.org/abs/2502.20489)
3. **Liu et al. 2024, "Analyst Reports and Stock Performance: Evidence from the Chinese Market,"
   arXiv:2411.08726**:报告**情绪**预测次日超额 ~±6.4%,但**仅次日(短衰减)** → 月度调仓+成本下
   恐捕获有限。承认 A 股系统性乐观偏差。(https://arxiv.org/html/2411.08726v1)
4. **A 股分析师乐观/羊群偏差(多源)**:股权质押(避平仓)、佣金迎合(机构/送转)、情绪放大 →
   **原始 level/目标价系统性偏高且噪声大 → 必用变化/修正 + 横截面排序,不用绝对 level**。

## 3. report_rc 数据语义(真探针实证;**纠正字段错标**)

- **`tp` = 利润总额(万元,税前)≠ 目标价**(收入表级联 op_rt→op_pr→tp→np,全万元;茅台 tp med
  =14,008,757 万元=~1400 亿,介于营收与净利之间)。**目标价 = `min_price`**(茅台 2236.80/平安
  13.50/招行 40.85;`max_price`≈0,both=0/only_min)覆盖 **~30-50%**。kickoff/旧 memory 标"tp=
  目标价"是错的,会静默污染 tp_impl 因子(已全改正)。
- **`create_time` 可得**(显式 `fields=` 请求,默认不返)= 入库/更新时戳 → 挡回填行(create_time ≫
  report_date 在 report_date 当时不可知 → 剔除)。
- 覆盖:`eps`/`np` **99.6%**(主修正因子)/ op_rt 86% / roe 83% / pe 78% / tp(利润总额)74% /
  op_pr **12%(弃)** / max_price **0.1%(弃)**。`quarter`=预测目标财年(`YYYYQ4`=年度;一报告多 FY
  行 mean 2.85);单日单股 ~1.49 家券商 → 必 trailing 窗口聚合。`rating` 文本异构。

## 4. PIT/数据完整性陷阱(condensed;provenance)

1. **陈旧未更新预测污染"修正"** → staleness 窗口 **N=90d 主/180d 稳健**;券商级算修正,陈旧剔除≠
   修正(I/B/E/S 105/120d;学术 90/180d)。
2. **厂商预清洗 consensus 内生污染** → 绝不消费现成 consensus,自建明细中位数(report_rc 本就只给明细)。
   (Kaplan-Martin-Xie, JAR, SSRN 3213833)
3. **发布日≠可得日**:report_rc 当晚入库 → **report_date=D 的行 D+1 才可交易**;`create_time` 测滞后+
   剔回填行。(Glushkov WRDS;tushare doc 292)
4. **历史库追溯重写/删史** → 钉 vintage、字节存档+checksum(K-001);report_rc 唯一审计字段=create_time。
   (Ljungqvist-Malloy-Marston, JF, SSRN 889322)
5. **券商停覆盖=隐性下调** → staleness 自动剔 + Δ覆盖数当看空护栏。(NYU Stern WP)
6. **自选择偏差(McNichols-O'Brien 1997, SSRN 2813)** → **决定性:用个股时序修正,绝不用横截面
   level/rating**(A 股评级 95% 买入印证)。
7. **覆盖偏大盘 → 全市场长多里退化为 size bet**(round-2/3 被罚的 size 漂移) → **限 constituent_only**。
8. **未覆盖股**:选**中性/持基准权重**(增强指数保守 null;复用 `benchmark_relative` unscored→hold
   原语;勿全 UW=丢 neglected 溢价)。CSI300 内覆盖 >90%(2011+),全 A 仅 50-65%。
9. **保留行业+log 市值中性化**(覆盖带 size/glamour 倾斜)→ 残差信号。
10. **3000/调用 cap(doc 292)/ 实测范围 cap=5000** → 必分页 + coverage fail-closed。

## 5. 实操因子公式表(卖方金工/开源,provenance;agent B)

记号:股 s 日 d,FY1=前向财年;`C_d(np)`=staleness 窗口内跨券商**中位数** FY1 净利 consensus(万元);
`C_{d−k}`=k 日前同口径;`n_d`=d 日有 live FY1 估计的券商数。**全因子 = 个股时序修正(非 level)→
winsor 1/99% → 横截面 z → 行业哑变量+log 市值 OLS 残差中性化**(复用既有 `neutralize`)。

| # | 因子 | 公式 | 窗口 | 聚合 | 符号 | 出处 |
|---|---|---|---|---|---|---|
| 1 | **NP 修正动量** | `(C_d(np)−C_{d−k}(np))/|C_{d−k}(np)|` | k=**90d**(测 60/180) | 跨券商中位数 FY1 np | **+** | revision-drift 开源代码 verbatim(Number531/Legal-API) |
| 2 | EPS 修正动量 | `(C_d(eps)−C_{d−k}(eps))/|C_{d−k}(eps)|` | k=90d | 中位数 FY1 eps | **+** | nifty-50 factor_engine `_analyst_rev` |
| 3 | **修正扩散(breadth)** | `(N_up−N_down)/N_total`(各券商升/降自身 FY1 估计) | trailing 90d(经典 12m) | 计数净比,忽略幅度 | **+** | **canonical** Duke/Granite "Up vs Down FY1 ratio" |
| 4 | 目标价隐含收益 | `median_b(min_price)/close_d − 1` | 180d | 中位数目标价 | **+** | 常规;report_rc 字段=min_price |
| 5 | **评级上调净数** | rating→ordinal{强烈推荐5…卖出1};`Σ_b(rating_{b,d}−rating_{b,prior})` 或 `(N_upg−N_dng)/N_total` | 90d(事件衰减 60d) | 净 over 券商,先 ordinal 映射 | **+** | 星火《分析师评级上调 Alpha》 |
| 6 | 预测分歧度 | `std_b(eps_b)/|mean_b(eps_b)|`(FY1 eps 变异系数) | 180d,**需 n≥3** | std÷mean | **−** | 常规;Diether-Malloy-Scherbina |
| 7 | Δ覆盖广度 | `n_d−n_{d−k}` 或 `ln(n_d/n_{d−k})`(覆盖=trailing 180d 有≥1 FY1 估计) | 90d | 券商计数 | **+**(弱) | 海通覆盖定义 |
| 8 | SUE 式修正 | `(C_d(np)−C_{d−k}(np))/σ`,σ=d 日跨券商 FY1 np 估计 std | 90d | 中位数分子,std 分母 | **+** | SUE 形 `(Q−E)/σ`;华泰/腾讯云 |

**先实现 4-5 个(最标准/稳健)**:**#1 NP 修正动量**(最经典 PEAD-revision;np 覆盖最佳)+ **#3 修正
扩散**(全可归因 Duke/Granite,计数→抗单券商异常,与 #1 正交=幅度 vs 广度,**最佳组合**)+ **#5 评级
上调**(A 股事件 alpha;与数值修正正交)+ **#4 目标价隐含收益**(用不同字段=增量信息)+(tiebreak)
**#6 分歧度(−号,唯一负向 diversifier)**——仅当过 R4-4 |t|≥3 中性化门才留。

**共线性(stack 前剪)**:① **#1↔#2(NP↔EPS)ρ>0.8 → 二选一**(取 #1 NP,share-count PIT 坑少);
② **#1↔#8(drift↔SUE)高**(同分子,#8=#1/分歧度=#1 风险调整,非独立);③ **#1↔#3(幅度↔广度)
~0.4-0.6 = 好正交对,两个都留**;④ #6↔#7 经 n 相关;**#6↔size**(小盘高分歧)→ **必中性化否则成 size
proxy**(round-2 amihud→size 教训);⑤ #4↔#5 中等正(上调常伴 TP 升),够独立两留。
**数据稀疏护栏**:~1.49 券商/股/日 → 90d 窗 N_total 仍可能小(~5-15)→ **扩散/分歧度需 n≥3 否则
NaN fail-closed**,否则被 1-2 券商噪声主导 = constituent_only 内 factor breadth 的**约束瓶颈**。
**聚合**:朝阳永续按 券商影响力×时近 加权;report_rc 无影响力分 → **时近衰减中位数**(等权,避免过拟合
加权方案)。**窗口(90d)/中位数是 common practice 映射,须 R4-4 IC 扫描确认,非厂商 canonical。**

## 6. PIT-correct recipe 决策表

| 决策 | 选择 | 依据 |
|---|---|---|
| 可得时戳 | `report_date < d`,D+1 交易;剔 `create_time≫report_date` | §4.3 |
| staleness N | **90d 主/180d 稳健** | §4.1 |
| 对比滞后 Δ(k) | **90d 主/60/180 稳健** | §5 |
| 目标期对齐 | **FY1=最近前向 `YYYYQ4`,两端同规则**,处理跨年滚动 | 绝不跨目标年差分 |
| 券商去重 | canonicalize `org_name` 后每券商取最新 | 不重复计同一家 |
| consensus 统计量 | **跨券商中位数** + winsor;扩散/分歧度 **n≥3** | 抗异常 |
| 信号类型 | **个股时序修正/扩散,非 level/rating** | §4.6(决定性) |
| universe | **constituent_only**(CSI300,覆盖>90%) | §4.7 |
| 未覆盖处置 | **中性→持基准权重** | §4.8 |
| 中性化 | **行业+log 市值残差** | §4.9 |
| 目标价 | **`min_price`/close−1**(非 `tp`=利润总额) | §3 |
| 分页 | limit+offset 到短页 + coverage fail-closed | §4.10 |

## 7. R4_FACTORS 定稿(R4-3 实现;R4-4 诊断按 |t|≥3+共线≤0.7 剪 → R4_CARRY)

R4-3 **构建全集**(让 R4-4 数据决定去留,不预先假设剪枝):
- `np_rev`(NP 修正动量 k=90d)—— 头号
- `eps_rev`(EPS 修正动量 k=90d)—— 与 np_rev 大概率共线,R4-4 二选一
- `rev_diff`(修正扩散 (N_up−N_down)/N_total,90d,n≥3)
- `rating_chg`(评级 ordinal 净上调/扩散,90d;**先建 frozen rating→ordinal 映射,未知词 NaN fail-closed**)
- `tp_impl`(median(min_price)/close−1,180d;覆盖 ~30-50%)
- `disp`(分歧度 std/|mean|,180d,n≥3,**负向**)
- (备选)`cover_chg`(Δ覆盖广度,弱)

**符号一律从零在 train_val 验证**(A 股 Liu-Zhang 符号有别,铁律②);全走 R2-2 协议(中性化 |t|≥3 +
低共线 + 机制注册;弱则如实丢,同 SUE/动量/资产增长)。机制注册:revision/analyst-sentiment 类
**故意不入 governance EconomicMechanism enum**(同 R2/R3,晋升门 fail-closed until amendment,不动 enum)。

## 8. 与三轮 FAIL 的关系 + 正交性裁判(诚实)

三轮 FAIL=缺真正交 alpha(价值/质量/反转/SUE/应计均零成本财报衍生,净相关高、不够)。分析师修正是
**信息流(非价格流、非财报衍生)**,JKKL 证评级变化"与大量预测因子正交",CJL 证修正在控 size/BM/动量
后仍有 drift → 与既有 E/P、ROE/gpm、反转、低波**大概率独立**。**关键差异**:两项 A 股证据说效应**在
大盘段**(Lv 4.14% vs 1.74%;增长异常在高覆盖信息性预测最强)= 与 cap 加权 CSI300 基准**对齐**,正是
价值/反转/低波**失效**之处。**但非稳赢**:① A 股是 growth-forecast/level 异常、符号与美股有别 → 须从零
验符号,美股下修非对称(Womack −9.1%)未必移植到 long-only;② **覆盖衰减**(Gleason-Lee:高覆盖 CSI300
drift 更小)蚕食 alpha;③ 乐观/羊群偏差 → 只有净 consensus-drift 的变化干净;④ **短衰减**(次日)警告
月度+成本下捕获有限。**裁判:四轮最强 prior(大盘集中证据终于匹配基准),但非 slam-dunk;净超额系于
大盘 revision drift 能否扛住覆盖衰减+偏差过滤+成本。预期 DSR/PBO/SPA 仍是约束门,为"小正但不显著"
留余地,补不出就如实 FAIL + 下一轮。**

## 9. 红线 + 本轮新验证原则(待回写 CLAUDE.md «研究专项已验证原则»)

provenance-gated;LLM 仅文献调研、绝不进数值策略;数据源仅 Tushare 官方 SDK;不烧 test。
**新验证原则**:① report_rc `tp`=利润总额非目标价、目标价=min_price(字段须真值消歧,勿信社区文档);
② 分析师因子用**个股时序修正非 level**(A 股评级无区分度);③ 主场=constituent_only(覆盖>90%)与
胜出构造重合;④ A 股符号与美股有别 → 从零验证(铁律②);⑤ 扩散/分歧度需 n≥3 fail-closed。

## 10. 关键 URL(provenance)

学术:CJL1996 onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1996.tb05222.x · DMS2002
diether.org/papers/dms.pdf · Brav-Lehavy2003 wiley 10.1111/1540-6261.00593 · Womack1996 wiley
10.1111/j.1540-6261.1996.tb05205.x · JKKL2004 ssrn 291241 · Gleason-Lee2003 ssrn 370425 ·
McNichols-O'Brien ssrn 2813 · Ljungqvist ssrn 889322 · Kaplan-Martin-Xie ssrn 3213833。
A 股:Liu-Zhang2023 sciencedirect S0927538X23002639 · Lv2025 arxiv.org/abs/2502.20489 ·
Liu2024 arxiv.org/html/2411.08726v1。
公式/开源:Duke/Granite Up-vs-Down FY1 ratio(people.duke.edu/~charvey)· 星火评级上调 Alpha
(asset.quant-wiki.com)· 广发一致预期指标构建(asset.quant-wiki.com)· 海通选股因子系列 ·
Number531/Legal-API、nifty-50--multi-factor-alpha、market-cockpit(github,revision-drift 代码)。
