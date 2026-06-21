# 量化选股策略研究 第 4 轮 — 新 session kickoff(2026-06-20)

> **新 session 直接读本文件即可无缝接手。** 写作纪律:exhaustive —— 完整命令 + 预期输出 +
> 待写代码骨架 + 红线,future session 不必重新推导。本轮触发 = owner 已把 Tushare 充值到
> **8000 积分**(解锁分析师盈利预测 `report_rc` 等)+ owner 给了两条新方法论铁律(§3)。

═══════════════════════════════════════════════════════════════════════

## 0. 一句话使命 + 如何开工

**round-1/2/3 三轮全 FAIL**(净赚但跑不赢强势 CSI300;反过拟合门 DSR/PBO/SPA **三次正确
预警**)。诊断:**零成本财报衍生因子(价值/质量/反转/SUE/应计)不足以在大盘强势年产生正
指数超额** —— 缺的是**真正不同类别的正交 alpha 原料**。8000 积分解锁了**分析师修正**这一最
经典的正交 alpha 源。本轮 = **以分析师修正动量为头号候选**(+资金流/事件/筹码为备选),
**文献/牛人驱动 → 严格验证**,补不出仍如实 FAIL。

**开工第一步(按序):**
```bash
cd /home/ps/papers/QuantMind && git log --oneline -3   # 应见 0834b60 起的 round-3 终判链
# 读全局上下文(本轮的来龙去脉):
#   docs/research/factor-strategy-round3-result-2026-06-19.md   ← round-3 FAIL 报告 + §7 下一轮方向
#   ~/.claude/projects/-home-ps-papers-QuantMind/memory/MEMORY.md + project-factor-strategy-research-2026-06-16.md
#   reference-tushare-statement-vip-row-cap.md + reference-tushare-entitlements-2026-06-19.md
# 然后执行 §2(权限实探)→ §4(选方向)→ §9(R4-1..R4-6 流水线)。
```

═══════════════════════════════════════════════════════════════════════

## 1. 背景:为什么第 4 轮(三轮 FAIL 的模式)

| 轮 | 终选 | 净 | 超额 vs CSI300 | 判定 | 门事先信号 |
|---|---|---|---|---|---|
| 1 | long-only top-5 防御组合 | +5.48% | **−16.36%** | FAIL | DSR 0.066 |
| 2 | 增强指数(11 因子) | +21.58% | **−0.26%** | FAIL | DSR 0.056 / SPA 0.126 |
| 3 | 增强指数(11+应计) | +17.83% | **−4.00%** | FAIL | DSR 0.032 / SPA 0.110 |

**核心教训(已验证,见 CLAUDE.md «研究专项已验证原则»):**
- 反过拟合门(DSR/PBO/SPA)**三次精准预言失败** → **信它**;低 DSR 不是"门保守",是真脆弱。
- 构造工程已对(beta≈1、size 漂移守住、回撤<8%、扣成本真盈利);**输在缺正交 alpha 原料**。
- **强势大盘年里,长多增强指数要跑赢 cap 加权 CSI300,必须有真·正交 alpha**;财报衍生因子不够。

═══════════════════════════════════════════════════════════════════════

## 2. R4-1 首要任务:8000 积分权限实探(owner 指定)

**owner 指令**:① 到 `https://docs.qq.com/sheet/DT0FIYUxYakJ5c1FF?tab=BB08J2`(积分权限表)详细研究;
② **用真实探针探查实际权限**(docs 是 JS 渲染的腾讯文档,WebFetch 取不到 → **真实 API 探针才权威**)。

**已起头确认(2026-06-20,现 8000 积分 token 实测 `[OK]`):**
- ✅ **`report_rc`(券商分析师盈利预测/评级,本轮头号 alpha 源)** —— 2016+ 有数据;按 `report_date`
  查(=PIT 可得日)或 `ts_code`+range。字段:`ts_code/report_date/org_name/author_name/quarter/
  op_rt(营收预测)/op_pr(营业利润)/**tp(利润总额万元,⚠️非目标价!实测 2026-06-20)**/np(净利润预测,万元)/eps(EPS 预测)/pe/roe/**目标价=min_price**/
  rating/max_price/min_price`。单 report_date 全市场 ~170(2016)→700+(2020),稀疏流式(非日快照)。
- ✅ `cyq_chips`(筹码分布)/ `stk_factor_pro`(技术因子 pro,含复权 OHLC+预算因子)/ `ccass_hold`(中央结算持股)。

