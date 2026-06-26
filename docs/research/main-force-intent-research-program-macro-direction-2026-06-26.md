# 主力意图大数据研究纲领 —— 宏观工作方向报告(交 Fable 5 接续)

> **本文定位**:**PROGRAM 级宏观方向**,不是实施 spec。回应 owner 2026-06-26 的认识论校正(「别让美股文献的三言两语预判 A 股;在海量真实 A 股数据中找规律」),把「研判主力意图」立为一个**大数据研究纲领**,并交 **Fable 5** 接续研究与开发。
> **配套文档**:`docs/research/main-force-intent-lowbase-transition-system-design-2026-06-26.md`(窄范围战术设计,= 本纲领的**第一条落地子流之一**,但下方 §2 的本土证据**重定了它的优先级**)。
> **方法过程**:codebase/数据侦察 + 两轮联网调研(第一轮偏英文学术 → owner 校正 → 第二轮 A 股本土证据,20+ 子 agent)+ codex 两轮对抗。**部分子 agent 撞限流**(见 §8 诚实边界),但覆盖足够定方向。
> **日期**:2026-06-26 · **作者**:Claude(Opus 4.8)· **状态**:未实施、未改代码、未改 governance。

---

## 0. 给 Fable 5 的一句话

把「主力意图」当成一个**可观测足迹的大数据研究问题**,不是一个要直接编码的玄学战术。**先建基础设施 + 严格竞技场,再让真 A 股数据回答 owner 的每个问题,并对每条假说承诺『过不了就报 FAIL』。** 本次调研最重要的、可直接指导你排序的发现是 §2 的**非对称**:主力足迹的可交易边,**RISK/EXIT/避险闸 远强于 ENTRY 择时 alpha** —— 这恰好契合 owner「绝对净盈 + 控回撤」判据,**所以先做风控/退出/避险闸,后做进场内核**。

---

## 1. owner 命题 + 我的认识论更新(写清楚改了什么)

### 1.1 owner 的命题(**采纳**,这不是玄学)
- A 股 ≠ 美股:**投机驱动**,题材炒作非散户能定;**所有真实大波段都是主力军/大机构拉起来的**;散户跟风套利、或被割、或在无逻辑波动中无法稳定盈利。这是**国情决定的客观结构**,主流共识。
- 要做的:在**海量真实 A 股数据**里,学会用**客观数据 + 消息面数据(注意时间对应 = PIT)**判断主力意图 —— 何时炒作什么题材/为什么、何时撤出、分批建仓减仓与洗盘的客观模式、何时转避险、避险题材是什么。
- 方法论铁律:**基于真实数据找规律,不靠固有认知或网络三言两语下『玄学/科学』的轻率判断。**

### 1.2 我的更新(**校正点**)
| 之前(第一轮,偏英文文献) | 现在(owner 校正后) |
|---|---|
| 用美股文献把主力假说**先验判为 folklore** | 主力现象**真实**;footprint 可否**可交易**是**开放经验问题**,由**真 A 股数据 + 严格竞技场**仲裁,不由文献预判 |
| 范围窄(低位叠加层 + 减持) | 范围放大为**主力足迹大数据纲领**(题材轮动/进出/吸筹派发/避险),含我曾降级的端点(资金流/龙虎榜/北向)—— 不是天真使用,而是**纳入研究、严格证伪** |
| 倾向少摄数据 | 倾向**建基础设施、扩 PIT 端点**,让数据说话 |
| **保留**:PIT + 反过拟合 + fills-aware 严格性 | **保留且强化**(owner 自己也要求「不靠玄学/轻率」=同一严格性);严格竞技场 = **真理仲裁者** |

> 核心态度:**affirm owner 的结构命题 + 纠正我的文献先验 + 让真数据决定 + 严格性不可旁路。**

---

## 2. 关键经验发现(A 股本土证据,recalibrated)—— 本次调研学到的真东西

> 第二轮专门偏 A 股本土/中文源(知网/券商金工研报/聚宽/BigQuant/雪球 + 新兴市场学术)。结论**没有推翻严格性,但纠正了"方向"并给出 owner 问题的诚实答案**。

