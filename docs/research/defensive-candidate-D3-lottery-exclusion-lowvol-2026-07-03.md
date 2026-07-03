# 防御候选 D3 —— 彩票排除 + 低波倾斜(最小防御,可作宇宙过滤器)

> **状态**:provenance-gated committed **设计**,评测前存档。防御选股线候选 3/4,测试顺序 D1→D2→**D3**→D4(owner 2026-07-03)。上游 = `defensive-selection-research-synthesis-2026-07-03.md`。
> **判据(owner)**:熊市/股灾不亏 + 回撤可控。置信:**HIGH**(排除门本身;A 股最特异防御边)。

---

## 1. 假设(可证伪)

**H1(D3)**:A 股最肥的左尾来自**散户彩票/高 MAX/高 IVOL 名**(情绪反转崩得最狠);**硬排除这批 + 低波倾斜**,能以最简、最 A 股特异的方式砍左尾、控回撤,且可作宇宙过滤器叠加任何 ranker。**H0**:彩票排除+低波倾斜扣债后不改善回撤/左尾。

## 2. Committed 先验(评测前 hash 定死)

**硬排除门(选股前二值)**:
- **高 MAX/彩票**:`max_20d`(及 `max_5d`)顶 decile 剔;**理想 RMAX**(涨跌停 ±10% 修正:涨停日 winsorize/cap,防 MAX 被涨停虚高)——RMAX 需**新代码**,一期可先用 raw max_20d 顶 decile 披露 caveat。
- **高 IVOL**:特质波动率(FF3/CAPM 日残差 std 20-60d)顶 decile 剔——**需新代码**(残差回归);一期可用 `vol_20d` 顶 decile 近似(total vol),披露近似。
- 排除四件套 + 涨跌停不可成交名。

**正向倾斜(committed,单因子)**:`vol_20d` − 低better(低波倾斜),截面 z-score 排名。

**权重**:排除门二值;正向仅低波单因子(最小设计,最少自由度)。

## 3. Horizon 与引擎配置

- **horizon = 5d(默认)** 或 20d(可选披露);低波是慢因子但排除门对任何 horizon 有效。
- 引擎 = `run_gate_backtest`;≤5 槽 + buffer 容器。
- **可组合性**:D3 = 宇宙过滤器,可(a)独立作低波 ranker,或(b)套在 D1/D2 前作宇宙净化层。一期先独立测。

## 4. 数据 + 复用(今天可建,无需摄取)

`max_20d`/`max_5d`/`vol_20d` 全已在 factor_lib;`daily` 收益算 IVOL 残差(新代码,可选)。**一期零新数据 + 可选 RMAX/IVOL 新代码**。panel `build_qgr_panel.py`;中性化删30%。

## 5. 机制(为何控回撤)

散户驱动的彩票名(高 MAX/高 IVOL)在情绪反转/崩盘时崩得最狠(A 股特异,retail-structural)→ 排除去掉最肥左尾;低波倾斜进一步偏向峰谷小的名。**这是最直接的「砍左尾」干预**,且证据在 A 股最强(MAX→收益显著负,2000-2017)。

## 6. Dev 测试协议(train_val only,不碰近期)

按 synthesis §4。**关键 = 左尾/股灾切片**:比 D3 vs 全宇宙在 6 股灾切片的 cum_return + worst_period,看排除彩票是否显著收窄左尾;regime 熊市累计;MDD。size+行业中性化删30%;`deflated_sharpe_hac` 对非清零账本(D3 append kind=ablation);对「随机剔等量名」placebo 消融(隔离「排除彩票」vs「随机缩宇宙」)。

## 7. 诚实预期 + caveat

- 预期:左尾/股灾切片改善(排除门高置信);但纯低波 ranker 的 5 日净盈可能低(慢因子)→ D3 更可能作为**过滤器叠加 D1/D2** 而非独立赢家。
- caveat:一期用 raw max_20d/total vol 近似 RMAX/IVOL(涨跌停未修正)→ 披露;精确版需新代码。彩票排除若与反转冲突(反转买超卖=有时是崩后彩票名)需 placebo 隔离。
- **FAIL 报 FAIL**。

## 8. 判据落点(owner 判)

dev 表出后:D3 股灾切片左尾显著收窄 且 MDD 降 且 净盈不劣化太多 且胜随机剔 placebo → 晋级(或采纳为 D1/D2 的过滤层);否则 FAIL 报 FAIL + 下一候选(D4)。
