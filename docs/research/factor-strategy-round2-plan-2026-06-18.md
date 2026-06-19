# 量化选股策略研究 — 第 2 轮方案(2026-06-18)

> **协议阶段 1/3 交付**(§E 三段走:**本文件 = 书面方案** → codex 对抗评审 → 修完 P0/P1/P2 后才执行)。
> 母任务书 `factor-strategy-research-brief-2026-06-16.md`;第 2 轮 kickoff `factor-strategy-round2-kickoff-2026-06-18.md`;
> 第 1 轮 FAIL 报告 `factor-strategy-result-2026-06-16.md`;文献依据 `factor-theory-survey-2026-06-16.md`。
> **本文件不写实现代码**,只定方向、构造、数据、评估协议、任务拆解、风险。

---

## 0. 一句话方案

第 1 轮 long-only top-5 因 **基准无关 + 防御 regime 错配** 跑输大盘牛市。第 2 轮改 **基准相对长多(增强指数)** 为可落地主路径
+ **多空/市场中性** 为研究上界参考臂;补 **趋势/质量/成长** 因子族 + **行业/市值中性化**;**判定性 OOS = 冻结策略后、跑在
2026-06-12 之后新累积的封存前向窗口**(开发期用全历史 CPCV/walk-forward 出证据,但**不**当判定)。**owner 要真指数超额,不是过门;达不到如实 FAIL。**

---

## 1. 已敲定的 owner 决策(2026-06-18,AskUserQuestion)

| 硬问题 | owner 决策 | 含义 |
|---|---|---|
| **C-1 干净 OOS 窗口** | **混合:CPCV 开发 + 前向封存判定** | 现在用全历史 anchored walk-forward + CPCV + 严格 deflation 做开发与稳健性证据(绝不碰已烧测试集)→ **git 冻结唯一策略** → 判定性 PASS/FAIL 一次性跑在 2026-06-12 之后新累积的**封存前向窗口**(需先恢复仅数据摄取/等数据累积)。 |
| **C-2 构造路线** | **两条都做(主+参考臂)** | **基准相对长多 = 主**(唯一可落地,接现役 long-only 系统)→ 唯一进前向判定的策略;**多空/市场中性 = 辅**(研究上界参考,明确**不可上线**,只作 alpha 上界 + IR 披露,永不当 PASS 主张)。 |

> **判据不放宽**:仍是 owner 锁定四门(① 扣成本净>0 ② 跑赢沪深300 累计超额≥0 ③ MDD≤15% ④ 年化夏普≥0.5,**四条全过才 PASS**)。
> 基准相对策略**额外**披露 **信息比率 IR / 跟踪误差 TE / 主动夏普**,这是**新增披露非替换门**(红线:严禁悄悄放宽判据)。

---

## 2. C-1 诚实性核心:为什么没有现成干净 OOS + 前向窗口怎么保证诚实

### 2.1 现状(已验证,2026-06-18)
- 数据 = 2779 交易日 **2015-01-05 → 2026-06-12**(snapshot store endpoints:`daily/adj_factor/daily_basic/fund_daily`)。
- **测试窗口 2025-06-04→2026-06-12 已烧**(第 1 轮一次性用过,契约=复用即作废;锁 `config/research/test_set_lock.json`)。
- **train_val 2015-01→2025-04 全程进过第 1 轮**:IC 研究(全 rebalance 日)+ 权重搜索(train≤2022-12、val 2023-2025 上选过)。
- **我们还已知该测试期是大盘强势年**(CSI300 +21.84%)+ **第 1 轮为何 FAIL**(防御组合在 cap-weighted 牛市落后)。这层认知**无法擦除**。
- 今天 06-18,数据止于 06-12 → **新数据 ≈ 0**,远不够切新窗口。

### 2.2 结论:**现有数据里没有任何真·处子 OOS 窗口**
2015→2026-06 每一天非已烧(test)即开发已见(train_val);且我们已知整段的 regime 行为与第 1 轮失效原因。
据此**任何在 2015-2026 既有数据上重切的"earlier holdout"(kickoff 选项 b/d)都不是真 OOS** —— 即便第 2 轮的新因子/新构造没在那段单独拟合,
该段的横截面行为/regime 已在第 1 轮 IC 研究里被看过,且"挑已知在该段有效的因子再回测该段"是循环论证。**只有数据生成于策略冻结之后的前向数据,才是真 OOS。**

### 2.3 前向判定的诚实机理(owner 选定的混合方案如何 bulletproof)
1. **开发证据(现在做,显式非判定)**:全历史 anchored walk-forward + CPCV(purge+embargo≥20td)+ DSR/PBO/SPA/哨兵,产出唯一基准相对策略。报告时**明确标注**:这是"方法严格但非单一处子 holdout、因子族/搜索空间选择受先验知识影响、由 DSR/PBO/哨兵 deflate"的开发证据,**不是 PASS/FAIL**。
2. **git 冻结(firewall)+ 前向协议同冻**(codex P1-1 修订):唯一策略 commit `C` 钉死(同第 1 轮 commit-before-test 纪律)。firewall 的钉子是**数据存在/公开时间,不是摄取时间** —— 摄取时间可人为延后,不算数。具体:
   - 前向窗口**只含市场收盘(=数据存在/公开)时间晚于 `C` 的 commit UTC 的交易日**;`C` 当日及之前已公开的任何日期(含 2026-06-13..冻结日 已存在的几个交易日)**一律排除**。
   - **窗口规格在冻结时一并预声明并写进 `round2_forward_test_lock.json` 的 firewall 段**(非判定时再拍):窗口起点规则 = "`C` 之后首个合格交易日"、**预声明窗口长度**(owner 在 R2-5 冻结时定:120td≈6 月最小 / ~250td 优,**二选一写死**)、判定触发 = "窗口满 N td 后首个工作日"。**判定时机/长度不得在看到数据后改。**