### 2.1 ⭐ 头号发现 = 非对称:足迹的可交易边 RISK/EXIT >> ENTRY 择时
- **fade-the-hype 方向有强本土证据**(多篇同行评审,A 股原生):
  - salience/乖离反转:高乖离 **−1.30%/月**、低乖离 **+1.41%/月**,稳健于 lottery/sentiment/reversal/attention 控制(ScienceDirect S0927538X25003749)。
  - 异常成交量→反转(日/周/月,赢家组合反转;S1057521924001972)。
  - 涨停日「大户买、次日卖」→ 预测**长期反转**(账户级 + ST 5% 涨停自然实验,**因果级**;Chen-Gao-He-Jiang-Xiong, J.Econometrics 2019 / NBER w24014)。
  - T+1 机制 → 强短期**逆向**;A 股**只有日频动量且一周内反转**,**无中期动量**(Gao-Jiang-Xiong NBER w31839)。
  - 散户/attention 涌入(Baidu ASVI、MAX/彩票)→ 后续**完全反转**(Financial Innovation; MAX 低−高 ≈ +1%/月)。
- **拥挤度预测『崩盘概率』而非『均值收益/择时』**(载重结论,多 agent 独立汇聚):
  - PMO 因子拥挤对未来收益**显著负、但短期且快速衰减**(Applied Economics Letters 2024,A 股 2000–2023,建模卖空约束=制度正确)。
  - 跨市场:拥挤预测 reversal 因子崩盘概率 ~1.7–1.8×,**不预测均值收益**;「observable but efficiently priced」(arXiv 2512.11913;SSRN 3803954)。
  - **Feb-2024 微盘崩盘事后复盘:连顶级量化都承认『无法精确测量拥挤度以及时撤出』**(知乎年终盘点;stcn)= **可证伪的负面结论**。
- **risk/exposure 择时 > direction/return 择时**(MSCI + 新兴市场学术 + 美股 OOS 三方一致)。
- ➡️ **对 owner 判据(绝对净盈 + 控回撤)是好消息**:能稳定赢的正是**给暴露/回撤择时**(拥挤闸、vol-scaling、避险轮入、不追拥挤),这正是控回撤的引擎。

### 2.2 owner「何时撤出热门题材」的诚实答案
**用『拥挤极值 + 反转信号』当 REDUCE/EXIT/veto 闸,不指望它精确点顶。** 券商拥挤度三件套(成交集中度〔**须 float-cap 归一剔 size**〕+ 内部关联度 + 收盘价乖离)在 90–95 分位极值对应阶段高点(华泰金工「崎岖之路」自曝失败=可信度高);国君式 6 维拥挤 EXIT filter **回撤 49%→28%**(最可信部分,但 26% 收益是 in-sample 多参扫描产物)。**当风控/退出,不当收益引擎。**

### 2.3 owner「何时转避险 / 避险题材是什么」的诚实答案
**避险目的地 = 红利低波**(银行/公用/煤炭/钢铁/交运倾斜)是**最有据的 A 股下行防御者**:Jan-2024 微盘崩盘窗口 **中证红利低波 TR +5.93% vs CSI300 −5.94%**(真实 dated 事件收益,非拟合);长期 maxDD 16.87% vs CSI300 45.6%(上证报/CCMS)。**caveat:防御自身会拥挤**(end-2024 红利低波 85.5% 机构持有 + 单月纪录流入)→ 避险**regime-conditional,非免费对冲**;最稳的不是 macro timer,而是「**波动/拥挤 regime 闸 + 估值便宜锚(股息率分位)**」。

### 2.4 题材轮动 ONSET(炒作什么/何时进)= 难,WEAK/in-sample
行业动量仅日/月频(周频反转);券商两位数超额轮动模型**绝大多数 in-sample 单窗口、size 污染**(中信建投 +28.8%/73% 当营销丢弃)。**可 PIT-safe 用的是『政策主旋律资格』+ 主题动量 context,不是 onset 择时 alpha。**

### 2.5 政策/主旋律 = 真实但有 PIT 硬陷阱
顶层会议**事前漂移**真实大(会前 48h 平均 **+42bp t=2.22**;经济类政治局 **+52bp**;高不确定 **+91bp**;Pan-Peng SAIF)。**但最强效应挂在『日期事前不可公开』的经济类政治局上 → 把已实现会议日当已知信号=look-ahead。** PIT 安全编码 = 仅对**可预告集**(党代会/全会/两会/CEWC 节律)编 ex-ante 日期;经济类政治局只用「季节窗 / 距上次天数」作 PIT 特征。**且五年规划早年常以紧缩开局,『day-1 买规划主题』可能是负漂移陷阱**(Rhodium)。

