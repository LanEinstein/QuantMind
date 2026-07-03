# 防御候选 D1(红利低波防御核心)dev 回测结果(train_val only)— **FAIL 报 FAIL**

> **窗口**:rankable **`20170323 → 20250418`**(99 monthly rebalances / 1981 daily / universe 942)。sealed test 永不读(真 OOS = owner-gated look-once)。
> **spec**:`defensive_d1_spec.py` 冻结,`spec_hash=883640b2a4193ec5…`(评测前定死,评测中未改)。账本 append `ds.d1_dividend_lowvol`(kind=ablation,effective_n=1;非清零 floor 2417→2418)。
> **⚠️ 覆盖硬限**:`accr`(Sloan 应计)需两期年报,statements 存档始于 2015 → **all-7 齐全的可排名窗口从 2017-01 起**,**2015 股灾 + 2016 熔断两切片空缺(n=0)**;2018/2020/2022/2024 四股灾已覆盖。这是数据限制非选择(spec 冻结 accr 为 committed 因子,不事后剔)。
> **裁决**:**candidate_edge = False** —— D1 防御**选股排名**在 dev 上**输给同宇宙随机 placebo**、DSR≪0.95、两股灾切片为负 → FAIL on 关键门。**但**:防御**宇宙过滤 + 现金 buffer 确实控回撤**(正面 partial,见 §4)。

## 1. 装置

`build_defensive_d1_panel`(7 因子 PIT panel:vol_20d/max_20d/dv_ratio/roe/gpm/accr + 新 beta/tail_beta 对 CSI300 510300.SH 滚动 OLS;industry+log_size 中性化删 30%;PIT ST/排除四件套/bottom-30% 硬排除)→ 块加权 z-blend ranker(低波 0.35/红利 0.35/质量安全 0.20/尾部 0.10,committed 符号)+ 评测前排除门(max_20d 顶 decile / ROE≤0 / GPM 底 decile / dv_ratio<中位反拥挤)→ `run_gate_backtest`(冻结事件循环,T+1/分板块滑点/¥5 佣金/≤1 rotation/日,horizon=20)× 双容器(eq_5 科学门 / buf40_5 部署门 40% gross)+ **暴露匹配** placebo(每容器同 slots/cap)。codex read-only 前置门:无 P0,2 P1 + 3 P2 全修(含暴露匹配 placebo)。

## 2. 逐臂主表(初始 ¥1,000,000)

| 臂 | net P&L | MDD(披露) | 均暴露 | DSR | 熊市累计 | vs 随机 placebo(paired-t) | 守恒 |
|---|---:|---:|---:|---:|---:|---:|:--:|
| **eq_5**(D1 科学门) | **+475,262** | 25.62% | 0.77 | 0.0037 | **+0.209** | **−0.98** | ✅ |
| **buf40_5**(D1 部署门) | **+159,064** | **14.78%** | 0.36 | 0.0021 | **+0.082** | **−0.85** | ✅ |
| placebo_sizematched_eq_5 | +266,807 | 33.86% | 0.99 | 0.0023 | — | — | ✅ |
| **placebo_random_eq_5** | **+1,489,980** | 51.20% | 0.99 | 0.0036 | — | — | ✅ |
| placebo_sizematched_buf40_5 | +106,071 | 13.89% | 0.37 | 0.0016 | — | — | ✅ |
| placebo_random_buf40_5 | +600,358 | 31.11% | 0.47 | 0.0029 | — | — | ✅ |
| csi300_hold(beta 门) | +165,036 | 45.09% | 1.00 | 0.0012 | +0.233 | — | ✅ |

- **决定性负结果**:**同宇宙随机选股 placebo 大胜 D1** —— random_eq_5 **+1.49M ≫ eq_5 +475k**;random_buf40_5 +600k > buf40_5 +159k。D1 排名 vs 随机 paired-t **−0.98 / −0.85**(须 ≥2 才算有排名 alpha;实为负)→ **D1 的块加权防御排名在此窗口无选股 alpha,甚至劣于宇宙内随机**。
- **regime(sum_return)**:random_eq_5 熊市 **+1.445** / 震荡 +0.609(满仓吃 2017-25 反弹)vs eq_5 熊 +0.209 / 震荡 +0.274。D1 更防御(暴露 0.77 vs 0.99、MDD 26% vs 51%)但把收益也让掉了。