3. **前向数据累积**:恢复**仅数据摄取**路径(无交易、无 simulation_auto、永禁真实下单),对合格交易日继续摄取(`daily/adj_factor/daily_basic` + §4 新端点),字节存档+checksum+幸存无偏 + **记录每端点供应商 fetch/release 时间戳**(供 firewall 时间审计)。开发期**绝不读**这些前向日期。
4. **一次性判定**:窗口满预声明 N td 后,生成 `round2_forward_test_lock.json`(dates_sha256 + firewall 时间审计:确认每日数据公开时间 > `C`)→ 跑**且只跑一次**冻结策略,**以全引擎 event-loop 净结果为判定主口径**(§5)→ 对照四门判 PASS/FAIL。
5. **前向契约**:**一个冻结策略对一个前向窗口只判一次**;看了前向结果回头改策略 = 烧掉该前向窗口、须再等下一段新数据。**严禁迭代凑过线。**

> **本轮"现在能交付"的 = 开发+冻结的唯一策略 + CPCV 开发证据 + 前向判定 harness(就绪待数据);"判定"延后到前向数据足量。**
> 这正是 owner 选混合的代价与诚实:**宁可判定延后,也不拿污染数据假装 OOS。**

---

## 3. C-2 构造设计:基准相对长多(主)+ 多空/中性(参考臂)

### 3.1 第 1 轮失效 → 第 2 轮修法对照
| 第 1 轮失效根因 | 第 2 轮修法 |
|---|---|
| long-only top-5 **基准无关**(5 等权 vs 300 市值加权) | **基准相对主动权重**:从 CSI300 成分权重出发做有界 tilt,beta≈1,逐期超额=主动权重·因子收益 |
| **防御 regime 错配**(大盘牛跑输) | **行业+市值中性化**消除系统性大盘落后;趋势 sleeve 在牛市跟得上 |
| **横截面 IC ≠ 跑赢市值加权指数**(目标错配) | 直接优化/考核 **相对 CSI300 超额**(IR/TE 控制),不再优化纯截面 rank |
| 防御因子 2026-H1 衰减 | 因子族多元化(价值/防御/反转 + 趋势/质量/成长)+ regime 条件化 |

### 3.2 主路径:基准相对长多(增强指数,**可落地**)
**投资域** = 现役广义筛后域(板块白名单 + 流动性 + 单价 + 剔底30%市值,≈2000 名;复用 `build_factor_panel` 排除链)。
**基准** = CSI300 成分权重 `w_bench`(PIT,来自 `index_weight`,§4)。
**每个 rebalance 日**:
1. 截面算**中性化复合因子分** `s_i`:各因子先对 **行业哑变量 + log(circ_mv)** 截面回归取残差(去行业/市值暴露),再按搜索权重合成。
2. **主动权重** `a_i = clip(k · z(s_i), [-a_max, +a_max])`,约束 `Σa_i = 0`(主动腿净零→满仓 beta≈1);
   `z(·)`=截面标准化;`k`=tilt 强度(搜索量)。
3. **最终权重** `w_i = w_bench_i + a_i`,守约束:**long-only `w_i ≥ 0`**(非成分名 `w_bench=0` → 只能正 active;成分名可超/低配,低配下限 = −`w_bench_i`)、**单名 active 上限**(如 ±2%)、**行业 active 上限**(中性化后剩余有界)、**TE 目标**(年化跟踪误差上限,如 4-8%)。
   - **约束后再投影(codex P2-1)**:`w_i≥0`/clip/行业/TE 约束会破坏 `Σa_i=0`(尤其 long-only floor 把负 active 截断)→ 约束后**重新归一/投影**使主动腿净零、满仓;最终**披露 residual beta、行业 active、市值(log circ_mv)active、实现 TE**,确认 tilt 确为行业/市值/beta 中性而非偷偷押 beta。
   - **被排除成分名处理(codex P2-3)**:CSI300 成分若被本系统排除链(板块白名单/ST/流动性/单价/剔底30%)剔除 → **强制持 0 = 满额低配(active = −`w_bench_i`)**,这本身是一笔被动 active bet,须**计入 TE 并单列披露**(forced-underweight TE 来源);若该被动 active 过大,改为"基准截到可投资交集"并披露基准口径调整。**不得**静默忽略。
