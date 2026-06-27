# MFI 批 B2 · 避险目的地 + 永久防御 sleeve 规格(pre-committed,train_val only)

> **状态**:spec(实施前预登记 + 预承诺判据;research 侧离线 = spec 即可,不改 live 决策边界 → 无需 amendment)· **日期**:2026-06-27 · **作者**:Claude(Opus 4.8,自驱动 loop 第 2 刀)
> **上位**:`mfi-batch-b1-regime-derisk-spec-2026-06-26.md` + `b1-regime-derisk-results-2026-06-26.md`(B1 起点:regime rotate-to-cash FAIL,timing 负技艺,结构墙)+ `main-force-intent-research-program-macro-direction-2026-06-26.md` §2.3(避险目的地=红利低波)。
> **代码**:`defensive_overlay_panel.py`(纯变换,复用/泛化 B1 `derisk_overlay_panel`)+ `defensive_sleeve_ablation.py`(编排,fork B1)+ tests。

## 0. 起点与本刀两问

B1 已证:(a)**结构墙** = rotation-only 满仓竞技场任何 overlay 都把 MDD 卡 50–60%;(b)**regime 择时是负技艺**(盲目铺开现金严格胜在检测崩盘里集中持现金)。本刀转向 owner 关心的**避险目的地**(纲领 §2.3),并据 B1 的 timing-failure 发现改打**永久防御 sleeve**(无择时):

- **Q1(目的地)**:给定 1 槽永久防御,**目的地身份**(合成现金 / 红利equity 510880 / 国债 511010)对 net-P&L/MDD trade-off 影响多大?
- **Q2(强度→最优诚实 partial)**:**永久现金 sleeve 强度扫 K=1..4**(合成 cash,可填任意槽)→ 决定性回答「要多防御才到 MDD≤8%、代价多少 return」。这是给 owner 的**最优诚实 partial**(诚实证伪 STOP 须附)。

> **关键数据洞察(probe 2026-06-27)**:**上证红利 510880 是 equity ETF,2015 broad crash 同样回撤 ~45%**(§2.3「红利低波防御」证据是 Jan-2024 style-specific crash,非 2015 broad);专用红利低波 ETF 512890/515080/515100 **post-2018/2019,缺 2015/2018 崩盘覆盖** → 全窗只能用 510880(红利equity)/511010(国债,真防御)/518880(黄金)。**broad crash 里只有国债/现金真防御,红利equity 不防御** —— 这本身是 B2 的诚实发现之一。

## 1. 机制(复用 B1 + 泛化到真 ETF 目的地;引擎字节不动)

- **竞技场**:同 B1/QGR-2 冻结事件循环(≤5 槽 / 5td / T+1 / 分板块滑点 / 涨停不可成交)。结构约束同 B1 §1(满仓 by construction,唯一暴露杠杆=轮入赢得轮动的目的地)。
- **真防御 ETF**:510880 / 511010 / 518880 **本就在 PIT 库 `fund_daily`**,PitBarSource 直接定价(经 fund_daily + classify_board=ETF)→ **加进 universe 即可,无需合成 bar source**(比 B1 更干净)。**合成现金 sleeve**(B1 `CashAugmentedBarSource`)仅 cash 强度扫用。
- **永久防御 sleeve 机制**:目的地资产**每个再平衡日**作 top 候选(高分)+ **strong health 永不 independently_weak(被保护永不轮出)** → day-1 买入后**永久持有 1 槽**;其余槽 = 正常 ranker 股票轮动。K 槽防御 = K 个不同防御码(cash sleeve 可任意多)。
- **臂(预承诺)**:
  ① **baseline** = ranker 股票单独(0 防御,MDD ~54%)。
  ② **perm_cash1 / perm_cash2 / perm_cash3 / perm_cash4** = 永久 K 槽合成现金(强度扫,Q2;cash 收益 0 回撤 0,付真实 friction)。
  ③ **perm_div1**(510880 红利equity)/ **perm_bond1**(511010 国债) = 1 槽永久防御,**目的地对比**(Q1,vs perm_cash1)。
- **部署/防御 baseline**:csi300_etf_hold / **bond_etf_hold(511010 买入持有,应 MDD~5% return~小)** / div_etf_hold(510880 买入持有,应 MDD~45%)。
- **统计**:SPA/Romano-Wolf vs csi300;DSR-HAC(非清零 N);regime 分层;Q2 强度扫的 MDD/return frontier 表。

## 2. 达标判据(pre-committed,FAIL 报 FAIL — oracle)

一个臂「达标 PASS」须**同时**:① **MDD≤8%** ② **绝对净 P&L>0** ③ **DSR≥0.95** ④ SPA/RW 公平比击败可部署 beta baseline ⑤ regime 逆境不毁灭。
> **诚实预期(预承诺报 FAIL)**:**B1 结构墙强烈预示无臂过 MDD≤8% 同时 net-P&L>0 + 击败 beta**。最可能结果:强度扫显示 **MDD 随防御槽数单调降但 net-P&L 同步降向 ~bond/cash 收益**(到 MDD≤8% 时 book ≈ 全防御 = 不再是选股闸而是防御基金);红利equity 目的地(510880)**在 broad crash 不防御**(MDD 仍高),只有国债(511010)/现金真降 MDD。**这正是给 owner 的决定性 partial:在 ≤5 槽 + A 股 40–55% 回撤下,MDD≤8% 须 ~全防御配置 → 量化选股闸与 owner MDD≤8% 在本构造下不可兼得 → 须引擎级真 EXIT 或重定框定。**

## 3. 证伪台账(预承诺报 FAIL)

| ID | 假说 | 先验 | 门 | 预承诺 FAIL 报法 |
|---|---|---|---|---|
| **B2-a** | 永久防御 sleeve 把 ≤5 槽股票书 MDD 压到 ≤8% 且 net-P&L 仍正/击败 beta | **弱**(B1 结构墙;到 8% 须近全防御) | MDD≤8% + net-P&L>0 + DSR≥0.95 | 若到 8% 时 net-P&L≈bond 收益/不胜 beta → 报「选股闸与 MDD≤8% 不可兼得」 |
| **B2-b** | 红利equity(510880)目的地 broad crash 防御 | **弱**(510880 是 equity,2015 同崩) | 目的地对比 MDD | 若 510880 MDD≈baseline → 报「红利equity 非 broad-crash 防御,只国债/现金真降 MDD」 |
| **B2-c** | regime 择时目的地优于永久(B1 已证 timing 负技艺) | **极弱** | — | B1 已证;本刀不再重测 timing,聚焦永久 sleeve + 目的地 |

> **预承诺**:任一不过 → 明确写 FAIL + 最优诚实 partial(强度扫 MDD/return frontier)+ 对纲领含义。**绝不**为达标放宽门 / 把暴露缩减伪影当 alpha / 烧 test。

## 4. 红线合规

train_val only(防火墙断言 bar-read ⊆ train_val)· 离线/确定性/LLM 零参与 · 仅 PIT 字节 · 不接 moneyflow · 北向仅历史 · **不碰 backend value-sleeve(AF-*)/ 既冻结面板字节 / 引擎字节**(真 ETF 经 universe,cash 经 B1 包装器,引擎一行未动)· 不动 governance · 改判据不清零(append family `qgr.defensive_sleeve`)· sim 暂停 · codex 前置门 · push owner-gated · 报告中文 / 代码 commit 英文。
