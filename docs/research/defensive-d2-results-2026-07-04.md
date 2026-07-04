# 防御候选 D2(防御宇宙 × 反转排名)dev 回测结果(train_val only)— **branch (c):反转排名边被推翻**

> **重定性(amendment 2026-07-04)**:D2 回答「≤5 槽**反转排名层**配不配叠在防御 sleeve 上」,**不**回答「能否过四门」(不能,DSR 预声明必 FAIL,照披露)。判据 = **选拔门**(胜自身暴露匹配随机 placebo,paired-t≥2 双容器 joint)+ owner 三判据(熊市累计≥0 / 股灾切片不崩 / 净盈>0);DSR/SPA/RW 降披露。
> **窗口**:`20150209 → 20250425`(**497** weekly rebalances / **2484** daily / universe **1823**;D2 防御宇宙保留 **56.5%** 行)。sealed test 永不读(真 OOS = owner-gated look-once)。
> **spec**:`defensive_d2_spec.py` 冻结,`spec_hash=a548273b9e46cbb2…`(评测前定死,评测中未改;仅分支 KEY 入 hash,prose reword 不扰动)。账本 append `ds.d2_reversal_on_defensive`(kind=ablation,effective_n=1,仅 2 D2 臂计债;非清零 floor **2418→2419**)。
> **✅ A0 字节锚(fail-closed 通过)**:a0_eq_5 net **+571,098.64** / MDD **56.04%**、a0_buf40_5 net **+294,340.42** / MDD **31.40%** —— **逐字节复现 `slot_frontier_result.json`**,确保 A0 vs D2 同引擎/同窗口可比。
> **裁决**:**branch = (c)**。**a0_beats_own_placebo_joint = False** —— **纯反转排名在组合书层不胜同宇宙随机 top-5**(其历史净盈主要是宇宙/暴露/轮动,非排名 alpha);D2 防御宇宙反转同样不胜随机(选拔门 FAIL)。防御**宇宙过滤**改善 eq_5 熊市累计(−0.22→+0.20,复证 D1 洞察=宇宙效应),但**在现金 buffer 部署容器上反而把净盈打成负**(d2_buf40_5 −64k)。

## 1. 装置

单 ablation / 单 bar source / 单窗口,A0 weekly(horizon=5,与 frontier 同节奏)。A0 中性化因子(`rev_1d`/`max_5d`/`turn_spike`/`ideal_amplitude_20d` + industry SW-L1 + log_size 删30%,winsor 1%,min_obs 20)**读自 crowding CSV**(frontier 精确输入,byte anchor 前提;D2 panel 的 CSV round-trip 有 ~1e-15 浮点漂移会翻转 top-5 平局 → 破锚),仅 D2 过滤列 `dv_ratio`/`roe`/`gpm` 从 D2 panel 逐行 splice(不入锚)。ranker = **复用** `exit_veto_panel.build_ranker_table`(与 A0 字节一致,非复制)。**D2 唯一改动 = 宇宙过滤**(vol_20d ≤60 分位 keep + max_20d 顶 decile 剔 +〔dv_ratio≥中位 **OR** roe>0∧gpm>底 decile〕inclusion)。11 臂 × 双容器 + **暴露匹配** random/sizematched placebo;`run_gate_backtest` 冻结事件循环(T+1/分板块滑点/¥5 佣金/≤1 rotation/日)。code-review high 前置门:3 correctness/robustness 修(falsy-zero owner 门→UNTESTED / benchmark 出 SPA·RW family / 分支 prose 出 hash)。

## 2. 逐臂主表(初始 ¥1,000,000)

| 臂 | net P&L | MDD(披露) | 均暴露 | DSR(披露) | 熊市累计 | vs 自身 random(paired-t) | 守恒 |
|---|---:|---:|---:|---:|---:|---:|:--:|
| **a0_eq_5**(全宇宙反转,**字节锚**) | +571,099 | 56.04% | 0.719 | 0.0058 | **−0.220** | **−1.082** | ✅ |
| **d2_eq_5**(候选,科学门) | +741,873 | 51.72% | 0.725 | 0.0088 | **+0.204** | **−0.978** | ✅ |
| **placebo_random_a0_eq_5** | **+2,156,151** | 53.76% | 0.999 | 0.0324 | −0.088 | — | ✅ |
| **placebo_random_d2_eq_5** | **+3,205,016** | 53.94% | 0.997 | 0.0537 | −0.085 | — | ✅ |
| placebo_sizematched_d2_eq_5 | +774,903 | 43.06% | 0.999 | 0.0101 | +0.138 | — | ✅ |
| **a0_buf40_5**(**字节锚**) | +294,340 | 31.40% | 0.296 | 0.0052 | **−0.159** | **−0.924** | ✅ |
| **d2_buf40_5**(候选,部署门) | **−64,050** | 36.54% | 0.334 | 0.0002 | **−0.113** | **−1.646** | ✅ |
| placebo_random_a0_buf40_5 | +777,714 | 32.38% | 0.570 | 0.0135 | −0.047 | — | ✅ |
| placebo_random_d2_buf40_5 | +1,191,009 | 44.57% | 0.627 | 0.0207 | −0.064 | — | ✅ |
| placebo_sizematched_d2_buf40_5 | +307,172 | 22.82% | 0.519 | 0.0050 | +0.060 | — | ✅ |
| csi300_hold(beta 门) | +176,112 | 46.33% | 1.00 | 0.0018 | −0.035 | — | ✅ |

