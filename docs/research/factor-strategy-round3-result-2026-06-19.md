# 量化选股策略研究 第 3 轮 — 锁定测试集判定报告(R3-6,2026-06-19)

> 母方案 `factor-strategy-round3-plan-2026-06-19.md`;执行接手
> `factor-strategy-round3-handoff-2026-06-19.md`;R3-3 因子诊断
> `factor-strategy-round3-r3-factor-diagnostics-2026-06-19.md`;测试集复用决策
> `factor-strategy-round2-test-reuse-decision-2026-06-19.md`;round-2 FAIL 报告
> `factor-strategy-round2-result-2026-06-19.md`。
> **结论:FAIL**(扣成本净赚 +17.83%、夏普 1.51,但**超额 −4.00%**,未跑赢沪深300)。
> **判定运行完成 2026-06-20**(摄取/搜索跨日);**绝不改口径凑过线** —— 诚实上报。

---

## 0. 诚实保障披露(必读)

- **冻结-再读**:终选策略在读测试集**之前** git 冻结(commit `cb779d0`,
  `FROZEN_R3_*` 钉死 3dp 权重 + constraint/k/a_max/cap;`load_frozen_strategy`
  复验 gitignore 的搜索 artifact 一致后,**实际打分用 git 冻结的常量**(非 artifact
  的全精度值,故冻结为 0.000 的因子严格保持 0),任一漂移即 fail-closed)。
- **测试集第 3 次评测**:本锁定测试集(2025-06-04→2026-06-12)此前已被
  **round-1 + round-2 终选策略各评测 1 次**;本轮是**第 3 个**被该测试集评测的策略 →
  **跨策略多重检验存在但有界(3 次,非数百次)**。不假装这是处子 OOS。
- **四门判据未放宽**(owner 锁定,全过才 PASS):净>0 / 超额≥0 / MDD≤15% / 夏普≥0.5;
  IR/TE 仅补充披露。
- **多重检验 deflation 已在 R3-4 做**:DSR/PBO/SPA 按累计 N=612 deflate。
- **数据完整性(本轮新增的诚实点,见 §6)**:首次真摄取的三表(income/cashflow/
  balancesheet_vip)被 Tushare 单调用行上限**静默截断**(cashflow 23/45 期钉死 6400 行,
  丢约 2400 票/期);**已在建面板前用分页重摄修复**(commit `a24a6ba`),本判定跑在
  **完整数据**上。这是离线 codex 抓不到、只有真跑+coverage manifest 才暴露的真 bug。

---

## 1. 一句话结论

在**冻结后才读的锁定测试集**上,终选策略 **constituent_only 增强指数(round-2 十一
因子 + 应计 accr)** **扣成本净赚 +17.83%、回撤仅 7.00%、夏普 1.51**,但**累计超额
−4.00 个百分点**(同期 CSI300 **+21.84%**)。四门 **3 过 1 不过 → 判定 FAIL**。

**这是第三次诚实的样本外失败,且 R3-4 的诚实门事先精准警告了它**:DSR=0.032(远不过
0.95)/ PBO=0.571 / **SPA-vs-passive p=0.110**(无法拒绝『不优于被动持有 CSI300』)。
本轮唯一新增 alpha 源 **应计 accr 只拿到 0.006 权重**,**未能转正指数超额**;且因 k 由
round-2 的 0.10 降到 0.05(更弱 tilt),超额从 round-2 的 −0.26pp **退到 −4.00pp**。

---

## 2. 终选策略(R3-4 选出,R3-5 冻结)

- **exposure_constraint = constituent_only**(真增强指数:仅在 CSI300 成分内 tilt)
  / **k=0.05** / **a_max=0.01** / nonconst_cap 0.10(constituent_only 下不触发)。
