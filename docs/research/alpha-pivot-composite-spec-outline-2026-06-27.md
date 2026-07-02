# Alpha-pivot 行动大纲 —— 单一 committed 复合 ≤5 ranker spec(2026-06-27)

> **状态**:第二步「行动大纲」**v3 = codex 2 轮对抗收敛**(R1 8 findings 全吸收 → R2 「sound,3 口径点修后可进 plan mode 无需重开 R1」→ 3 点全修)· **⏭️ 进第三步 plan mode → owner ExitPlanMode 批准 → 实施** · **作者**:Claude(Opus 4.8)
> **上位**:`docs/handoff/quant-alpha-pivot-handoff-2026-06-27.md` + `qgr-criterion-rebar-amendment-2026-06-27-*` + `qgr-confirmation-stop-swing-sizing-amendment-2026-06-27`(P-A..P-E)+ `quant-first-gate-rearch-plan-2026-06-21.md` §4(两层评测)
> **owner 第二步决策(2026-06-27,AskUserQuestion)**:① alpha 方向 = **建复合 committed spec**;② 主旋律维度 = **后置**;③ push = hold。
> **codex R1 处置摘要(§12 详)**:P0-1/P0-2(spec 是 adaptive 不是 1 trial)→ **采纳 Fix A:删 inclusion screen,先验符号等权全集 = fixed prior spec,IC 仅披露**;账本口径重写(nominal 2412 / effective 2387 实测核对);P1-3 底部确认机制声称降级 + 加过滤 baseline;P1-4 双容器都过四门;P1-5 PIT 硬验收测试;P1-6 baseline 面板扩 attribution 臂;P2-7 加**收益盲的 power/MinBTL 前置计算**;P2-8 加 size-drift/增强指数残影披露 = FAIL 信号。

---

## 0. 一句话

在 owner 已答的固定容器(≤5 集中 + 现金 buffer,P-E)里,用 **provenance-gated 文献/已验先验**把**预声明的全因子集**(反转快腿 + 分析师修正 + 质量,先验符号、等权 z-score)拼成**一个固定先验 spec(fixed prior spec,非数据自适应筛选)** + 底部确认作宇宙健康过滤,在可复用竞技场过**反过拟合四门**(不放宽)。**绑定约束是 ranker 的风险调整 alpha 太弱**(frontier 证无配置过 DSR≥0.95)。**先做收益盲的 power 前置计算**判「过门理论可达性」,再决定是否花 promotion trial;过门才冻结 + owner-gated look-once 前向。**诚实预期:过门是陡坡,很可能不过 → 预承诺 FAIL 报 FAIL + owner fork,绝不移动球门/调权重凑 DSR(=round-4 死法)。**

---

## 1. 框定回顾(已答,不再 litigate)

| 维度 | 结论 | 数据依据 |
|---|---|---|
| 风险容器 | **≤5 集中 + 现金 buffer**(P-E);分散**伤害** | `slot-frontier-results-2026-06-27` |
| EXIT/de-risk overlay | **净有害,不再建** | C1a / B1 / B2 / QGR-4 全 FAIL |
| 绑定约束 | **选股 alpha 质量**(非容器) | frontier:全配置 DSR 0.003–0.006,eq_5 最高 0.0059 |
| 判据 | 绝对净盈 + 控回撤(MDD 仅披露)+ **四门不放宽** | `qgr-criterion-rebar-amendment-2026-06-27` §2 |

---

## 2. 科学假说(可证伪;机制声称按 codex R1-P1-3 校准)

**H0**:在 ≤5 容器里,纯反转 ranker(`{rev_1d,max_5d,turn_spike}` size-neut)= 当前最优可得风险调整边(eq_5 DSR 0.0059);任何先验复合扣 deflation 债后不可辨于运气。