- **决定性负结果**:**同宇宙随机 top-5(买入持有,暴露~1.0,turn~0)大胜所有排名臂** —— random_d2_eq_5 **+3.21M** / random_a0_eq_5 **+2.16M** ≫ d2_eq_5 +742k / a0_eq_5 +571k。反转排名 vs 随机 paired-t 全负(a0 −1.08/−0.92,d2 −0.98/−1.65),**须 ≥2 才算有排名 alpha → 全 FAIL**。
- regime(bear n=128 / bull 138 / side 230):random 臂靠满仓吃 2015-25 大盘反弹(bull +1.15/+1.21),排名臂让掉了收益(暴露 0.72 vs 0.99)。

## 3. 四大对照读数

- **① A0 vs A0-random(证据洞裁决 —— 补 D1 ablation 明文 DEFERRED 的债)**:a0 纯反转从未在**组合书层**与随机 placebo 对比过(QGR-3 只验 IC **符号**;C1a/QGR-4 placebo 全在 EXIT 层;frontier 无 placebo 臂)。本刀首次对比:**a0_eq_5 vs random paired-t −1.082,a0_buf40_5 −0.924 → `a0_beats_own_placebo_joint = False`**。**⇒ 纯反转的组合书层排名边被推翻**:其历史 +571k 净盈主要是**宇宙 + 暴露 + 轮动**效应,不是排名技艺(random top-5 净盈 4×)。这是对既往「反转 = 已验证快腿排名 alpha」定性的**修正记录**(见 §6)。
- **② D2 vs D2-random(选拔主门)**:d2_eq_5 vs random **−0.978**,d2_buf40_5 **−1.646** → `d2_beats_own_placebo_joint = False`。防御宇宙上的反转排名同样无选股 alpha,输给防御宇宙内随机 top-5(+3.21M)。
- **③ D2 vs A0(宇宙因果)**:d2_eq_5 vs a0_eq_5 paired-t **+0.309(不显著)**。**防御过滤把 eq_5 熊市累计从 −0.220 翻正到 +0.204**(净盈 +742k>571k,MDD 51.7%<56.0%)= **复证 D1「防御=宇宙质量过滤器」**。**但部署容器相反**:d2_buf40_5 净盈 **−64k**(vs a0_buf40_5 +294k),MDD **升** 31.4%→36.5%,熊市仍 −0.113 —— **60% 现金 buffer + 防御过滤 + 5d 反转churn(turn 0.10/fills 139)在股灾里反复接刀,把 buffered 容器打成负**。
- **④ D2 vs sizematched(防御过滤 ≠ size 倾斜)**:d2_eq_5 +0.078(平),d2_buf40_5 −1.062(输 size 匹配)→ D2 相对 size 控制无增量。

## 4. 反过拟合门(披露,预声明 FAIL,照算照报)

- **DSR(非清零 N=2419)**:全臂 **0.0002–0.0088 ≪ 0.95**(主门);无臂存活。
- **SPA p = 0.024**(Hansen)但**唯一 Romano-Wolf rejected 的臂 = `placebo_random_d2_eq_5`**(t vs CSI300 = **2.37**)—— **过门的是随机臂,不是技艺臂**(所有 a0/d2 排名臂 t vs CSI300 < 1)。这恰是「显著性属于随机暴露而非排名」的教科书图示;benchmark 已排除 family(code-review 修)。
- 账本 append `ds.d2_reversal_on_defensive`(effective_n=1,ONC 去相关;债只增不减,floor 2419)。

## 5. 选拔门 / 披露门落点表

| 判据 | d2_eq_5 | d2_buf40_5 | 角色 | 读 |
|---|:--:|:--:|---|---|
| 胜自身 random(t≥2) | ❌ −0.978 | ❌ −1.646 | **选拔主门** | **joint FAIL** |
| 熊市累计 ≥ 0 | ✅ +0.204 | ❌ −0.113 | owner 判据 | eq_5 过(宇宙效应)/ buf 不过 |
| 股灾切片全不崩 | ❌ | ❌ | owner 判据 | 六切片(含 2015/2016)几乎全负 |
| 净盈 > 0 | ✅ +742k | ❌ −64k | owner 判据 | buf40_5 亏损 |
| MDD(披露) | 51.72% | 36.54% | disclosure | 远高于 D1 buf40_5 14.78% |
| DSR ≥ 0.95 | ❌ 0.0088 | ❌ 0.0002 | disclosure | 预声明 FAIL |