### 2.6 概念成分 look-ahead = 主题研究的头号陷阱(≥3–8%/yr 假收益)
供应商常在票**涨完之后**才加进概念板块(成分对涨幅**内生**),比指数幸存者偏差更severe。**处理**(可直接实施):PIT in_date/out_date + **自建前向每日 roster 字节快照** + 公告/观测日滞后隔离 + 含死亡剔除名 + 不用「今天的申万/概念标签」当历史标签(申万 2021-07-30 改制,按版本分段 effective-date join)。**这正是要烙进 theme_mapping / 主旋律 gate 的纪律,与 marketdata_pit 字节存档同源。**

### 2.7 资金流/北向/龙虎榜 = 弱或已死(但纳入证伪,非天真使用)
- **北向**:pre-2024-08 强(AHVR **+1.94%/月 t=2.97**,CICC north_hold_prop IC 3.5%);**但日度个股披露 2024-08 已停 → live 不可复现**;**假外资**(He-Wang-Zhu NBER w30893,境内资金伪装,2018 改革后衰减)。→ **历史研究因子,不上 live;市场级 north_money 仅作粗 regime overlay。**
- **moneyflow**:符号在同花顺/东财/Tushare 间翻转;订单失衡无预测力(Shenoy-Zhang 2007)→ **不接入主路径**(若研究,须 orthogonalize + 仅小盘 + 从零验)。
- **龙虎榜**:净买仅短期(~52–57% 次日,快衰),**净卖更可预测** → 作排除/降权,非买入。

### 2.8 size/换手 混淆 = 贯穿所有信号的母陷阱(= 项目自己 round-1..4 死法)
反转/换手/MAX/拥挤/limit-up 全部集中微盘/壳区;**Feb-2024 微盘崩盘就是这个陷阱杠杆化后爆炸**(雪球敲入→基差→DMA 强平,小盘暴露解释最差量化基金 ~30% 跌幅)。**任何信号必须 size/行业中性化 + 删最小 30%(LSY CH-3 shell zone),否则就是重新发现小盘因子。**

### 2.9 本次调研诚实分级表(给 Fable 5 排序用)
| 等级 | 信号/用法 | 最强本土锚 |
|---|---|---|
| 🟢 **优先建测** | 拥挤度作**风控/EXIT/veto**(float-cap 归一,90–95 分位) | PMO[AEL2024] / 崩盘概率[arXiv2512.11913] / 国君回撤49→28% / Feb-2024 post-mortem |
| 🟢 **优先建测** | **fade-the-hype 反转**(salience/异常量/T+1/涨停后) | J.Econometrics2019(因果) / 多篇 ScienceDirect / NBER w31839 |
| 🟢 **优先建测** | **红利低波避险目的地** + 估值便宜锚 | +5.93% vs −5.94% @Jan-2024(dated 事件) |
| 🟡 谨慎(付账本债) | 政策事前漂移(经济类政治局/CEWC 前窗,季节窗编码) | Pan-Peng SAIF(in-sample) |
| 🟡 谨慎 | 风格切换 macro regime(期限利差/M1−M2/相对波动) | 西南 OOS 胜率 68.57–80%(窗口敏感) |
| 🟡 谨慎 | 低位结构转折**进场叠加层**(子设计文档) | 须 §配套文档 ablation/placebo;先验中等 |
| 🔴 证伪台账/不建 | 炸板=吸筹 / 封单 / 隐性吸筹避龙虎榜 / 北向 live / moneyflow / 券商高超额轮动 P&L | 玄学或数据已死或 in-sample 营销 |

### 2.10 ⭐ 吸筹/派发/洗盘足迹专题(owner 2026-06-26 点名补;2 聚焦 agent 本土实证)
> owner 最关心的一块(**分批建仓减仓 / 洗盘的客观模式**)。结论**不推翻、反而强力印证 §2.1 非对称**,并给出可证伪的清晰边界。

**① 决定性诚实点:吸筹 vs 派发在日频盘面 ex-ante 几乎无法区分。** 主力**洗盘(看涨)**与**出货/破位(看跌)**的日频足迹**几乎同形**,只能事后贴标签。本空间最严谨工件 **民生金工《威科夫概率云》** 也**不声称** ex-ante 可分;其无泄漏版(WSO 直接映射)**IC 仅 ~0.07/ICIR 1.11**(16.78% 年化但作选股器极弱);更高的 WSS 25% 年化 = 样本内按 ≥55% 胜率挑形态序列 + 评测窗重叠 = **泄漏,须重折**。"低位吸筹"(集中度↑ / 缩量企稳 → 预判拉升)= **NULL → 证伪台账**(集中度**方向无关**,"低位"是 ex-post 标签)。