- 权重(R3_CARRY 中性化复合,价值/质量为主、动量≈0、应计权重极小):
  `amihud_20d 0.236 · np_yoy 0.213 · gpm 0.147 · vol_20d 0.094 · turn_20d 0.087 ·
  roe 0.068 · ep_ttm 0.057 · ret_20d 0.043 · rev_yoy 0.027 · max_20d 0.021 ·
  accr 0.006 · ret_5d 0.000`。

---

## 3. 锁定测试集结果(2025-06-04→2026-06-12,49 调仓期)

| 指标 | round-3(+accr) | round-2(benchmark-relative) | round-1(long-only,对照) |
|---|---|---|---|
| 调仓期数 | 49 | 49 | 49 |
| 累计净收益(扣成本) | **+17.83%** | +21.58% | +5.48% |
| 年化 | +18.39% | +22.26% | +5.64% |
| 夏普(年化) | **+1.51** | +1.80 | +0.54 |
| 最大回撤 | **7.00%** | 6.11% | 7.78% |
| 同期 CSI300 | +21.84% | +21.84% | +21.84% |
| **累计超额** | **−4.00%** | −0.26% | −16.36% |
| IR | −0.84 | (贴住) | — |
| TE | 4.21% | 5.37% | — |
| size_active | +0.059 | −0.064 | — |
| forced-UW | 12.6% | — | — |
| turnover | 0.10 | — | — |

分年:**2025 +24.36%(29 期)/ 2026 −5.24%(20 期)**。

**四门**:`[PASS] 净>0` · `[FAIL] 超额≥0` · `[PASS] 回撤≤15%` · `[PASS] 夏普≥0.5` → **FAIL**。

---

## 4. 为什么 FAIL(诚实归因)

1. **没有正的指数超额 alpha 源**。这是 round-2 已诊断、本轮试图修复的核心缺口。本轮补了
   2 个零成本正交候选(盈余惊喜 SUE + 应计/资产增长),严格 R2-2 协议验证后**只有应计
   accr 一个幸存**(中性化 |t|=4.35),且搜索只给它 **0.006** 权重 —— **太弱,不足以
   把全复合的指数超额从负转正**。SUE 中性化后 |t|=2.74<3 被丢(行业+市值中性化吃掉信号);
   资产增长符号错被丢。**A 股大盘强势年里,这套价值/质量增强指数仍跑不赢 cap 加权的 CSI300。**
2. **诚实门事先精准预言**。R3-4 开发证据:**DSR=0.032**(比 round-2 的 0.056 更低)/
   PBO=0.571 / **SPA-vs-passive p=0.110**。三个指标都说『扣掉 612 次多重检验后,这个
   样本内 IR 0.88 与运气不可区分,且无法证明优于被动持有指数』。**样本外 −4.00% 超额
   兑现了这个警告** —— 反过拟合门第三次正确预测了脆弱性,没让一个指数 loser 上线。
3. **构造是对的,料不够**。测试期 beta≈1、size_active +0.059(漂移守住)、TE 4.21%、
   回撤 7.00%、夏普 1.51 —— 这是个**纪律良好、扣成本真盈利**的增强指数;它输在**缺真
   超额原料**,不是输在工程。比 round-2 更差的 −4.00pp 主要来自搜索选了更弱的 k=0.05
   tilt(样本内更稳但样本外更贴指数下方)。

---

## 5. 与 round-1 / round-2 的关系(三次评测全 FAIL)

| 轮次 | 终选 | 净收益 | 超额 | 判定 | 反过拟合门事先信号 |
|---|---|---|---|---|---|
| round-1 | long-only top-5 防御组合 | +5.48% | −16.36% | FAIL | DSR 0.066 警告 |
| round-2 | 增强指数(11 因子) | +21.58% | −0.26% | FAIL | DSR 0.056 / SPA 0.126 |
| round-3 | 增强指数(11+accr) | +17.83% | −4.00% | FAIL | DSR 0.032 / SPA 0.110 |

**三轮、三次 FAIL、反过拟合门三次正确预警**。本测试集已被评测 3 次,**之后慎再迭代本测试集**
(再迭代等于把它磨成事实上的训练集,诚实价值递减)。