4. **逐期超额** = `Σ w_i·r_i − r_bench`,**扣保守换手成本(codex P1-4)**:买卖分开计 `买额=Σ max(Δw_i,0)`、`卖额=Σ max(−Δw_i,0)`,各自套真实费率(佣金含 5 元底 / 卖出印花 0.1% / 过户费 / 分板块滑点 1.5-3.5bp / 整手约束 / 涨跌停·T+1 拒单 / 容量·冲击)→ 搜索期即用此保守口径,**绝不门控毛收益**;调向 CSI300 月度再平衡 + 因子 tilt 换手比 top-5 高,成本敏感。
> 这是标准"带 off-benchmark 暴露的增强指数 tilt"。落地 = 现役 `CandidateSelector`/`FACTOR_WEIGHTS` 可演进为"基准相对打分 + 槽位"(接线 owner-gated,本轮**不**改线上)。

### 3.3 参考臂:多空 / 市场中性(**研究上界,不可上线**)
- **市场中性** = long 因子篮子 − short CSI300(用 `index_daily 000300.SH` 收益或股指期货 IF 近月代理对冲 beta)。报告**纯 alpha**夏普/IR(对冲掉 beta 后)。
- **永禁真实下单 + A 股散户基本无法融券做空** → **明确标注研究参考、永不当 PASS 主张、永不进前向判定的"被判定策略"**。
- 用途:界定 **alpha 上界**;若市场中性 IR 远高于基准相对 IR,说明基准相对 tilt 漏了 alpha(下一轮放宽 TE/active 上限);若两者都弱,说明因子本身在该期无 alpha(诚实负面结论)。

---

## 4. C-3 数据 + 因子族扩充(PIT / 幸存无偏 / 无前视)

### 4.1 需新增的 PIT 数据(Tushare 官方 SDK only;字节存档+sha256+幸存无偏)
现役 ingest = "每交易日一次全市场"(`DEFAULT_ENDPOINTS=daily/adj_factor/daily_basic/fund_daily`,`job.py`)。新端点**异质 cadence**,R2-1 须扩展或并行研究摄取路径(仍存原始字节+checksum 进 SnapshotStore 或并行 store):

| 数据 | Tushare 端点 | cadence / 键 | 用途 | **PIT 前视红线(codex P1-3 强化)** |
|---|---|---|---|---|
| **CSI300 成分权重** | `index_weight(index_code='000300.SH')` | 月度快照,键 `trade_date` | 基准权重 `w_bench` + 参考臂 short 腿 | 不只 `trade_date ≤ d`:加**可得性 lag** —— rebalance 日只用 `trade_date ≤ d−1`(或供应商 release 时间 < d 开盘)的最近一期权重,防当日公布的权重被当日使用 |
| **行业分类(PIT)** | `index_member_all`(申万成分,带 `in_date/out_date`) | 成员表(非每日),版本化单快照 | 行业中性化哑变量 | 用 `in_date ≤ d < out_date` 的 PIT 成员 + 记录该表 release/effective 规则;**严禁** `stock_basic.industry`(current-only=前视)/ 严禁用最新分类 backfill 历史 |
| **质量/成长基本面** | `fina_indicator`(roe / grossprofit_margin / netprofit_yoy / or_yoy …) | 季度,键 `ts_code+end_date`,**含 `ann_date`** | 质量/成长因子 | **landmine**:join 用 **`ann_date`(公告日)≤ d** 非 `end_date`(报告期)+ 安全 lag;**重述/修订(codex P1-3)**:优先用**首次公告 vintage 值**;若 Tushare 只返回最新修订值(后验污染)→ 该因子**不得声称 PIT**,须加 vintage 版本审计,否则**降级/剔除**;绝不用未公告报告期数据 |

> **幸存无偏 = 可验收标准非口号(codex P1-5)**:R2-1 须产 **coverage manifest** 并 fail-closed 校验 ——
> ① `fina_indicator` 覆盖须按 `SurvivorshipUniverse.all_codes()`(在市 + 退市,AE-001 `universe.py`)枚举,**不得从当前在市域派生**;
> ② `index_weight` 须覆盖历史**调入/调出**成分(含已调出/退市名);
> ③ 行业成员须对**每个 panel code-date** 给出 PIT 行业**或显式 `unknown`→fail-closed**(剔出该 code-date,不臆造);
> 任一覆盖缺口 → manifest 记录 + 该 code-date 因子 None(fail-closed),绝不静默补默认。
> 摄取 = **owner-gated 重活**(同 AE-001),`--dry-run` 先验 + 字节稳定 + 幂等续传 + redline 扫描(`grep` 字节存档非 hash-only)。

### 4.2 新因子族(填补"全防御/价值/反转"缺口;文献依据 `factor-theory-survey` §2)
| 族 | 因子(原始值) | 数据 | 文献先验(A 股) | 角色 |
|---|---|---|---|---|
| **趋势/动量延续** | 12-1 月动量(`close[-21]/close[-251]−1`,跳近月避反转污染)/ 距 250d 高 / 60-120d 斜率 | **现有 price**(`daily+adj_factor`)即可,无需新端点 | A 股动量**弱/缺失**(survey §2.9)→ 预期标准独立 IC≈0;价值=**牛市跟指数 + regime 条件化**,诚实测,**不假设有效** | 跟踪/regime |
| **质量** | ROE / 毛利率 GP/A / 盈利稳定性 | `fina_indicator`(PIT ann_date) | **弱/条件**(survey §2.7;用毛利率抗盈余管理。**不设"限非 SOE"条件**(codex P2-2):无 PIT 所有制数据,用 current SOE 标签=前视;若要做须先补 PIT 所有制并计入 N) | 次级 |
| **成长** | 净利润 YoY / 营收 YoY | `fina_indicator`(PIT ann_date) | 弱;诚实测 | 次级 |
| **(保留)第 1 轮 7 因子** | reversal / 低波 / MAX / E/P / turnover / Amihud | 现有 | 已实测 IC 全同号(§A) | 核心 |