**H1(校准后,不过度声称)**:纯反转**无差别买超卖名**;加以下 honest 正交成分**可能**提升 ≤5 篮子的**风险调整**边,但每条都须被 attribution 臂从 beta/质量/覆盖/过滤暴露中**隔离**出来才算数:
- **(a) 底部确认作全截面宇宙健康过滤**(QGR-3 ⑧:**全截面**有效 t +3.8/+4.2/+5.0,但 **dip 池内增量弱不显著 t≈1.4 → 是宇宙质量/健康过滤器,非 dip 择时器**)。**声称降级**:不声称「精准剔飞刀」,只声称「全截面健康过滤**可能**改善候选池」;须 `纯反转+同一过滤` baseline 把「过滤暴露变化」与「ranker alpha」分开。
- **(b) 质量地板/tilt**(roe/gpm/ep +、accr −;round-1/AF-003)。
- **(c) 分析师修正信息流 tilt**(round-4 R4-4 正交块;价格无关信息流)。

**判据**:H1 成立 ⟺ 复合 spec 过四门 **且**严格胜 attribution baseline 面板(尤其纯反转 + 各单加成分臂)。**任一成分的「胜出」若被 size-drift / 增强指数残影解释(R1-P2-8)= FAIL 报 FAIL。**

---

## 3. 候选 alpha 组件 + provenance + 诚实分级(**全集预声明,先验符号,等权,不筛**)

> **关键纪律(R1-P0 修正)**:组件集 + 每组件**先验符号** + 等权规则**全部在评测前预声明并 hash**(§4)。**绝不用 train_val IC 决定哪些进 spec / 不在样本内拟合符号**——否则就是数据自适应筛选(隐藏多重比较)。从零验 IC(§6)**仅披露**(看先验符号是否在 5td 兑现 + 机制可见 + FAIL 报 FAIL),**不改 spec 组成**。

### 3.1 ranker 因子(入 ≤5 排名;先验符号 committed)

| 块 | 因子(预声明) | 先验符号 | provenance | 分级 |
|---|---|---|---|---|
| 反转 | `rev_1d` `max_5d` `turn_spike` | − − − | **QGR-3 ⑦ 已从零验存活**(neut\|t\| 4.4/11.3/5.3;rev_3d 已因共线 ret_5d 0.72 在 QGR-3 剔,**该剔已 debit 于 legacy/QGR-3**,本刀沿用不重筛) | ✅有据 |
| 分析师 | `np_rev`(幅度)`rev_diff`(广度)`cover_chg`(覆盖) | + + + | round-4 R4-4 **已定的正交子集**(np_rev/rev_diff 幅度vs广度正交;`tp_impl` 因 `tp`=利润总额歧义**预声明剔除**);`report_date<d` PIT | 🟡谨慎(月级验,5td 兑现存疑,IC 披露) |
| 质量 | `roe` `gpm` `ep_ttm` `accr` | + + + − | round-1/2 carry + AF-003;`ann_date<d` vintage PIT | 🟡谨慎(月级验,5td 兑现存疑) |

> 块内/块间**等权**(§4)。R4-4 的块内正交子集选择是 round-4 既做、已 debit 的 provenance,非本刀样本内筛选。

### 3.2 宇宙过滤(二值 include/exclude,**不入排名**;committed 阈值)

- **底部确认 core-4**(QGR-3 ⑧ committed:缩量 vol_dryup / 无技术破位 / 无困境 PIT-ST via namechange / 质量地板)——**原样不调**;cyq 成本带 QGR-3 ⑧ 证非承重 → 🔴 **预声明剔除**。
- **排除四件套**(系统既有硬排除)+ **at-limit 不可成交名剔除**(反转 loser-leg 跌停飞刀)。

### 3.3 明确不入首个 spec(诚实剔除)