---

## 6. 本轮的工程诚实记录(真跑暴露 + 修复的真 bug)

- **静默截断**:R3-1 首次真摄取的 income/cashflow/balancesheet_vip 被 Tushare 单调用
  每端点行上限静默截断(cashflow_vip 6400 / balancesheet 7000 / income 9000)。因三表
  每票多行(多 report_type/ann_date),cashflow **23/45 期钉死 6400 行**,20250331 仅取
  3241 票、实际 5650 票。**coverage manifest 的 fail-closed 设计正确地把它报成 59% 揪出来**。
- **影响**:应计 = n_income − n_cashflow_act,截断的现金流会污染本轮两大候选 alpha 源之一。
- **修复**:`TushareClient` 加分页(limit+offset,每页 throttle);ingest 加 append-only
  版本递增重摄(截断 v1 字节保留,`store.latest` 返回完整 v2)+ `--phase round3-restate`。
  47 个截断期写 v2,coverage 最差从 59% 修到 99.83%。本判定跑在**完整数据**上。
  commit `a24a6ba`(codex 二轮 0 finding,344 测试绿)。
- **教训**:绿测试 + 离线 codex ≠ 数据正确;**真跑 + coverage manifest** 才暴露供应商行上限。

---

## 7. 下一轮方向(补不出正超额 → 换原料类别)

本轮证明:**零成本财报衍生因子(SUE/应计/资产增长)不足以在强势大盘年产生正指数超额**。
要再攻,需换**真正不同的 alpha 原料类别**(均为新 PIT 摄取,现有 token 多数 ¥0):

1. **资金流 / 北向 / 龙虎榜**(`moneyflow` / `hk_hold` / `top_list`,¥0):
   交易型 alpha,与财报因子正交,可能在大盘年捕捉资金轮动。
2. **事件因子**(`forecast_vip` 业绩预告 / `express_vip` 业绩快报,¥0):
   PEAD 的预告版,比已摄取的 fina 季报更早、信息含量可能更高。
3. **分析师上修**(`report_rc`,**需 8000 积分**,约差 3000,需 owner 登录确认+充值):
   盈利预测修正是最经典的正交 alpha 源之一;受积分门槛阻,需 owner 决策是否充值。
4. **重定义判据**(owner 决策):若坚持『跑赢 cap 加权 CSI300』,在大盘强势年对长多增强
   指数是高门槛;可考虑 (a) 等权基准 / (b) 行业中性多空(参考臂,**不可上线**)/ (c) 接受
   『扣成本真盈利 + 控回撤』为主目标、超额为加分。**此为口径变更,须 owner 明确拍板,非偷改。**

> **本测试集已 3 次评测**:任何下一轮若仍用它判定,须显式披露第 4 次;更稳妥是**冻结新策略、
> 等真前向窗口**(test_end 2026-06-12 之后新增的数据)做处子 OOS。

---

## 8. 产物清单

- `data/factor_research/round3_locked_test_result.json` —— R3-6 四门判定(本报告数据源)。
- `data/factor_research/round3_search_result.json` —— R3-4 开发证据(DSR/PBO/SPA/哨兵/CPCV)。
- `data/factor_research/round3_crosscheck_result.json` —— R3-5 成本压力(摩擦单调 ✓)。
- `docs/research/factor-strategy-round3-r3-factor-diagnostics-2026-06-19.md` —— R3-3 因子验证。
- 代码:`scripts/factor_research/{ingest_round2_data,statements_pit,namechange_pit,factor_lib,
  build_factor_panel,r3_factor_diagnostics,benchmark_relative,round2_search,
  full_engine_crosscheck,round3_locked_test}.py` + `config/research/round3_experiment_manifest.json`。
- commit:`120ea82`(R3-1)`5678f45`(R3-2)`40255b3`(R3-3)`a24a6ba`(截断修复)
  `8c932cc`(R3-4)`cb779d0`(R3-5 冻结)+ 本报告 docs commit。