**② 主发现:所谓"吸筹/派发足迹"因子绝大多数是 CGO/处置效应/反转/size 换皮,非独立主力探测器。** 中金筹码因子(`doc_vol_pdf90_std` ICIR 1.52、L/S >28%)**"主要呈反转特征"**;筹码收益 = 反转型;广发筹码 = **CGO(资本利得突悬)= 换手率半衰期加权,根植处置效应**。CGO 有 A 股正向前向证据**但控动量后显著性消失**(动量本身≈处置效应)。→ 筹码/CGO 须**对动量+反转+size 正交化后看增量**,否则等于重新发现反转。

**③ 可诚实交易的切片(全落 RISK/EXIT 侧,再证非对称):**
- **理想振幅因子**(高价态振幅−低价态振幅):IC −0.067/ICIR −2.97/L/S 23.3%,但是**负向/退出因子**(高振幅→低未来收益),与"暴力洗盘后拉升"看涨民间叙事**相反**;size-中性化声明**未经独立复现核实** → 须用我们自己 size 中性化+删最小30% **重测**,作**退出叠加非看涨进场**。
- **高位派发退出叠加**(放量滞涨 + 获利盘饱和 + 量价/OBV 背离):顶部方向**无歧义**,契合 EXIT。
- **筹码集中度作 conditioning/质量过滤**(非择时器):华创"双重筹码集中"18%/IR 0.99(2009-19,**全 in-sample + 基本面叠加非纯筹码**)→ 作宇宙质量/健康过滤(同 QGR-3 ⑧ 慢腿结论);茅台 winner_rate 矛盾 caveat 仍在。

**④ 新增证伪台账(玄学/事后/可对敲欺骗):** 吸筹 vs 出货 ex-ante 可分(日频)= **NULL**(连民生都不声称);HMM/changepoint 相位定标→预测前向 = **NULL + smoothed-state 泄漏**(仅 filtered/online 态诚实,无人证 A 股有效);主力/大单净流入作独立预测 = **WEAK/perverse**(可被**对敲/自买自卖**制造假净流入 + 基于已成交是后视)→`moneyflow` 继续**不接入信号路径**;VSA"努力 vs 结果" = A 股**无严谨检验,NULL**;缩量企稳作 timer = WEAK(仅作宇宙健康过滤)。

**⑤ 一句话(给 owner)**:主力建仓周期是**真实的结构叙事,但不是 PIT 可交易的相位信号** —— 吸筹与派发在日频 ex-ante 近乎同形(连最严谨的民生 Wyckoff 无泄漏版也只有 IC~0.07),唯一诚实可提取的是 (a) 振幅结构作 size-核验过的**负向/退出**因子 +(b) 筹码作 conditioning/质量过滤,两者都再次印证"边在 RISK/EXIT 侧,不在吸筹进场"。

出处:民生金工 威科夫概率云(sina inezhcf7966112);开源证券 振幅因子隐藏结构(BigQuant w5WH1P01Bl);华创 双重筹码集中;中金/广发 筹码(QuantsPlaybook hugo2046 复现);Grinblatt-Han CGO(SSRN 298258);股票复盘网 洗盘vs出货(无 ex-ante 自承)。

---

## 3. 把 owner 的问题翻译成可研究命题(研究矩阵)