> **中性化工具**(新):截面 `因子 ~ 行业哑变量 + log(circ_mv)` 回归取残差(纯 numpy/stdlib,确定性,缺失 fail-closed)。
> **共线性纪律**(承第 1 轮):vol/max/turn 高相关≈一个"散户过度定价"因子,别重复计;ep_ttm 是唯一正交分散源;新趋势/质量与现有相关性须先测再纳。

---

## 5. 评估协议(诚实门;对齐既有 `strategy_evolution` + `backend.backtest`)

| 环节 | 默认 | 出处 / 复用 |
|---|---|---|
| **开发评估** | **anchored walk-forward(扩窗)+ CPCV**(N_groups=10,k=2,45 split/9 path),每界 purge + **embargo≥20td**(≥最长 label horizon) | LdP 2018;`anti_overfit.purged_kfold_splits` |
| **预声明 N(`experiment_manifest.json`,codex P1-2)** | R2-4 前冻结 manifest,枚举**全部自由度**:两臂 / 因子族纳入·剔除 / 变换·标准化·winsor / neutralization / rebalance·horizon / tilt k / a_max / TE / 行业 cap / fundamental lag / 成本网格 / 选择指标 / tie-break / **失败·人工中止的变体**。DSR/PBO 的 N = **实际跑过 + 人工筛掉**的全部变体累计(非仅存活) | `weight_search` 模式;Bailey-LdP 2014 |
| **成本模型** | 扣 A 股往返成本(佣金+印花+分板块滑点+过户费)于换手分数;基准相对换手更高 → cost-aware;**绝不门控毛收益** | 现 `ROUND_TRIP_COST` + 全引擎 |
| **全引擎 = 判定主口径(codex P1-4)** | 冻结策略过 `backend.backtest` event-loop(T+1 / 涨跌停 at-fill / 分板块滑点)+ rqalpha oracle 差分;**R2-6 前向 PASS/FAIL 以全引擎净结果为准**,portfolio-sort 仅开发近似;**rqalpha 差分 >25bps = fail-closed**(不放行) | `backend/backtest/`;第 1 轮缺,本轮补 |
| **DSR 主门** | `deflated_sharpe_ratio ≥ 0.95`(喂累计 N + V[SR]) | `stats_disclosure.disclose` |
| **PBO(CSCV)** | ≤0.5 硬 / ≤0.2 目标,含**全搜索**矩阵 | 同上 |
| **SPA 披露** | vs 三基准:被动 CSI300 / 第 1 轮冻结策略 / 现役 momentum incumbent | `spa_disclosure` |
| **哨兵** | shuffle 因子值截面对照掺入每批,放行=门坏 | `sentinel.make_sentinels` |
| **机制门** | 每因子附经济机制(白名单);纯数据胜出拒 | `mechanism_registry` |
| **新增披露(非门)** | IR=年化超额/TE、TE、主动夏普、容量/换手 | 基准相对自然补充 |

**判定边界(写死,防混淆)**:
- 开发评估(§5 CPCV)产出**唯一带入前向的基准相对策略 + 开发证据**,**不**发 PASS/FAIL。
- **前向判定虽只带 1 个策略,但它是一整轮搜索的胜者** —— **不是单假设**:DSR/PBO 必须用 `experiment_manifest.json` 的累计 N deflate(codex P1-2),报告显式说明"前向那 1 个是 N 选 1 的产物"。
- **PASS/FAIL 唯一来源 = 前向一次性判定(§2.3 / Phase R2-6),以全引擎净结果为主口径**,四门全过才 PASS,**只判一次**。
- 参考臂(多空/中性)**永不**进 PASS 判定,只出 IR 上界披露;**且参考臂结果不得反向影响主臂的因子/参数选择**(若影响,须计入 N 并在 manifest 预声明交互规则)。

---

## 6. 分阶段任务拆解(每码模块:TDD + 门禁 pytest+ruff+mypy strict + per-module codex-review + feature commit;push owner-gated)

