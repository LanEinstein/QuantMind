# 量化选股策略研究 第 3 轮方案 — 补正超额 alpha 源(2026-06-19)

> 上轮 `factor-strategy-round2-result-2026-06-19.md`(R2-6 终判 **FAIL**,超额 −0.26%);
> 测试集复用决策 `factor-strategy-round2-test-reuse-decision-2026-06-19.md`;
> 因子诊断 `factor-strategy-round2-r2-2-factor-diagnostics-2026-06-18.md`;
> Tushare 权限地图(2026-06-19 探测)= memory `reference-tushare-entitlements-2026-06-19.md`。
> **本文件 = 书面方案(plan-first);执行(摄取=owner-gated 重活)在 owner 批准后。**

---

## 0. 一句话方案

round-2 证明**构造已对**(benchmark-relative 把 round-1 的 −16.36pp 灾难收窄到 −0.26pp 近平手,
测试期 beta≈1 + size 漂移守住),但**无真 alpha**(DSR 0.056 / SPA-vs-passive 0.126 / 参考臂夏普
仅 0.30 = 因子 alpha 天花板就低)。**第 3 轮 = 抬天花板:补 2 个零成本的新正交 alpha 源** ——
① **盈余惊喜 / PEAD(SUE)**(零新数据)+ ② **应计 / 资产增长(Sloan / investment)**(数据现有
token 已可取,¥0)—— 用 R2-2 那套严格验证后,重跑 R2-4→R2-5→R2-6。**owner 要真指数超额,
补不出仍如实 FAIL。**

---

## 1. 诊断依据(为什么补这两个)

- **R2-3 市场中性参考臂 = alpha 上界,只有夏普 0.30 / +31.67%(2016-2025)** → 现有 carry 集
  (反转/低波/价值/换手/流动性 + roe/gpm/np_yoy/rev_yoy)的 alpha 本就温和,扣成本 + forced-UW
  + 半年衰减后净超额≈0。**构造修不出 alpha,只能换/加原料。**
- **R2-2 已证 roe/gpm 是真·新正交 alpha**(中性化后 |t| 5.9/4.4,与反转簇低共线)→ 沿「盈利质量」
  这根轴往外扩(应计=盈余质量、资产增长=过度投资)最有先验支撑、最可能正交。
- **盈余惊喜(SUE/PEAD)** 是 A 股文献最稳的异象之一,且**零新数据**(用已有季度盈利序列算)。

---

## 2. 两个新因子族(定义 + 数据 + 机制 + 正交性)

### ① 盈余惊喜 / PEAD(SUE)— **零新数据**
- **数据**:**已有的** `fina_indicator_vip`(季度盈利序列,ann_date PIT)。无需新摄取。
- **定义(确定性)**:`SUE_t = (E_t − E_{t-4}) / σ(ΔE)`,其中 `E` = 单季归母净利(或 EPS),
  `E_{t-4}` = 去年同季(季节性随机游走预期),`σ(ΔE)` = 过去 8 季同比变动的标准差。
  PIT:用 `ann_date < d` 的 as-known 季度值(`fundamentals_pit.asof` 已支持);**严禁用未公告季度**。
- **机制**:盈余公告后漂移(PEAD)—— 正向盈余惊喜的股票公告后继续跑赢(注意力/反应不足)。
- **正交性预期**:与价值/质量正交(SUE 是「变化」非「水平」);与反转可能负相关(需测)。

### ② 应计(Sloan)+ 资产增长 — **现有 token 已可取,¥0**
- **数据(2026-06-19 探针确认现有 token 全能取)**:`cashflow_vip`(含 `n_cashflow_act` 经营现金流 /
  `depr_fa_coga_dpba` 折旧)+ `balancesheet_vip`(`total_assets` / `total_cur_assets` / `total_cur_liab`
  / `money_cap`)+ `income_vip`(`n_income` 净利)。季度,ann_date PIT,多 vintage。
- **定义(确定性,两个子因子)**:
  - **应计(cash-flow 法,更稳)**:`ACCR = (净利 − 经营现金流) / 期初期末平均总资产`。高应计 = 盈余质量差
    → 未来低收益。机制:应计部分不可持续/易盈余管理。
  - **资产增长(investment factor)**:`AG = (总资产_t − 总资产_{t-4}) / 总资产_{t-4}`。高增长(过度投资)
    → 未来低收益(A 股实证较强)。
- **正交性预期**:应计与 roe(水平)正交(质量不同维);资产增长与价值/质量正交(投资维)。**两者
  互相关需测,可能算一根「资产负债表质量」轴。**

> 方向(prior):SUE、roe/gpm、应计反向(低应计好)、资产增长反向(低增长好)—— 全部写进 R2 registry 的
> `attractive_high`/机制注册;**growth_premium 标签的治理 enum 门仍 fail-closed until amendment**(同 R2-2,
> 不动 governance)。

---

## 3. 实施阶段(每码模块:TDD + 门禁 + codex/`code-review` + feature commit;push owner-gated)

