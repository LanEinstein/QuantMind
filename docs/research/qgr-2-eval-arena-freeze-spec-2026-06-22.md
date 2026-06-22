# QGR-2 评测口径冻结规格(Evaluation Arena Freeze Spec)

> **状态**:**DRAFT — 待 owner 确认后冻结**(QGR-2 末关键 checkpoint:先冻评测口径再进 QGR-3,主文档 §7)。
> **日期**:2026-06-22 · **作者**:Claude(Opus 4.8)· **代码**:feature `8ad9116`(QGR-2 竞技场骨架,本地未 push)。
> **这是什么**:把可复用竞技场的**评测口径**(主指标 + CPCV 配置 + 账本 legacy 块 + baseline 面板 + 诚实门参数 + Layer-B)一次性钉死,使 QGR-3 因子库 / QGR-4 搜索在固定标尺上进行(诚实门**永不**指导搜索)。本规格 ≈ 全部"已定",**只有 §1 的 3 个 owner 决策待拍板**;拍完即把本文件标 FROZEN。

---

## 0. owner 待确认的 3 个决策(其余全为推荐默认,owner 不反对即采用)

1. **主目标函数形态**(§1.1)——推荐 **(A) 主=事件循环扣成本净 P&L + MDD/换手作硬约束**。
2. **MDD 硬上限**(§1.2)——推荐 **8%**(对齐现役 P0-6 验收门 §2.8 最大回撤 ≤8%)。
3. **主腿 horizon**(§1.3)——推荐 **5td 为 canonical**(贴系统最短持仓 5td)+ 10td 作稳健变体;快腿 T+1/1-2td 同竞技场公平比(QGR-4)。

---

## 1. 主指标(冻结对象 = ≤5 篮子本身的绝对净盈 + 控回撤)

### 1.1 目标函数(owner 决策①)
owner 判据 = **绝对净盈 + 控回撤**(去 CSI300 超额硬门,仅披露)。三个候选形态:

| 形态 | 定义 | 评 |
|------|------|----|
| **(A) 净 P&L 目标 + 风险硬约束(推荐)** | **最大化** = 事件循环**扣成本净总 P&L**(`GateBacktestResult.net_pnl_yuan`/`total_return`,复利);**约束**(违则淘汰):MDD ≤ cap(§1.2)+ 月换手 ≤ cap(§1.4)+ **跨 CPCV 稳定为正**(§1.5)。precision@K/rank-IC 仅诊断 | 最贴 owner 原话(净盈=目标,控回撤=约束);避免单比率 Goodhart;搜索目标单一可排序 |
| (B) Calmar 比率 | 年化净收益 / MDD | 把两者捆成一个数,但易被极小 MDD 放大、对短样本不稳 |
| (C) 净收益−λ·MDD 效用 | 需定 λ;λ 的选择本身是自由度 | 引入隐藏超参 |

**推荐 (A)**。下文按 (A) 写;若 owner 选 (B)/(C),§1.2-1.5 相应改为比率/罚项参数。