**新 session 须做的完整探针(R4-1)**:把候选端点全部 `[OK]/[FAIL]` 探一遍 + 取字段 + 历史深度 +
单调用行上限(**警惕 `*_vip` 静默截断,见 [[reference-tushare-statement-vip-row-cap]] → 必分页**),
落 **新 reference memory `reference-tushare-entitlements-8000-2026-06-20.md`** + 更新本文件 §2。探针骨架:
```python
import os, tushare as ts
ts.set_token(os.environ["TUSHARE_TOKEN"]); pro = ts.pro_api()
def probe(name, fn):
    try:
        df = fn(); print(f"[OK] {name}: rows={len(df)} cols={list(df.columns)[:16]}")
    except Exception as e: print(f"[FAIL] {name}: {str(e)[:120]}")
# 候选(按 §4 方向):report_rc / moneyflow / moneyflow_hsgt / hk_hold / top_list /
# forecast_vip / express_vip / cyq_chips / cyq_perf / stk_factor_pro / ccass_hold / 其它高积分端点
```

═══════════════════════════════════════════════════════════════════════

## 3. 方法论铁律(owner 2026-06-20 锁定 —— 已写入 CLAUDE.md,本轮强制)

1. **不闭门造车**:因子挖掘 & 策略确定**从前沿文献 / 牛人分享汲取灵感和成功经验**,然后**我们严格
   验证**。开工前走 [development-workflow §0 Research&Reuse]:`gh search`、arXiv/SSRN、券商金工/
   知名量化博客(provenance-gated,记来源)→ 再实现。**严禁拍脑袋造因子。**
2. **从零数学提取 → 沿用既有数据划分**:若用统计/数学方法**从零提取**新因子/权重,**必须用既有
   locked split**(train_val 开发、test 封存;但 **test 已被评测 3 次**,见 §5)。
3. **测现成策略 → 可用更广数据**:若**测试现成(文献已发表/牛人公开)的策略**,**可用更广数据
   (不再局限 12 个月 test 窗口)** 做复现确认 —— 这是"该 edge 在 A 股是否存在"的复现,**不烧 test 集**。
4. **先试上一轮建议方向**(§4):分析师修正(report_rc)优先,再资金流/事件/筹码。
5. **验证过的原则及时写入 CLAUDE.md 等跨 session 文件**(owner 指令)—— 本轮新验证的原则要回写
   CLAUDE.md «研究专项已验证原则» + memory。

═══════════════════════════════════════════════════════════════════════

## 4. 候选 alpha 方向(优先级 + 为什么 + 数据)

### ① 分析师修正动量(`report_rc`)—— **头号,最经典的正交 alpha**
- **文献依据(须查实+扩充)**:EPS/盈利预测**上修**(revision momentum)、评级上调、目标价隐含
  收益、预测**分歧度**(dispersion)、覆盖券商数变化 = 全球最稳健的正交 alpha 之一;与价格动量
  不同(A 股价格动量缺失,但分析师修正是**信息流**非价格流)。A 股卖方覆盖偏大盘 → **可能正好补
  "大盘年跑赢 cap 加权指数"的缺口**。
- **因子设计(候选,待文献校准)**:① `eps_rev` = 近 N 月 EPS 预测中位数环比变化 / |上期|;
  ② `np_rev` = 净利预测上修幅度;③ `tp_impl` = 中位目标价(=`min_price`,⚠️非 `tp`=利润总额)/现价 −1;④ `rating_chg` = 评级上调净家数;
  ⑤ `coverage_chg` = 覆盖券商数变化;⑥ `disp` = 预测分歧度(std/mean,反向)。
- **PIT**:用 `report_date < 决策日` 的报告;trailing 窗口(如 90/180 天)聚合每票。稀疏流式 →
  摄取按 `report_date` 跨日/跨月分块拉全市场,再 PIT 聚合。**字节存档+checksum+coverage,同 K-001。**

### ② 资金流 / 北向 / 龙虎榜(`moneyflow` / `moneyflow_hsgt` / `hk_hold` / `top_list`)
- 交易型 alpha,与财报因子正交;北向持股变化(聪明钱)、主力净流入、龙虎榜机构净买。大盘年
  可能捕捉资金轮动。**5000 积分已可取**(见 entitlements memory),¥0。

### ③ 事件(`forecast_vip` 业绩预告 / `express_vip` 业绩快报)
- PEAD 的**预告版**:比已摄取的 fina 季报**更早**(预告先于正式财报)、信息含量可能更高。零成本。

