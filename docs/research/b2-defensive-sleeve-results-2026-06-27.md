# 批 B2 · 永久防御 sleeve 事件循环消融(train_val only)—— 旧 8% gate FAIL,触发判据重定

> **状态**:真 train_val 跑结果(loop 第 2 刀:弃 regime 择时〔B1 已证负技艺〕→ 测永久防御 sleeve + 目的地对比 + MDD/return frontier)· 未改 governance / 未碰 live / 引擎字节未动 / sim 暂停 · **日期**:2026-06-27 · **作者**:Claude(Opus 4.8,自驱动 loop)
> **上位**:`mfi-batch-b2-defensive-destination-spec-2026-06-27.md`(预承诺)+ `b1-regime-derisk-results-2026-06-26.md`(起点)+ **`qgr-criterion-rebar-amendment-2026-06-27-avoid-top-dynamic-exit-swing.md`(本刀 FAIL 触发的 owner 判据重定)**。
> **代码**:`arena_ablation.py`(共享:PIT 防火墙/账本/baseline/protected health)+ `defensive_overlay_panel.py` + `defensive_sleeve_ablation.py`(commit `9a7015b`)+ 12 单测。codex `/code-review high`(44 agent)全修。

## 0. 一句话裁决

**旧 MDD≤8% gate:FAIL on every arm** —— 永久防御 sleeve 扫到 4 槽现金(80% 现金)MDD 仍 21.6%,唯一 MDD≤8% 者是**全仓国债(4.89%)= 债券基金,非选股闸**。**这正是 owner「8% 过于激进、不符 A 股客观现实」的铁证 → 触发判据重定(2026-06-27 amendment:弃 8% 硬门 → 避顶部 + 动态退出 + 做 T)。** **新判据下**:**perm_cash2(永久 2 槽现金,+966k 净盈翻倍 / 34% MDD 减半 / 熊市+震荡改善)是 batch-B 第一个真改善 trade-off 的正面 partial** —— 但 DSR 0.044 仍不过反过拟合门(provisional)。**目的地身份重要**:国债 > 现金 > 红利equity(红利 510880 是 equity,broad crash 同崩,非避险)。

## 1. 方法(复用 B1 + 共享 arena_ablation;引擎字节不动)

- **竞技场**:同 QGR-2 冻结事件循环(≤5 槽 / 5td / T+1 / 分板块滑点 / 涨停不可成交)。497reb / 2484日 / universe 1825;20150209–20250425。
- **永久防御 sleeve 机制**:目的地资产**每个再平衡日**作 top 候选 + **strong protected health(永不 independently_weak)** → day-1 买入永久持有;其余槽正常 ranker 轮动。真 ETF(510880/511010)经 PitBarSource(fund_daily)定价;合成现金经 B1 `CashAugmentedBarSource`。**两道 fail-closed 守门**:每个 sleeve 必须真 engage(>0 目的地买入,防 fund_daily 缺口静默塌成 baseline)+ 每臂守恒。
- **7 臂**:baseline(0 防御)/ perm_cash1..4(现金强度扫,Q2)/ perm_div1(红利510880)/ perm_bond1(国债511010)(目的地,Q1)。**部署/防御 baseline**:csi300_etf_hold / bond_etf_hold / div_etf_hold。
- **判据(旧 = pre-committed spec §2)**:某臂 deployable 须 MDD≤8% + net P&L>0 + DSR≥0.95 + 胜 beta。

## 2. 结果(真 train_val 跑;初始 ¥1,000,000)

### 2.1 FRONTIER(防御强度 → 净盈 / MDD)—— 决定性最优诚实 partial

| 臂 | 防御槽 | 净盈 (¥) | **MDD** | 均暴露 | 月换手 |
|---|---|---|---|---|---|
| baseline | 0 | +459,000 | 54.23% | 0.58 | 0.23 |
| perm_cash1 | 1 | +841,121 | 43.06% | 0.54 | — |
| **perm_cash2** | 2 | **+966,096** | **34.14%** | 0.54 | — |
| perm_cash3 | 3 | +904,043 | 22.53% | 0.57 | — |
| perm_cash4 | 4 | +200,120 | 21.62% | 0.73 | — |
| perm_div1(红利) | 1 | +896,464 | **46.94%** | 0.56 | — |
| perm_bond1(国债) | 1 | +898,544 | 42.47% | 0.55 | — |