### 1.2 MDD 硬上限(owner 决策②)
推荐 **MDD ≤ 8%**(对齐现役 P0-6 验收门「最大回撤 ≤8%」§2.8;事件循环回撤从起始资本起算,已修 codex P2 #4)。

### 1.3 horizon(owner 决策③)
推荐 **canonical = 5td**(= `slot_rotation_policy` 最短持仓 + rebalance 间距);**robustness 变体 = 10td**;**快腿 = T+1 / 1-2td** 同竞技场公平比(QGR-4 决定是否动 live 机制,需独立 amendment)。`run_gate_backtest(horizon=...)` 已参数化;period_returns 按 horizon 非重叠重采样。

### 1.4 换手约束
`GateBacktestResult.monthly_turnover` 披露;硬上限**暂留软披露**(≤5槽 + 5td 最短持仓 + 粘滞轮动已天然约束换手)。若 (A) 需硬 cap,QGR-4 标定后补(amendment)。

### 1.5「跨 CPCV 稳定为正」量化
- **CPCV combination 净 P&L 分布**(15 个 held-out combination):`frac_positive ≥ 0.80` + `min-combination 净 P&L > −X%`(X 待 QGR-4 标定,暂 5%)。
- regime 切片(牛/熊/震荡,纯指数派生确定性)各自净 P&L 披露,不要求每个为正但要求综合为正 + 无单 regime 灾难。

### 1.6 诊断指标(不入目标,仅披露)
precision@K(top-K 篮子未来 horizon 上涨命中率)/ rank-IC(信号与未来收益秩相关)/ avg_exposure / 单+总仓 cap 越界计数(`exposure_cap_violations`,§4 proxy 边界)。**诊断权重 = 0(不进搜索目标)**,只用于事后理解(化解主文档 §9.3 开放项)。

---

## 2. CPCV 配置(冻结)

- **N=6 组 / k=2 测 → C(6,2)=15 combination,φ=(k/N)·C(N,k)=5 path**(`cpcv.QGR_N_GROUPS/QGR_CPCV_K`,自检 `path_count_verified`)。
- **purge/embargo ≥ 标签 horizon**:非重叠 5td bet(rebalance 间距 = horizon)→ 期间无重叠 → **embargo = 1 period**(块首丢 1 期作边界卫生);若 rebalance < horizon → `embargo = ceil(horizon/rebalance_freq)`。**重叠路径绝不当独立样本喂 DSR**。
- **固定序列零 path 离散**(诚实记录):固定非重训配置 path 离散=0(每 path 拼同样 OOS 切片);真 path 离散来自 QGR-4 的选择程序(IS-best 选在 complement 上)。
- **block size ≤ embargo 时 fail-closed 空报告**(已修 codex P2 #11)。

---

## 3. 累计 trial 账本 + legacy 块(冻结;codex P0「改判据不清零 mining 债」)

- **legacy 块(code-defined,不可丢)**:R1 网格 512 + R2 612 + R3 612 + R4 612 = **2348(精确,来源 `round4_locked_test.py:9`)** + 4 次锁定 test 读 + 诊断 screens(R2-2≈9/R3-3≈3/R4-4≈7=19)+ R5 稳健 5 = **floor ≈ 2376**(诊断/消融为保守下界)。
- **deflation N = max(cumulative_effective, 本批 ONC 有效 N)**;appended QGR 批次按其 **ONC 有效计数**(`effective_n`)累加,**近重复网格不以 raw 膨胀**(已修 codex P2 #6)。
- DSR floor **0.95** 不变;MinBTL target Sharpe **1.0** 不变;PBO/SPA 披露不否决(减法门,小样本不崩)。

---

## 4. 诚实门参数(冻结)

| 门 | 参数 | 值 |
|----|------|----|
| DSR | floor / HAC lag(重叠持仓) / kurtosis | 0.95 / `daily_returns` 用 Bartlett lag=4(=horizon−1);非重叠 period_returns 用 lag=0 / full(非 excess) |
| ONC 有效 N | 相关单链阈值 | \|ρ\| ≥ 0.5(flat 零方差并 1 簇) |
| MinBTL | target Sharpe / periods_per_year | 1.0 / 252 |
| SPA(Hansen) | bootstrap / seed / avg_block | stationary 500 / 20260614 / 5 |
| Romano-Wolf StepM | alpha / n_boot / seed / avg_block | 0.05 / 1000 / 20260622 / 5(预声明 family,块 bootstrap) |
| BH / BY FDR | q | 0.10(BY 任意依赖下有效) |
| PBO-CSCV | n_splits | 10(披露) |

**铁律**:诚实门**永不**指导搜索(Goodhart);窗口/超参 committed 在前。

---

## 5. baseline 面板(冻结;防 long-beta 假象,codex P1)

候选闸门须**稳定击败**(SPA/Romano-Wolf 公平比):

| 名 | 机制 | 备注 |
|----|------|------|
| random_top5 | 随机无技艺信号经 ≤5槽机制 | **粘滞轮动 → 当前=持有随机篮**(真 beta 门槛);**fully-rebalanced 变体 = QGR-4 精化**(codex #13) |
| live_momentum_0p40 | 现役 screener `ret_20d` 0.40 | 需 QGR-3 因子面板喂分 |
| pure_liquidity | 纯换手/流动性排序 | 需 QGR-3 因子面板喂分 |
| etf_only_510300 | CSI300 ETF 占 1 槽(≤5槽机制) | ETF 经 `fund_daily`(codex #15) |
| csi300_etf_hold | **全额投资** CSI300 ETF 买入持有(直接算,非 ≤5槽) | 最硬 beta 门槛(codex #2);跳过涨停不可成交开盘(codex #14) |

---

## 6. Layer-B 前向确认(冻结骨架;上线 gate)

- **预注册**(`PreRegistration`,content-addressed,**含冻结 spending schedule**,codex #12):strategy_artifact_sha256 + 成功判据(`*_min`/`*_max`)+ overall_alpha **0.05** + bet_horizon **5td** + min_observations **20**(非重叠 5td bet)+ target_observations(规划全窗)。
- **独立观测 = 完整非重叠 5td bet**;**ACCRUING 绝不在噪声出 verdict**(< min_observations → ACCRUING)。
- **alpha-spending = OBF**(早期保守;`spending="obf"`)。多次窥视按 spending 预算计入。
- **go-live gate**:前向 PASS + 真成本/容量/regime sanity + owner gate + `LiveArtifactRegistry` + **45 日真管线 shadow replay** + 人工 pin + 重启。

---

## 7. proxy 边界(§4.4,冻结认知;非 bug)

事件循环回测 = **量化机制 proxy**,**不含** LLM 辩论 / 全 RiskEngine / Line-2 盘中风控。两处已文档化 proxy 边界(live RiskEngine 盘前强制,proxy 经 `exposure_cap_violations` 暴露,与 `conservation_ok` 硬保证分离):
- 单股 15% cap:盘后严格校验对 friction-epsilon 的 sub-percent 越界(gap-up fill)。
- 总仓 70% cap:`decide_day` 等权 5 槽 ~75% gross 超 70%(冻结 backtest 代码,强制须 amendment)。

**go-live 仍须真管线 shadow replay**(§6);量化回测过门 ≠ 全系统验证。

---

## 8. 冻结后约束(进 QGR-3 前生效)
- 本规格 owner 确认 → 标 FROZEN → QGR-3 因子库 / QGR-4 搜索在此标尺上跑,**口径不再改**(改 = amendment 先行)。
- QGR-3 另有**关键人工 gate**:预注册「政策→主题」映射(战略主题清单 + 每主题政策发布日依据,防 hindsight)+ THS 概念成分受控用法 → 须先拿 owner 确认再冻(主文档 §7 / §9.2)。
