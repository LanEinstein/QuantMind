# C1 避顶部派发 EXIT-on-held 消融结果(train_val only)— **FAIL 报 FAIL**

> **窗口**:train_val `20150209 → 20250425`,497 rebalance(5td)/ 2484 daily / 1823 universe + CSI300 ETF。sealed test 永不读(真 OOS = owner-gated look-once,B 层)。
> **byte-exact 不变量**:`baseline`(NoOp overlay)= 冻结引擎 `run_backtest` **逐字段相同** ✅(plumbing 忠实,overlay 是纯叠加)。
> **判定**:**deployable_edge = False** —— 避顶部 EXIT-on-held 在 ≤5 槽反转排序上**不仅无独立可部署边,反而净有害**,FAIL on **每一道**预承诺门。
> **结论一句话**:batch-A 证的是 `ideal_amplitude_20d` 的 **GROSS 十分位尾部**信号(A2 PASS);本刀证它**不转化**为净 EXIT-on-held trade-off —— 在短线反转账本上,按滚顶卖在位名 = **在反转反弹前砍在地板**,**错失(②588k)> 避损(①485k)**。与 QGR-4(同因子作买入 veto 亦 FAIL)一致。

## 1. 装置

C0b `run_e2e_backtest`:同一 QGR-3 反转排序买入策略(`{rev_1d,max_5d,turn_spike}` size-neut,attractive-LOW = 买超卖)经冻结引擎跑;**5 臂只差 EXIT overlay**,共享同 fill / 友好成交 / 分板块滑点 / ¥5 min 佣金 / 守恒 plumbing。committed 阈值(owner blessed,评测前冻结):`top_q=0.90`(batch-A §3)/ 滚顶确认窗 5d·跌幅 −3% / P-B 硬止损 −12% / reentry 锁 5d / P&L 反事实 horizon 10td。

## 2. 逐臂结果(主表)

| 臂 | net P&L | MDD | 月换手 | 成交 | overlay 卖 | 均暴露 | 守恒 |
|---|---:|---:|---:|---:|---:|---:|:--:|
| **baseline**(无 EXIT) | **+459,000** | **54.23%** | 0.23 | 179 | 0 | 0.58 | ✅ |
| stop_only(仅 P-B 止损) | −431,261 | 78.51% | 1.12 | 911 | 241 | 0.71 | ✅ |
| **avoid_top**(治疗) | **−663,283** | **82.46%** | 1.30 | 1061 | 341 | 0.69 | ✅ |
| placebo_sell_calendar | −629,005 | 79.52% | 1.28 | 1071 | 447 | 0.69 | ✅ |
| placebo_random_held | −537,522 | 75.01% | 1.32 | 1083 | 429 | 0.69 | ✅ |

**触目的事实**:① **baseline(NoOp)是远好于一切的臂**(+459k vs 所有 EXIT 臂 −431k…−663k);② **任何** EXIT-on-held(止损 / 避顶部 / 随机)都把净盈打成大负 + 把 MDD 从 54% **推高**到 75–82%(与「降回撤」初衷**相反**);③ avoid_top 比 stop_only **更差**(多卖、多换手、净盈更低、MDD 更高)。

## 3. P&L 四分解(冻结反事实,avoid_top − stop_only)

| 项 | 值(¥) | 读 |
|---|---:|---|
| net_trade_off | **−232,022** | 避顶部相对止损底**净伤** |
| ① avoided_loss | 485,226 | 卖对、躲掉的跌 |
| ② missed_gain | **588,576** | 卖错、错失的涨(**> ①**) |
| ③ redeployment(残差) | −116,405 | 释放现金再部署**亏** |
| ④ exit_cost(显式费) | 12,267 | 佣金+印花+过户(滑点已在价里,不重计) |
| n_avoid_top_exits | 99(+6 trapped) | powered(≥30)✅ |

`favourable_trade_off = ① > ②+④` → **False**(485k < 588k+12k)。**避顶部退出错失的涨 > 躲掉的跌** —— 直接坐实 §9 Erratum:滚顶结构在反转账本上**多半先于洗盘后拉升**(owner 想穿过的那种),而非派发。

## 4. 避顶部 vs placebo(paired-t)+ 反过拟合四门(全 FAIL)

| 比较 | mean_diff | t | 读 |
|---|---:|---:|---|
| avoid_top − stop_only | −0.0012 | **−1.65** | 比止损底**更差** |
| avoid_top − placebo_calendar | −0.0002 | −0.44 | **输**给同日历随机卖 |
| avoid_top − placebo_rate | −0.0007 | −0.83 | **输**给同速率随机卖 |

- **beats_both_placebos = False**(须 t≥2,实测均为负)→ 避顶部**选名**毫无特异增量,连「腾槽换手」「减暴露速率」两个零信息对照都跑不赢。
- **DSR**(非清零 N=2386):avoid_top **2.4e-6** / stop 8.7e-5 / placebo 6e-6…2e-5 / baseline 5e-3 —— 全 ≪ 0.95(主门),**避顶部最低**。
- **SPA p = 0.658**(Hansen,family `qgr.avoid_top`)/ **Romano-Wolf 全 not-rejected** → 无一臂相对 CSI300 hold 有显著超额。
- 非清零账本已 append `qgr.avoid_top`(kind=ablation,window 20150209–20250425;改判据**不清零** legacy floor)。

## 5. 平衡诊断(held-book 暴露,codex R2-M1)