### ④ 筹码分布(`cyq_chips`/`cyq_perf`,8000 新解锁)
- 获利盘比例/筹码集中度 = 行为金融的套牢盘/获利了结代理。较新颖,文献较薄 → 谨慎、当备选。

> **建议执行序**:先 ①(report_rc,头号)做到底;若 ① 单独不够,再叠 ②/③ 做多源正交组合。
> **每个新因子都走 R2-2 验证协议**(中性化 |t|≥3 + 低共线 + 机制注册;弱则如实丢,同 SUE/动量)。

═══════════════════════════════════════════════════════════════════════

## 5. 继承的诚实约束(已验证原则,违反即停)

1. **反过拟合门 = 真预言,信它**:DSR≥0.95 主门 / PBO≤0.5 / SPA-vs-passive;三轮三次正确。
   开发证据 ≠ 判定;低 DSR 别当"保守"忽略。
2. **测试集已评测 3 次**(round-1/2/3)。本轮判定路径**二选一,owner 拍板**:
   - **(强烈推荐)冻结新策略 → 等真前向窗口**:用 `test_end=2026-06-12` **之后新增**的数据做**处子
     OOS**(唯一没被污染的 OOS)。需先摄取 2026-06-13+ 的新行情/财报。
   - **(次选)既有测试集第 4 次评测**:须显式披露"第 4 次评测、跨策略多重检验 4 次";四门不放宽。
     本测试集每多评一次,OOS 价值递减 → **能避则避**。
3. **数据划分铁律(§3.2/3.3)**:从零提取→既有 split;测现成策略→可用更广数据(复现不烧 test)。
4. **Tushare 分页**:`*_vip` 及任何多行/票端点单调用静默截断 → **必 limit+offset 分页**
   ([[reference-tushare-statement-vip-row-cap]]);coverage manifest fail-closed 兜底。
5. **PIT/幸存无偏/无前视**:`report_date`/`ann_date < 决策日`;字节存档+checksum+coverage;
   退市股不删(append-only);survivorship universe 含退市。
6. **数据源仅 Tushare 官方 SDK**;`TUSHARE_TOKEN` 不入 LLM/飞书池;IPv4-only(`local_address="0.0.0.0"`);
   **不引 akshare 进研究 PIT 路径**。
7. **离线 only**;LLM 只用于文献调研(provenance-gated),**绝不进数值策略**;不碰 simulation_auto;
   不接线上 FACTOR_WEIGHTS 不经 owner gate;**永禁真实下单**;**不动 governance EconomicMechanism enum**。
8. **codex 前置门**:含代码任务 commit 前 `codex review --uncommitted </dev/null`(撞额度回退
   `/code-review high`),修完 P0/P1/P2;新文件先 `git add -N`;docs/配置/记账 commit 豁免。
9. **import 隔离**:`scripts/factor_research` 可 import `backend.{marketdata_snapshot,backtest,
   strategy_evolution}`;`backend.data.*` 须 per-line `# noqa: TID251`;**严禁** `backend.{llm,agents,mirofish}`。
10. **诚实**:无 data-snooping;DSR/PBO/SPA/哨兵全报;四门不放宽;FAIL 报 FAIL + 下一轮方向。

═══════════════════════════════════════════════════════════════════════

## 6. 复用资产(round-2/3 全代码栈,改动面应小)

| 文件 | 角色 | 本轮怎么用 |
|---|---|---|
| `backend/data/tushare_client.py` | 端点 + **`_fetch_paginated`(分页+throttle)** | 加 report_rc/资金流等新端点(分页) |
| `scripts/factor_research/ingest_round2_data.py` | 字节存档+幂等+coverage+`reingest`/`--phase` | 加新端点摄取(仿 ingest_statement) |
| `scripts/factor_research/statements_pit.py` | `PeriodStatementPIT`(asof/as_known/vintage) | 分析师修正 PIT 聚合可仿照(report_date 门) |
| `scripts/factor_research/factor_lib.py` | `R3_FACTORS` registry + 纯因子函数 | 加 `R4_FACTORS`(eps_rev 等;机制注册) |
| `scripts/factor_research/neutralize.py` | 行业+log市值 OLS 残差 | 直接复用 |
| `scripts/factor_research/r3_factor_diagnostics.py` | IC+共线+vintage+carry 决策 | 仿照写 `r4_factor_diagnostics`(R2-2 协议) |
| `scripts/factor_research/benchmark_relative.py` | `CARRY_FACTORS`/`R3_CARRY_FACTORS` + 构造 | 加 `R4_CARRY_FACTORS`(R3_CARRY ∪ 幸存) |
| `scripts/factor_research/round2_search.py` | **carry 参数化**搜索 + `resolve_carry_inputs` + `--carry` | 加 `--carry r4` 映射 + round4 manifest |
| `scripts/factor_research/full_engine_crosscheck.py` | carry 参数化成本压力(firewall 加固) | 加 `r4` 映射,直接复用 |
| `scripts/factor_research/round3_locked_test.py` | 四门 + firewall(打分用 git 冻结常量) | 克隆 `round4_*`(若走既有 test);或写 forward-window runner |
| `scripts/factor_research/build_factor_panel.py` | `build_panel_r3`/`build_test_panel_r3`/`build_r3_inputs` | 加 r4 变体(并入新因子列) |
| `config/research/round3_experiment_manifest.json` | FROZEN 预声明搜索空间(N=612) | 克隆 round4(carry dim 改;重声明 N) |
| `config/research/test_set_lock.json` | locked split | 不变;若走前向窗口=新增 forward 段 |