🔴 大资金/moneyflow/北向日频/龙虎榜(陷阱清单弱死)· held-EXIT/止损/避顶部/regime overlay(C1a/B1/B2/QGR-4 净有害)· ⏸️ 主旋律维度(owner #2 后置)· ⏸️ P-A 入场确认门 overlay(独立刀,C1a 证 EXIT 侧确认有害,入场侧未验)· ⏸️ 1日动量/overnight/涨停结构(QGR-3 ⑦ 已丢)。

---

## 4. 单一 committed spec 防债(铁律;R1-P0 重写)

> 非清零账本 **AP-前基数实测 nominal 2412 / effective 2387**(legacy = 2348 grid + 4 test_read + 19 diagnostics + 5 robustness;+ MFI/QGR/frontier 块)。**越搜越难过门**。本刀**含 AP 后**:nominal 2412+33=**2445**,effective 2387+30=**2417**(§4.4)= A4 DSR 的 deflation N。

**spec = fixed prior spec(非自适应筛选)**:

1. **预声明并 hash**(family `qgr.alpha_pivot`,评测前,先于结果):§3.1 全因子集 + 每因子先验符号 + 等权规则 + §3.2 过滤阈值 + §5 双容器协议 + 四门口径。
2. **a priori 权重(committed,评测前 hash,只用一套,非 best-of)**= 块内等权 z-score(每因子按先验符号对齐 → 截面 z-score,size+行业中性化后)→ **块间按 ex-ante 信心/horizon 权重**(codex R2:naive 1/3 等权可能被 5td 存疑的慢成分〔分析师/质量〕拖垮 A4)。**块间权重从 provenance 分级派生,非样本内拟合**:
   - **默认(推荐)= horizon/信心加权**:反转块(✅有据,5td-native)权重高、分析师+质量块(🟡谨慎,仅月级验)权重低。committed 规则:权重 ∝ 分级分(✅=2,🟡=1)→ 反转 0.5 / 分析师 0.25 / 质量 0.25(owner 可在 plan 批准时改具体数,但**评测前定死**)。
   - **备选 = naive 块间 1/3 等权**(零权重自由度,最简,但 codex 警自我击穿风险)。
   - **铁律**:owner plan 批准时**选一套**→ hash → 评测后**绝不改**;**无符号/组件任何样本内自由度**,IC 出来后不许调权重(=round-4 死法)。
3. **无 inclusion screen**:全预声明因子**一律入** spec(即便某因子 5td IC 弱/符号疑)——这是先验复合的诚实代价,不许样本内丢。从零验 IC = **纯披露**(§6)。
4. **账本记账(可审计 nominal/effective,R2 数值订正)**:在 AP-前基数(nominal 2412 / effective 2387)上新增:
   - **diagnostics 块** `qgr.alpha_pivot.ic`:分析师(3)+ 质量(4)= 7 因子 × 4 horizons = **28 nominal**(反转已在 QGR-3),kind=diagnostics;按既有 `legacy.diagnostics` 约定 **effective_count = nominal = 28**(保守:虽 IC 不喂 spec,不抵债)。
   - **ablation 块** `qgr.alpha_pivot.attribution`:§5.3 attribution 臂(A1/A2/A3)= **3 nominal**,kind=ablation,**effective 1**。
   - **single** `qgr.alpha_pivot.composite`:A4 复合 spec × 双容器(eq_5 门 + buf40_5 部署门,joint pass 非 best-of)= **2 nominal**,kind=single,**effective 1**。
   - **合计本刀:nominal +33(→2445)/ effective +30(→2417)**。**A4 DSR 的 deflation N ≈ 2417** = `max(legacy effective floor + appended effective, 新 ONC N)`。
5. **绝不做**:权重网格搜 / 阈值调优 / inclusion screen 选因子 / 样本内拟合符号 / 多 spec 择优 / 报 best-of-horizon 当 spec(=round-4/Goodhart 死法)。

---

## 5. 评测协议(可复用竞技场,A 层)

### 5.0 ⚡ AP-0.5 收益盲 power 前置(R1-P2-7,**省债关键,先于竞技场**)

**不读任何 A4 收益**,仅用 deflation N(=2417,§4.4)+ 样本结构 + **AP-0 预声明的矩/HAC 规则** + **已披露的纯反转 eq_5 Sharpe**(frontier 既有,非新 peek)算 DSR≥0.95 所需的 **annualized-SR 下界**(SR0 deflation 阈 + Φ⁻¹(0.95) 反解;Bailey-LdP DSR 公式)。
- **AP-0 必须预声明的输入(codex R2-1,否则 go/no-go 主观)**:① 矩 = normal(skew=0,kurtosis=3);② HAC inflation = 5td-overlap 的**结构保守上界**(`honest_gates.hac_variance_inflation`,lag=horizon−1=4,取保守上界规则而非样本估计);③ T = `onc_effective_n`(497 rebalance 重叠压缩);④ 参照基准 = 已披露的纯反转 eq_5 annualized SR(从 frontier 推,**零新 peek**)。
- **数值 go/no-go 边界(committed)**:令 `SR_req` = 反解所需 SR,`SR_ref` = 纯反转 eq_5 SR。**go iff `SR_req ≤ K · SR_ref`**(默认 **K=2**:honest 正交复合最多把风险调整边提到纯反转 ~2×;owner plan 批准时可改 K,**评测前定死**)。
  - `SR_req > K·SR_ref` → **no-go,不烧 promotion trial** → 报 owner:(a) 重审 ≤5 闸门前提,或 (b) 本刀降纯诊断(只跑 attribution IC + 相对纯反转 SPA,不申报四门)。
  - `SR_req ≤ K·SR_ref` → 进 AP-2 竞技场。
- **此前置零债**(不读 A4 收益,纯解析 + 已披露参照)。

### 5.1 容器(双容器都过四门;R1-P1-4)

- **eq_5(科学/ranker 门,主)**:full exposure,评 ranker 风险调整边最干净。
- **buf40_5(部署门)**:≥40% 现金(P-E floor)。**buffer 仅近似风险缩放器**(eq_5 DSR 0.0059 vs buf40_5 0.0052;固定佣金/整手/cash-drag/cap 致非纯线性)→ **buf40_5 须独立过四门**才可声称 deployable;否则结论限定为「ranker science gate」不声称可部署。
- 两容器**均预声明**(非 best-of);P-E 置信集中(单名~60%)**不在首个 spec 建模**(独立 sizing 层,诚实 caveat)。byte-exact:eq_5 与 frontier `eq_5` 字节核对。

### 5.2 真竞技场(全留)

`run_gate_backtest`(冻结引擎 + 前视即抛 + 涨跌停不可成交 + 分板块滑点 + ¥5 min 佣金 + 印花/过户 + T+1 + 5td 最短 + 7 弱势门轮动)。`PanelScoreProvider` 喂 committed 复合分。

### 5.3 baseline + attribution 面板(R1-P1-6 + R2-2;**双容器各一套,容器匹配**)

**关键(codex R2-2)**:A0-A4 + B1-B5 **在 eq_5 和 buf40_5 各跑一套**;比较**只在同容器内**(绝不拿 buf40_5 A4 对 eq_5 baseline)。随机 top-5 **同容器 + 同过滤宇宙 + 同交易约束/现金规则**匹配。

| 臂 | 内容 | 作用 |
|---|---|---|
| A0 | 纯反转 | H0 对照(eq_5:frontier 已测 +571k/DSR 0.0059;buf40_5:frontier 已测 +294k/DSR 0.0052) |
| A1 | 反转 + 底部确认过滤 | 隔离「过滤暴露变化」 |
| A2 | 反转 + 质量(floor/tilt) | 隔离质量贡献 |
| A3 | 反转 + 分析师 tilt | 隔离信息流贡献 |
| **A4** | **全复合(反转+质量+分析师+过滤)** | **promotion 候选(双容器 joint pass)** |
| B1-B5 | 随机 top-5(容器+过滤宇宙+约束匹配)/ screener-momentum / 流动性 / ETF-only / CSI300-hold | 防 long-beta(`run_baselines`) |

A4 须**在各自容器内严格胜 A0-A3 + B1-B5**(paired-t/SPA/RW);**单加成分臂(A1/A2/A3)揭示哪条载边 + 是否仅过滤/质量/覆盖暴露**。

### 5.4 反过拟合四门(**不放宽**;applied to A4 in BOTH containers)

DSR≥0.95(`honest_gates.deflated_sharpe_hac`,ONC+HAC+非清零 N=2417)/ PBO≤0.5(针对**真实选择规则**=本 fixed prior spec)/ SPA(Hansen,预声明 family + block bootstrap)/ Romano-Wolf(`multi_strategy_compare`)。真 CPCV 路径(`cpcv.py`,purge+embargo≥horizon,重叠路径不当独立样本)。制度分层 + 6 股灾切片 + **size/增强指数残影披露 = 量化暴露回归(R1-P2-8 + R2)**:对 A4 篮子日收益做 size(log circ_mv)+ 行业暴露回归 + 持仓平均 log-mv 分层,**量化**(非叙述)A4 相对 A0 的 size-drift;**若 A4 胜出可由 size/大盘暴露回归显著解释 → FAIL 报 FAIL(增强指数残影,不接受)**。

### 5.5 防火墙(全留)

bar-read ⊆ **train_val**(`20150209→20250425`,sealed test 永不读);真 OOS = **owner-gated look-once 前向**(post-`2026-06-12`,过 train_val 四门后才花);研究/评测**零 LLM**;size/行业中性化**删最小 30%**。

---

## 6. 从零验 IC = 纯披露(R1-P0-2;不改 spec)

1. **panel 扩展**:`build_qgr_panel`(已含 ⑦ 快腿 + ⑧ 底部确认 + fundamentals)**新增合并** 分析师(`analyst_revision_pit`,`report_date<d`)+ 质量(`fundamentals_pit`,`ann_date<d`)列;防火墙不变。
2. **从零验 IC(披露)**:分析师/质量块在 5td horizon 的 size+行业中性化 IC/ICIR/t/先验符号兑现 + vs carry/反转簇共线。**仅报告**(先验符号是否兑现;FAIL 报 FAIL),**不据此增删 spec 组件**。⚠️ IC t = 乐观 screen 非裁决(重叠窗自相关,有效 N < n_dates;round-1..4 全有强 IC 却 test FAIL 三次)。
3. **PIT 硬验收(R1-P1-5,AP-1 红线测试)**:① 每 decision date 不得见未来 `report_date`/`ann_date`(月度 snapshot 整月可读但 as-of-day 闸);② EOD close 特征**只能 T+1 执行**;③ 质量 ep/accr **来自 vintage PIT**,不混 vendor 当日 `pe_ttm` 潜在重述口径;④ 复权因子 asof pin 不混未来;⑤ `leak_probe` 全绿。

---

## 7. 决策树 + 诚实预期(预承诺,不移球门)

```
AP-0.5 power 前置(收益盲)
├─ 所需 SR 远超可达 → 不烧 trial → 报 owner:(a) 重审 ≤5 前提 / (b) 本刀降纯诊断
└─ 所需 SR 可能够到 → AP-2 竞技场
    A4 复合 spec(eq_5 + buf40_5 双容器)结果:
    ├─ 双容器都过四门 + 严格胜 A0-A3/B1-B5 + 无 size-drift 假象
    │   → ✅ 候选晋级 → owner-gated 冻结(git)+ look-once 前向(B 层处子 OOS)
    │       → 兑现 → go-live gate;不兑现 → FAIL 报 FAIL
    ├─ 胜纯反转但不过 DSR≥0.95(provisional 弱边)/ 仅 eq_5 过 buf40_5 不过
    │   → 🟡 FAIL 报 FAIL(四门不放宽)+ owner fork:
    │       (a) 接受 provisional 弱边 + 冻结等前向(原则 #1:低 DSR=低置信非门错)
    │       (b) 重审 ≤5 闸门前提(frontier 已暴露真实可能)
    ├─ 胜出仅由 size-drift/增强指数残影解释(A2/A3 attribution 揭示)
    │   → 🔴 FAIL 报 FAIL(重蹈 round-1..4 = 不接受)
    └─ 不胜纯反转
        → 🔴 H0 不被拒 → 报 owner:绑定约束或非「更多因子」而是 ≤5 前提/持仓机制/超短腿
```

**诚实预期(R1-P2-7 校准)**:DSR 是 deflated probability-like 门**非 Sharpe 倍数**;0.0059→0.95 不是「线性翻 160 倍」。AP-0.5 会先用收益盲 power 算出「过门所需 period-SR」是否在历史可达范围 —— 这是比拍脑袋「很可能不过」更硬的前置判断。**本刀科学价值**:① power 前置 + attribution 把「绑定约束是否真在因子层 vs ≤5 前提本身」用数据钉死给 owner;② 若过门(frontier 提示 deflation 可能过度惩罚,真仲裁=前向)→ 稀缺前向。**绝不为过门移动球门/调权重凑 DSR。**

---

## 8. 复用件 / build-new 映射(grep 核实)

**REUSE**:`gate_backtest`(`run_gate_backtest`/`PanelScoreProvider`/`default_*`)+ `gate_bar_source.PitBarSource` + `slot_frontier`(eq_5/buf40_5 容器参照,AP-2 runner 模板)+ `build_qgr_panel`(⑦+⑧+fundamentals)+ `factor_lib`(反转/质量/分析师全注册)+ `analyst_revision_pit`/`fundamentals_pit`/`bottom_confirmation`/`neutralize`(删30%)/`leak_probe` + `honest_gates`(DSR-HAC+ONC)+ `multi_strategy_compare`(SPA/RW/FDR)+ `cpcv` + `trial_ledger`(非清零+legacy)+ `baselines` + `arena_ablation`。

**BUILD-NEW(最小)**:① panel 合并(分析师+质量并入 QGR panel)+ PIT 硬验收测试 ② 从零验 IC 披露 runner(`factor_ic_study` 模式)③ **AP-0.5 收益盲 power 前置**(纯解析,用 honest_gates 的 SR0/DSR 反解)④ committed 复合 assembler(等权 z-score,a priori)+ AP-2 竞技场 runner(`slot_frontier` 模板:A0-A4 + B1-B5 + 双容器 + 四门 + CPCV + 切片 + size-drift 披露)⑤ 结果 doc(诚实分级 + 决策树落点)。

**DEPRECATE(不碰)**:`benchmark_relative`/`benchmark_weights`/`exposure_constraints`/`long_short`;round{2,3,4}_locked_test。

---

## 9. 红线合规(全留)

sim 暂停 · 永禁真实下单 · 离线 · 仅 Tushare 官方 SDK · PIT 字节存档+checksum · **train_val only**(test 封存,真 OOS=owner-gated look-once,第 5 次极慎)· 防火墙 bar-read⊆train_val · **研究/评测零 LLM** · size/行业中性化删最小 30% · **反过拟合四门不放宽** · **非清零账本不清零,fixed prior spec 防 mining 债** · 不接 moneyflow 主路径 · 北向仅历史 · 不做 L2 · **不再建 held-EXIT/regime/避顶部 overlay** · 不碰 backend value-sleeve(AF-*)/冻结引擎字节/RiskEngine/单一构造点 · governance enum 不动(分析师机制故意不注册) · codex 前置门 · FAIL 报 FAIL · **push/摄取/live 激活/look-once = owner-gated** · 报告中文/代码 commit 英文。

---

## 10. 实施阶段(供第三步 plan mode 细化)

| 阶段 | 内容 | 交付 | gate |
|---|---|---|---|
| **AP-0** | 预声明 fixed prior spec(因子集+先验符号+**块间权重规则**+过滤阈值+双容器协议+四门口径+**AP-0.5 power 矩/HAC/K 输入**)+ hash 进 trial_ledger(diagnostics 28 + ablation 3 + single 2 nominal;effective +30→N=2417,先于结果)+ spec 常量冻结 | family `qgr.alpha_pivot` 记录 + spec 常量 | docs/ledger only |
| **AP-0.5** | 收益盲 power 前置(`SR_req` vs `K·SR_ref`,K=2) | power doc + go/no-go | **owner gate(若 no-go)** |
| **AP-1** | panel 合并 + PIT 硬验收测试 + 从零验 IC 披露 | panel builder + IC 披露 doc | 门禁绿 + codex + leak_probe 全绿 |
| **AP-2** | committed 复合 assembler + 竞技场(A0-A4 + B1-B5 + 双容器 + 四门 + CPCV + 切片 + size-drift) | runner + 结果 doc(诚实分级 + 决策树落点) | 门禁绿 + codex + **FAIL 报 FAIL** |
| **AP-3** | 决策树落点 → 报 owner | owner 定向(冻结+look-once 提案 / fork) | **owner gate** |

每编码阶段:TDD(非 risk>70%)+ 本地门禁(pytest+ruff+mypy strict+redline)+ commit 前 codex 代码门(撞限流→`/code-review high`)+ 一任务一 feature commit + 回填 SSoT;docs/ledger-only 豁免 codex。

---

## 11. owner 决策点(plan 批准时确认)

1. **块间权重规则**(评测前冻结,只用一套):**默认推荐 = horizon/信心加权**(反转 0.5 / 分析师 0.25 / 质量 0.25,从 ✅/🟡 分级派生);备选 = naive 1/3 等权(codex 警可能被慢成分自我击穿)—— 选哪套 / 调具体数?
2. **AP-0.5 power 的 K** = 2(`SR_req ≤ K·SR_ref` 才 go)—— 认可,还是调?
3. **双容器**:eq_5 科学门 + buf40_5 部署门,**都须过四门(joint pass)**才声称 deployable —— 认可?
4. **AP-0.5 no-go 分支**:若 `SR_req > K·SR_ref`,(a) 重审 ≤5 前提 还是 (b) 本刀降纯诊断?
5. **决策 #3 预承诺**:若过纯反转但不过 DSR≥0.95 → (a) provisional 冻结等前向 还是 (b) 重审 ≤5 前提?
6. **先验符号 spec 的诚实代价**:分析师/质量 5td 即便 IC 弱/符号疑仍**全入**(不样本内丢)→ 接受这种诚实设计(而非 cherry-pick)?

---

## 12. codex 对抗记录(2 轮收敛)

### R1(2026-06-27,read-only;8 findings,全吸收)
- **P0-1 / P0-2(致命)**:「单一 spec=1 trial」不成立——§6 inclusion screen 用 train_val IC 决定组件 = 数据自适应筛选(隐藏多重比较),与「committed 在前」冲突。**处置 = Fix A:删 inclusion screen → fixed prior spec(全集预声明、先验符号、等权),IC 仅披露不改 spec**(§3 纪律 / §4.3 / §6.2 重写)。账本口径重写为可审计 nominal/effective(实测 2412/2387;新增 diagnostics 28 + ablation 3 + single 2,§4.4)。
- **P1-3**:底部确认机制过度声称(QGR-3 ⑧ 证是宇宙健康过滤器非 dip 择时器,dip 内 t≈1.4)→ §2 H1 降级 + 加 A1「反转+过滤」隔离臂。
- **P1-4**:eq_5/buf40_5 容器错位(buffer 非纯线性缩放,DSR 0.0059 vs 0.0052)→ §5.1 双容器**都过四门**才声称 deployable。
- **P1-5**:PIT 验收不够硬 → §6.3 加 AP-1 红线测试(未来 report_date/ann_date 闸 / EOD→T+1 / vintage ep-accr / 复权 pin / leak_probe)。
- **P1-6**:baseline 面板不足隔离 H1 → §5.3 加 A1/A2/A3 单加成分臂 + 宇宙匹配 random top-5。
- **P2-7**:DSR 非 Sharpe 倍数 → §5.0 加 AP-0.5 **收益盲 power 前置**(省债,先判过门可达性)。
- **P2-8**:重蹈 round-1..4 风险(慢质量/分析师塞进快反转篮 = 增强指数残影)→ §5.4 size-drift 披露 + §7 决策树「仅 size-drift 解释 = FAIL」。
- **R1 结论**:不能进 plan mode,须先修。→ **本 v2 已修全部。**

### R2(2026-06-27,read-only;判定「大方向 sound,3 口径点修后可进 plan mode,无需再重开 R1」)
R1 八点核为**实质闭合**(P0-1/2 Fix A 成立 = fixed prior spec;P1-3/4/5/6、P2-7/8 闭合)。残留 3 口径点(本 v3 已修):
- **R2-1**:AP-0.5 power 输入未全预声明(SR0 依赖 variance_of_sr,HAC 受自相关)→ §5.0 + AP-0 **预声明矩(normal)+ HAC 保守上界规则 + T=ONC + 数值边界 `SR_req≤K·SR_ref`(K=2)+ 参照=已披露纯反转 SR(零新 peek)**。
- **R2-2**:attribution/baseline 须容器匹配 → §5.3 **eq_5/buf40_5 各一套,只同容器内比,random top-5 容器+宇宙+约束匹配**。
- **R2-3**:账本总数歧义 → §4 + §4.4 **明确 2412/2387=AP-前基数;本刀 nominal +33→2445 / effective +30→2417;A4 DSR 的 N=2417**。
- **R2 附加建议(已纳)**:naive 1/3 等权可能自我击穿 → §4.2 默认改 **ex-ante 信心/horizon 加权(provenance 派生,非 IC)**;size-drift 判定做成**量化暴露回归**(§5.4)。

### 收敛判定
**已收敛**:codex R2 明示「修上面 3 口径点后无需再重开 R1」;本 v3 已全修(§4/§5.0/§5.3/§5.4 + AP-0)。**→ 可进第三步 plan mode。**(三步法「codex 2 轮对抗到收敛」满足。)
