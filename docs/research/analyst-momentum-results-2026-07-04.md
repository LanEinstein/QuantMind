# 排名候选 #2:分析师修正动量 dev 回测结果(train_val only)— **candidate_edge=False,但唯一薄边活口**

> **定位(owner 2026-07-04 定向,amendment 选拔协议)**:DS-D2 branch (c) 推翻了**价量反转**的书层排名边后,测**机制不同的信息流因子**(分析师修正动量)——branch (c) 唯一未覆盖者。判据 = **选拔门**(胜自身暴露匹配随机 placebo,paired-t≥2 双容器 joint)+ owner 三判据(熊市累计≥0 / 股灾不崩 / 净盈>0);DSR/SPA/RW 降披露。
> **窗口**:`20150202 → 20250418`(**114** monthly rebalances / 2501 daily / universe 907;分析师覆盖行 **22.9%**)。sealed test 永不读。
> **spec**:`analyst_momentum_spec.py` 冻结,`spec_hash=a84dd243ebb5a704…`(评测前定死,评测中未改)。**反 p-hacking 锚 = 复用 round-4 R4-4 正交子集 `{np_rev,rev_diff,cover_chg}` 全 +1(逐字节取自 `alpha_pivot_spec` 分析师块,不重选/不重定符号);因子构造 = `analyst_revision_pit` 逐字复用(report_date<d PIT / 同 FY 修正 / 跨券商中位 / n≥3)。** 账本 append `ds.analyst_momentum`(effective_n=1,非清零 floor **2419→2420**)。
> **裁决**:**candidate_edge = False**(选拔门 joint FAIL + owner 门 FAIL)——分析师动量作**独立 ≤5 槽排名层**不过关键门。**但它是所有排名候选里最强的一个**:部署容器 am_buf40_5 是**唯一未被同宇宙随机明显击败**的候选臂(+853k > random +620k,vs_own_random_t **+0.14** / vs_sizematched **+1.17**,方向为正但远不显著);双容器熊市累计均正(+0.31/+0.23),buf40_5 MDD 20.2%,DSR 0.039(全候选最高)、turnover 0.04–0.07(慢稳信号)。**与外部交叉验证 memo 完全一致:薄边+辅助,无法单独作核心**(round-4 权重 0.25 定位;standalone ≤5 槽 fail ≠ 机制无正交边)。

## 1. 装置

D1 式双容器 ablation(无 A0 字节锚——分析师动量无 slot_frontier 对应,基线 = 自身随机 placebo)。月度 R4 panel 复用 `build_panel_r4`(rebalance_freq=20,分析师窗口 90/90/180 = spec committed;analyst reader firewalled `< test_start`)。ranker = 三因子 **等权 signed z-blend**(`mean_f sign_f·zscore(neut_f)`,industry SW-L1 + log size 删30%,winsor 1%,min_obs 20;**dropna 全 3 → 只排分析师覆盖名**)。`run_gate_backtest` 冻结事件循环(T+1/分板块滑点/¥5 佣金/≤1 rotation/日,horizon=20)× 双容器(eq_5 科学门 / buf40_5 部署门)+ 暴露匹配 random/sizematched placebo。code-review high 前置门:3 findings 修(min-breadth fail-fast 门〔避免稀疏覆盖日中途 abort 46min run〕/ covered_frac 用全表 / xv._zscore 复用去重)。

## 2. 逐臂主表(初始 ¥1,000,000)

| 臂 | net P&L | MDD(披露) | 均暴露 | DSR(披露) | 熊市累计 | vs 自身 random(t) | 守恒 |
|---|---:|---:|---:|---:|---:|---:|:--:|
| **am_eq_5**(候选,科学门) | +1,225,600 | 37.60% | 0.656 | 0.0199 | **+0.308** | **−0.492** | ✅ |
| placebo_random_eq_5 | **+1,547,940** | 52.21% | 0.999 | 0.0141 | +0.685 | — | ✅ |
| placebo_sizematched_eq_5 | +1,254,985 | 56.96% | 0.995 | 0.0125 | +0.480 | — | ✅ |
| **am_buf40_5**(候选,部署门) | +853,933 | **20.21%** | 0.373 | **0.0388** | **+0.229** | **+0.137** | ✅ |
| placebo_random_buf40_5 | +620,200 | 39.32% | 0.591 | 0.0078 | +0.351 | — | ✅ |
| placebo_sizematched_buf40_5 | +147,870 | 31.06% | 0.480 | 0.0015 | +0.135 | — | ✅ |
| csi300_hold(beta 门) | +196,200 | 46.32% | 1.00 | 0.0020 | +0.241 | — | ✅ |

- **eq_5(满仓)**:分析师动量 **输随机**(+1.23M < random +1.55M,t −0.49)——同反转/D1 模式(满仓随机吃反弹)。
- **buf40_5(部署门,60% 现金)**:分析师动量 **赢随机**(+854k > random +620k,t +0.14;vs sizematched +1.17)——**唯一在部署容器上对随机呈正向的候选臂**(反转 D2 在此 t −1.65,D1 t −0.85)。但 t 远 < 2 → **方向对、不显著**。

## 3. 四大对照读数

