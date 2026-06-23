# QGR-3 ⑧ 底部确认门(§3.8B 慢腿)— 交付 + 接手锚点

> **状态**:**✅ done(代码 + 真 panel + 诊断 + review;本地未 push,待 owner 授权)** · **日期**:2026-06-22(诊断真跑 2026-06-23)· **作者**:Claude(Opus 4.8)
> **这是什么**:QGR-3 build-new ⑧ 的**底部确认门**(§3.8B,慢"持仓"腿)交付锚点 —— 门设计 + 条件前向收益区分度结果 + cyq_perf 诚实处理 + 数据划分 + 门禁/review。主文档 = `quant-first-gate-rearch-plan-2026-06-21.md`(§3.5 筹码/风险 overlay / §3.8B 客观底部确认 / §6.1 build-new ⑦⑧);评测口径 = `qgr-2-eval-arena-freeze-spec-2026-06-22.md`(FROZEN);快腿(⑦)= `qgr-3-short-factor-lib-2026-06-22.md`。
> **诚实红线**:底部确认门是**门/overlay 非可加排名因子** → 验证用**条件前向收益区分度**(确认名 vs 非确认名),不强当 rank-IC 轴;强区分度 = **必要非充分**(round 1-4 都强 IC 却 test FAIL 三次);本阶段**只建门 + 验区分,不搜索、不晋升**,诚实门(DSR/SPA/Romano-Wolf)永不指导本阶段,真判据在 QGR-4 竞技场 + QGR-6 前向。

---

## 0. ⑧ 主旋律维度的 gate 状态(关键人工 gate — 本次未解锁)

⑧ 有两块:**(a) 主旋律择场维度**(消费 AF-001 政策→主题→申万 L3 registry)+ **(b) 底部确认门**(本文档)。

- **(a) 主旋律维度 = gate 状态本 session 内变化**:session 起手时 `config/policy_themes.yaml` = `status: "draft"`(FREEZE-READY 未冻)→ 我按红线**未消费、未擅自冻**;**session 进行中 owner 在 backend 侧并行冻结(commit `4e97db2` "AF-001 freeze policy-theme mapping, owner-confirmed";现 `status: "frozen" / version: frozen-2026-06-22-v2`)+ AF-002 接线(`f1e78c5`,并把传统高股息层从 theme-map 移为 value 因子)**。⇒ **gate 现已解锁,但 ⑧ 主旋律维度未在本 commit 实施**,原因:① 它是独立大任务(须新建申万 `index_member_all` L3 PIT 成分 reader〔带 in/out date〕+ policy `effective_from` tilt + 主题 OVER 非主题 baseline 的 IC 增量 §3.8E + 专门 codex PIT-soundness 门 §9.2),宜单独聚焦一轮,不在底部确认门尾巴硬塞;② owner **此刻仍在编辑 value-sleeve**(本 session 又新增 `backend/value_entry/`,theme-map 刚被 AF-002 改动)→ 在 registry 仍在动时建消费层有风险,须待其稳定。**实施时与 AF-001 共享单一真相源,不重复造;`ths_index` 非 PIT 仅对照。**(见 §6 下一步 + owner 决策点。)
- **(b) 底部确认门 = 本次交付**(不卡 gate;§3.8B,慢腿)。

---

## 1. 门设计(§3.8B;门/overlay,非可加排名因子)

owner 的「买跌票」要客观区分**健康筑底**(优质名回调到支撑、缩量筑底)vs **洗盘/接飞刀**(跌了再跌)。这是多指标**门**,不是排名轴。每条件 = 纯 `bool|None`(None = 输入缺失→无法评估→fail-closed),composite = **一票否决**(任一条件 False → 不确认)。

**条件映射(§3.8B ①②④⑤⑥;③ 有意降级)**:

| # | 条件 | 实现 | 数据 / 洁净度 | 机制 tag(复用既有枚举) |
|---|------|------|------|------|
| ① 缩量 | `volume_dryup` | 近 5d 换手 < 前置 20d 基线(= `turnover_spike < 0`,复用 ⑦ 已测件) | daily_basic turnover,**纯 PIT** | liquidity_premium |
| ④ 无破位 | `no_breakdown` | 收盘**未创**近 20d 新低(刀一直破位,底守得住) | adj_close,**纯 PIT** | mean_reversion |
| ⑤ 无困境 | `no_distress` | 非 PIT-ST/退 名(namechange) | namechange,**纯 PIT**(停牌名无 daily 行→天然不入 cohort) | quality_premium |
| ⑥ 质量地板 | `quality_floor` | ROE≥0 且 毛利率≥0 且 E-P>0(盈利真业务,非亏损价值陷阱) | 研究侧 `fundamentals_pit` + pe_ttm,**纯 PIT**(**不碰 backend/quality_fundamentals**) | quality_premium |
| ② 站稳筹码成本带 | `above_cost_band` | 收盘 ≥ cyq_perf 中位持仓成本 `cost_50pct`(收复成本带→上方套牢盘有限) | cyq_perf,**⚠️模型派生(§3.5)** | mean_reversion |
| ③ 资金流企稳 | **有意降级,非疏漏** | — | — | — |

- **core 门(4 条纯 PIT)= `bc_core_confirmed`**;**cyq_perf 成本带(②)单独 = `bc_full_confirmed = core + above_cost_band`**,可消融。
- **阈值 = 自然边界,事前 committed,无数据调参**(近/基线比 1.0;中位成本;新低;零盈利地板)→ 极小研究自由度(反 p-hacking,§4.1 底部确认 caveat)。
- **机制 tag 仅复用既有 `EconomicMechanism` 字符串值**(本模块不晋升任何东西)→ **未动 governance 枚举**。
- **新文件**(高内聚低耦合,factor_lib 已超 800 行上限→不再塞):`cyq_perf_pit.py`(reader)+ `bottom_confirmation.py`(纯门逻辑)+ `bottom_confirmation_diagnostics.py`(条件 IC 诊断);`build_qgr_panel.py` 扩列;均 import 隔离、零 backend.{llm,agents,mirofish}、不碰 live。

### 1.1 ③ 资金流企稳 = 诚实降级(强制披露)
`moneyflow`/`moneyflow_hsgt`/`margin` **QGR-1 根本没摄取**(不在 `data-inventory-marketdata-pit` 清单),且主文档 **§3.6 明列日频 `moneyflow` 为陷阱**(撮合拆单破坏「大单=聪明钱」,无稳健日频预测力,多半 fit 当日已发生价格)。故该组件**不硬造**——其本意代理的「企稳」由干净的 **①缩量 + ④无破位**价量条件承载。**这是有意范围决策,非疏漏**(诊断 doc + 本文档均显式记账)。

### 1.2 cyq_perf 诚实处理(§3.5,强制)
cyq_perf 是 Tushare **模型派生**的筹码分布摘要(**非纯市场观测,字节存档存疑**)+ **floor 2018**(pre-2018 rebalance 日 `chip=None` fail-closed,同 ⑦ limit_list_d 2020 处理)+ 退化零值(新股 cost 全 0 → reader 丢弃 fail-closed)。**实测佐证不可靠**:600519.SH @20200102 收盘 1130 已高于 cost_95pct(1075.8),`winner_rate` 却 = 20.12(应≈100%),且 `his_low`=6.6(茅台真实低位非 6.6)——直接证据。**处理**:① 门只用 `cost_50pct`(中位成本,实测形态合理:cost_5<15<50<85<95 单调、均在合理带内)做 `above_cost_band`;② `winner_rate` **永不进门**,仅作 SECONDARY 披露列(其 rank-IC 实证其不可靠);③ cyq 成本带**非 core、可消融**——诊断 §6 在 cyq 可用子集上比 core vs full,若 full 不胜 core 则成本带非承重件。

## 2. 验证方法(条件前向收益区分度,非 rank-IC)

门的验证 = **区分度**:每 rebalance 日,确认组前向收益是否高于非确认组。每日 spread `mean(fwd|confirmed) − mean(fwd|not-confirmed)` 聚合成 mean/t/hit/n_dates = 门的「条件 IC」。慢腿 horizon = 5/10/20d(筑底论持有数周-数月)。每侧 ≥5 名才计该日。

- **§2 core 门(全窗)** + **§3 full 门(2018+ cyq 可用)** + **§4 dip 池条件**(ret_20d 底 1/3 = 「买跌票」候选池内 confirmed vs not,= §3.8B 精确主张)+ **§5 单条件边际**(哪条承载区分)+ **§6 cyq 消融**(core vs full 同子集)+ **§7 连续 cyq 读 rank-IC(SECONDARY)**。
- **t 是乐观 screen 非判据**(重叠前向窗自相关,有效 N < n_dates)→ 真控制 = QGR-2 竞技场 DSR/SPA/Romano-Wolf + 累计 N deflation(QGR-4);QGR-3 不搜不晋升。