**carry 参数化已就位**(round2_search 全函数吃 `carry: Sequence[str]`,默认 `CARRY_FACTORS` byte-
unchanged),加 r4 只需:`R4_CARRY_FACTORS` + `resolve_carry_inputs` 的 `_CARRY_DEFAULTS["r4"]` +
`--carry r4` + round4 manifest + 回归测试守门。

═══════════════════════════════════════════════════════════════════════

## 7. 执行流水线(R4-1..R4-6;每码模块 TDD + 门禁 + codex + feature commit;push owner-gated)

- **R4-1 权限实探 + 文献调研**(§2+§3.1):full 探针 → entitlements memory;`report_rc` 修正动量 +
  资金流/事件因子的**文献/牛人方案调研**(provenance-gated,落 `docs/research/round4-literature-*.md`)。
- **R4-2 PIT 摄取**:新端点(report_rc 优先)加 `tushare_client`(分页)+ `ingest_round2_data`
  `--phase round4`(字节+checksum+coverage+幂等+`--dry-run` 先验);owner-gate「开」才真摄取。
- **✅ R4-3 因子库 + PIT 聚合 DONE(2026-06-20,commit `f4a6805`,本地未 push)**:`R4_FACTORS`
  (np_rev/eps_rev/rev_diff/rating_chg/tp_impl/disp/cover_chg)+ 新 `analyst_revision_pit.py`
  (report_date<d trailing 聚合,**PIT 闸=report_date<d only** —— step0 实探发现 `create_time` 是
  2022 统一 bulk-load 时戳,用它当闸会清零 2022 前全部 train_val 数据;FY1 用 `target_fy_asof(d)`
  两端同锚;rating ordinal 未知→NaN;扩散/分歧 n≥3)+ `build_panel_r4` + 防火墙 `report_rc_month_keys`
  (<test_start)+ `--factor-set r4`(既有列 byte-unchanged)。门禁 352 绿 + ruff + mypy --strict +
  redline;codex 600s stall → `/code-review high` 3-angle **0 findings**(防火墙 SOUND)。真跑
  `panel_train_val_r4.csv` = **325,718 行 / 3001 码 / 498 日**,np_rev 投资域覆盖 67%。
- **✅ R4-4 诊断 DONE(2026-06-21,commit `1c97319`)= 4 轮最强 IC 结果**:新 `r4_factor_diagnostics`
  (R2-2 协议:行业+市值中性化 IC |t|≥3 + **PAIRWISE 2-way-support 共线** + **新增 mutual dedup**〔np_rev/
  eps_rev/rev_diff 同簇〕+ coverage 披露 + §7 诚实 read〔IC t 膨胀=SCREEN 非判定〕)。真跑:**4 分析师
  因子幸存且全正交于 R3_CARRY 12 簇(max 共线 0.38)= 四轮首次「强 + 真新轴」** —— np_rev(neut t5.64)/
  rev_diff(5.06)/tp_impl(6.77)/cover_chg(5.11)。如实丢 rating_chg(neut t1.22)/disp(中性化吃掉)/eps_rev
  (0.90 共线 np_rev)。**R4_CARRY = R3_CARRY(12) ∪ {np_rev, rev_diff, tp_impl, cover_chg} = 16**。稳健双参
  (staleness 180/lookback 60):4 核心两参均幸存,rating_chg 仅 180d 过→保守不纳。codex 480s stall →
  `/code-review high` 0 correctness bug + 4 披露 finding 全修;361 测试绿。报告
  `factor-strategy-round4-r4-4-factor-diagnostics-2026-06-21.md`(+robust)。
