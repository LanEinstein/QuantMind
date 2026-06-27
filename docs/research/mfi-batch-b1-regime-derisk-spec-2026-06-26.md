# MFI 批 B1 · regime-gated 暴露 de-risk overlay 规格(pre-committed,train_val only)

> **状态**:spec(实施前预登记 + 预承诺判据;research 侧离线 = spec 即可,**不改 live 决策边界一行** → 无需 amendment)· **日期**:2026-06-26 · **作者**:Claude(Opus 4.8)· **loop**:main-force-intent loop-engineering 自驱动第 1 刀(B1)
> **上位**:`main-force-intent-research-program-macro-direction-2026-06-26.md` §2.1/§2.3(非对称:可交易边在 RISK/EXIT/避险侧)+ `main-force-intent-lowbase-transition-system-design-2026-06-26.md` §6.5/§6.6(regime 分层 + 三臂消融/placebo 纪律)+ `qgr-2-eval-arena-freeze-spec-2026-06-22.md`(评测口径冻结:净 P&L + MDD≤8%)+ `qgr-4-exit-veto-ablation-results-2026-06-26.md`(本刀的起点:买入集 veto 不控回撤)。
> **代码**:`scripts/factor_research/regime_detector.py`(纯 PIT regime)+ `derisk_overlay_panel.py`(纯变换 + 合成 cash 增广 bar source)+ `derisk_regime_ablation.py`(编排,fork `exit_veto_ablation.py`)+ tests。

## 0. 一句话与起点

QGR-4 已证:**买入集拥挤 veto 不控回撤**(结构上只决定买谁,不减已持仓暴露;全臂 MDD 54–58% ≫ 8%)。本刀把火力转向 owner 判据的真正引擎 = **regime-gated 真·去暴露**。诚实预期(**预承诺,见 §6 证伪台账**):节流必机械降 MDD(少暴露),真问题 =(a)冻结竞技场的 **≤5 槽满仓轮动引擎**能否被节流到 MDD≤8%(结构上它是**满仓 by construction**,见 §1),(b)**regime 择时是否击败"恒定/随机持现金"naive placebo**(同平均暴露缩减),(c)DSR/CPCV 是否扛得住 regime 阈值的过拟合自由度。**先验偏 FAIL**(§2.1 拥挤/波动 regime 预测崩盘概率但难精确择时;满仓轮动引擎去暴露迟滞)。

## 1. 关键结构约束(决定机制,诚实披露)

冻结竞技场 `gate_backtest` 事件循环**只建模 ≤5 槽轮动机制**:`decide_day` 的 SELL **只来自轮动**(`propose_rotation`,每再平衡日 ≤1),且**轮动需要一个挑战者**(shortlist 内非持仓码)→ 它**永远只换不减**(满仓 by construction)。`protective_stop_active`/`hard_exit_pending` 使在位者**被保护(不被轮出)** —— live 系统的真 EXIT 路径(Line-2 盘中风控 / RiskEngine 保护止损)**不在本 proxy**(§4.4/§7 已文档化 proxy 边界)。

**推论(对 B1/B3 均成立)**:本竞技场内**唯一**忠实的「每日暴露杠杆」= **把弱槽轮入一个赢得轮动的低风险目的地资产**。因此 B1 的 de-risk 机制 = **regime-gated 轮动到合成 CASH sleeve**(见 §3)。这本身是一个**对 owner 有价值的发现**:若节流也压不住 MDD,说明 MDD≤8% 需要比 ≤5 槽轮动更快的真 EXIT 机制(= 引擎级 amendment,owner 决策)。

## 2. PIT-clean crash/risk regime 检测器(无前视,预承诺阈值)

市场序列 = **CSI300 ETF `510300.SH`**(`fund_daily`;`index_daily` 未摄取)。每个再平衡日 `T` 的 regime **仅用 ≤T 的收盘**派生(causal/trailing-only,单测断言)。

**预承诺检测器(最小自由度,headline)**:`high_risk(T) = trailing_drawdown_from_peak(T) ≤ −10%`,其中 drawdown 从 ≤T 的 trailing `PEAK_LOOKBACK=60` 交易日峰值起算。
**披露用稳健变体(非 headline,不调 headline)**:`vol_variant = high_risk OR realized_vol_20d(annualized) ≥ 0.30`。

> **DOF 纪律(铁律)**:`DD_THRESHOLD=−0.10` / `PEAK_LOOKBACK=60` / `VOL_*` **预承诺、绝不在结果上回调**(kickoff §2 + qgr-2 §4「门永不指导搜索」)。regime 参数是过拟合雷区。

## 3. 机制:regime-gated 轮动到合成 CASH(忠实、确定性、可证伪)

**合成 CASH sleeve**(`CASH1.SH..CASH5.SH`,5 个以填满 ≤5 槽):flat 价 ¥100、board=`etf`(最低 1.5bp 滑点)、宽 limit(永不封板)、巨 ADV(永不受容量限);经**新 `CashAugmentedBarSource` 包装器**(委托 `PitBarSource` + 叠加 cash 日线,**不动冻结引擎/bar source 字节**)。CASH 收益恒 0、回撤恒 0 → 忠实代表「该槽持现金」。
**保守 caveat**:friction 模型对**每笔卖**收 0.1% 印花(无 ETF 豁免)→ cash 解仓付真实往返 friction(~0.13%/round),**偏向不利于发现 de-risk 边**(保守),documented proxy 边界。