| Phase | 任务 | 产出 | 依赖 |
|---|---|---|---|
| **R3-1** | **PIT 摄取扩充**:`TushareClient` 加 `cashflow_vip`/`balancesheet_vip`/`income_vip`(period|range,只读);`ingest_round2_data.py` 加三表摄取(字节存档+sha256+幂等续传+fail-closed+限速+per-period coverage manifest,仿 fina_indicator_vip)+ `--dry-run` 先验 + redline 扫描 | 扩 `ingest_round2_data.py`/`TushareClient` + 数据落 `data/marketdata_pit` | **owner-gated 重活** |
| **R3-2** | **因子库扩 + PIT join**:`fundamentals_pit.py` 暴露三表行项(同 ann_date<d vintage gating);`factor_lib.py` R2 registry 加 `sue`/`accr`/`asset_growth`(机制注册、方向 prior、round-1+R2 因子 byte-unchanged);`build_panel_r2` 产出新列;`r2_factor_diagnostics` 扩 | 新因子 + 扩面板 | R3-1 |
| **R3-3** | **R2-2 式诚实验证**:IC 研究(原始+中性化)+ 共线性 + vintage 审计 → 诚实诊断 doc;**纳入门 = 中性化后 \|t\|≥3 + 与现有 carry 簇低共线 + 机制注册**;弱则如实丢 | `docs/research/factor-strategy-round3-r3-factor-diagnostics-*.md` + 更新 carry 集 | R3-2 |
| **R3-4** | **重跑搜索**:R2-4 `round2_search` 用**扩充 carry 集**重跑(新 frozen manifest,N 重新声明=累计 deflation;预声明全 DoF)→ 选唯一策略 | 新 search result + 诊断 doc | R3-3 |
| **R3-5** | R2-5 成本压力交叉确认 + **git 冻结新策略** | crosscheck + 冻结 commit | R3-4 |
| **R3-6**(判定) | **诚实抉择(见 §5)**:① 既有测试集第 3 次评测(披露多重检验已 3 次)或 ② 冻结后等真前向窗口(2026-06-12+)第 3 次判定 → 四门 PASS/FAIL | `factor-strategy-round3-result-*.md` | R3-5 + owner 决定判定路径 |

---

## 4. 复用(不重建)

R2-1..R2-6 基建几乎全可复用:`ingest_round2_data.py`/`TushareClient`(加端点)、`fundamentals_pit.py`
(三表与 fina 同形状,ann_date/vintage gating drop-in)、`neutralize.py`/`factor_lib.py` R2 registry、
`build_factor_panel.build_panel_r2`、`benchmark_relative.py` + `exposure_constraints.py`(constituent_only
已胜出,可设为默认)、`round2_search.py`(改 carry 集 + 新 manifest)、`walk_forward_eval.py`、
`full_engine_crosscheck.py`、`build_test_panel_r2` + `r2_locked_test.py`(R3-6 复用)。**核心改动面小。**

---

## 5. 红线 & 诚实约束(全继承)

1. **测试集神圣**:开发期(R3-1..R3-5)零碰 test;**本测试集已被 round-1 + round-2 评测 2 次** —— R3-6
   判定路径 owner 选:**(优先)冻结新策略 + 等真前向窗口(2026-06-12 之后)第 3 次判定**,或既有测试集
   第 3 次评测(报告须披露「第 3 次评测,跨策略多重检验 3 次」)。冻结-再读纪律不变。
2. **PIT/幸存无偏/无前视**:三表用 `ann_date < d`(非 end_date)+ vintage 审计;SUE 用已公告季度;
   覆盖 fail-closed(缺报→None)。
3. **数据源仅 Tushare 官方 SDK**;`TUSHARE_TOKEN` 不入 LLM/飞书池;IPv4-only 出站。**不引 akshare 进研究 PIT 路径。**
4. **LLM 不进数值策略**(因子定义/打分/中性化/搜索全确定性);LLM 只用于文献。
5. **多重检验 deflation**:R3-4 新 manifest 预声明全 DoF,DSR/PBO 按累计 N;**四门不放宽**,IR/TE 仅披露。
6. **诚实**:新因子弱则如实丢(同 R2-2 丢 mom/trend);补不出正超额则如实 FAIL + 下一轮方向;开发证据≠判定。
7. **codex 前置门**:含代码任务 commit 前过 codex（撞额度回退 `/code-review high`),修完 P0/P1/P2;
   docs 豁免;import 隔离(`scripts/factor_research` 可 import `backend.{marketdata_snapshot,backtest,strategy_evolution}`,`backend.data.*` per-line `# noqa: TID251`,**严禁** `backend.{llm,agents,mirofish}`)。

---

## 6. 风险 / 诚实

1. **可能仍无 alpha**:参考臂上界夏普 0.30 是硬约束;新因子若中性化后 |t|<3 或与现有簇高共线 → 如实丢,
   且 R3-6 可能仍 FAIL。**不假设有效。**
2. **测试集第 3 次评测的代价**:跨策略多重检验累积;**优先走真前向窗口**(owner 停服,需恢复仅数据摄取累积)。
3. **应计 PIT 复杂度**:三表的 ann_date 对齐 + 期初期末平均总资产需跨期取值,须对抗 leakage 审计(同 R2-2)。

---

## 7. 探针副产物(本轮不做,记录备查)

2026-06-19 广度探针发现现有 token 还能取(未来轮的潜在 alpha/工具,需各自验证):
- **`namechange`(曾用名/ST 史)** → 终于能做 **PIT ST 排除**(补 `build_factor_panel` 已知缺口「无 PIT 名称→未显式排 ST」);可在 R3-1 顺带接入做 universe 提纯。
- **资金流/微结构**:`moneyflow`(个股资金流)/`hk_hold`(北向持股)/`margin_detail`(融券余额)/`top_list`(龙虎榜)→ 资金流因子族(未来轮)。
- **`forecast_vip`/`express_vip`**(业绩预告/快报)→ 早于正式财报的「指引超预期」信号(未来轮)。
- **不可用**:`report_rc`(券商分析师预测,需正式 **8000 积分**;当前 ≥~5000,约差 3000,owner 登录确认+充值才取)。

> 完整 Tushare 权限清单见 CLAUDE.md §2.5 + memory `reference-tushare-entitlements-2026-06-19`。