| Phase | 任务 | 产出 | 依赖 |
|---|---|---|---|
| **R2-0** | 本方案 + codex 对抗评审(本阶段) | 本 doc 定稿 + `docs/reviews/round2-plan-codex-*.md` | — |
| **R2-1** | PIT 数据扩充摄取(`index_weight` / `index_member_all` 申万行业 / `fina_indicator`),字节+checksum+幸存无偏+`--dry-run`先验+redline扫描 + **coverage manifest fail-closed 校验(按 `SurvivorshipUniverse.all_codes()`,codex P1-5)** + 记录供应商 fetch/release 时间戳 | 新 `scripts/factor_research/ingest_round2_data.py`(或扩 `historical_ingest`)+ 数据落 store + coverage manifest | owner-gated 重活 |
| **R2-2** | 新因子库(趋势/质量/成长)+ 中性化工具(行业+市值残差化);fundamentals PIT **ann_date + vintage 审计** join(codex P1-3) | 扩 `factor_lib.py` + 新 `neutralize.py` | R2-1 |
| **R2-3** | 基准相对构造模块(主动权重 builder + 约束后再投影 + TE 控制 + **买卖分拆保守成本** + active-exposure 披露)+ 多空/中性参考臂;扩 `portfolio_backtest` | 新 `benchmark_relative.py` + `long_short.py` | R2-2 |
| **R2-4** | **先冻结 `experiment_manifest.json`(全自由度,codex P1-2)** → CPCV/anchored-WF 评估 harness + 预声明搜索 + 全披露(DSR/PBO/SPA/哨兵/IR/TE)→ **选唯一基准相对策略** | 新 `walk_forward_eval.py` + `round2_search.py` + manifest + 开发证据 JSON | R2-3 |
| **R2-5** | 全引擎交叉确认(`backend.backtest` + rqalpha oracle,差分>25bps fail-closed)→ **git 冻结唯一策略 + 同冻前向协议(预声明窗口长度 120/250td + firewall 时间规则,codex P1-1)** | cross-check 报告 + 冻结 commit + `round2_forward_test_lock.json` firewall 段 | R2-4 |
| **R2-6**(判定,**owner-gated 时机**) | 恢复仅数据前向摄取 → 攒够预声明窗口 → 封 `round2_forward_test_lock.json`(dates_sha256 + firewall 时间审计)→ **一次性前向判定(全引擎主口径)** → 结果报告 + owner-gated 上线建议 | `factor-strategy-round2-result-*.md` | R2-5 + 前向数据 |

> R2-1..R2-5 现在可连做(开发);**R2-6 判定须等前向数据**(owner 拍窗口大小+时机)。

---

## 7. 红线(全继承,违反即停)
1. **测试集神圣**:开发期零触碰**已烧** test(2025-06→2026-06)+ 前向窗口冻结前零触碰;一切日期经 `LockedSplit.assert_not_test`(前向窗口加新 lock guard);泄漏=作废换窗口。
2. **离线 only**:不碰 simulation_auto 实时路径;不接线上 `FACTOR_WEIGHTS` 不经 owner gate;永禁真实下单;参考臂 short **仅纸面研究**。
3. **PIT/幸存无偏/无前视**:新数据存原始字节+checksum + 幸存无偏 + walk-forward purge+embargo;**`fina_indicator` 用 ann_date 非 end_date**;`index_weight`/行业用 PIT as-of;**严禁** `stock_basic.industry`(current-only)。
4. **数据源**:仅 Tushare 官方 SDK;`TUSHARE_TOKEN` 不入 LLM/飞书凭证池。
5. **LLM 不进数值策略**:权重/打分/构造/中性化全确定性;LLM 只用于文献。
6. **诚实**:严禁 data-snooping 凑过线;FAIL 报 FAIL;开发证据≠判定,显式区分;DSR/PBO/SPA/哨兵全报;**判据不放宽**(IR/TE 是新增披露非替换四门)。
7. **codex 前置门**:含代码任务 commit 前过 codex-review,修完 P0/P1/P2;docs 豁免;import 隔离(`scripts/factor_research` 可 import `backend.{marketdata_snapshot,backtest,strategy_evolution}`,`backend.data.*` 须 per-line `# noqa: TID251`,**严禁** `backend.{llm,agents,mirofish}`)。
8. **git**:每模块一 feature commit;**push 受 owner auth 门控**;`M CLAUDE.md` 是 owner 在途改动**别碰**。

---

## 8. 风险 / 诚实约束
1. **前向判定延后**:owner 停服,前向数据需恢复"仅摄取"累积 → 判定可能延后数周/数月。**窗口大小+时机 owner 在判定时定。** 现在能交付的只到"冻结策略+开发证据+harness"。
2. **`fina_indicator` PIT 复杂**:ann_date vs end_date 前视陷阱 → 须 ann_date join + lag + 对抗 leakage 审计(同第 1 轮双 agent leakage audit)。
3. **基准相对 alpha 可能弱**:CSI300 megacap 内因子 alpha 弱;off-benchmark tilt 抬 TE → IR 可能温和;**可能仍 FAIL,如实报。** 参考臂界定上界辅助判断。
4. **CPCV 非处子 holdout**:开发证据由先验知识影响,DSR/PBO/哨兵 deflate;**真判定靠前向**(§2.3)。
5. **质量/成长 A 股弱**(survey):纳入为诚实/完整,**不假设有效**;若无 alpha 如实剔。
6. **成本/容量**:基准相对换手高,成本可能吃掉超额;cost-aware + 全引擎确认兜底。
7. **多重检验膨胀**:两臂 + 多因子族 + tilt 搜索 → 试验数膨胀;预声明 N + DSR 累计 deflate + 仅**一个**策略进前向判定(单假设)控制。

---

