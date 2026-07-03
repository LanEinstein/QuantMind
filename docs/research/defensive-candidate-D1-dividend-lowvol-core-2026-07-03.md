# 防御候选 D1 —— 红利低波防御核心(纯防御选股)

> **状态**:provenance-gated committed **设计**,评测前存档。防御选股线候选 1/4,**测试顺序 D1→D2→D3→D4**(owner 2026-07-03)。上游 = `defensive-selection-research-synthesis-2026-07-03.md`。
> **判据(owner)**:熊市/股灾不亏 + 回撤可控 + 跨 regime 稳定;**非**牛市高弹性。置信:**HIGH**(内外文献收敛,最直接答判据)。

---

## 1. 假设(可证伪)

**H1(D1)**:在 ≤5 容器里,**直接选低波+可持续高股息+质量安全的名**(纯防御选股,较长持有),能在**老牛熊 dev 上做到熊市/股灾不亏 + 回撤显著低于纯反转**(目标 maxDD 对标红利低波 ~17% 而非反转 56%),即便牛市弹性低。**H0**:防御选股扣 deflation 债后回撤/净盈不可辨于「随机低波名 + 现金」。

## 2. Committed 先验(评测前 hash 定死;绝不样本内改)

**正向防御倾斜(z-blend on surviving universe,符号 committed)**:

| 块 | 因子 | 先验符号 | factor_lib 状态 |
|---|---|---|---|
| 低波(anchor) | `vol_20d` | − 低better | ✅ 已编(`return_volatility`) |
| 红利(anchor) | `dv_ratio`(股息率) | + 高better | 需 port(backend `valuation.py` 已读 `daily_basic.dv_ratio`) |
| 质量安全 | `roe` + / `gpm` + / `accr` − | +/+/− | ✅ 已编(R2/R3) |
| 尾部(小权重 tie-breaker) | `tail_beta` / `beta` | − 低better | **需写新代码**(对 CSI300 滚动 OLS;tail=最低分位市场日 beta) |

**块间权重(committed,provenance 派生非拟合)**:低波 0.35 / 红利 0.35 / 质量安全 0.20 / 尾部-beta 0.10。

**排除门(硬,选股前)**:① 高 MAX/彩票(`max_20d` 顶 decile,理想 RMAX 涨跌停修正)② 价值陷阱(质量地板:ROE≤0 或 GPM 底部剔)③ 排除四件套(ST/科创/北交/可转债)+ 涨跌停不可成交名。

**估值便宜锚(anti-crowding,committed)**:红利腿要求**股息率分位不在最贵极端**(dv_ratio 截面分位 ≥ 中位),防买在 2023-25 红利低波拥挤高位。

## 3. Horizon 与引擎配置

- **horizon = 20d(月级),低换手**(纯防御选股是慢因子;5 日不匹配)。
- 引擎 = `run_gate_backtest(horizon=20)` 冻结引擎 + 真摩擦 + T+1 + 涨跌停不可成交;≤5 槽 + 现金 buffer 容器(eq_5 科学门 + buf40_5 部署门,复用 slot_frontier 容器)。
- ranker 插入 = `PanelScoreProvider({day:[(code, defensive_score)]})`。

## 4. 数据 + 复用(今天可建,无需摄取)

`daily`(vol/beta)+ `daily_basic.dv_ratio`(股息率)+ `*_vip`(roe/gpm/accr via `fundamentals_pit.py`)。**唯一新代码 = beta/tail-beta 滚动回归**。panel 模板 `build_qgr_panel.py`;中性化 `neutralize_panel`(industry+log_size 删30%)。

## 5. 机制(为何控回撤)

买 inherently 防御名 → 熊市/股灾结构上跌得少(红利低波 2024 微盘崩 +5.93% vs CSI300 −5.94%;长期 maxDD 16.87% vs 45.6%)。双防御:股息现金流支撑估值地板 + 低波剔脆弱名 + 质量安全剔爆雷名 + 彩票排除去最肥左尾。

## 6. Dev 测试协议(train_val only,不碰近期)

按 synthesis §4:regime 分层(牛/熊/震荡)+ 6 股灾切片,判绝对净盈+MDD+**熊市/股灾是否非负**;size+行业中性化删30%;`deflated_sharpe_hac` 对非清零账本(D1 append kind=ablation);对 size-matched random 低波 placebo + 纯反转 A0 baseline 消融(防「胜出仅低波 beta/大盘暴露解释」)。

## 7. 诚实预期 + caveat

- 预期:**MDD 显著低、熊市不亏**;但牛市弹性低、5 日 IC 弱(慢因子)、净盈可能不高——**若你判据是「不亏+可控」而非「高收益」,这正是目标形态**。
- caveat:红利低波拥挤(regime-conditional,非免费对冲)→ 估值便宜锚缓解但不消除;ETF 成分法无 2015/2018 覆盖 → 用个股 dv_ratio 全史;质量/价值 A 股弱独立(仅子分)。
- **FAIL 报 FAIL**:若扣债后不胜 placebo/不控回撤,如实报。

## 8. 判据落点(owner 判,我不替)

dev 结果表出后按 synthesis §4 判据:熊市累计≥0 且股灾切片不崩 且 MDD 可控 且净盈>0 且胜 placebo → 候选晋级(→ 近期 holdout);否则 FAIL 报 FAIL + 下一候选(D2)。