- **① 选拔主门(vs 自身 random,joint t≥2)**:am_eq_5 **−0.492** / am_buf40_5 **+0.137** → **joint FAIL**(eq_5 输、buf40_5 正但微)。**独立 ≤5 槽排名层无可认证边**。
- **② vs sizematched**:eq_5 −0.286 / buf40_5 **+1.168**(正、接近但不达显著)→ buf40_5 上的正向不是纯 size 倾斜,有微弱真信号残留。
- **③ 机制画像(vs 反转 D2 / 防御 D1)**:分析师动量 **双容器熊市累计均正**(+0.31/+0.23,反转 D2 eq_5 为 −0.22)、**buf40_5 MDD 20.2%**(反转 36.5% / D1 红利 14.78%)、**DSR 0.039 全候选最高**、turnover 0.04–0.07(慢稳)。**regime 分解**:正向集中在 **震荡市**(am_eq_5 sideways +0.538 vs random +0.073),牛市让掉上行(am bull +0.161 vs random +0.634,防御性低暴露)。
- **④ 股灾切片**:六切片(含 2015/2016)**几乎全负**(2018 −0.248/−0.145,2015 −0.19/−0.125,2022 −0.144/−0.032;仅 2024 微正)→ **仍在每次股灾亏**,慢信号不避崩盘。

## 4. 反过拟合门(披露,预声明 FAIL)

- **DSR(非清零 N=2420)**:全臂 0.0015–0.0388 ≪ 0.95;**am_buf40_5 0.0388 是全项目所有候选臂历史最高**(反转 ~0.005 / D1 ~0.003),但仍 ≪ 0.95 → **AP-0.5 算术结论不因换信息流因子而改变**(SR_req 2.67 候选无关,样本内不可达,认证只走前向)。
- **SPA p = 0.124**(不显著)/ **Romano-Wolf 无臂 rejected**(D2 曾有随机臂被 reject,此处无)→ 无臂显著胜 CSI300(t vs csi300:am_eq_5 +1.09 最高但 <1.65)。
- 账本 append `ds.analyst_momentum`(effective_n=1;floor 2420)。

## 5. 选拔门 / 披露门落点表

| 判据 | am_eq_5 | am_buf40_5 | 角色 | 读 |
|---|:--:|:--:|---|---|
| 胜自身 random(t≥2） | ❌ −0.49 | ❌ +0.14 | **选拔主门** | **joint FAIL**(buf40_5 正但微) |
| 熊市累计 ≥ 0 | ✅ +0.308 | ✅ +0.229 | owner 判据 | **双过**(优于反转) |
| 股灾切片全不崩 | ❌ | ❌ | owner 判据 | 六切片几乎全负 |
| 净盈 > 0 | ✅ +1.23M | ✅ +854k | owner 判据 | 双过 |
| MDD（披露） | 37.6% | **20.2%** | disclosure | buf40_5 优于反转 36.5% |
| DSR ≥ 0.95 | ❌ 0.0199 | ❌ 0.0388 | disclosure | 全候选最高但仍 FAIL |

**candidate_edge = False**:joint 选拔门未过 + 股灾切片崩 → 不作独立 ≤5 槽排名层上线。

## 6. 诚实解读(按 memo 校准:薄边/辅助/standalone≠机制)

1. **排名层作独立 ≤5 槽可认证核心 = 两种机制双双证否**:价量反转(D2 branch c)+ 信息流分析师动量,均不过 joint 选拔门。这把"排名层整体存疑"从单机制推进到**跨机制**结论。
2. **但 standalone ≤5 槽 fail ≠ 机制无正交边**(外部 memo + round-4 印证):分析师动量在部署容器上**对随机呈正向**(唯一)、双容器熊市正、DSR 全候选最高、慢稳低换手——**薄边真实存在,只是装不进 standalone ≤5 槽的显著性门**。round-4 它作 0.25 权重 composite 分量兑现 +2.68%;本刀作 absolute standalone ≤5 20d 不显著——**两者不矛盾,正是"辅助补充素材,无法单独作核心"的实测**。
3. **AP-0.5 不变**:DSR 0.039 ≪ 0.95,认证只走前向(certification rearch amendment)。即便这薄边,样本内也不可认证。

## 7. 诚实 caveat

- **train_val only**;真 OOS = owner-gated look-once。DSR 预声明 FAIL 照报。
- **覆盖偏大盘**(分析师覆盖 = 卖方研究,结构性偏大盘):全 3 因子覆盖 2015/2016 仅 6–8%(~20–79 名/日,最稀疏日 20 名),2018+ ~45%(150–350 名/日);min-breadth fail-fast 门确保每日 ≥10 covered 名可跑暴露匹配 placebo。**这一刀本质是大盘覆盖名内的排名**(round-4 §5 "卖方覆盖偏大盘正好补 cap 加权缺口"的另一面)。
- **月度 panel 用 build_panel_r4 rebalance_freq=20 新建**(canonical monthly dates=train_val[::20]);因子/中性化/PIT 与 round-4 一字不差。
- **FAIL 报 FAIL**:不移球门、不事后调符号/权重/剔因子。

## 8. 对纲领的意义(供 owner 定向,非自动推进)

1. **排名层作独立 ≤5 槽核心 = 跨机制双证否**(反转 + 分析师动量)→ 产品终态更坚定收敛 **sleeve-only**(D1 式慢腿防御宇宙 + 现金 buffer,承重 = 风险性质非排名 alpha)。
2. **分析师动量 = 唯一有薄边活口**,但按 memo/round-4 定位应作**辅助分量或宇宙质量倾斜**(非 standalone 核心):其部署容器正向 + 熊市正 + 慢稳低换手,契合叠加在 sleeve 上作**弱信号 tilt**(不是 ≤5 槽独立选择器)。若 owner 想用,应作 composite 分量或 sleeve 内质量排序,认证走前向。
3. **下一批候选(若 owner 续找)= 事件型信息流数据**(memo §3:stk_holdertrade 增减持 → top_list 龙虎榜/block_trade 大宗 → share_float 解禁),owner-gated 摄取 + 同四门 + 账本 + SELECTION 框架(严禁择时 overlay)。原则 = **扩数据正交性,不扩挖掘次数**。
