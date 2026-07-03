# 防御性选股 alpha —— 调研综合基础(2026-07-03)

> **状态**:两路并行调研(外部文献 provenance + 内部复用件/数据盘点)的综合基础,供 D1-D4 四个候选 spec 共同引用。**上游触发**:2026-07-03 owner 看 alpha-pivot dev-screen(纯反转 A0 家族在 train_val 老牛熊:全配置熊市累计为负 + 每股灾都亏 + MDD 56%,过不了 owner 两门)后判定**现有 alpha 不合格 → 令搜寻/设计新候选**;目标 = **熊市/股灾不亏 + 回撤可控的低回撤 alpha(选股层面,非择时 overlay)**。
> **作者**:Claude(Opus 4.8)· owner:dr.zhang · **测试顺序**:D1→D2→D3→D4(owner 2026-07-03 定)

---

## 0. 核心结论(两路收敛)

1. **方向确认未探索且有据**:过去所有防御尝试(C1a 避顶部 / B1 regime de-risk / B2 防御 sleeve / QGR-4 exit-veto)**无一例外是择时/停泊 overlay**(WHEN to de-risk),**从未把「选 inherently 防御的股票」当 alpha 源**(WHAT to pick)。防御**选股**是真空白。
2. **「避险=红利低波」内外独立佐证**:内部 MFI 已发现(2024 微盘崩:中证红利低波 +5.93% vs CSI300 −5.94%;长期 maxDD **16.87% vs 45.6%**);外部 S&P 中国低波高息 50 **+7.1%/年**、SSE 红利低波指数(H50040)方法学一致。
3. **全部今天可建,无需 owner-gated 摄取**:`daily`/`daily_basic`(含 `dv_ratio`)/`stk_factor_pro`(ATR)/`*_vip` 财报全在库;`vol_20d`/`max_20d`/`roe`/`gpm`/`accr`/`ep_ttm`/`amihud_20d` 已在 `factor_lib.py`。唯一新代码 = **beta**(对 CSI300 滚动回归,无 beta 端点)+ 可选 RMAX(涨跌停修正 MAX)/ F-score。
4. **关键结构洞察(内外都指,吻合 QGR-3 ⑧)**:5 日调仓下防御因子都是**慢因子(月级)→ 应作『宇宙质量过滤器 + 风险降低器』,不是 5 日择时器**。这决定候选形态(D1/D4 想要更长 horizon;D2/D3 契合 5 日)。

---

## 1. Provenance 表(committed 先验来自文献,评测前定死,绝不样本内挖)