**三臂 + 1 placebo(同一 ranker / 同一 cash 机制 / 同一撮合 / 同一持有窗;唯一差异 = cash 处理的日期集)**:
- **baseline** = QGR-3 幸存 ranker `{rev_1d,max_5d,turn_spike}` 单独,**满仓,无 cash**(= QGR-4 baseline,MDD ~54%)。
- **derisk_regime** = 在**高危 regime 再平衡日**注入 cash 处理。
- **placebo_constant** = 在 regime-**盲**的等距(每 floor(R/H) 个再平衡日)日期注入,**匹配总处理日数 H**。
- **placebo_random** = 在 H 个随机(seeded)再平衡日注入,匹配 H。

**cash 处理 = 两步确定性(忠实表达「该 regime 下 de-risk 持仓」,placebo 同样处理只是错时)**:
1. **cash 候选**:`CASH1..5` 进 `quant_candidates`,分数 = 天文常数(top shortlist)+ cash health 强(percentile 1.0,qualified)→ 赢 margin、填空槽。
2. **股票在位强制 de-risk 资格**:该日所有股票 health 置为 `independently_weak`(percentile 0 / entry_percentile 1.0 / anomaly_flag / composite=其真 ranker 分)→ 轮动可把最弱股票槽换入 cash(每日 ≤1,迟滞但 over 持续熊市累积)。
> 这是 **proxy 内对 live EXIT 的最忠实表达**(rotation-only 竞技场无直接 SELL 路径)。它**对 placebo 同样施加**(同机制、错时)→ **不能为 regime 臂制造 spurious EDGE,只有 TIMING 不同**。非高危/非处理日 = base health(panel 驱动)+ 无 cash 候选 + 持有的 cash(弱 health)轮回股票。

**部署 baseline(beta 守门,复用)**:`random_top5` / `etf_only_510300` / `csi300_etf_hold`。

## 4. 达标判据(pre-committed §6.6 + qgr-2 freeze;FAIL 报 FAIL — oracle 非建议)

一个 de-risk overlay「达标 PASS」须**同时**:
- ① **MDD ≤ 8%**(owner 硬约束,主目标)。
- ② **绝对净 P&L > 0**(扣成本;de-risk 不得把净盈打负)。
- ③ **严格击败 placebo**:`derisk_regime` 的逐期收益**严格**胜 **两个** placebo(配对 t ≥ 2.0)→ 即 **regime 择时在恒定/随机同量暴露缩减之上加值**(这是核心科学问题;不胜 = 仅 beta 缩减伪影,非择时技艺)。
- ④ **DSR ≥ 0.95**(对非清零累计 N 去通胀;CLAUDE.md 原则 #1)。
- ⑤ **regime 分层逆境不毁灭**(熊市非灾难)+ 跨 CPCV 稳定(披露)。
> 任一不过 = **不达标**(provisional 低 DSR 不算达标)。达标 ≠ 上线:train_val PASS → git 冻结 + 写 B 层前向预注册 → STOP 交 owner。

## 5. 红线合规

train_val only(test 封存;防火墙断言实际 bar-read 窗口 ⊆ train_val,含 HORIZON 延伸)· 离线/确定性/LLM 零参与 · 仅 PIT 字节 · 不接 moneyflow · 北向仅历史 · **不碰 backend value-sleeve(AF-*)/ 既冻结面板字节 / 引擎字节**(cash 经新包装器叠加,引擎一行未动)· 不动 governance enum · 改判据不清零 mining 债(append family `qgr.derisk_regime`)· sim 暂停 · codex 前置门 · push owner-gated · 报告中文 / 代码 commit 英文。

## 6. 主力意图假说证伪台账(预承诺报 FAIL)

| ID | 假说 | 先验 | 预承诺门 | 预承诺 FAIL 报法 |
|---|---|---|---|---|
| **B1-a** | regime 节流能把 ≤5 槽满仓轮动的 MDD 压到 ≤8% | **弱**(满仓轮动去暴露迟滞;首段回撤在 de-risk 完成前已破 8%) | MDD≤8% | 若 MDD 仍 ≫8% → 报「rotation-only 竞技场结构上去暴露太慢,MDD≤8% 需引擎级真 EXIT amendment」 |
| **B1-b** | regime 择时击败恒定/随机持现金(择时技艺) | **弱→中**(§2.1 拥挤/波动 regime 预测崩盘概率但难精确择时;"连顶级量化都无法精确测拥挤及时撤") | 配对 t≥2 vs 两 placebo | 若 t<2 → 报「de-risk 改善是 beta 缩减伪影,非 regime 择时;时点无技艺」 |
| **B1-c** | de-risk 臂的 DSR 扛得住 regime 阈值自由度 | **弱**(regime 参数 DOF + 非清零 N≈2383) | DSR≥0.95 | 若 DSR<0.95 → 报 provisional/脆弱,不当达标 |

> **预承诺**:三条任一/全部不过 → 明确写 FAIL 报告 + 最优诚实 partial(如「MDD 从 54%→X% 但未达 8% / 不胜 placebo / DSR 不过」)+ 对纲领的含义。**绝不**为达标放宽门 / 调 regime 阈值拟合结果 / 烧 test / 把暴露缩减伪影当 alpha。