## 3. 数据划分铁律(遵守)
从零提取新门 → 用既有 locked split(**train_val 20150105..20250430,test 20250604..20260612 封存未读**);CPCV 池 = 2015..2026-06-12;消费 `data/marketdata_pit/` 既有 PIT(禁重下);复权 pin / `<d` 或同日 EOD vintage 无前视 / 幸存无偏 cohort(与 ⑦/round-1 同,**未加 st_filter→ST 名真在样本里**,使无困境门有可分对象,非 no-op)全留。cyq_perf 同日 = EOD vintage,与 day-d 收盘同时点已知 → day-d(非 `<d`)PIT 正确(EOD overlay)。

## 4. 真 panel + 诊断结果(train_val,完整表 = `qgr-3-bottom-confirmation-diagnostics-2026-06-22.md`)

**真 panel**:`build_qgr_panel.py` 加 9 列 → **326,854 行 / 3003 码 / 498 rebalance 日**(20150202..20250425);**⑦/round-1 因子列字节回归校验 PASS**(逐列 max|old−new|=0.0,bool flag 全等 → 仅新增 9 列,⑦ 结果不变)。

### 4.1 Coverage(可评估率 / 确认率)
| 列 | defined-rate | confirmed-rate(of evaluable) |
|---|---|---|
| `bc_vol_dryup` | 99.84% | 57.87% |
| `bc_no_breakdown` | 100% | 89.16% |
| `bc_no_distress` | 100% | 99.72% |
| `bc_quality_floor` | 81.22%(无 fina ~19% → None) | 96.06% |
| `bc_above_cost_band` | 69.73%(cyq 2018+/退化 None) | 72.64% |
| `bc_core_confirmed`(4 纯 PIT) | 90.76% | **42.68%** |
| `bc_full_confirmed`(+cyq band) | 81.57% | 24.92% |

### 4.2 区分度(确认名 − 非确认名 前向收益 spread;**t 是乐观 screen 非判据**)
| 门 | 5d | 10d | 20d | 读 |
|---|---|---|---|---|
| **core 门(全窗,4 纯 PIT)** | +0.22% (t **+3.82**) | +0.37% (t **+4.24**) | +0.61% (t **+4.99**) | **正、显著、随 horizon 单调增** → 慢腿有效过滤器(贴筑底论持有数周-数月) |
| full 门(+cyq,2018+) | +0.17% (t +2.32) | +0.34% (t +3.21) | +0.57% (t +4.00) | 正显著,但小窗 |

### 4.3 ⚠️ dip 池条件(ret_20d 底 1/3 = 「买跌票」候选内 = §3.8B 精确主张)
| core 门 \| dip 池 | 5d | 10d | 20d |
|---|---|---|---|
| spread | +0.11% | +0.10% | +0.21% |
| t | +1.57 | +1.08 | +1.44 |

**这是本轮最关键的诚实发现**:门在**全截面**区分强(t 到 +4.99),但**仅限于已大跌名内**(dip 池)其增量区分**弱、不显著(|t|<3)**。读法:门的区分力大半来自「全市场把健康名从困境/下行名里分出来」(= 宇宙质量/健康过滤器),**而非「在已跌票里精分筑底 vs 接飞刀」**(= dip 择时)。⇒ QGR-4 把它当**宇宙质量/健康过滤层**用更稳,当「买跌择时器」用须谨慎(本轮证据不支持后者的强主张)。不夸大、不掩盖。

### 4.4 单条件边际(5d)
| 条件 | spread | t | 读 |
|---|---|---|---|
| `bc_vol_dryup` | +0.16% | **+2.54** | 缩量最强单条件 |
| `bc_quality_floor` | +0.28% | **+2.25** | 质量地板次强 |
| `bc_no_breakdown` | +0.14% | +1.27 | 弱 |
| `bc_above_cost_band` | +0.10% | +1.15 | cyq,最弱 |
| `bc_no_distress` | +1.26% | +1.04(仅 33 日) | ST 在 cohort 内太稀→每日可比样本薄;但仍作硬排除 |

→ **缩量 + 质量地板**承载大半区分;cyq 成本带最弱;无困境(ST)样本薄(ST 稀)但作硬排除保留。