| # | 因子族 | committed 先验方向 | A 股证据 | 降回撤机制 | 置信 |
|---|---|---|---|---|---|
| 1 | **低波 / 低 IVOL** | 低波 ↓(long 低 realized/idio vol) | Robeco/Blitz-Hanauer-van Vliet《Volatility Effect in China》(J.Asset Mgmt 2021):最低波组收益最高;**驱动是波动率非 beta**;低换手 | 低波名峰谷更小,anomaly 使防御不牺牲收益 | **HIGH** |
| 2 | **低 beta / BAB** | 低 beta ↓(long-only,BAB 空腿 N/A) | Sehgal 2022:CAPM BAB 中国显著正,但被 profitability 吸收;Robeco:低风险由波动率非 beta 驱动 | 低 beta 名市场跌时跌得少 | LOW-MED(与 #1 冗余,仅小权重 tie-breaker) |
| 3 | **质量安全(QMJ)** | 高质量:ROE/GPM ↑ + **低杠杆/低盈利波动/低应计/稳定** ↑(safety 权重>纯 profitability) | Asness-Frazzini-Pedersen QMJ 2019(含中国);**Hanauer/Robeco 2021:A 股 size/quality/past-return 证据"substantially weaker"** → 质量仅加性非独立赢家 | safety(低杠杆/低破产风险)直降崩盘期特质爆雷 | MED(safety 子分>纯 profitability) |
| 4 | **红利低波(红利+低波)** | 高**可持续**股息 ↑ ∧ 低波 ↓(要连续派息+适度派息率,非纯高息) | S&P 中国低波高息 50:+7.1%/年(2009-2019);S&P 研究:混合息+低波给"enhanced risk-adjusted+incremental defensiveness";SSE H50040 方法学 | 双防御:股息现金流支撑估值地板+低波剔脆弱名;A 股抛售期机构轮入目的地 | **HIGH** |
| 5 | **彩票/MAX/高 IVOL 规避** | **排除**高 MAX/高 IVOL/彩票名(负倾斜) | 强 A 股证据:MAX→未来收益显著**负**(2000-2017);需 **RMAX**(±10% 涨跌停修正);集中于高散户博彩名 | 散户彩票名情绪反转时崩得最狠,排除去掉最肥左尾 | **HIGH**(A 股最特异防御边) |
| 6 | **下行/尾部 beta** | **仅** tail-beta ↓;**不承诺** downside-beta/半方差 | tail-beta→收益显著**负**(低 tail-beta=高收益+低崩盘,双赢,Applied Econ 2019);**但 Ang 式 downside-beta 中国有正报酬**(反向)→ 半方差 mixed | 低 tail-beta 名系统抛售期共崩少;但 downside-beta/半方差符号不可靠 | MED(仅 tail-beta)/ **LOW 不入 committed**(downside-beta/半方差) |
| 7 | **防御价值(F-score 门)** | 便宜(低 PE/PB/高 dv)↑ **∧ 高质量/高 F-score**(value ∧ quality,绝不 value alone) | Piotroski F-score 中国:High-minus-Low +0.57%/月 EW;与 B/M+短期反转结合增强 | F-score 滤掉基本面恶化的"便宜"陷阱名(经典回撤源) | MED |

**结构约束(压过以上,吻合 QGR-3 ⑧)**:A 股强短期反转、弱/无动量、size 效应。**5 日调仓下所有防御因子(波动/股息/质量/F-score)都慢(月级)→ 应作宇宙质量过滤器+风险降低器,非 5 日收益择时器**。5 日收益引擎仍是反转;防御因子重塑反转operating的**宇宙**。

---

## 2. 数据可用性:今天可建 vs 需摄取

| 防御因子 | 数据源 | 今天可建? |
|---|---|---|
| realized 波动率(低波) | `daily` closes → `return_volatility`(vol_20d 已编) | ✅ 已在 factor_lib |
| ATR 区间波动 | `stk_factor_pro.atr_qfq`(PIT) | ✅ 已摄,未接因子 |
| **beta**(低 beta) | `daily` 收益 vs CSI300(`fund_daily` 510300.SH)滚动 OLS | ✅ **可算**(需写新因子,无 PIT beta 端点) |
| **股息率**(红利倾斜) | `daily_basic.dv_ratio`/`dv_ttm`(PIT 全史) | ✅ backend `valuation.py` 已读,port |
| 质量(ROE/GPM/accr) | `fina_indicator_vip`/`*_vip`(PIT)via `fundamentals_pit.py` | ✅ R2/R3 已编 |
| 价值(E/P,PB,PS) | `daily_basic`(pe_ttm/pb/ps) | ✅ |
| Amihud 非流动性 | `daily` close+amount | ✅ 已编 |
| 筹码 winner_rate | `cyq_perf`(2018+)via `cyq_perf_pit.py` | ✅(floor 2018) |
| 现金分红**事件**/派息史 | `dividend` 端点 | ❌ **需摄取**(entitled 但 0 快照);仅派息**持续性/成长**信号需要,股息**率** dv_ratio 已有 |
| 红利低波 ETF 成分 | 512890/515080/515100 | ❌ 需摄取 + 仅 post-2018(无 2015/2018 崩覆盖) |
| 北向/moneyflow | hk_hold/moneyflow | ❌ 0 快照;**信号路径永不接**(数据死/符号翻) |

**底线**:低波+低beta+高股息+质量安全的选股 ranker **今天可建,无需摄取**;唯一新代码 = beta 滚动回归(+ 可选 RMAX/F-score)。

---

## 3. 引擎复用面(新 ranker 如何插入)

冻结引擎字节不动,新 ranker **只需产出每日分数表**插入:
- `gate_backtest.run_gate_backtest(*, bar_source, provider, strategy_config, horizon=5, …)` = QGR-2 冻结竞技场(≤5 槽/5td/T+1/分板块滑点/涨停不可成交)。
- **`PanelScoreProvider(scores_by_day, health_overrides=None)` = 插入点**:新 ranker 只需 `scores_by_day: {day: [(code, score)]}`(分高=更优),`signals_asof` 自动派生分位+CodeHealth。纯选股 ranker 只需分数表。
- `gate_bar_source.PitBarSource` = PIT 字节存档 bar 源(qfq as-of pin,真 stk_limit),reuse as-is。
- `slot_frontier.run_frontier` = slot×sizing frontier 模板(现金 buffer 是唯一 MDD 杠杆,复用扫防御 ranker 的容器)。
- **`neutralize.neutralize_panel`(industry+log_size,winsor)+ 删最小 30% = 强制**(杀 size-tilt 陷阱)。
- `honest_gates.deflated_sharpe_hac` + `trial_ledger`(非清零 legacy 债,新族 kind=ablation append)+ `stats_disclosure`(SPA/RW)+ `cpcv` + `regime_detector`(牛/熊/震荡分层,证熊市非负+每股灾切片)+ `arena_ablation`(PIT 防火墙/baseline/placebo 模板)。

**插入 recipe**:建每日 `{code: defensive_score}` panel(算 vol_20d + 新 beta + dv_ratio + roe/gpm/accr,industry+log_size 中性化,删最小 30%,按 registry 符号 z-blend)→ `PanelScoreProvider` → `run_gate_backtest`(train_val only)→ regime 分层 → `deflated_sharpe_hac` 对非清零账本 → 对 size-matched + random placebo 消融。panel-builder 模板:`build_qgr_panel.py` / `defensive_overlay_panel.py`。

---

## 4. 共享 dev 测试协议(D1-D4 通用)

1. **窗口 = train_val only**(`20150209→20250425` 老牛熊,含 2015 股灾/2016 熔断/2018 熊/2020 covid/2022 回撤/2024 微盘崩);**不碰近期 holdout**(近期 = test 20250604→20260612 已读 4×,或 post-20260612 处子;只在 owner-gated 一次读时用)。
2. **判据(owner)= 绝对净盈 + 回撤可控 + 跨 regime 稳定**(牛/熊/震荡分层 + 6 股灾切片);**熊市/股灾不亏**是硬要求;MDD 仅披露 + 现金 buffer 控回撤。不背 CSI300 超额硬门。
3. **反过拟合**:size+行业中性化删最小 30%;`deflated_sharpe_hac` 对非清零账本(每新族 append kind=ablation,债只增不减);SPA/Romano-Wolf 对 placebo;CPCV。**每个候选 append 账本 → 债增,过门更难(诚实代价)**。
4. **消融**:对 size-matched random top-5 placebo + 纯反转 A0 baseline 比;防「胜出仅 size-drift/大盘暴露解释」。
5. **诚实预期**:5 日下防御因子贡献是**降回撤/砍左尾**,不是 5 日 IC;纯防御选股(D1/D4)可能 5 日 IC 弱但 MDD 显著低。**FAIL 报 FAIL,绝不移球门/样本内调符号权重。**

---

## 5. 共享 caveat(全记进各 spec)

- **红利低波 2023-25 拥挤**(机构 85.5% 持有)→ 防御 regime-conditional 非免费对冲;spec 加**估值便宜锚(股息率分位)**防买拥挤高位。
- **质量/价值 A 股弱独立** → 仅作 safety 子分/价值陷阱门,非主权重。
- **不承诺** downside-beta/半方差符号(中国证据相反/mixed)。
- 红利低波专用 ETF post-2018 → 无 2015/2018 崩覆盖;个股层(dv_ratio)全史可建,优于 ETF 成分法。
- 5 日 horizon 下防御因子是过滤器非择时器 → 纯防御候选考虑 horizon=20d。

---

## 6. 来源(external agent 引用,provenance)

Robeco/Blitz-Hanauer-van Vliet《The Volatility Effect in China》(J.Asset Mgmt 2021)· NYU Shanghai 荣誉论文 2025 · Hanauer《Anomalies in the China A-share market》(Pac-Basin Fin J 2021)· Frazzini-Pedersen《Betting Against Beta》· Asness-Frazzini-Pedersen《Quality Minus Junk》2019 + AQR QMJ 数据集 · S&P 中国 A 股低波高息 50 指数 + SSE 红利低波 H50040 方法学 · Bali-Cakici-Whitelaw《MAX》2011 + 中国 RMAX 论文(Cogent 2023 / EFMA 2025 / Acc&Fin 2025)· 《Tail beta and the cross-section in China》(Applied Econ 2019)· Piotroski F-score 中国(SSRN Shi / UCT)· microsoft/qlib Alpha158/360。内部:`main-force-intent-research-program-macro-direction-2026-06-26.md`(§2.3 红利低波 / §2.9 优先建测)+ `main-force-intent-lowbase-transition-system-design-2026-06-26.md`。