## 3. 反过拟合门(全 FAIL)

- **DSR(非清零 N=2418)**:全臂 **0.001–0.004 ≪ 0.95**(主门);D1 两容器 0.0037 / 0.0021。**无臂存活**。
- **SPA p = 0.27**(Hansen)/ **Romano-Wolf 全 not-rejected** → 无臂显著胜 CSI300 hold(t vs csi300:eq_5 +0.49 / buf40_5 −0.26)。
- 账本已 append `ds.d1_dividend_lowvol`(effective_n=1,ONC 去相关;债只增不减)。

## 4. owner 判据落点(synthesis §4)+ 正面 partial

| 判据 | eq_5 | buf40_5 | 读 |
|---|:--:|:--:|---|
| 净盈 > 0 | ✅ +475k | ✅ +159k | 过 |
| 熊市累计 ≥ 0 | ✅ +0.209 | ✅ +0.082 | **过**(防御宇宙熊市不亏)|
| 股灾切片全不崩 | ❌ | ❌ | 2018 −0.171/−0.072、2022 −0.072/−0.030 为负;2015/2016 空缺 |
| MDD 可控(披露)| 25.62% | **14.78%** | vs 反转 54% / CSI300 45% **显著低**(buf40_5 尤佳)|
| **胜 placebo(t≥2)** | ❌ −0.98 | ❌ −0.85 | **输给随机 = 决定性 FAIL** |
| DSR ≥ 0.95 | ❌ 0.0037 | ❌ 0.0021 | 全 FAIL |

**裁决 = FAIL**:D1 防御选股**排名**不过关键门(输随机 placebo + DSR≪0.95 + 两股灾负)。

**🔑 正面 partial(诚实,值得带进 D2-D4)**:防御**宇宙过滤(排除门)+ 现金 buffer** 确实控回撤 —— buf40_5 MDD **14.78%**(vs CSI300 45% / 反转 54%),熊市累计正,暴露 0.36。**即:「选 inherently 防御的宇宙 + 留现金」控住了回撤,但宇宙内的块加权排名没有选股 alpha(随机同样甚至更好)**。吻合 synthesis §0.4「防御因子 = 宇宙质量过滤器,非排名择时器」—— 本刀在 20d horizon 上把这条从假说变成实测:**过滤器有效,排名器无效**。

## 5. 诚实 caveat

- **train_val only**;真 OOS/前向 = owner-gated look-once,未做。
- **可排名窗 2017-2025**(accr 两年报限制 + statements 存档始 2015)→ 2015/2016 两大股灾未覆盖;若要覆盖须 statements 存档回补 2013/2014(不在库)或改 spec 剔 accr(= 事后改冻结 spec,不做)。
- **随机 placebo 暴露高于 D1**(0.99 vs 0.77;buf 0.47 vs 0.36)→ 部分收益差来自暴露;但暴露匹配容器下(同 cap)D1 仍不胜随机(t<0),且「beats-placebo t≥2」是判 alpha 的门,D1 明确不过。
- **beta 用 ETF 原始 fund_daily close**(非除息调整),二阶影响,已披露。
- FAIL 报 FAIL:不移球门、不事后调符号/权重/剔因子拯救。

## 6. 对纲领的意义(供 owner 定向,非自动推进)

1. **D1 as-a-ranker 被证伪**(输随机 + DSR 不过);**D1 as-a-universe-filter + 现金 buffer 控回撤成立**(正面 partial)。
2. **下一步(owner 判,不替选)**:按测试序 → **D2 防御宇宙反转**(在 D1 式防御宇宙上跑反转排名,测「防御宇宙 + 反转 alpha」是否 > 纯反转 / 纯防御排名)。D2 正好检验本刀的核心洞察:如果防御是宇宙过滤而反转是排名 alpha,D2 = 两者结合可能是出路。
3. 若 owner 认可「防御宇宙 + buffer 控回撤 + 反转排名」方向 → D2 是自然承接;若认为选股 alpha 整体不成立 → 回到风控/退出线(但那些已 FAIL)或前向裁决现有反转(FW look-once)。
