# 主力意图研究 · 后续行动规划大纲(system roadmap outline)

> **定位**:**大纲(非实施 spec)**。回应 owner 三步法第二步 —— Claude 起草 + codex 对抗,双方无异议后才进第三步(EnterPlanMode 详细计划 + 科学评价协议)。
> **判据基础**:`docs/decisions/qgr-criterion-rebar-amendment-2026-06-27-avoid-top-dynamic-exit-swing.md`(弃 MDD≤8% 硬门 → 避顶部 + 动态退出 + 做T + 短窗稳定可观净盈;反过拟合四门绝不放宽;做 a 不做 L2;LLM 综合研判 evidence-only)。
> **上位**:macro-direction §2.1 非对称(边在 RISK/EXIT 侧)+ §2.10 吸筹派发诚实分级 + lowbase §6 时序 MASK 伏击模拟器 + qgr-2 评测口径冻结 + qgr-plan §4 两层评测 A/B。
> **日期**:2026-06-27 · **作者**:Claude(Opus 4.8)· **状态**:**R1→R2 修订稿**(已吸收 codex R1 全部 17 条;详 §10)· 未实施、未改代码、未改 governance。
> **方法主轴(owner 强调)**:**重视真实数据测试 + 大数据分析** —— 在海量真 A 股 PIT 数据找规律,不靠固有认知/网络三言两语;每条假说从零验 + 进竞技场付账本债 + 承诺报 FAIL。

---

## 0. 框定:从「找控回撤的 overlay」转向「装配端到端可盈利系统」

三刀实证已坐实**结构墙**(QGR-4 买入集 veto / B1 regime de-risk / B2 永久防御 sleeve 全 FAIL on 旧 8%):**rotation-only 冻结竞技场结构上无法表达控回撤的 overlay**(SELL 只来自轮动 ≤1/日,`protective_stop`/`hard_exit` 保护在位者不卖 → 满仓 by construction;8% 在 ≤5 槽集中股票书 + A 股 40–55% 个股回撤下结构不可达)。

owner 据此**重定判据**(非研究失败)。本大纲根本转向:

| 维度 | 旧(三刀) | 新(本大纲) |
|---|---|---|
| 目标 | 找一个能把 MDD 压到 8% 的 overlay | 装配**端到端系统**,验证它**规则驱动持有期稳定可观净盈** |
| 评测主指标 | 净 P&L + **MDD≤8% 硬门** | 规则驱动持有期**绝对净盈** + **「不被挂山顶」硬门** + MDD 仅披露 + 做T 增益 |
| 卖出路径 | rotation-only(arena 唯一表达) | **忠实建模 live 双卖路径**:Line-1 轮动 + **Line-2 确定性 EXIT** = arena 表达不了、live 真有的那条路径 |
| 严格性 | 四门 | 四门**完全不放宽**(只放宽了 MDD) |