## 9. 工作量估计
- R2-1(数据摄取)+ R2-3(构造)+ R2-4(评估)= 重;R2-2(因子)中;R2-5(全引擎)中;各约一 session 级。
- R2-6 判定 = 轻代码 + **等前向数据**(主要是时间成本)。
- 总:开发链(R2-1..R2-5)约 4-5 个工作单元;判定(R2-6)owner-gated 择时。

---

## 10. 给 codex 的对抗评审 brief(§E 阶段 2 重点攻击点)
1. **C-1 是否真诚实**:前向判定机理(commit 时间戳 < 数据存在)有无漏洞?CPCV 开发证据有没有被悄悄当判定?"earlier holdout"被否是否成立(有没有更干净的现成窗口被我漏掉)?
2. **多重检验是否到位**:预声明 N 是否覆盖**全部**自由度(两臂 + 因子族 + tilt + 权重)?DSR 累计 deflate 对不对?只一个策略进前向判定是否真单假设?
3. **基准相对构造有无前视**:`index_weight` as-of、`fina_indicator` ann_date、行业 in/out date —— 任一处用了未来已知信息?中性化回归有无用到当期 label?
4. **成本/容量是否现实**:基准相对换手成本是否低估?全引擎交叉确认是否足够?
5. **判据是否被悄悄放宽**:四门口径与第 1 轮一致?IR/TE 是否被当成"过门替身"?参考臂会不会被误当 PASS?
6. **幸存无偏**:新数据(index_weight/fina_indicator)退市/调出成分是否含全?

> codex 不可用时回退 `/code-review high`(3-angle)或多 agent 对抗。修完 P0/P1/P2 后本方案定稿,再进 R2-1 执行。

---

## 11. codex 对抗评审结果 + 处置(2026-06-18,§E 阶段 2 已完成)

codex (gpt-5.5, xhigh) 评审本方案,**判定无 P0**(大方向成立:现有数据无真处子 OOS、CPCV 仅开发证据非判定、四门未被 IR/TE 替换、参考臂排除在 PASS 外)。**5 P1 + 3 P2 全部已在本稿修订**(逐条):

| # | 级别 | codex 异议 | 处置(本稿章节) |
|---|---|---|---|
| 1 | P1 | 前向窗口"摄取时间"漏洞 + 窗口长度判定时才定 | §2.3(2)(4):firewall 改钉**数据存在/公开时间 > freeze commit UTC**(非摄取时间)+ 排除冻结前已公开日 + **冻结时预声明窗口长度/触发** |
| 2 | P1 | 多重检验 N 未覆盖全自由度;"单假设"过强 | §5 预声明 N 行 + 判定边界:冻结 `experiment_manifest.json`(全自由度含失败/人工中止变体)+ DSR/PBO 用累计 N + 软化"单假设"措辞 + 参考臂交互规则 |
| 3 | P1 | PIT 不够硬(`fina_indicator` ann_date ≠ 全 PIT 重述) | §4.1 表:`index_weight` 加可得性 lag(≤d−1)+ `fina_indicator` vintage/重述审计(只返修订值则降级/剔除)+ 行业 effective 规则 |
| 4 | P1 | 成本搜索期低估;判定引擎应为主口径 | §3.2(4) 买卖分拆保守成本(搜索期即用)+ §5 全引擎=判定主口径 + rqalpha 差分>25bps fail-closed |
| 5 | P1 | 幸存无偏只是原则非验收标准 | §4.1:R2-1 加 coverage manifest fail-closed(按 `SurvivorshipUniverse.all_codes()` 覆盖在市+退市;缺口→None) |
| 6 | P2 | 中性化+long-only clip 后主动暴露未保证净零 | §3.2(3):约束后再投影使主动腿净零 + 披露 residual beta/行业/市值 active/TE |
| 7 | P2 | "限非 SOE"无 PIT 所有制数据 | §4.2:删除"限非 SOE"条件(避免 current 标签前视) |
| 8 | P2 | 投资域与 CSI300 成分可能不一致 | §3.2(3):被排除成分名强制持 0=满额低配 active,计入 TE 单列披露;过大则截基准到可投资交集 |

**codex 总评**:"方案方向可以,无 P0;P1 会影响诚实性/可审计性,先修方案再执行。" → **已全修,本稿即定稿。**
评审原文存档:`docs/reviews/round2-plan-codex-review-summary.md`。**方案定稿,owner 批准(2026-06-18 "开")后进 R2-1 执行。**

---

## 12. 执行进度

### ⚠️ 方法论变更(2026-06-19,owner;放宽 §2/§11 C-1)
**测试集可跨实验复用** → **R2-6 改在既有锁定测试集(2025-06-04→2026-06-12)做一次性判定**,
不再等前向封存窗口。理由:每次搜寻是独立实验,不偷看测试集 + 测试期严格 PIT ≠ look-ahead。
诚实保障四条全留(冻结-再读 / 累计-N deflation / 测试集第 2 次评测披露 / 四门不放宽)。
**记录全文 = `docs/research/factor-strategy-round2-test-reuse-decision-2026-06-19.md`(先读)。**