- **✅ R4-5 搜索 + 成本压力 + 冻结 DONE(2026-06-21,feature `8d497ff`〔a-d 参数化〕+ `ffc1db3`〔冻结〕)**:
  `--carry r4` 搜索(carry 参数化 + DSR/MinBTL 按累计 N=2348 deflate 解耦,grid 仍 612)+ crosscheck
  (base +52.2%→stressed +35.9%,摩擦单调,oracle UNAVAILABLE)+ **读 test 前 git 冻结 `FROZEN_R4_*`**
  (constituent_only/k0.2/a_max0.04/16 权重 3dp;firewall 打分用 git 冻结常量)。**开发证据(非判定)**:
  val IR 0.71/超额 +4.55%/CPCV 全正 IR_mean 1.45/**SPA-vs-passive 0.056(四轮最接近显著)**,但
  **DSR 0.007(四轮最低)+ PBO 0.329 + 哨兵不过**(噪声 val IR 1.34 > 真 0.71)。门禁 383 绿 +
  `/code-review high`(a-d 0 P0-P2 + 1 P3 cleanup 已修;f 0 correctness/PIT-leakage)。
- **✅ R4-6 判定 DONE = PASS(provisional,2026-06-21,owner「开」→ 一次性读 test)**:既有 test 第 4 次评测
  (累计 N=2348 deflate + 显式披露 + 四门不放宽)。**净 +24.51% / 超额 CSI300 +2.68% / 夏普 1.81 / 回撤
  6.40% = 四门全过 = 四轮首个 PASS**(策略读 test 前 git 冻结 `ffc1db3`,无偷看)。**但 provisional**:
  dev DSR/哨兵未背书 + 第 4 次评测 → 须前向窗口确认。报告 `factor-strategy-round4-result-2026-06-21.md`。
- **✅ 收尾 DONE**:本文件 §7 + CLAUDE.md «已验证原则»(#1 精化/#4 第4次/#5 分析师 alpha 兑现)+ memory + 中文报告。
- **✅ R5 前向 OOS 启动 + 稳健体检 DONE(2026-06-21,owner「开」;feature `c2a8c6e`)**:前向管线搭好
  (`forward_trade_dates`/`build_forward_panel_r4`/`round4_forward_test`,打分用同一 git 冻结,fail-closed
  ACCRUING + csi300 覆盖闸)+ 启动时钟(前向 daily 06-15..18 + report_rc June「20260618」摄取)。**前向 =
  ACCRUING**(test_end 后只 4 td + 端午封市 → 0 可评分,需累积 ~20-40 期数月,出不了 verdict)。**稳健体检**:
  倾斜-稳健 ✓(k0.05/0.1/0.2 IR~1.5)/ 哨兵薄胜(真 1.53 vs 噪声地板 1.46 = 构造 small-cap tilt 占大块)/
  消融分析师扛 >半数超额但重度 rev_diff-集中 → **+2.68% = 真分析师边际(集中 rev_diff)+ 构造/size tilt 混合,
  provisional 既非纯真也非纯假**。报告 `factor-strategy-round5-forward-oos-and-robustness-2026-06-21.md`。
- **⏭️ NEXT(owner 拍板)**:主 = **前向窗口累积重跑**(`$PY -m scripts.factor_research.round4_forward_test`,
  每月,≥20-40 期出可信前向 verdict;verdict 前 refresh csi300/index_weight)。改策略(缓解 rev_diff 集中/加
  组合级 size 中性)= 新冻结 + 新前向窗口,**不可在已 4 评 test 再迭代**。本地 `c2a8c6e` 待 owner 授权 push。

═══════════════════════════════════════════════════════════════════════

## 8. 环境 + 命令速查

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
FEISHU_INTERACTIVE_ENABLED=false $PY -m pytest tests/factor_research/ -q   # 当前 316 绿(只增不减)
$PY -m ruff check scripts/factor_research <你改的文件>
$PY -m mypy --strict scripts/factor_research
bash scripts/redline-check.sh
git add -N <新文件> && codex review --uncommitted </dev/null   # 撞额度→ /code-review high
# 数据 gitignored(data/{marketdata_pit,factor_research}/);报告用中文、推理英文、代码/commit 英文;
# commit 末尾保留 Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

**owner 拍板待定项(开工时确认)**:(a) §5.2 判定路径 = 前向窗口处子 OOS(推荐)还是既有 test 第 4 次;
(b) 方向是否就按 §4 优先序(report_rc 头号);(c) 若走前向窗口,是否现在摄取 2026-06-13+ 新数据。