> **核心认知 + 头号风险(codex R1-#1/#2/#3,合并写在最前)**:三刀 FAIL 的根因是 arena 不含 Line-2 盘中确定性 EXIT 路径。**本大纲中枢 = 建一个忠实建模该路径的端到端模拟器**。但 codex 指出这恰是**头号自欺源**:① 把 entry/rotation 路径 byte-exact 核对冻结引擎,只证了**无争议的那半**;EXIT/做T 路径恰是无法对照冻结引擎、能藏调参的那半;② 「忠实移植 live Line-2」对 X1 避顶部是**循环论证**——避顶部派发 EXIT 是**新信号,live 根本还没有**;③ 「避顶部改善」可能**纯是机械减暴露**(B1/B2 已证减暴露机械改 P&L/MDD),不是 alpha。**故本大纲把『EXIT 执行契约先冻结(§6.1)+ EXIT-专属 placebo(§4.3)+ P&L 四分解(§4.4)』升为不可旁路前置,而非事后补丁。**

---

## 1.(a)系统组件端到端组装蓝图 —— 每组件 输入/输出/红线

> 全市场 ~5000 票 → 量化第一选股闸(ENTRY)→ 持仓/退出层(EXIT)→ ≤5 槽轮动账户。全确定性、离线、PIT;**研究/评测路径零 LLM**;LLM 仅 **live** evidence-only(§1.4)。

### 1.1 ENTRY 侧(选股闸,已大半建)

| ID | 组件 | 输入 | 输出 | 状态 | 红线 |
|---|---|---|---|---|---|
| **E1** | 排除四件套 + universe 规则 | 全市场 daily/basic | 合格宇宙(禁 ST/科创/北交/可转债;新股≤30td/次新≤180td/20d额<¥2亿/价>¥500) | ✅ 已建 `screening` | PIT;含退市名防幸存偏差 |
| **E2** | 主旋律资格 tilt | `theme_mapping`(政策→主题→申万L3,PIT `effective_from`)+ `ths_index`/`index_classify` | 主题资格标 + 拥挤排除;**资格非排序** | 🟡 AF-001 已建,QGR ⑧ 主旋律**维度未实施** | 政策发布日 PIT(防 hindsight);成分**自建前向 roster 字节快照**;申万改制按版本 effective-date join;**严禁** LLM 剪 universe/否决板块 |
| **E3** | 容量/流动性闸 ≤10% ADV + 预算分层 | `avg_amount_20d` + 本金档 | 可负担集(Micro<¥2k 仅 ETF / Small / Normal≥¥10k) | ✅ 已建 `screening`/`budget` | harsh-fill ≤10% ADV |
| **E4** | **减持硬排除** | `stk_holdertrade`(减持,**待摄,owner-gated**)on `ann_date` | 硬排除集(确定性 PIT,**只排除不排序**) | ⏳ 待摄取 | 仅排除;不进 evidence-only;付账本债 |
| **E5** | **排序权威 = 快腿幸存因子** | `{rev_1d, max_5d, turn_spike}`(中性化 survivors) | buy-score 排名 → Top-N | ✅ QGR-3 已验 | registry-signed 符号;新排序因子先付账本债 + 过 ablation |
| **E6** | **底部确认门** | `bottom_confirmation` core-4(缩量/守支撑/非困境PIT-ST/质量地板)+ cyq 成本带 | 健康筑底 veto(**作宇宙质量/健康过滤器,非择时排序**) | ✅ 已建;QGR-3 证 dip 池内择时弱(t≈+1.4) | 不接飞刀;不作排序权威 |
| → | **入场候选** | E1∩E3∩¬E4 → E5 排名 → E6 过滤 → E2 tilt | **≤5 入场(close T 决策 → T+1 可成交)** | — | — |

### 1.2 EXIT/持仓侧(待建,本大纲核心)

> **执行时序铁律(codex R1-#6)**:所有 EXIT/做T 信号用**日频 EOD 特征**(OBV/放量滞涨/日频 cyq 收盘后才齐)→ 信号 as-of **close T → 下单 T+1**(**绝非当日成交**);T+1 若跌停/停牌**不可卖** → **挂单排队 + 累计被套 MTM 损失**(codex R1-#8:live 卖不掉就是被套,**绝不丢弃**)。

| ID | 组件 | 输入 | 输出 | 状态 | 红线 |
|---|---|---|---|---|---|
| **X1** | **避顶部派发 EXIT** | 放量滞涨 + 获利盘饱和(cyq,§3 raw 重算)+ OBV/量价背离 + 拥挤极值(batch-A `ideal_amplitude_20d`) | 持仓退出/减仓触发(**只增卖压,永不松止损**) | ⏳ 待建(下一刀) | **「左尾风险假说」非「方向无歧义」**(§2);size/行业中性化(held-book 条件,§3);付账本债 |
| **X2** | 破位/止损/thesis-break | 价格破关键位 + RiskEngine 14-check + thesis 量化失效 | 确定性 SELL | ✅ live 已建 | 确定性派生,不经 LLM |
| **X3** | **做T overlay** | 持仓 + 日频低吸高抛信号(确定性) | T+1 摊薄买卖(底仓地板,**守 T+1**;**worst-case 日内序列,非幻想成交**,§4.5) | 🟡 `value_swing` 骨架已建(env-OFF) | 守 T+1;**日频做T = 保守下界**;真做T 须日内数据(§9 决策点);付账本债 |
| **X4** | **动态退出整合** | X1∪X2 触发 + 持有状态 | **规则驱动持有期**(持有到触发,**非固定 horizon**)→ SELL | ⏳ 待建(整合刀) | 确定性规则派生卖;持有期内缩量回调可接受 |
| **X5** | LLM 综合研判(**仅 live**) | 客观信息(量/筹码/事件)+ evidence_context | **evidence-only / advisory** 盘后复盘 | live Phase W 已建 | **研究/评测零 LLM;live LLM 不写决策**,确定性规则派生实际卖/做T(§1.4) |

### 1.3 组装顺序(依赖)

```
E1∩E3 合格宇宙 ─┬─ E4 减持硬排除 ──┐
                └─ E2 主旋律资格 tilt ┘
                                      ▼
                       E5 快腿排序 → E6 底部确认过滤 → ≤5 入场(close T→T+1)
                                      │
                                      ▼  持有期(规则驱动,非固定 horizon)
                       X3 做T overlay(worst-case 日序)─┐
                       X1 避顶部 EXIT(close T→order T+1)┼─ X4 动态退出整合 → SELL(首个可成交价/被套则排队)
                       X2 破位/止损 ─────────────────────┘
                                      │
                                      ▼  (仅 live)X5 LLM evidence-only advisory
```

### 1.4 LLM 角色硬切分(codex R1-#15,消除内部矛盾)

amendment Q1「让大模型基于客观信息综合研判何时卖/做T」与本大纲「LLM evidence-only」的统一裁决:
- **研究/评测路径 = 零 LLM**(确定性规则唯一派生卖/做T 信号;LLM 不进 PIT/评测/账本任何环节)。
- **live 路径 = LLM 仅 evidence-only / advisory 盘后复盘**(展示层 `evidence_context`,前缀化);**确定性规则**派生实际卖/做T 订单。
- **裁决**:任何 LLM 派生字段若「参与卖/做T 选择」,即须**毕业进确定性量化层、付同样 PIT/账本/DSR 债**(macro §9 / lowbase §4.3 硬切分)。本大纲所有 C-刀评测**不含任何 LLM**。

> **红线合规自检(全留)**:永禁真实下单 / 单一构造点 / RiskEngine 纯函数 / PIT 可复现字节存档 / ≤5 持仓 / 排除四件套 / 不买涨停不卖跌停 / 做T 守 T+1 / **不接 moneyflow 主路径** / 北向仅历史 / **不做 L2** / 不碰 backend value-sleeve(AF-*)/引擎字节/既冻结面板 / 改判据不清零 mining 债 / 改决策边界先落 amendment / codex 前置门 / FAIL 报 FAIL / sim 暂停 / push·摄取·live 激活 owner-gated。

---

## 2.(b)每组件信号假说 + 从零验路径 + 诚实分级

> 每条:**预登记假说 → 从零验符号/显著性(不事后翻供)→ 进竞技场付账本债 → 承诺报 FAIL**。分级沿用 macro §2.10。

**避顶部方向的诚实降级(codex R1-#4,关键)**:macro §2.10① 自己说**吸筹(看涨洗盘)vs 派发(看跌出货)日频 ex-ante 近乎同形**。故「放量滞涨+获利盘饱和+OBV背离」**既可能先于派发,也可能先于洗盘后拉升**(owner 恰想持有穿过的那种)。→ **避顶部 EXIT 降级为「左尾风险假说」,不再声称「方向无歧义」**;从零验须同时报:**条件前向收益/左尾 @1/3/5/10/20td + 错失拉升成本(missed-rally)+ 误卖率(false-exit)+ 卖后再入机会损失**,而非只报「派发命中」。这是避顶部到底**帮还是伤**的真正裁判。

| 组件 | 信号假说(PIT) | 从零验路径 | 诚实分级 | 关键陷阱守护 |
|---|---|---|---|---|
| **X1 避顶部 EXIT** | 高位放量滞涨/获利盘饱和/OBV背离/拥挤极值 → 前向**左尾肥**(**左尾风险假说,非择顶**) | IC 从零 + 崩盘概率条件(top-decile vs rest 配对尾Δ,\|t\|≥3)+ 正交化 vs carry/QGR/reversal(\|corr\|≤0.7)+ size/行业中性化(held-book 条件)+ 删最小30% + **missed-rally/false-exit/再入损失** | 🟢→🟡 **左尾有据,但帮/伤须 净 trade-off 实证**(batch-A A1/A2 PASS 仅证 GROSS 尾部,**非** EXIT-on-held 净盈) | 拥挤崩盘概率 ≠ 均值择时;buy-set veto ≠ 持仓 EXIT(QGR-4 教训);吸筹/派发同形 |
| **X3 做T** | 机械低吸高抛在持仓内摊薄成本 → 更低成本 | 端到端模拟器内测**做T增益**(worst-case 日序,inventory-feasible)vs **no-do-T baseline + 同频随机择时做T placebo** | 🟡 机械无方向歧义**但日频粗粒度 = 保守下界**(无日内,不假设 low 在 high 前) | 守 T+1;min 佣金 floor(§4.5);幻想成交(codex R1-#7) |
| **E4 减持硬排除** | 减持公告 → 负漂移 + 崩盘预测 | 事件研究(`ann_date` PIT,日历组合,可交易 CAR)+ 条件前向;硬排除不得过度降净盈 | 🟢 **有据**(最干净派发硬信号,确定性) | `ann_date` 非披露泄漏;只排除不排序 |
| **E2 主旋律资格** | 主题 tilt **OVER 非主题 baseline** 有 IC 增量 | 主题 OVER baseline IC 增量 + 政策发布日 `effective_from` PIT + baseline 面板证增量 | 🟡 **谨慎**(资格可,onset alpha 弱;hindsight 头号风险) | 概念成分 look-ahead = 自建前向 roster 字节快照 |
| **E6 底部确认** | 缩量企稳/守成本带 → 健康筑底(非洗盘) | 条件前向收益区分度(dip 池内);作过滤非排序 | 🟡 **谨慎**(QGR-3 证 dip 池内择时弱 t≈+1.4) | 「低位」是 ex-post 标签 → PIT 客观代理 |
| 洗盘 vs 派发日频判别 | ex-ante 可分 | — | 🔴 **NULL,不建**(民生 Wyckoff 无泄漏版 IC~0.07;须账户级数据=拿不到;**做 a 不做 L2**) | ex-post 贴标签陷阱 |
| 大资金占比 / moneyflow / 北向 live | 主力净流入 → 正前向 | — | 🔴 **弱/死,不接主路径** | 符号翻转/可对敲伪造/北向2024-08断更 |

---

## 3.(c)大数据分析方法 —— 在海量真 A 股 PIT 数据找规律

> owner 校正核心:**别用网络三言两语/固有认知预设阈值,让海量真数据说话。** 每个 EXIT/做T 假说先做**大规模发现式条件研究**,再固化阈值进竞技场。

1. **数据规模**:全市场 ~5000 票 × 2015–2026 PIT 字节存档(`data/marketdata_pit/`,~29GB/23 端点,content-addressed + checksum + coverage);**禁重下,只增量**;新端点(`stk_holdertrade`)owner-gated 摄取同 K-001 纪律。
2. **发现式条件研究(非 hard-code 阈值)**:对 X1 避顶部,不预设「放量滞涨=量比>X 且涨幅<Y」,而在全 panel 上做横截面 IC + 分位 + 崩盘概率条件 + 3 子周期稳定性(batch-A 方法论推广)。阈值/符号**从零提取**。
3. **发现≠免费:4 路数据切分(codex R1-#10)**。发现步骤本身是大规模模型选择,故强制显式切分,各司其职、互不串用:
   - **discovery** → 仅定符号/候选阈值族;**calibration** → 锁定**单一**选择(一组阈值,不再扫);**validation(train_val held-out)** → CPCV + 四门评测;**sealed test / look-once** → 最终判定(owner-gated)。
   - **非清零账本计入**:所有阈值网格 + 特征变体 + **模拟器每次改动** + **被丢弃的假说**全 append(改判据不清零)。
4. **母陷阱防御(防 round-1..4「越做越小盘」死法)= 强制 + held-book 操作化(codex R1-#9)**:
   - **size/行业中性化** 申万 L1 + log(circ_mv) per-date OLS,winsor=0.01,min_obs=20;**删最小 30%**(LSY CH-3 shell zone)—— 适用**所有信号宇宙含 EXIT/做T 诊断**,非只 entry。
   - **组合层暴露审计(EXIT 特有)**:EXIT 触发**条件于持仓书非全宇宙** → 须审计 {入场名/在位名/被 EXIT 触发名/EXIT 后存活名} 的 size/行业/价格/龄/未实现盈亏漂移;**placebo 须 held-book 匹配**(§4.3)。
   - **正交化**:新因子 vs carry/QGR/reversal/momentum/size cluster 看增量;\|corr\|>0.7 = 换皮(诚实披露,不当新 alpha 卖)。
5. **泄漏门(基建级)= future-NaN 投毒(`leak_probe`)+ cyq 特例(codex R1-#5)**:
   - 通用:把所有 `knowledge_date > cutoff` 数据置 NaN 重算,picks/因子值须**逐字节相同**;**同时扰动复权因子/股本等公司行动字段**(不只价量行)。
   - **cyq_perf 特例**:它是**模型派生 final-vintage**(vendor backfill / qfq / 流通股本变更 / 全历史标定可**不改 `knowledge_date` 而泄漏**)→ future-NaN **不足**。对策:**从 raw PIT 原语 prefix-by-prefix 重算 cyq 类特征** + 版本化特征快照;**未经 provenance 证因果可得的 final-vintage vendor 因子一律禁用**(cyq 若不能 raw 重算 → 降级为可选/披露项,不作承重件,印证 memory cyq 存疑)。
6. **诚实分母**:命中率/precision 类诊断分母**必 fills-aware**(仅可成交/已入场名);**「signal hit」与「fillable hit」分开报**(codex R1-#8)。
7. **足迹检测若用状态模型**:用 **filtered(单边在线)非 smoothed** 概率(避泄漏)。

---

## 4.(d)真实测试方法 —— 严格竞技场不可旁路

> 严格性即产品;任何产物不过门不得晋级「候选」。复用成熟件 + 新建端到端模拟器(§6)。

### 4.1 真事件循环 + fills-aware + 显式成本(codex R1-#13)
- `gate_backtest` 真撮合(≤5槽/5td/T+1/**分板块滑点**/**涨停不可成交**)经 `PitBarSource`(K-002 PIT 字节,qfq as-of pin,真 `stk_limit`);**`initial_capital_yuan` 已参数化**(核实)。
- **显式成本模型(¥1万下决定做T 生死)**:**最低佣金 floor(典型 ¥5)** + 印花(卖 0.1%)+ 过户费 + 分板块滑点 + **失败成交** + lot 级换手容量;做T 增益**必须扣这些**才计。

### 4.2 可交易标签(三重屏障)+ entry/exit 非对称处理(codex R1-#6/#8)
- 波动缩放非对称屏障;入场 = **close T 决策 → T+1 harsh-fill**(非自定义 VWAP 幻想);≤10% ADV;**不用「命中涨停」标签**。
- **entry**:不可买(一字板/停牌)→ 该名**丢出买入集**(买不到)。
- **exit(持仓)**:不可卖(跌停/停牌)→ **绝不丢弃**,**挂单排队至真可卖 + 累计被套 MTM 损失**(live 现实)。

### 4.3 ⭐ EXIT-专属 placebo(codex R1-#3,头号补丁)
> 「避顶部改善」可能纯是**机械减暴露**(B1/B2 已证)。买入集 placebo(size-matched/random/纯突破)对 **EXIT-on-held** 是**错 placebo**。EXIT 须**自己的对照**:

- **同频随机持仓 EXIT placebo**:同 EXIT 频率/同持仓龄/同 size/行业/同未实现盈亏/同入场分,**随机挑在位名卖**(+同现金再部署 + 同再入锁定窗)→ 揭穿「卖得早/卖得少」的机械效应。
- **同卖出日历 placebo**(codex R1-#16):同「腾出槽位的日期」但随机挑名 → 隔离「释放槽给 entry ranker 的换手 alpha」。
- **等暴露对照 + 闲置现金调整效用**:把减暴露的机械收益剥离后,看避顶部信号**是否仍有特异增量**。

### 4.4 ⭐ P&L 四分解(codex R1-#16,EXIT 边的解剖)
每个 EXIT 臂的净盈拆成:**① 避免的损失(avoided loss)② 错失的收益(missed gain)③ 再部署收益(redeployment gain)④ 交易成本**。**避顶部要赢,必须 ①>②+④ 且 ③ 不是主因**(否则是换手/再部署 alpha 冒充避顶部)。

### 4.5 做T 现实(codex R1-#7,CRITICAL)
- 日频 OHLC **无法证 low 在 high 前** → **worst-case 日内序列**(假设最不利次序)或**仅计 inventory-feasible 交易**(T+1 只卖得了昨仓);basis 下降只在保守序列下计。
- 对照 **no-do-T + 同频随机择时做T placebo**;做T增益是**保守下界**。**真做T 声明须日内数据**(§9 owner 决策点)。

### 4.6 CPCV + 动态退出路径依赖(codex R1-#14)+ 四门 + 非清零账本
- **CPCV 按日期分组**:N=6/k=2 → 15 combination / φ=5 path;purge+embargo ≥ 标签 horizon;固定序列零 path 离散诚实记录。
- **动态退出特有**:持有期**可变** → fold 须定 **state reset/warmup + 可变持有 embargo + fold 边界挂单处理 + path 级独立**;**重叠的动态交易收益绝不当独立样本喂 DSR**。
- **反过拟合四门(绝不放宽)**:**DSR≥0.95**(HAC lag;ONC 有效 N)/ **PBO**(针对真实选择规则)/ **SPA**(Hansen)/ **Romano-Wolf StepM**(预声明 family + block bootstrap)。
- **非清零账本**:legacy floor(≈2376)+ batch-A + exit_veto + derisk_regime + defensive_sleeve + **本程序所有 C-刀**;deflation N = max(累计有效, 本批 ONC 有效);**C1/C3/C5 family 各自预声明 + 结果出炉前 publish 账本条目带 hash**(codex R1-#17)。

### 4.7 制度分层(含股灾切片)= 逆境不毁灭检验
- 净盈/避顶部命中/missed-rally/MDD 分 {牛/熊/震荡} × 含 **2015-06股灾 / 2016-01熔断 / 2018熊 / 2020-02疫情 / 2022调整 / 2024-02微盘崩**;逆境**非永久套牢**(避顶部生效 + 缩量回调可接受),不只看均值。**直防 R5「+2.68% 一半来自 size tilt」教训。**

### 4.8 look-once 前向
train_val only 开发(防火墙断言 bar-read ⊆ train_val,coverage 守门);test 封存,真 OOS = B 层 **owner-gated look-once**;低 DSR/勉强显著 = **provisional**(原则 #1)。

---

## 5.(e)系统级盈利验证总体设计(对接第三步评价协议)

> 整套系统(选股闸 → 减持排除 → 避顶部 EXIT → 做T → 动态退出)真实模拟,验证「能否稳定而可观盈利」。详细阈值/协议第三步 EnterPlanMode 与 owner 敲定;本节定**总体设计**。

1. **数据两类**:
   - **股灾期间数据**(逆境不毁灭):train_val 内 6 个股灾切片 —— 系统须不被挂山顶(避顶部生效)、缩量回调可接受、非永久下跌。
   - **近期真实数据**(处子 OOS):test 封存窗 / test_end 2026-06-12 之后新数据 —— **owner 授权才烧**(look-once 红线)。
2. **资金曲线,非单点(codex R1-#12,重要修正)**:
   - **alpha 统计证明在『研究资本』**(¥100万或不受限,lot-rounding 不主导)做 —— ¥1万单独做 alpha 证明会被**整手取整噪声 + 现金拖累**主导(15% cap=¥1500,>¥15 的票连 1 手都买不了)。
   - **¥1万 = 实盘执行可行性验证**(能否在小账户现实约束下兑现 alpha),**非 alpha 统计证明**。
   - **报 capital curve ¥1万/5万/10万/100万**:各档报合格宇宙缩减 / 成交笔数 / 现金拖累 / 固定费侵蚀;**¥1万 与研究资本结论须一致才算系统稳健**。→ **§9 owner 决策点**。
3. **真实模拟(端到端)**:真事件循环 + fills-aware + 涨跌停不可成交 + 分板块滑点 + 显式成本 + 减持排除 + 避顶部 EXIT + 做T,**整套跑**;对接 lowbase §6 时序 MASK 伏击模拟器(as-of T−1 收盘 → T+1 入场 → 规则驱动持有 → 避顶部/破位/thesis 退出 + 做T;future-NaN 泄漏门;三重屏障)。
4. **新判据评测(amendment)**:① 规则驱动持有期**绝对净盈**(扣全成本)② **避顶部命中 + missed-rally 净 trade-off**(及时退派发、不挂山顶,且不过度误卖)③ **做T增益**(保守下界,扣 min 佣金)④ 跨 CPCV/regime **frac_positive 稳定为正** ⑤ MDD/缩量回调**仅披露** ⑥ 四门不放宽 + 非清零账本 + 制度分层。
5. **「稳定而可观」阈值 = 看结果前冻结(codex R1-#11)**:在**第三步 plan mode 内、跑 C1/C5 之前**与 owner 敲定并冻结:`frac_positive 阈` + **最少成交笔数** + 1万本金年化净盈阈 + **最长亏损持续期** + provisional/pass 标签判定。**严禁**看了 train 结果再定阈值(=营销非科学)。
   - **稳定** = 跨 CPCV combination + regime(含股灾期)`frac_positive ≥ 阈` + 股灾期不毁灭。
   - **可观** = 研究资本绝对净盈年化 ≥ 阈 + ¥1万 执行可行性一致。
   - **look-once 前向 + provisional 标记**(低 DSR/勉强显著 = provisional 不当达标)。
6. **须稳定击败 baseline 面板**(防 long-beta;SPA/Romano-Wolf 公平比):random_top5 / 现役 screener momentum-0.40 / 纯流动性 / etf_only_510300 / csi300_etf_hold。

---

## 6.(中枢)端到端模拟器:先冻契约,再做信号

> codex R1-#1/#2:新模拟器是头号自由度,EXIT/做T 路径无法对照冻结引擎、能藏调参;「移植 live Line-2」对避顶部是循环(live 还没这信号)。**对策 = 把『执行契约』与『信号研究』彻底分离,契约先冻。**

### 6.1 第一步:冻结**可执行 EXIT/做T 执行契约**(信号研究之前,任何 P&L 之前)
独立于任何具体信号,先写死**订单怎么流**:信号时间戳 → 下单时间戳(close T→T+1)→ 成交优先级 → 部分/无成交处理 → 跌停/停牌**挂单排队** → 再入规则 → **做T inventory 记账**(T+1 只卖昨仓)。契约 **hash 冻结 + append 账本**;**任何修订都 debit 账本**(改判据不清零)。

### 6.2 第二步:模拟器忠实性 = 三道 fail-closed 不变量
| 不变量 | 内容 |
|---|---|
| **① exit-disabled ≡ 冻结引擎** | **关掉全部 EXIT/做T overlay 时,模拟器必 byte-exact 复现 `backend.backtest`**(`full_engine_crosscheck`)—— 这才证 EXIT 路径是**纯叠加**而非偷改基线 |
| **② entry/rotation byte-exact** | 入场/轮动路径对照冻结引擎逐字节(同上) |
| **③ EXIT 执行 = 契约移植** | EXIT/做T 订单流严格按 §6.1 冻结契约,**非为 backtest 现拟**;golden-path + 对抗涨跌停 + shadow-replay 测试 |

### 6.3 信号阈值与模拟器分离(防自欺)
EXIT/做T **阈值全经 §3 大数据条件研究从零定 + §3.3 四路切分**,committed 在评测前;**诚实门永不指导搜索**(Goodhart);模拟器每次改动 debit 账本。

### 6.4 诚实边界(scope,预先声明)
- 端到端模拟器仍是 **proxy**(不含 LLM 辩论 / 全 RiskEngine 实时态 / 真盘口微结构 / 日内);**go-live 仍须真管线 shadow replay + look-once 前向**。
- 日频 EOD:做T 增益是**保守下界**;避顶部「及时」是日频粒度(非精确点顶)。
- 忠实性以 §6.2 三不变量 fail-closed 守门,否则空报告。

---

## 7. 分阶段路线图(每刀:预承诺 spec + codex 前置门 + 真竞技场 + 报 FAIL)

> 排序 = 先验最强 + 最易独立证伪先做。**每刀先落 amendment 再写码**;sim 暂停贯穿;每刀 owner gate;**C1/C3/C5 family 各自预声明 + 账本 hash 先于结果**(codex R1-#17)。

| # | 刀 | 内容 | 门(预承诺) | 依赖 |
|---|---|---|---|---|
| **C0** | 端到端模拟器骨架 + **EXIT 执行契约冻结** | §6.1 契约 hash 冻结 + §6.2 三不变量 | exit-disabled≡冻结引擎 + entry byte-exact + 契约移植测试通过 | — |
| **C1** | **避顶部派发 EXIT(下一刀)** | X1 大数据条件研究(从零,左尾假说)→ C0 模拟器 + **EXIT-专属 placebo + P&L 四分解** | 净 trade-off(①避损>②错失+④成本,③非主因)+ 严格胜 EXIT placebo + DSR≥0.95;FAIL 报 FAIL | C0 |
| **C2** | 减持硬排除 | E4(`stk_holdertrade` owner-gated 摄取)→ 事件研究 + 硬排除 | 可交易 CAR 负显著;硬排除不过度降净盈;只排除不排序 | 摄取 |
| **C3** | 做T overlay | X3 确定性低吸高抛,T+1,**worst-case 日序** → C0 测做T增益 | 做T增益(保守下界,扣 min 佣金)严格胜同频随机择时 placebo;守 T+1 | C0 |
| **C4** | 主旋律资格维度 | E2 QGR ⑧ 主旋律(申万 L3 PIT,政策发布日)→ baseline 面板证增量 | 主题 OVER 非主题 baseline IC 增量;PIT codex 门 | theme-map 稳定 |
| **C5** | **动态退出整合 + 系统级盈利验证** | X1∪X2∪X3∪X4 整合 → 端到端 capital curve 真实模拟(近期+股灾期) | §5 全判据 + **看结果前冻结的稳定可观阈值** + capital curve 一致 + look-once 前向 | C0-C4 |

> **第一要务**:C0(含契约冻结)+ C1 —— `baseline` vs `+避顶部EXIT` vs `+EXIT-专属 placebo`,配 P&L 四分解。最干净一刀,暴露「避顶部是真避损 vs 机械减暴露/换手」。

---

## 8. 红线合规自检

| 红线 | 本大纲 |
|---|---|
| 永禁真实下单 / 飞书人工 / 127.0.0.1 | ✅ 纯研究 + 模拟,sim 暂停 |
| LLM 不写决策 / 单一构造点 | ✅ **研究零 LLM**;live X5 evidence-only;X1-X4 确定性派生(§1.4) |
| RiskEngine 纯函数 / 不碰引擎字节 | ✅ C0 模拟器研究码,**exit-disabled≡冻结引擎** byte-exact,不碰冻结引擎/arena/value-sleeve(AF-*) |
| PIT 可复现字节存档 + 泄漏门 | ✅ §3 future-NaN + 公司行动字段扰动 + cyq raw 重算;禁重下只增量 |
| ≤5 持仓 / 排除四件套 / 不买涨停不卖跌停 / 做T 守 T+1 | ✅ §1 + §4.2 + X3 |
| 不接 moneyflow 主路径 / 北向仅历史 / **不做 L2** | ✅ §2 🔴 级 |
| 改判据不清零 mining 债 / 四门不放宽 | ✅ §4.6 非清零账本(含模拟器改动/弃假说);只放宽 MDD |
| 改决策边界先落 amendment / codex 前置门 / FAIL 报 FAIL | ✅ §7 每刀 |
| push · 摄取 · live 激活 · 实施 = owner-gated | ✅ 全 owner-gated |

---

## 9. owner 决策落定(2026-06-27,AskUserQuestion + plan 审批;原待定项已拍板)

> 原 §9 待定 6 项中,owner 已就 1/3/4 拍板,2 由红线/codex 收敛定,5 入第三步 plan,6 默认采纳。

1. **¥1万 定位** → **双资本**:alpha 在研究资本(¥100万/不受限)证 + ¥1万 作执行可行性 + capital curve ¥1万/5万/10万/100万 结论须一致。
2. **C0 路线** → 建研究模拟器(exit-disabled≡冻结引擎 byte-exact,不碰引擎),codex R2 已背书。
3. **刀序** → **C1 避顶部 EXIT 先行**(已摄数据);C2 减持待 owner 授权摄取 `stk_holdertrade` 后并入。
4. **做T 数据** → **先 worst-case 日频保守下界**;真做T 精确声明待日内数据(后置)。
5. **「稳定可观」阈值** → 第三步 plan §A8 提案默认值(frac_positive≥0.80 / 成交≥30 / 年化≥12-15% 占位 / 永久套牢=FAIL),**跑 C1/C5 前 owner 冻结**。
6. **股灾切片** → 6 切片(2015-06/2016-01/2018/2020-02/2022/2024-02)采纳。

### 🆕 owner 新交易原则(P-A..P-E,决策边界级,amendment 已落 C0a)

- **P-A 确认门**:形成观点 ≠ 立即下单;入场须市场确认上涨已启动(不追飞刀),EXIT 侧对称(避顶部须确认滚顶 = §10.2 M3 erratum「左尾非择顶」一致)。
- **P-B 强制止损(两条腿硬底线)**:反常下跌必止损,硬触发不等确认。
- **P-C 做T profit-gate**:绝不在无正浮盈时做T。
- **P-D 安全底线投机**:资本保全优先,下行保护权重高于收益最大化(永久套牢=FAIL)。
- **P-E 仓位口径**:取消单股 15% cap(置信集中,单名可达 ~60%)+ ≤5 名上限非必占满 + **强制 ≥40% 现金 buffer 严禁梭哈**;live RiskEngine check#5/§2.4 改动 = live 部署 owner-gated。

> amendment = `docs/decisions/qgr-confirmation-stop-swing-sizing-amendment-2026-06-27.md` + rebar §9 Erratum/§10。**第三步 plan(已批)= `misty-doodling-pnueli`**,实施序 C0a→C0b→C1→C3→C2→C4→C5,逐刀 owner gate。

---

## 10. codex 对抗审查 + 收敛记录

### 10.1 R1(`codex exec --sandbox read-only`,gpt-5.5 xhigh,session `019f0703`)

codex 返 **17 findings(4 CRITICAL / 9 HIGH / 3 MEDIUM / 1 LOW)**,裁决 **「未达标,须先修订」**。Claude 评估:**17 条全部成立、无误读**,逐条吸收(无驳回)。映射:

| # | codex finding(级别) | 本稿处置 |
|---|---|---|
| 1 | 模拟器是头号自由度,byte-exact 只核对无争议半(CRIT) | §0 + §6.2 **exit-disabled≡冻结引擎**不变量 + §6.3 阈值/模拟器分离 + 每改动 debit 账本 |
| 2 | 「移植 live Line-2」对 X1 循环(CRIT) | §6.1 **EXIT 执行契约先冻结**(信号研究/任何 P&L 之前)hash + 账本 |
| 3 | 避顶部改善 = 纯减暴露(CRIT) | §4.3 **EXIT-专属 placebo**(同频随机持仓 EXIT,held-book 匹配)+ 等暴露/闲置现金调整对照 |
| 4 | 「方向无歧义」过度声称(HIGH) | §2 **降级为「左尾风险假说」** + missed-rally/false-exit/再入损失 @1/3/5/10/20td |
| 5 | future-NaN 对 cyq 不足(HIGH) | §3.5 cyq **raw PIT 重算** + 公司行动字段扰动 + final-vintage vendor 禁用/降级 |
| 6 | EOD 不支持当日 EXIT 成交(HIGH) | §1.2 + §4.2 **close T→order T+1** 铁律 |
| 7 | 日频做T 幻想成交(CRIT) | §4.5 **worst-case 日序 + inventory-feasible + 同频随机做T placebo**;保守下界;真做T 须日内(§9-4) |
| 8 | 丢弃不可成交 EXIT 偏置标签(HIGH) | §4.2 EXIT 不可卖**挂单排队 + 累计被套 MTM**;signal-hit vs fillable-hit 分报 |
| 9 | size/行业纪律未对 held-position 操作化(HIGH) | §3.4 **组合层暴露审计** + placebo held-book 匹配 + 删30% 含 EXIT/做T 宇宙 |
| 10 | 「发现后冻结」仍允许 in-sample 搜(HIGH) | §3.3 **discovery/calibration/validation/sealed 四路切分** + 账本计阈值网格/变体/弃假说 |
| 11 | 「稳定可观」后定义=营销(HIGH) | §5.5 **看结果前冻结阈值**(第三步 plan、跑 C1/C5 之前) |
| 12 | ¥1万 过度受 lot 约束做不了 alpha 证明(HIGH) | §5.2 **alpha 研究资本证 + ¥1万 执行可行性 + capital curve**;§9-1 owner 决策点 |
| 13 | min 佣金/做T 换手成本未显式(MED) | §4.1 **显式成本模型**(min 佣金 floor/印花/过户/滑点/失败成交/lot 容量) |
| 14 | 动态退出使 CPCV 更路径依赖(HIGH) | §4.6 fold reset/warmup + 可变持有 embargo + 边界挂单 + 重叠动态交易不当独立 DSR 样本 |
| 15 | LLM 角色内部矛盾(MED) | §1.4 **硬切分**:研究零 LLM;live evidence-only;参与选择即毕业付账本债 |
| 16 | EXIT 边可能来自换手/再部署非避顶(MED) | §4.4 **P&L 四分解**(避损/错失/再部署/成本)+ §4.3 同卖出日历 placebo |
| 17 | baseline/四门对但须预声明 family + hash(LOW) | §4.6 + §7 C1/C3/C5 family 各自预声明 + 账本 hash 先于结果 |

### 10.2 R2(`codex exec --sandbox read-only`,gpt-5.5 xhigh,session `019f070b`)—— 收敛达成

codex 二审逐条核对 R1 17 条:**全部 CLOSED**(无 PARTIAL/NOT-CLOSED);**无新增 CRITICAL/HIGH 方法学问题**。**裁决 = `PROCEED to detailed plan`**。

**Claude 与 codex 均无异议** → 大纲方法学定稿,可进第三步。

**3 条 MEDIUM = codex 明确点名「第三步详细计划里落实」(非大纲级阻塞,已登记带进 plan mode)**:

| MED | codex 提示 | 第三步处置 |
|---|---|---|
| M1 | held-book placebo 可行性未钉死:≤5 持仓下按 龄/size/行业/未实现盈亏/入场分**精确匹配会稀疏** | Step 3 定**回退匹配规则 + 平衡诊断 + fail-closed 判据**(匹配不足则空报告/降级) |
| M2 | P&L 四分解须**冻结反事实代数**:horizon / no-X1 baseline / 再入锁 / 再部署记账 / 归因规则 | Step 3 把四分解做成**不可游戏化**的冻结算法 |
| M3 | **上游 amendment 仍有「方向无歧义」字样**(`qgr-criterion-rebar-amendment-2026-06-27` §3 表)与本稿「左尾风险假说非择顶」不一致 | **owner 决策**:实施/governance 改动前,给 amendment 加 erratum 对齐「左尾风险,非择顶」措辞(amendment 是决策边界文档,Claude 不擅改 → 报 owner) |

> **收敛记录**:R1(17 findings,REVISE)→ Claude 逐条吸收(无驳回)→ R2(17 CLOSED + 0 新 CRITICAL/HIGH,PROCEED)。原文存 scratchpad `codex_r1_out.txt` / `codex_r2_out.txt`。