**六股灾切片(cum_return,现全覆盖,不再 n=0)**:a0_eq_5 = 2015 **−0.395** / 2016 **−0.273** / 2018 **−0.349** / 2020 +0.096 / 2022 **−0.251** / 2024 −0.049;d2_eq_5 类似(2015 −0.448 … 2022 −0.155)。**反转书在每一次 A 股股灾都深亏**(接刀本性),防御过滤未改这一点。

## 6. 三分支落点(预注册,诊断面 owner 判)

| 分支 | 布尔 | 机制解读 |
|---|:--:|---|
| **(a)** D2 胜自身 placebo joint AND owner 门改善 → 排名层入围送前向 | **False** | D2 选拔门 joint FAIL(−0.98/−1.65),owner 门未改善(buf 亏损/熊市负/股灾负)。 |
| **(b)** NOT joint AND d2 容器仍呈 sleeve 风险画像 → 弃排名层,sleeve-only | **False** | d2_buf40_5 **不呈** sleeve 画像(净盈 −64k、熊市 −0.113、MDD 36.5%)——**5d 反转 churn 在防御宇宙上也不防御**;真正的 sleeve 画像属 D1 式**慢腿**(dividend-lowvol,20d,buf40_5 MDD 14.78%/熊市正),不属 D2。 |
| **(c)** NOT a0 胜自身 random → 反转书层排名边被推翻 + 定性修正 | ✅ **True** | **a0 纯反转组合书层不胜随机 top-5(−1.08/−0.92)** → 反转历史净盈 = 宇宙/暴露/轮动,非排名 alpha。排名层整体**死刑**(a0/d2 同命)。**(c) 与 (a) 不并存**(无矛盾需披露)。 |

## 7. 诚实 caveat

- **train_val only**;真 OOS = owner-gated look-once,未做(DSR 预声明 FAIL 已照报,认证按 amendment 移前向)。
- **D2 覆盖 2015/2016**(反转/过滤不用 accr → 无 D1 两年报限制)→ **六股灾切片齐**,D1 空缺补上,结论更稳。
- A0 中性化/因子读自 **crowding CSV**(精确复现 frontier);dv/roe/gpm splice 自 D2 panel(round-trip 无害,只喂过滤,不入 byte anchor)。行序逐行 assert 对齐。
- 等日期集 fail-closed 门(spec §5.2 committed)**全窗验证通过**(a0_dates==d2_dates,d2 每日非空)—— 防御过滤未清空任何 rebalance 日。
- `beats_own_random` 是 amendment 选拔主门(paired-t≥2 joint);random 暴露高于排名臂(~1.0 vs 0.72),但选拔门问的是「排名是否胜过同宇宙随机」,答案明确否(t<0)。
- **FAIL 报 FAIL**:不移球门、不事后调阈值/符号/剔因子拯救。falsy-zero owner 门已修(空 bucket = UNTESTED 非 pass),否则 2015/2016 未覆盖时会虚高 promotion 读。

## 8. 对纲领的意义(供 owner 定向,非自动推进)

1. **反转排名层 = 组合书层被证伪**(branch c):a0/d2 均输同宇宙随机 top-5。**这是对『反转 = 已验证快腿 alpha』的定性修正** —— QGR-3 验的是横截面 IC 符号,不等于 ≤5 槽组合书 + 轮动机制下的可交易排名边。round-1..4 与 slot_frontier 的反转净盈应重读为**暴露/宇宙/轮动**,非选股技艺。
2. **复证 D1 核心洞察**:防御**宇宙过滤 = 宇宙质量效应**(改善 eq_5 熊市 −0.22→+0.20),**非排名效应**;且 5d 反转 churn 会**抵消** sleeve(在 buffered 容器上把 +294k 打成 −64k)。**真正控回撤的是 D1 式慢腿防御 sleeve(dividend-lowvol,20d,现金 buffer),不是反转-on-defensive**。
3. **下一步(owner 判,不替选)**:
   - 按 amendment,**D3/D4 默认跳过**(同族防御排名,机制预测同命,每刀加账本债);branch (c) 是「意外证据」但方向是**否定排名层**,不构成回访 D3/D4 的理由。
   - **排名层(反转 + 分析师动量候选#2)整体存疑** → 若 owner 认可 branch (c),产品终态收敛到 **sleeve-only(D1 式防御宇宙 + 现金 buffer,承重=风险性质非排名 alpha)**,认证移前向预注册 + kill-switch。
   - 或 owner 要求把 **分析师修正动量**(排名层候选 #2)按同一选拔协议测一刀,以确认「排名层死刑」是否对信息流因子亦成立(反转是价量因子,分析师动量是信息流,机制不同,可能是唯一未被 (c) 覆盖的排名候选)。
