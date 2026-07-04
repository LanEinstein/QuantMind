# 防御 sleeve 确认性科学门回测结果(train_val only)— **✅ 科学门 PASS(DS 线首个)**

> **性质**:SLV-0 Step1 确认性科学门(风险性质,非排名 alpha)。上位 = `defensive-sleeve-spec-and-forward-validation-plan-2026-07-04.md` + amendment(sleeve 承重=风险性质,placebo/DSR 披露不作门,认证移前向)。
> **窗口**:`20150202 → 20250418`(125 monthly rebalances / 2501 daily / universe 1818)。sealed test 永不读。
> **spec**:`defensive_sleeve_spec.py` 冻结,`spec_hash=c1d058c3…`(评测前定死)。账本 append `ds.defensive_sleeve`(effective_n=1,floor 2420→2421)。
> **一句话**:**部署 sleeve(D1 防御宇宙 gates + dv_ratio top5 等权 + buf40 现金 buffer)科学门 PASS** —— net **+1,500,889** / MDD **19.58%**(≤20% 上界,vs CSI300 46%)/ 熊市累计 **+0.196** / turnover **0.01**(极稳,125 月仅 9 fills=红利低波慢腿)。**且意外地胜同宇宙随机 placebo(paired-t +2.54,三 regime 全胜)**——dv_ratio 选择本身有真实(部分 size 驱动)边,不同于反转/分析师动量输随机。**但**:DSR 0.228 ≪ 0.95(披露,样本内认证仍不可达,AP-0.5 墙不变)+ 股灾切片非全正(2018 −15.3%,但远轻于反转 −40%)。→ **sleeve 作可部署风险产品验证成立,送预注册前向。**

## 1. 装置

D1 防御宇宙 gates(`defensive_d1_ranker.apply_exclusion_gates`:彩票顶decile剔/ROE>0/GPM非底decile/dv_ratio≥中位/排除四件套/涨跌停剔/bottom-30% size cut)→ **最简选择 = 原始 dv_ratio 降序 top-5 等权**(无中性化,无 block ranker)→ `run_gate_backtest` 冻结事件循环(T+1/分板块滑点/¥5佣金,20d 月度)× buf40_5 部署容器 + eq_5 满仓对照 + 暴露匹配 random/sizematched placebo。**诚实**:sleeve 宇宙 = D1 exclusion **gates only** = D1 排名器**持有书的严格超集**(D1 另要求全 7 因子 neut 齐,dv_ratio 简规则不需)→ 测的是 sleeve **自己**的风险性质,非 D1 14.78% 字节复现。code-review high 前置门 4 findings 修(超集诚实披露/删死 neut 计算〔byte-identical〕/gate 列存在检查/isfinite 守卫)。

## 2. 逐臂主表(初始 ¥1,000,000)

| 臂 | net P&L | MDD | 均暴露 | DSR(披露) | 熊市累计 | vs 随机(t) | turnover | fills | 守恒 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|:--:|
| **sleeve_buf40_5**(部署) | **+1,500,889** | **19.58%** | 0.453 | 0.2278 | **+0.196** | **+2.54** | 0.01 | 9 | ✅ |
| sleeve_eq_5(满仓对照/naive 基线) | +3,436,270 | 33.48% | 0.717 | 0.1924 | +0.346 | — | 0.02 | 11 | ✅ |
| placebo_random_buf40_5 | +105,957 | 28.96% | 0.404 | 0.0014 | +0.149 | — | 0.00 | 5 | ✅ |
| placebo_sizematched_buf40_5 | +701,267 | 22.70% | 0.505 | 0.0269 | +0.340 | — | 0.00 | 5 | ✅ |
| csi300_hold(beta 门) | +196,200 | 46.32% | 1.00 | 0.0020 | +0.241 | — | 0.00 | 1 | ✅ |

- **部署 sleeve 净盈 +1.5M ≫ CSI300 +196k**,MDD **19.58% vs 46%**(≈减半),暴露 0.45(buffer working);极低换手(dv_ratio top-5 近乎买入持有最高息防御名)。
- **胜同宇宙随机 top-5**(random +106k;paired-t **+2.54 ≥ 2**)—— **dv_ratio 选择在防御宇宙内真有边**,三 regime 全胜(bull +0.50 vs random −0.10 / bear +0.20 vs +0.15 / side +0.28 vs +0.11);**vs sizematched +1.13**(正但不显著)→ **边部分来自 size(大盘高息倾斜),dv_ratio 增量真实但弱**。