| owner 问题 | 客观信号假说(PIT) | 数据 | 本土先验 | 用法 | 门 |
|---|---|---|---|---|---|
| 炒作什么题材?为什么? | 政策主旋律资格 + 主题动量 context | ths/dc_member(PIT roster)+ index_classify + 政策日历 | 资格=可;onset alpha=弱 | 资格/上下文,非排序 | 概念 look-ahead 处理 + 政策 PIT 编码 |
| **何时撤出热门题材?** | **拥挤极值 + 反转 blow-off** | daily/换手/成交集中度/乖离 + limit_list_d | **🟢 强(作 EXIT)** | **REDUCE/EXIT/veto 闸** | float-cap 归一 + size 中性化 + 竞技场 |
| 分批建仓减仓/洗盘?(详 §2.10) | 进场侧:吸筹结构;退出侧:派发结构 + 理想振幅负因子 | daily + cyq_perf/chips | **进场侧 NULL/玄学(ex-ante 不可分);退出侧 MIXED** | 退出叠加 + 筹码作 conditioning,**非进场择时** | 正交化动量/反转/size + 独立 size-中性化复测 + filtered-state 才诚实 |
| **何时转避险?避险是什么?** | **红利低波 destination + 波动/拥挤 regime 触发** | 红利低波指数 + 波动 + 拥挤 | **🟢 目的地强;触发中等** | **避险轮入闸 + 估值锚** | regime PIT(filtered)+ 防御自身拥挤 caveat |
| 主力进场(外资/换帅)? | 事件研究(top10_floatholders/stk_managers on ann_date) | 待摄 | 弱(滞后衰减/符号负) | 证伪台账上下文 | 日历组合事件研究 + 可交易 CAR |
| 减持/派发压力? | 减持硬排除 | stk_holdertrade(待摄) | 🟢 净向负(最干净事件) | **硬排除** | 确定性 PIT,不排序 |

---

## 4. 大数据分析基础设施蓝图(owner 的「基础设施」要求)

> 五层 + 复用/新建。**贯穿全层 = 严格竞技场作不可旁路管线。** QuantMind 已有相当地基。

| 层 | 内容 | 复用 / 新建 | 参考设计 |
|---|---|---|---|
| **L0 PIT 字节存档** | 两时间戳(`knowledge_date` 何时可知 / `effective_date` 描述何期)+ as-of 读 + 复权 as-of pin + 幸存者无偏 + restatement vintage | **复用** `marketdata_snapshot`(content-addressed + checksum + coverage,~29GB/23 端点);**扩端点**(§7) | Qlib(`date/period/value/_next` 链表 + `P()` 算子)/ Zipline Pipeline(`asof_date` vs `timestamp` + searchsorted)/ ArcticDB(bitemporal `as_of` + snapshot) |
| **L1 特征工厂** | 特征族:①微结构(量价/换手/振幅)②资金流(moneyflow/北向/融资/大宗,**全带数据死/翻转 caveat**)③持仓(十大股东/股东人数/龙虎榜席位)④筹码(cyq)⑤事件(减持/增持/回购/换帅/业绩)⑥主题轮动(成分/拥挤/广度)⑦NLP | **新建**(部分散在 screening/factor_lib);每族 **PIT + size 中性化纪律** | factor_lib + neutralize 扩展 |
| **L2 足迹/制度检测** | changepoint / HMM / 聚类 / 异常检测;拥挤度构造;吸筹派发足迹 | **新建** | **⚠️ 用 FILTERED(单边)非 SMOOTHED 概率**(hmmlearn `predict_proba`=smoothed=泄漏;statsmodels `filtered_marginal_probabilities`)/ BOCPD / PELT / CUSUM |
| **L3 知识图谱** | 政策→主题→个股→资金,双时态 + SUPERSEDES;实体链接 新闻→股→主题 | **复用规划**(R 阶段 SQLite+NetworkX 双时态) | 金融 KG provenance |
| **L4 PIT 新闻/公告语料** | 时间戳化历史公告/新闻语料(**当前最大缺口**:仅 ~2 周 live feed,无历史) | **新建(务实获取)** | 交易所公告/巨潮 + 时间戳纪律避泄漏 |
| **贯穿:严格竞技场** | CPCV(按日期分组)+ DSR/PBO/SPA/Romano-Wolf/BHY + fills-aware + **非清零 trial 账本** | **复用** `cpcv/trial_ledger/stats_disclosure/honest_gates/gate_backtest` | López de Prado 全套 |

**会静默毁掉研究的 top-3 基建错误**:① **泄漏**(smoothed-state 当特征 / 概念成分回填 / 复权未 as-of pin / 转换在全样本拟合)② **幸存者偏差**(非 PIT 宇宙、丢死亡退市名)③ **vendor 数据不稳/已死**(moneyflow 符号翻转、北向 2024-08 断、cyq 模型派生)。**对策**:future-NaN 投毒测试 + 自建前向 roster 快照 + PIT 宇宙含退市名 + 字节存档 fail-closed。

---

## 5. 研究方法学(严格性即产品;owner 同此)