- **perm_cash2 是甜点**:永久 2 槽现金 → 净盈翻倍(+966k vs +459k)+ MDD 减半(34% vs 54%)。**现金 buffer 在逆境兑现**(见 §2.3)。
- **MDD 随现金槽单调降但 net P&L 非单调**:再加现金(cash3/cash4)净盈开始降;**即便 80% 现金(cash4),MDD 仍 21.6% ≫ 8%**。
- **目的地(Q1)**:perm_div1(红利equity)MDD **46.9% > perm_cash1 43.1%**(510880 是 equity,2015 同崩,**非 broad-crash 防御**);perm_bond1(国债)42.5% ≈ 现金(略优,国债崩盘期微涨)。net P&L 上 div/bond(+896k/+899k)略高于纯现金(ETF 有正 drift)。

### 2.2 部署/防御 baseline

| 名 | 净盈 | MDD |
|---|---|---|
| csi300_etf_hold | +176,112 | 46.33% |
| **bond_etf_hold(全仓国债)** | +323,491 | **4.89%** |
| div_etf_hold(全仓红利equity) | +310,098 | 47.47% |

- **bond_etf_hold = 唯一 MDD≤8%(4.89%)者,但净盈 +323k(10 年 32%,年化 ~2.8%)= 国债收益 = 不是选股策略,是债券基金。** 这就是「MDD≤8% 须近全防御」的实证。

### 2.3 DSR / SPA / Romano-Wolf / regime(全 FAIL + 机理)

- **DSR(N=2385)**:最高 perm_cash3 **0.0876 ≪ 0.95**;perm_cash2 0.044 / perm_div1 0.019 / perm_bond1 0.023 → **无臂存活反过拟合门**。
- **SPA p = 0.368;Romano-Wolf 全 rejected=False**;t_vs_csi300 全 ±0.5–0.7 → **无臂显著胜 CSI300**。
- **regime 分层(机理)**:perm_cash2 牛 +0.588(略让 vs baseline +0.774)/ **熊 −0.039(vs −0.221 大改善)/ 震荡 +0.216(vs +0.061 大改善)** → **现金 buffer 的价值真实地在逆境兑现**(留干火药 + 少暴露),非伪影。bond_etf_hold 熊市 +0.154(正!国债真避险)。

## 3. 判据裁决

### 3.1 旧判据(MDD≤8% gate)= FAIL on every arm
无臂同时过 MDD≤8% + net P&L>0 + DSR≥0.95 + 胜 beta（`any_deployable_edge=False`）。**这是触发 owner 判据重定的证据。**

### 3.2 新判据(2026-06-27 amendment)下重新解读
- **perm_cash2 = 正面 partial**:新判据「稳定可观净盈 + 接受中低位缩量回调 + 避顶部」下,净盈翻倍 + 回撤减半 + 逆境改善 = batch-B 第一个真改善 trade-off 的东西。**但 DSR 0.044 仍不过反过拟合门 → provisional,须前向确认**(原则 #1,不当达标)。
- **永久防御 buffer(perm_cash2/3)= 值得带进下一刀的设计元素**(简版 vol-targeting / 永久现金缓冲),但它本身不构成 alpha(DSR 不过)。

## 4. 结论与下一刀

1. **MDD≤8% 在 ≤5 槽集中股票书 + A 股结构上不可达**(全债券才到 8% = 非选股闸)→ **判据重定**(amendment 2026-06-27)。
2. **目的地国债 > 现金 > 红利equity(broad crash)**;红利 510880 是 equity 非避险(坐实 spec 预承诺 B2-b)。
3. **永久现金 buffer 改善 trade-off(逆境兑现)但不过反过拟合门**(B2-a FAIL on 旧 8%;新判据下 provisional)。
4. **⏭️ 下一刀(新判据务实路线 a)= 避顶部派发 EXIT**(放量滞涨 + 获利盘饱和 + OBV/量价背离,§2.10③,方向无歧义)→ 进竞技场 + 三臂 + placebo + 反过拟合门;新判据(避顶部命中 + 规则驱动持有期净盈)。后续:减持硬排除(stk_holdertrade,owner 授权摄取)→ 做 T overlay → 动态退出整合。详 `qgr-criterion-rebar-amendment-2026-06-27`。

## 5. 诚实 caveat

- **train_val only**(test 封存;防火墙 coverage 2484/2484 + sleeve engage 守门通过 = 真科学结果非数据/机制伪影);真 OOS/前向 = B 层未做。
- **quant-mechanism proxy**(§4.4/§7):事件循环不含 LLM 辩论 / 全 RiskEngine / Line-2 盘中风控。
- **合成现金 sleeve**:flat 零收益零回撤,付真实 ETF 往返 friction(偏保守);永久 sleeve 经 protected health 永不轮出(行为单测证)。
- **红利低波专用 ETF(512890/515080/515100)post-2018/2019,缺 2015/2018 崩盘** → 全窗只能用 510880(红利equity)/511010(国债);broad crash 防御结论基于此。
- **DSR 用非清零 legacy floor N=2385**;append `qgr.defensive_sleeve` family(kind=ablation,effective_n=1)。