### R2-1 ✅ DONE(2026-06-18;feature `434d2cb` + 真跑修复 `48809d2`;本地未 push)
PIT 数据扩充摄取落地 + **真实重活摄取已跑**:
- 新 `scripts/factor_research/ingest_round2_data.py`(离线编排器,字节存档+sha256+幂等续传+fail-closed+限速+幸存无偏 coverage)+ `TushareClient` 加 `index_weight`/`index_member_all`(只读)。codex 3 轮(cycle-1 5 + verify 3 + 真跑后 2 修)全清;门禁 ruff+mypy strict+184~283 测试绿;review summary `docs/reviews/round2-r2-1-ingest-codex-review-summary.md`。
- **真实摄取结果(data/marketdata_pit,字节级)**:`index_weight` 125 月(**2016-01-29→2026-05-29**;Tushare CSI300 权重无 2015 数据=供应商边界 → benchmark-relative 权重窗口自 2016 起)/ `fina_indicator_vip` 45 报告期(2015Q1→2026Q1)/ `index_member_all` 1(申万成分 in/out date)/ `stock_basic` L+D rosters / **45 per-period fina coverage manifests**(最差 completeness 99.83%,9 码当期未报=fail-closed→None)。exit 0 零失败。
- **真跑暴露并修的 2 坑**:① 退市股仍在 listed roster 使 delist_date 列浮点化(`20260610`→`20260610.0`)→ `_read_roster_frame` dtype=str 读;② index_weight 2015 无数据 → `CSI300_WEIGHT_FIRST_MONTH=201601` floor 跳过。
- **下一步 = R2-2**(新因子库 趋势/质量/成长 + 行业+市值中性化;fina PIT **ann_date + vintage 审计** join;消费 fina 日期列须消费端显式 dtype)。

### R2-2 ✅ DONE(2026-06-18;feature `847ea7a`/`175aee7`/`8f38cd7`/`54dd15b`/`281cfd0`/`32efcde`;本地未 push)
新因子族(趋势/质量/成长)+ 行业/市值中性化 + 基本面 PIT 摄入落地 + **真实 train_val 面板构建 + IC 诊断已跑**:
- 新模块(全 PIT、确定性、纯量化、import 隔离):`fundamentals_pit.py`(vintage-aware fina reader,**真 PIT**:多 vintage 按 ann_date<d 选 as-known 值 + vintage 审计)/ `industry_pit.py`(申万 L1 in/out date PIT,**严禁 stock_basic.industry**)/ `neutralize.py`(因子 ~ 行业哑变量+log市值 截面 OLS 残差,fail-closed)/ `r2_factor_diagnostics.py`(诊断 runner);扩 `factor_lib.py`(R2 独立 registry:mom_12_1/dist_high/trend_slope/roe/gpm/np_yoy/rev_yoy;**round-1 七因子 byte-unchanged**)+ `build_factor_panel.py`(`build_panel_r2` train_val-only,`--factor-set r2`)+ `factor_ic_study.py`(向后兼容扩 factor_names/by_name)。
- **设计判定**:① **不动** governance `EconomicMechanism` enum(模块自述加 mechanism 须 amendment);growth 用诚实研究标签 `"growth_premium"`(不在 enum → 未来晋升机制门 fail-closed,until amendment);② 真实面板 = **326854 行 / 3003 码 / 498 调仓日 2015-02→2025-04**(= round-1);③ 测试集零碰(fundamentals ann_date<d≤train_val_end<test_start 天然 PIT 安全)。
- **codex(`codex review --uncommitted`,gpt 0.137):无 P0/P1**;P2(`r2+mode=test` 会静默产训练数据)+ P3(neutralize 把 pandas `pd.NA` 当真行业)**全修 + 加测试**。门禁:**154 测试绿** + ruff + mypy --strict + redline 全清。
- **诚实诊断结论(train_val 样本内,`docs/research/factor-strategy-round2-r2-2-factor-diagnostics-2026-06-18.md`)**:
  - **质量 = 真·新正交 alpha 留**:roe(t+6.94)/gpm(t+3.75)过 |t|≥3 且中性化后存活(roe_neut t+5.92,gpm_neut t+4.40),与 round-1 反转/波动簇低共线(roe↔ep_ttm 0.46 唯一显著重叠)。
  - **成长 = 中性化后增强留(中性化形)**:np_yoy/rev_yoy 原始弱(t 2.35/2.88),**行业+市值中性化后翻倍**(np_yoy_neut t+4.59,rev_yoy_neut t+5.58)= 去 sector/size artefact 后的净成长溢价;两者互相关 0.52 算一轴。
  - **动量/52周高 = 无信号丢**:mom_12_1(t+1.10)/dist_high(t+0.77)原始+中性化全平 → **直接实证 Phase-1 survey「A 股动量弱/缺失」**;`trend_slope` t−7.51 **反号(reversal 伪装)**,与 round-1 反转簇冗余 → 丢。
  - **「跟不上牛市」不靠因子修**:横截面动量缺失、趋势=反转 → round-1 FAIL 根因须靠 **R2-3 基准相对构造**(对 CSI300 权重做 beta≈1、行业/市值中性 tilt)解决,**非新因子**。
  - **amihud 中性化翻号**:raw amihud≈size proxy;size 中性后残差=经典非流动性溢价(翻正)→ 别与显式 size 控制双重计 raw 方向。
  - **PIT 完整性强、行业覆盖是真缺口**:vintage 污染仅 **0.10%**(median ann lag 54d)→ ann_date gating 稳;**行业覆盖仅 66.3%**(`index_member_all` 是当前 SW 表,长退市/重分类码无 PIT L1 → 中性化 None,raw 保留)→ R2-3 须补历史完整 SW 表或 bound/披露 no-industry sleeve。
  - **R2-3 carry-forward 集** = round-1 七 ＋ 质量 roe/gpm ＋ 成长 np_yoy/rev_yoy(中性化形);**丢** mom_12_1/dist_high/trend_slope。`*_neut` 列 = 构造就绪输入。