- **时序 MASK as-of「伏击」模拟器**(配套设计文档 §6 已详述):as-of T−1 收盘 → T+1 入场(若可成交)→ 持有 5–10td → 三重屏障可交易标签 + 出场首个可成交价 + 丢一字板/封板。
- **晋级判据 = 绝对净盈 + MDD≤8%**(冻结);命中放量大涨/拥挤退出命中率 = **诊断(fills-aware 分母)**,不作晋级门。
- **反过拟合四门 + 非清零账本 + 制度分层 + look-once 前向** = 真理仲裁者。
- **把严格性做成 pipeline 不可旁路阶段**:任何策略产物不过 CPCV/DSR/PBO/fills-aware 不得晋级『候选』。

---

## 6. 分阶段纲领路线图(PROGRAM 级;每阶段 owner/codex gate;sim 暂停贯穿)

> 排序原则 = **先验最强 + 最契合控回撤的先做**(§2 非对称)。

- **P0 基础设施扩建**:L0 扩 PIT 端点(§7)+ L1 特征工厂骨架 + L4 新闻语料务实方案 + future-NaN 泄漏门。
- **P1 足迹研究批次**(每条假说:从零验 + 竞技场 + 证伪台账):
  - **批 A(先做)= RISK/EXIT 闸**:拥挤度(float-cap 归一)+ 反转 blow-off → REDUCE/veto。先验最强、最控回撤。
  - **批 B = 避险轮动**:红利低波 destination + 波动/拥挤 regime 触发 + 估值锚。
  - **批 C = ENTRY 内核**:低位结构转折进场叠加层(配套文档;ablation vs 纯突破 placebo)。
  - **批 D(弱先验,证伪台账)= 事件/主题**:政策漂移(PIT 季节窗)/ 概念 look-ahead 处理 / 减持硬排除(摄 stk_holdertrade)/ 外资·换帅事件研究。
- **P2 整合 = 双线架构**:全市场量化选股闸门(进场)+ 持仓监控/退出(拥挤/破位/避险轮出)+ 知识图谱 + 自进化多 agent。
- **P3 前向确认 → go-live**:git 冻结 + look-once 真前向 + 真管线 shadow replay;**sim 解冻 owner-gated**。

---

## 7. 数据扩摄优先级(只增量、PIT 字节存档、owner-gated;同 K-001 纪律)

| 优先 | 端点 | 用途 | 备注 |
|---|---|---|---|
| 1 | `stk_holdertrade`(减持) | 硬排除 | 最干净事件,确定性 |
| 2 | 概念成分 PIT(`ths_member`/`dc_member` 或自建每日 roster 快照) | 主题 look-ahead 修复 | **字段须对自己 token 实测**;宁可自建前向快照 |
| 3 | 红利低波/低波指数 + 成分 | 避险目的地 | 批 B |
| 4 | `top_list`(龙虎榜) | 证伪隐性吸筹 + 净卖降权 | 研究/负向 |
| 5 | `top10_floatholders` / `stk_managers` | 事件研究上下文 | 弱先验 |
| — | `moneyflow` / `hk_hold`(北向) | **不上主路径**;北向仅历史研究 | 翻转/已死 |

---

## 8. 诚实边界(给 Fable 5 标注待深挖处)

- **✅ 已补(2026-06-26,见 §2.10)**:owner 点名的『分批建仓减仓 / 洗盘 / 筹码足迹』专题已用 2 聚焦 agent 补齐 → **结论 = 吸筹进场检测 ex-ante NULL/玄学(连民生 Wyckoff 无泄漏版也只 IC~0.07),唯一诚实切片在退出侧 / 振幅负因子 / 筹码 conditioning,印证 §2.1 非对称**。剩余可再深挖(非阻塞):**filtered-state(在线 HMM)相位检测的诚实实现** + **理想振幅因子的独立 size-中性化复测**(券商 size-neutral 声明未经复现)。
- **(原限流 caveat)** 本次另有部分基建/事件类子 agent 撞限流/session-limit(reset 2pm Asia/Shanghai),覆盖足够定方向;深实施细节由 Fable 5 续。
- **未解**:概念板块**专属** look-ahead 幅度无源直接量化(3–8% 是指数级);**PIT 新闻语料获取**仍是最大基建缺口;北向死数据无 live 替代;cyq_perf 模型派生存疑(茅台 winner_rate 矛盾)。
- **未独立核验**:多个 t-stat/样本期来自搜索摘要(ScienceDirect/知乎/BigQuant 多页 403 或 JS 渲染);券商研报无 DSR/PBO/成本净/真 OOS。**一律以我们自己 PIT 字节复跑为准**(QuantsPlaybook GitHub 有华富净新高 + 华西量价代码可作复跑底料)。
- **codex**:本纲领尚未经 codex 对抗(配套窄设计文档已经两轮);建议 Fable 5 接续时对本纲领 + 批 A spec 跑 codex 前置门。