| 臂 | n | 退出名均持龄 | 均 log-mv | 均未实现盈亏 |
|---|---:|---:|---:|---:|
| avoid_top | 105 | **6.37** | 16.07 | +3,322 |
| placebo_sell_calendar | 105 | 12.80 | 16.25 | +3,308 |
| placebo_random_held | 105 | 24.41 | 16.16 | +3,637 |

**避顶部退出名显著更「年轻」**(持龄 6.4 vs 随机 12.8/24.4)→ 避顶部专砍**刚买入不久**的名 = 正是 ranker 刚抄底的超卖反转名,**在反弹前卖** = 机制证据。

## 6. 制度分层 + 6 股灾切片(P-D 不被挂山顶?— 否)

**制度**(bull 138 / bear 128 / sideways 230,sum_return):

| 臂 | bull | bear | sideways |
|---|---:|---:|---:|
| baseline | **+0.77** | −0.22 | **+0.06** |
| avoid_top | +0.12 | −0.21 | **−0.73** |

**伤害集中在 sideways(496 中 230 期)**:baseline +0.06 → avoid_top **−0.73** = 震荡市**卖→ranker 再买**的洗白 churn;bull 也从 +0.77 砍到 +0.12(避顶部把牛市赢利当「滚顶」卖光 = 错失)。

**6 股灾切片(cum_return,避顶部并未一致保护)**:

| 切片 | baseline | avoid_top | 读 |
|---|---:|---:|---|
| 2015-06 股灾 | −0.37 | **−0.26** | 避顶部**好**(唯一真躲) |
| 2016 熔断 | −0.23 | −0.19 | 略好 |
| 2018 熊 | −0.29 | **−0.41** | **更差** |
| 2020 COVID | +0.07 | **−0.14** | **更差**(卖在 V 底,错失反弹) |
| 2022 调整 | −0.19 | **−0.24** | 更差 |
| 2024 微盘崩 | −0.04 | +0.05 | 略好 |

避顶部只在 **2015/2024** 真躲,**2018/2020/2022 更差** —— **无一致逆境保护**,不是「不被挂山顶」,是「卖了错过反弹」。

## 7. 诚实判读(FAIL 报 FAIL)

- **避顶部 EXIT-on-held(X1)在 QGR-3 ≤5 槽反转排序上被证伪**:净盈 −663k(vs baseline +459k),MDD 推高到 82%,**输给两个零信息 placebo + 止损底**,P&L 四分解 ②错失 > ①避损,DSR 2e-6,FAIL on 每一道预承诺门。
- **机制清晰**:ranker = 短线反转(买超卖);EXIT 按滚顶/破位卖**刚买入的超卖名**=在反转反弹前砍仓 → 系统性 sell-low / miss-bounce,震荡市最毒。这正是 §9 Erratum 预言的失败模式(滚顶结构在反转账本上多先于「洗盘后拉升」)。
- **P-B 机械 −12% 硬止损本身亦净有害**(stop_only −431k vs baseline +459k):在反转账本上,固定止损卖在反弹前 = 与反转 alpha 正面冲突。**(owner P-B 原则不被否定 —— 它是安全底线;但「固定 −12% 止损」这一机械实现与反转 ranker 冲突,是决策边界级发现。)**
- **降 MDD 目标未达**:EXIT overlay 把 54% MDD **推高**到 75–82%,与初衷相反。
- **与 QGR-4 一致**:`ideal_amplitude_20d` 作买入 veto(QGR-4)已 FAIL,作 EXIT-on-held(本刀)FAIL 更甚 → 其 GROSS 十分位尾部边(batch-A A2 PASS)**在 ≤5 槽两种形态下都不转化为净可部署值**。

## 8. scope caveat（诚实边界）

train_val only(真 OOS = owner-gated look-once);decile 是 GROSS 显著性探针;DSR 用非清零 legacy floor(N=2386,round-1..4 mining 债不清零);滚顶确认用 raw close 路径(短窗公司行动 = 文档化 proxy 边界);≤5 持仓下 placebo 精确属性匹配稀疏 → 退回 count/calendar 匹配 + 平衡诊断 + fail-closed(<30 退出降级,本刀 99 exits 已 powered);**P-B −12% 是 committed 单值**,别的止损阈值可能没这么毒,但「固定止损 vs 反转 ranker」的冲突**方向**是机制性的(非单纯调参)。

## 9. 这对纲领意味着什么(供 owner 定向,非自动推进)

1. **避顶部/动态退出 thesis 在 QGR-3 反转 ranker 上不成立** —— EXIT 与反转 alpha 正面冲突。要么换 ranker(动量/趋势账本里 EXIT 才可能不砍反弹),要么避顶部只配 ENTRY 筛(但买入 veto 已 QGR-4 FAIL),要么放弃避顶部方向。
2. **降回撤须靠别的机制**:本刀证 held-EXIT 推高 MDD → 保护应来自 **P-E 仓位/现金 buffer**(置信集中 + ≥40% 现金,而非满仓 ~75% gross + 砍仓),或更换 ranker;不是 EXIT overlay。
3. **P-C 做T(C3)/ E7 入场确认门(C1b)** 是否仍做:做T 在反转账本上方向(低吸高抛)与反转 alpha 一致,可能不同命;E7 入场确认门(等反弹确认再入)也与反转账本互动复杂 —— 待 owner 定。