- **下一步 = R2-3**(基准相对构造:主动权重 builder + 约束后再投影 + TE 控制 + 买卖分拆保守成本 + active-exposure 披露;消费 R2-2 PIT 因子面板 raw+neutralized + `index_weight` 基准权重)+ 多空/中性参考臂。

### R2-3 ✅ DONE(2026-06-19;feature `4011316`/`fb60562`/`1fe5716`/`092a30b`;本地未 push)
基准相对长多构造(主,可落地)+ 多空/中性参考臂(辅,研究上界)+ 真实 train_val 诊断已跑:
- 新模块(全 PIT、确定性、纯量化、import 隔离、LLM 零参与):`benchmark_weights.py`(CSI300 成分权重 PIT reader,多 publish 日按 publish<d 取最新归一)/ `benchmark_relative.py`(核心:composite_score 用 carry `*_neut` 定向合成 + build_active_weights〔box clip k·z、long-only floor、unscored 严格持基准权重、scored sleeve 缩放至 Σw=1 → 净零 active beta≈1〕+ benchmark_relative_backtest〔逐期超额扣买卖分拆成本 + drift turnover〕+ 暴露披露)/ `long_short.py`(市场中性参考臂,**research-only/永不上线/不进判定**)/ `r2_benchmark_relative_diagnostics.py`(诊断 runner + 防火墙)。
- **设计判定**:① 净零 active = 最终 Σw=1 重归一(非 demean,守 a_max box);② amihud_neut 朝向覆写(size 正交残差=非流动性溢价,翻正);③ forced-underweight reconcile(被排除成分名 active=−w_bench,单列披露);④ 成本 = 买卖分拆 + **持仓漂移** turnover;⑤ **R2-3 不出 PASS/FAIL**(开发证据,判定在 R2-6 前向)。
- **codex 8 轮(`codex review --uncommitted`,gpt-5.5 xhigh)→ 第 9 轮撞额度 → 回退 `/code-review high`(3 agent)**:全程 **0 P0**;累计修 2 P1(均防火墙:基准侧输入须 < test_start + 流式 loader 不 materialize test 行)+ ~13 P2(amihud 朝向 / 列缺失 fail-closed / 净零 box / forced-UW reconcile / resize+drift turnover / 参考臂同期 / 数据派生叙述 / gap≤0 边界 / 超长函数重构 …)+ 数个 P3。`/code-review high` 独立确认**防火墙 SOUND 零泄漏**(csi300_daily.csv 实含 250 test 行,流式 loader 全挡)。门禁:**179 测试绿** + ruff + mypy --strict + redline 全清。
- **诚实诊断结论(train_val 样本内 2016-2025〔基准权重无 2015〕,`docs/research/factor-strategy-round2-r2-3-benchmark-relative-diagnostics-2026-06-18.md`)**:
  - **构造完整性**:net active ~1e-18(beta≈1)✓;但 **size active −0.60~−0.71 std + gross active 43-47%** → 在全投资域(~1700 名)tilt off CSI300〔300 名〕权重 → 高分非成分小盘获正 active → **系统性小盘漂移**(虽因子已 size 中性)→ 主臂 +15~17% 超额/IR 0.27-0.34 **实质混入小盘 bet**,非纯因子 tilt(披露揪出的隐性押注)。
  - **参考臂(同期可比 449 期)= +31.67% alpha / Sharpe 0.30**(早先 488 期含 2015 泡沫的 +101% 是样本错配)→ 与主臂 IR ~0.3 相当 → **有界 tilt 没漏多少 alpha,即因子在 2016+ 本就温和**(诚实)。
  - **forced UW 16.2%**(科创/北交/ST/流动性/底30%排除的 CSI300 成分;创业板在白名单不排)+ R2-2 行业覆盖 66% → tracking 受 universe 而非 tilt 限。
  - **R2-4 须约束 off-benchmark 暴露**(限成分内 tilt / 组合级 size 中性 / cap 非成分 active + TE 带);size 中性因子不防 universe-mismatch size 漂移。
- **下一步 = R2-4**(冻结 `experiment_manifest.json` 全自由度 → CPCV/anchored-WF 评估 + 预声明 N 搜索 k/a_max/权重/暴露约束 + DSR/PBO/SPA/哨兵全披露 → 选唯一基准相对策略;消费 R2-3 `benchmark_relative_backtest`)。R2-5 全引擎交叉确认 + git 冻结 + 前向协议;R2-6 一次性前向判定(等前向数据)。