---

## 9. 红线合规

全留:永禁真实下单 / 飞书人工 / 127.0.0.1 / LLM 不写决策(新闻=evidence-only,事件「参与选择」即须毕业进量化层付账本债)/ RiskEngine 纯函数 / 单一构造点 / PIT 可复现 / ≤5 持仓 / 排除四件套 / 改判据不清零 / 离线 / FAIL 报 FAIL / codex 前置门 / 改决策边界先落 amendment。**绝不**碰 backend value-sleeve 域;**绝不**接入 moneyflow 主路径;**绝不**把「主力意图」当产品宣称(= 主力意图假说,可交易内核诚实命名)。

---

## 10. 给 Fable 5 的接手清单(具体)

1. 读:本纲领 + `main-force-intent-lowbase-transition-system-design-2026-06-26.md` + `quant-first-gate-rearch-plan-2026-06-21.md` + `data-inventory-marketdata-pit-2026-06-21.md` + `plan.html#session-log`。
2. **第一刀**:P0 基础设施(L0 扩端点 + future-NaN 泄漏门)+ **批 A = RISK/EXIT 拥挤闸**(先验最强、最契合控回撤)。
3. 优先补 §8 标注的**吸筹/洗盘本土实证**深挖(限流未尽)。
4. 每条假说:从零验 + 竞技场 + 证伪台账 + 承诺报 FAIL;size 中性化 + 删最小 30% 必做。
5. 严格性不可旁路;codex 前置门;改决策边界先落 amendment;不碰 value-sleeve;sim 暂停。

---

## 11. 关键出处(provenance-gated;本次 A 股本土 + 第一轮 + 基建)

**A 股本土足迹/轮动/避险**:Chen-Gao-He-Jiang-Xiong《Daily Price Limits and Destructive Market Behavior》(J.Econometrics 2019/NBER w24014,涨停买-次日卖→反转,因果);Gao-Jiang-Xiong-Xiong《Daily Momentum and New Investors》(NBER w31839);salience 反转(ScienceDirect S0927538X25003749);成交量反转(S1057521924001972);PMO 拥挤(Applied Economics Letters 2024);crowding 崩盘概率(arXiv 2512.11913;SSRN 3803954 Kang-Rouwenhorst-Tang);红利低波避险(上证报/CCMS 2025-12-16);LSY《Size and Value in China》CH-3 删最小 30%(JFE 2019);北向 AHVR(Liao-Tang-Xu IJFE 2024)+ 假外资(He-Wang-Zhu NBER w30893);政策事前漂移(Pan-Peng SAIF《Top Government Meetings in China》);Feb-2024 微盘崩盘复盘(中国基金报/MSCI micro-cap crowding quick-take)。
**反过拟合/PIT 基建**:López de Prado《AFML》(CPCV/DSR/PBO/triple-barrier/sample uniqueness);Bailey-López de Prado《Deflated Sharpe》《PBO》;Harvey-Liu-Zhu(NBER w20592,t>3);Qlib PIT(date/period/_next/P 算子);Zipline Pipeline(asof_date/timestamp);ArcticDB(bitemporal as_of/snapshot);HMM filtered vs smoothed(statsmodels/hmmlearn);Dwork et al.《reusable holdout》(Science 2015)。
**codex 对抗**:配套窄设计文档 Round-1 session `019efea3-...`(gpt-5.5 xhigh)+ Round-2 收敛;原文 scratchpad `codex_r1_out.txt`/`codex_r2_out.txt`。

---

## 12. 新 session 接手协议

读完本文即可接手。**下一步指针**:owner 已定『写宏观方向 + Fable 5 接续』→ Fable 5 从 §10 清单起步,第一刀 = P0 基础设施 + 批 A RISK/EXIT 拥挤闸;对本纲领跑 codex 前置门;补 §8 吸筹/洗盘本土实证。**纲领级原则**:affirm owner 结构命题 + 真数据决定 + 严格性不可旁路 + FAIL 报 FAIL。