### 4.5 cyq_perf 消融(模型派生成本带是否承重?同 2018+ cyq 可用子集,227,910 行)
| | 5d | 10d | 20d |
|---|---|---|---|
| core(无 cyq) | t +2.43 | t +2.91 | t +3.12 |
| full(+cyq band) | t +2.10 | t +2.86 | t **+3.46** |

→ cyq 成本带**仅在 20d 略增**(3.12→3.46)、**5d 反略降**(2.43→2.10)→ **非承重件**,净中性偏微增(仅最长 horizon)。**证实「cyq 非 core、可消融」设计正确**:干净 core 承载区分,模型派生成本带加得很少。

### 4.6 连续 cyq 读(SECONDARY,rank-IC,非排名轴)
| 连续读 | 5d | 10d | 20d |
|---|---|---|---|
| `bc_cost_premium`(close/cost_50pct−1) | +0.019 (t +2.39) | +0.027 (t +3.50) | +0.035 (t +4.62) | 正(收复成本带越多→前向越好) |
| `bc_winner_rate` | −0.016 (t −1.85) | −0.014 (t −1.73) | −0.020 (t −2.52) | 弱负/噪声 → **实证 winner_rate 不可靠**(印证 §1.2 茅台 20.12 反例),正好佐证将其逐出门只作披露 |

## 5. 门禁 / review

- **新测试**:`test_cyq_perf_pit.py`(5)+ `test_bottom_confirmation.py`(24,含 registry↔列名一致守卫)+ `test_bottom_confirmation_diagnostics.py`(6)+ `test_build_qgr_panel.py` 扩(+2)。`tests/factor_research/` **575 passed**(round-1..4 + ⑦ 字节不变;⑦/round-1 列回归 max diff 0.0)。ruff + mypy strict + `redline-check.sh` 全绿;零 backend 改动 / 永不碰 live;机制复用既有枚举(未动 governance)。
- **`/code-review high`**(codex 兜底,[[feedback_codex_rate_limit_fallback]]):4 路 finder(correctness + PIT/fail-closed + cleanup + conventions)→ **2 路 correctness/PIT finder 零 bug**;cleanup/conventions 多为「与既有代码同模式」(per-module `_opt_float`、`.asof()`-per-row 同 round-3/4、`.groups.items()` 同 `neutralize.py`、~55 行 build_report 同 r2/r3/r4)→ 仅修 1 处自引入可读性(`_build_rows_qgr` 90 行→抽 `_bottom_confirmation_row` helper,行为不变)。
- **诊断阶段自查抓 1 真 bug**(review finder 漏):registry 名 `volume_dryup` ≠ 面板列 `bc_vol_dryup` → 缩量条件在 coverage/边际**披露表静默缺失**(门计算本身正确含缩量,headline 结果有效)→ 统一改名 `vol_dryup`(零重建,面板列不变)+ 加守卫测试防复发。

## 6. 下一步

1. **⑧ 主旋律维度(gate 现已解锁,待 owner 拍板作为下一独立单元)**:AF-001 已 frozen(`4e97db2`)→ 可建 scripts 侧消费维度(申万 `index_member_all` L3 PIT 成分 + policy `effective_from` tilt + 主题 OVER 非主题 baseline IC 增量 §3.8E + 专门 codex PIT 门 §9.2)。**待 owner 确认 value-sleeve theme-map 已稳定**(本 session AF-002 刚改动 + `backend/value_entry/` 在途)再起手;与 AF-001 共享真相源。
2. **QGR-4**:QGR-2 竞技场用 ⑦ 快腿幸存轴 `{max_5d, turn_spike, rev_1d}` + ⑧ 底部确认门(作慢腿候选过滤)+(主旋律解 gate 后)主题维度 + round-1 → 搜候选闸门 → SPA/Romano-Wolf 公平比 + 累计 N deflation;**5-10td 选股闸门 vs T+1 真超短两条腿公平比**(⑦ 已得证据:日频 T+1 是反转非动量,真超短动量须分钟数据 → universe/数据不支持,偏向 5-10td 选股腿)。

> **接手协议**:本文档 + `qgr-3-bottom-confirmation-diagnostics-2026-06-22.md`(区分度全表)+ `qgr-3-short-factor-lib-2026-06-22.md`(⑦ 快腿)+ 主文档 + `CLAUDE.md`。sim 暂停直到 QGR-6 前向过门。push 待 owner 授权。