## 3. 科学门落点(风险性质,amendment)

| 判据 | sleeve_buf40_5 | 角色 | 读 |
|---|:--:|---|---|
| 净盈 > 0 | ✅ +1.5M | owner 判据 | **过** |
| 熊市累计 ≥ 0 | ✅ +0.196 | owner 判据 | **过**(三 regime 全正) |
| 机械 MDD 上界(≤0.20 披露) | ✅ 19.58% | disclosure | **过**(buffer 把 eq_5 33.5%→19.6%,减 13.9pt) |
| **science_gate_pass** | ✅ **True** | 综合 | **DS 线首个科学门 PASS** |
| 股灾切片全不崩 | ❌(部分) | owner 判据(披露) | 2018 −15.3% / 2015 −8.2% / 其余 −0.2~−2.7%;2024 +3.3%——**远轻于反转 −40%,但非零** |
| DSR ≥ 0.95 | ❌ 0.228 | **disclosure** | 全候选最高(反转 0.005/分析师 0.039)但仍 ≪0.95;**样本内认证不可达(AP-0.5),认证走前向** |
| SPA/RW(披露) | — | disclosure | SPA p=0.016;唯一 RW-rejected=sleeve_eq_5(满仓 t vs CSI300 +2.29);buf40 t +0.89(低暴露) |

## 4. 诚实解读

1. **sleeve 作可部署风险产品验证成立**:承重主张(机械 MDD 上界 + 熊市不亏)确认——buf40 现金 buffer + 防御宇宙把 MDD 压到 19.6%(CSI300 46% 的 43%),熊市正,极稳。这是**整条 DS 线第一个过其判据的东西**,且正是策略收敛到的 sleeve。
2. **dv_ratio 选择胜随机(+2.54)= 意外正面**:不同于反转/分析师动量(输随机),**红利低波/dividend 选择在防御宇宙内有真实边**(三 regime 全胜)。但:① 部分是 size(vs sizematched 仅 +1.13);② dv_ratio=红利因子,memo 警示中高拥挤(well-known);③ **DSR 0.228 ≪ 0.95**,样本内不可认证(AP-0.5 不因结果强而改)。→ sleeve 承重仍应归**风险性质**(数月可验),dv_ratio 边作**披露的正面 bonus**,认证走前向。
3. **股灾非全正但轻**:2018 −15.3% 是唯一较深切片(其余 −0.2~−8%,2024 正)。owner"股灾全不崩"严格未满足,但幅度远轻于反转书(−40%),且被 MDD 上界(19.6%)聚合捕获。诚实披露。
4. **与"排名层双证否"不矛盾**:反转/分析师=**快/信息流排名**输随机;dv_ratio=**慢价值倾斜**(近买入持有),是 sleeve 的选择腿非快排名 alpha,且 DSR 不过、承重归风险。

## 5. 诚实 caveat

- **train_val only**;真 OOS = owner-gated 前向存活认证。DSR 0.228 预声明披露(不作门)。
- **sleeve 宇宙 = D1 gates only**(D1 持有书的超集,超集诚实已披露 §1);dv_ratio 原始未中性化(最简规则 committed)。
- **股灾 2015/2016 覆盖薄**(D1 panel accr 两年报限制→rankable 从 2017 起?实测 125 reb 全覆盖 20150202 起,dv_ratio 不用 accr→2015/2016 有;但切片 −8.2%/−2.7% 已含)。
- **红利因子拥挤**(memo):dv_ratio 是公开 well-known 因子;sleeve 承重是风险性质非该 alpha 的独占性。
- **FAIL 报 FAIL 原则不变**:此处是 PASS,如实报 PASS + 全部 caveat;前向若 kill-switch breach 如实停。

## 6. ⏭️ 下一步(SLV-0 Step2,owner-gated)

sleeve 科学门 PASS → **送预注册前向验证**(`defensive-sleeve-spec-and-forward-validation-plan-2026-07-04.md` Step2):git 冻结 spec(c1d058c3)+ kill-switch(mdd 0.25/bear −0.05/连6期跑输 naive eq_5 基线/min 8 期)+ 登记前向起点 → owner-gated 摄取增量 + 重启 + 月度 shadow + 存活认证 → P0-6 45 日 go-live shadow → 上线(模拟实盘/飞书人工,永禁真实下单;执行可行性用 ¥1万 真 shadow)。**分析师动量 tilt 仍 OFF**(可选,启用需重冻+重验)。
