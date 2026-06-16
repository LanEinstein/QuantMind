# 量化选股策略研究专项 — 新 session 接续 handoff(2026-06-16)

> **这是给一个全新 session 的自包含接续任务书。** 你(接手的 Claude)没有上文对话。先读本文件全文,再读 §0 的必读清单,然后从 §6「下一步」开工。本文件含精确命令 + 预期输出 + 代码骨架,照着做即可无缝衔接。
> **权威母任务书** = `docs/research/factor-strategy-research-brief-2026-06-16.md`(总背景/成功判据/红线)。本文件是其执行进度 + 接续指南。

---

## 0. 开工前必读(按序)

1. 本文件全文。
2. `docs/research/factor-strategy-research-brief-2026-06-16.md`(母任务书:目标/硬性成功判据/红线)。
3. `docs/research/factor-theory-survey-2026-06-16.md`(Phase 1 文献综述 = 因子设计权威依据)。
4. `~/.claude/projects/-home-ps-papers-QuantMind/memory/project-factor-strategy-research-2026-06-16.md`(跨 session 记忆)。
5. 已建代码:`scripts/factor_research/*.py`(6 模块,见 §4)。

**环境**:
```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
# 跑任何 python 都加 FEISHU_INTERACTIVE_ENABLED=false 前缀(否则飞书 WS 卡)
# 跑研究模块用 -m(相对 import):FEISHU_INTERACTIVE_ENABLED=false $PY -m scripts.factor_research.<mod>
# 跑测试用绝对 import:$PY -m pytest tests/factor_research/ -q
```

---

## 1. Owner 已锁定决策(不可改)

1. **锁定测试集 = 最近 250 交易日 = 2025-06-04 → 2026-06-12**(神圣,开发全程零触碰)。
2. **PASS 门槛(扣真实成本,测试集一次性评估)**:净收益>0 **且** 跑赢沪深300(累计超额≥0)**且** 最大回撤≤15% **且** 年化夏普≥0.5。**四条全过才 PASS**。
3. **最终在测试集上评估恰好 1 个策略**(validation 择优出唯一终选,测试集只跑一次)。
4. **强制指令**:系统任务=保证**真盈利**(非纸面);反过拟合/样本外纪律是手段不是借口;**严禁 data-snooping 凑过线**;达不到如实报 FAIL + 下一轮方向。

---

## ⚑ Phase 3 全 done(2026-06-16 更新)— 终选策略已冻结(git-anchored)

**Phase 3 = 7/7 模块 done。最后一个 `weight_search.py` 已落 commit `189af2e`**(feature;含 `portfolio_backtest` 加 `net_returns`/`orient`/`group_by_date`;review summary `docs/reviews/factor-weight-search-code-review-summary.md`)。本地未 push。

**终选策略(唯一,冻结供 Phase 4 一次性测试;本节即 git 时间戳的预承诺证据 —— 在读 test 之前已锁定,杜绝 data-snooping)**:
- **权重**(因子序 = `ret_5d, ret_20d, vol_20d, max_20d, ep_ttm, turn_20d, amihud_20d`):`ep_ttm=0.211 / amihud_20d=0.183 / turn_20d=0.173 / vol_20d=0.163 / max_20d=0.163 / ret_5d=0.089 / ret_20d=0.018`。= **分散型『避开投机』组合**(价值 ep_ttm 最高 + 流动性 + 低换手/低波),动量 ret_20d 近乎归零(0.018)—— 搜索自动剔除了现役误配的动量。
- **inner-val 样本内**(2023-01→2025-04,cutoff 20221230 + purge 4;108 调仓期):Sharpe **+1.23** / 累计 +63% / **超额 +70.77% vs 沪深300** / MDD **11.55%** / 换手 0.44。top_n=10 稳健对照 Sharpe +1.07。
- **动量 incumbent**(top-5 按高 ret_20d = 现役押注):Sharpe **−1.35** / **−93.86%** —— 实证现役 `momentum_20d 0.40` 方向灾难性误配。
- **诚实披露**(全 512 池):PBO **0.127**(<0.2 = 选择未强过拟)/ SPA p=**0.002**(强胜 incumbent)/ DSR **0.066**(**不过** 0.95 floor —— 512 试验去偏严苛 + 候选高相关致保守)/ MinBTL admits False(日历校准,周频保守底,非策略级拒)。
- **关键诚实判断**:val 漂亮(Sharpe 1.23)但 DSR 不过 → **不美化、不偷看 test 凑线**。最终判决 = Phase 4 锁定测试集一次性评估(§6.2),非这些门。产物 `data/factor_research/weight_search_result.json`(gitignored;权重已记于本节供复现)。

## ⚑⚑ Phase 4+5 全 done(2026-06-16)— **最终判定 = FAIL**(诚实样本外失败)

**Phase 4 锁定测试集一次性评估已完成(test 已『烧掉』,不可再用)。结论 = FAIL。** 全部报告见
**`docs/research/factor-strategy-result-2026-06-16.md`**。harness commit `cd9d5f4`(build_test_panel
+ phase4 runner + 基准 horizon 修正,经双 agent 泄漏审计 CLEAN + verdict firewall 验证)。

**TEST(2025-06-04→2026-06-12,49 调仓)**:净 **+5.48%** / 年化 +5.64% / 夏普 **0.54** / 回撤 **7.78%** /
CSI300 **+21.84%** / **超额 −16.36%** / 胜率 46.94% / 换手 0.57。分年:2025-H2 +10.67% / 2026-H1 −4.69%。
**四门 3 过 1 不过:净>0 ✅ / 回撤≤15% ✅ / 夏普≥0.5 ✅ / 跑赢沪深300 ❌(−16.36%)→ FAIL。**

**为什么 FAIL(诚实)**:① **DSR=0.066 事先警告兑现** —— 样本内 +69.68% 超额未能延续到样本外
(−16.36%),反过拟合门正确预测了 val 的脆弱,我们没上线一个会跑输指数的策略;② **regime 错配**
—— 测试期是大盘强势年(CSI300 +21.84%),防御型『避投机』组合结构上跑输 cap-weighted 指数(虽净赚
+5.48%、回撤仅 7.78%);③ **long-only top-5 对指数基准无关** —— 5 只等权多头无法跟踪 300 只市值加权
指数。**下一轮方向**:基准相对/多空构造、index-neutral 因子中性化、regime 适配动量 sleeve,或与 owner
重定义为风险调整绝对收益判据(那三门它过了)。**测试集已烧 + 本就是最新数据 → 干净新 OOS 窗口需等
新数据或重做开发留更早窗口。** 不上线;现役 momentum 0.40 亦未获验证(见报告 §5/§8)。

---

(下面是 Phase 4 实施前写的指南,留作历史参考。)

**~~下一步 = §6.2 Phase 4 一次性测试~~**(已完成,见上)。读 test = 一次性、不可逆、烧测试集的契约
时刻;策略已 git 冻结(commit `189af2e`),已一次性评估完毕。

---

## 2. 已完成(Phase 1+2 全done,**Phase 3 7/7 模块全done**)

**所有 commit 已落本地,未 push(push 受 owner auth 门控)**:

| commit | 内容 |
|---|---|
| `d5f5600` | fix: universe 接受 Tushare T 前缀退市码(`T600018.SH`) |
| `d4a62a1` | docs: Phase 1 文献综述 |
| `bd78274` | feat: `factor_lib`(A股因子库+非有限值fail-closed) |
| `1f6dd50` | chore: **锁定测试集** `config/research/test_set_lock.json` |
| `3e2b3bf` | feat: `locked_split` + `build_factor_panel` |
| `edb0972` | feat: `factor_ic_study` |
| `ffd52eb` | feat: `portfolio_backtest` + 面板改 CSV 输出 |
| `bb3b4e2` | feat: `stats_disclosure`(DSR/PBO/SPA/MinBTL) |
| `189af2e` | feat: **`weight_search`(Sobol simplex + 诚实择优,Phase 3 收尾)** + `portfolio_backtest` 加 net_returns/orient/group_by_date + review summary |

`git log --oneline -8` 应见以上(`M CLAUDE.md` 是 owner 在途改动,**别碰别提交**)。

### 数据产物(全部 gitignored,在本机)
| 文件 | 内容 |
|---|---|
| `data/marketdata_pit/`(2.9G) | PIT 快照 2779 td(2015-01-05→2026-06-12),daily/adj_factor/daily_basic/fund_daily,原始字节+sha256 |
| Mongo `quantmind.kline_daily` | 11.37M 行(便利层,研究用快照层) |
| `data/factor_research/csi300_daily.csv` | 沪深300基准(trade_date,close;2779行;sha256 d98aeac5) |
| `data/factor_research/panel_train_val.csv` | **train_val 因子面板:326854 行 / 3003 码 / 498 调仓日(2015-02-02→2025-04-25,never test)**。列:`date,code,ret_5d,ret_20d,vol_20d,max_20d,ep_ttm,turn_20d,amihud_20d,fwd_ret_5d,fwd_ret_10d,fwd_ret_20d` |
| `data/factor_research/ic_study.md` | IC 研究输出 |
| `config/research/test_set_lock.json` | **测试集锁(已 commit)**:train_val 2015-01-05→2025-04-30(2509td)/ embargo 2025-05-06→2025-06-03(20td)/ **TEST 2025-06-04→2026-06-12(250td 神圣)** + 各窗口 dates_sha256 |

---

## 3. 已得真实发现(train+val 样本内,**测试集仍封存**)

### 3.1 项目假设证实:现役 `momentum 0.40` 方向错配
`ret_20d`(=现役 `momentum_20d` 原始值)IC **负且统计压倒性**:IC(20d)=−0.070,**t=−9.35** → 高近期收益→低未来收益=**反转非动量**。现役给"高 ret_20d 赢家" +0.40 正权重 = 押反方向。**21 个 IC 符号全部符合文献先验**。完整表见 §3.4。

### 3.2 净成本回测(top-5,扣A股成本~0.16%/换手,vs 沪深300,样本内)
| 策略 | 累计 | 年化 | Sharpe | 回撤 | 超额HS300 | 换手 |
|---|---|---|---|---|---|---|
| 等权7因子 | +73.8% | +5.75% | 0.37 | 35% | +60.9% | 0.67 |
| **纯 turn_20d(低换手)** | **+178%** | +10.9% | **0.64** | 27% | +165% | 0.07 |
| 纯 vol_20d(低波) | +95% | +7.0% | 0.47 | 28% | +83% | 0.34 |
| 纯 ep_ttm(价值)极值 | −39% | — | −0.06 | 75% | −52% | 0.17 |
| 纯 ret_20d(反转)极值 | −68% | — | −0.01 | 93% | −81% | 0.68 |

### 3.3 关键洞察(影响 weight_search 设计)
- **稳健赢家 = "避开投机/高波名"**(低换手 turn_20d Sharpe0.64、低波 vol_20d 0.47),**不是**押极端反转/极值价值。
- **极端 top-5 尾部坑**:纯反转(买极端输家=下跌的刀)、纯价值(买极端便宜=价值陷阱)**亏损**,尽管广义 IC 符号对 —— **广义截面 IC ≠ 极端 top-5 表现**。复合(分散)等权就稳得多(+73.8%)。
- **共线性**:vol/max/turn 高度相关(0.54–0.83)≈ 同一"散户过度定价"因子,别重复计;**ep_ttm 与它们负相关(−0.2~−0.4)= 正交分散来源**,应保留。
- ⚠️ 以上**全是样本内**;最终判决只在 Phase 4 锁定测试集。

### 3.4 IC 全表(20日horizon,t值)
reversal: ret_5d −0.034(t−4.8)/ ret_20d −0.070(t−9.35)· low-vol: vol_20d −0.097(t−9.9)· anti-MAX: max_20d −0.091(t−12.0)· value: **ep_ttm +0.065(t+6.3)** · turnover: turn_20d −0.120(t−11.5)· amihud −0.022(t−3.5)。全部 aligned=yes。

---

## 4. 已建模块(API + 跑法 + 预期输出)

全在 `scripts/factor_research/`(package,相对 import;`python -m scripts.factor_research.X` 跑,pytest 用绝对 import)。**import 隔离**:可 `backend.{marketdata_snapshot,backtest,strategy_evolution}`;`backend.data.*` 需 per-line `# noqa: TID251`;**严禁** `backend.{llm,agents,mirofish}`。61 测试全绿。

1. **`factor_lib.py`** — 纯因子函数 + `FACTORS` 注册表(`FactorDef`:name/min_history/attractive_high/mechanism/expected_ic_sign)。`FACTOR_NAMES`=7 因子。`compute_factor_vector(closes,amounts,turnover_rates,pe_ttm)`。非有限值全 fail-closed→None。
2. **`locked_split.py`** — `LockedSplit.load()` 读锁+校验 dates_sha256(防漂移);`assert_not_test(date)`/`assert_all_not_test(dates)` 硬守门。**所有读日期的研究代码必须经此守门。**
3. **`build_factor_panel.py`** — `build_panel(split, store, *, rebalance_freq=5, max_rebalances=0)`。PIT/幸存无偏(per-date daily frame=可交易集)/`adj_close=raw_close×adj_factor`(hfq,已验证 future-invariant,比值消 ref→无前视)/排除(板块白名单+≥21bar历史+流动性2亿+raw单价≤500+截面剔底30%市值)。**只读 train_val+embargo,never test**。跑:`-m scripts.factor_research.build_factor_panel --rebalance-freq 5` → 写 `data/factor_research/panel_train_val.csv`。
4. **`factor_ic_study.py`** — `study(panel)`/`rank_ic_series`/`summarize_ic`/`factor_correlation`。跑:`-m scripts.factor_research.factor_ic_study` → §3.4 表 + 写 `ic_study.md`。
5. **`portfolio_backtest.py`** — `backtest(panel, weights, *, benchmark=None, horizon=5, top_n=5, cost=0.0016) -> BacktestResult`。`oriented_rank`(按注册表方向,attractive-low 翻转)。`load_benchmark(path)`。`equal_weights()`。`ROUND_TRIP_COST=0.0016`。**注意**:`BacktestResult` 目前**没有** per-period `net_returns` 字段 —— weight_search 做 PBO 矩阵需要,**先加这个字段**(见 §6.1)。
6. **`stats_disclosure.py`** — `disclose(*, selected_net_rets, candidate_return_matrix, incumbent_excess_matrix, n_trials, n_observations) -> DisclosureReport`(DSR≥0.95主门 / MinBTL / PBO / SPA);`deflated_sharpe(net_rets, *, n_trials)`。复用 `backend.strategy_evolution`。

---

## 5. 红线 / 护栏(违反即停)

1. **测试集神圣**:开发期(Phase 3)**零触碰** test 日期(2025-06-04→2026-06-12)。一切读日期经 `LockedSplit.assert_not_test`。Phase 4 才一次性读 test,且只跑 1 个策略 1 次。泄漏=作废换新窗口。
2. **离线 only**:不碰 `simulation_auto` 实时路径;不接线上 `FACTOR_WEIGHTS` 不经 owner gate;永禁真实下单。
3. **PIT/幸存无偏/无前视**:已建管线满足(adj_factor hfq future-invariant 已验证;退市股在快照内;排除全 PIT)。
4. **数据源**:仅 Tushare 官方 SDK;`TUSHARE_TOKEN` 不入 LLM/飞书凭证池。
5. **LLM 不进数值策略**:权重/打分全确定性;LLM 只用于文献综述。
6. **codex-review 前置门**:含代码的任务 commit 前过 codex-review;**codex 撞额度到 2026-06-18 → 回退 `/code-review high` 或单 agent 对抗审查**;修完 P0/P1/P2 再 commit;docs-only 豁免。
7. **诚实**:FAIL 就报 FAIL;样本内/样本外严格区分;绝不美化回测。
8. **git**:每模块一 feature commit;**push 受 owner auth 门控**(commit 落本地,push 待 owner 授权)。`M CLAUDE.md` 是 owner 在途改动**别碰**。

---

## 6. 下一步(从这里开工)

### 6.1 Phase 3 收尾:`weight_search.py`(最后一个模块)— ✅ **DONE(commit `189af2e`)**

> 已完成。实现细节与终选结果见本文件顶部「⚑ Phase 3 全 done」节。codex-review 改动:① 诚实披露改用**全 512 候选池**(非 16 finalists,守 `disclose()` 契约);② `group_by_date` hoist(分组复用,512 回测可承受);③ 复用 `_kraemer_simplex`;④ `search()` 拆 helper <50 行;⑤ 因子序 fail-closed 钉死。下面是原始设计规格(供参考/复现)。

**目标**:Sobol 预声明N 搜 7 因子非负权重(simplex,和=1);**内部 train/val 切分**(train 搜、val 择优,**绝不碰 test**);选出**唯一终选策略**;DSR/PBO/SPA 披露。

**先决:给 `portfolio_backtest.BacktestResult` 加 `net_returns: tuple[float,...]` 字段**(PBO/SPA 矩阵需 per-period 净收益)。改 `_summarize` 把 `net_rets` 存入;空态构造改关键字参数(加字段会破位置参数构造 `return BacktestResult(0,0,...)`)。同步改 `test_portfolio_backtest.py::test_empty_panel_is_safe`。

**weight_search 设计**:
- 内部切分:`TRAIN_VAL_CUTOFF='20221230'`(train=panel date≤cutoff ≈2015-2022 8yr;val=date>cutoff≈2023-01→2025-04-25 2.3yr;两者皆在 train_val,never test)。可选 purge gap(去掉 cutoff 后 ~20td 防标签泄漏)。
- 搜索:`scipy.stats.qmc.Sobol(d=6, scramble=True, seed=20260616)`,**预声明 N**(建议 256 或 512,记下供 DSR `n_trials`)。simplex 映射用 sorted-spacings(Kraemer):Sobol点 u∈[0,1]^6 → 排序取差分 → 7 维和=1 非负权重。
- 评估:每候选 `backtest(train_panel, w, horizon=5, top_n=5)` → 选择指标=val Sharpe(取 train 前 top-K=16 finalists,再在 val 评估,选 val Sharpe 最高的**唯一**策略)。
- **诚实门**:`stats_disclosure.disclose(selected_net_rets=val净收益, candidate_return_matrix=finalists 在val的净收益矩阵, incumbent_excess_matrix=候选−incumbent, n_trials=N, n_observations=val期数)`。incumbent=动量基线(top-5 按**高** ret_20d,即现役系统的押注)—— 需显式构造(翻转 ret_20d 方向或直接按 ret_20d 降序选)。
- **机制门**:终选权重每个非零因子都有注册机制(factor_lib.FACTORS),天然满足;无机制纯数据胜出=拒(本设计不会出现)。
- 输出:终选权重 dict + train/val BacktestResult + DisclosureReport;写 `data/factor_research/weight_search_result.json`。

**代码骨架**(放 `scripts/factor_research/weight_search.py`):
```python
from scipy.stats import qmc
from .factor_lib import FACTOR_NAMES
from .portfolio_backtest import backtest, load_benchmark
from .stats_disclosure import disclose

def simplex_sobol(n, dim, seed):
    pts = qmc.Sobol(d=dim-1, scramble=True, seed=seed).random(n)
    out = []
    for u in pts:
        cuts = sorted(float(x) for x in u); prev=0.0; w=[]
        for c in (*cuts, 1.0): w.append(c-prev); prev=c
        out.append(w)            # dim 个非负权重,和=1
    return out
# split panel by date; for each w backtest(train); top-K by train sharpe;
# backtest(val) for finalists; pick best val sharpe; disclose(); dump json.
```
**注意**:依 §3.3,极端单因子坑+稳健是低换手/低波;复合搜索应自然偏向分散。考虑也跑 top_n∈{5,10} 对照(生产 ≤5 槽,但 top_n 大更稳),如实记录。

跑通后:`-m scripts.factor_research.weight_search` → 打印终选权重 + train/val 指标 + DSR/PBO/SPA。**过 codex/code-review,commit**(`feat(factor-research): Sobol weight search + honest selection (Phase 3)`)。

### 6.2 Phase 4:锁定测试集一次性评估(**只跑一次**)

**需新建测试面板构建**(`build_factor_panel` 目前只建 train_val,断言 never test)。Phase 4 加一个 test-panel 路径:
- 建议给 `build_factor_panel` 加 `--mode test`:`feature_dates` = test_start 前 ~25td(取 trailing 特征,这些是 embargo+train 的非-test 日)**+ test 日期**;**rebalance_dates = 仅 test 日期**;特征用 ≤d 的 bar(含 test_start 前历史,合法),标签用 >d 的 test bar。**这是唯一允许读 test 的地方**(Phase 4 一次性)。写 `data/factor_research/panel_test.csv`。
- 用 §6.1 选出的**唯一**权重 `backtest(test_panel, selected_w, benchmark=csi300, horizon=5, top_n=5)`。
- 报告(扣成本):累计净收益 / 年化 / 夏普 / 最大回撤 / 沪深300累计超额 / 胜率 / 换手 / 分年。
- **判 PASS**(net>0 ∧ excess≥0 ∧ MDD≤15% ∧ Sharpe≥0.5)**/ FAIL**。
- **FAIL** → 如实写 FAIL + 失效分析 + 下一轮方向,**换一段全新未用数据**作下轮 test;**严禁**改口径凑过线。
- **强烈建议**:终选策略再过一遍 `backend/backtest/` 全引擎(run_backtest:真 T+1/涨跌停at-fill/分板块滑点)交叉确认净收益(portfolio_backtest 是 sort 近似,未建模 T+1/涨跌停)。

### 6.3 Phase 5:交付报告 + owner-gated 上线建议
- `docs/research/factor-strategy-result-2026-06-XX.md`:方法/数据切分/validation+test 全指标/PASS-FAIL/局限/下一步。
- 策略产物:终选因子集+权重(content-addressed),可被 `LiveArtifactRegistry` 按批准哈希 pin。
- 上线建议:是否/如何接 `FACTOR_WEIGHTS`/`candidate_weights/vN.yaml`。**接线=owner-gated**(amendment+LiveArtifactRegistry批准+45日前向shadow+人工pin+重启)。**本专项不直接改线上路径。**

---

## 7. 踩坑教训(别重蹈)

1. **`pkill -f "<pattern>"` 会自匹配命令本身的 shell** → 自杀 shell(exit 144)却没杀目标。用 `kill -9 <PID>` 按显式 PID 杀。
2. **别同时跑多个 build**:它们抢 CPU+互相干扰。一次一个。
3. **长任务用 `run_in_background:true`**(像 3 小时摄取那样能跑完);或 `setsid ... &` 完全脱离 session。前台 Bash 工具有 ~600s 上限会被截。
4. **面板写 CSV 不写 parquet**:env 无 pyarrow/fastparquet(`to_parquet` 会崩;`to_csv` 无依赖)。
5. **build_factor_panel 整面板在内存**(per-code series,~几百MB)。326k 行面板构建 ~5-13min。若要更稳:分块落盘/可续(本次未做,owner 问过可改)。
6. **广义 IC ≠ 极端 top-5 表现**(§3.3):单因子极端尾部会坑;信复合+分散+val择优。
7. **codex 撞额度到 2026-06-18** → 用 `/code-review high` 或单 general-purpose agent 对抗审查替代(见 [[feedback_codex_rate_limit_fallback]])。
8. **IPv4-only 出站**:httpx 须 `local_address="0.0.0.0"`(dashscope 等 AAAA 源否则 stall)—— 摄取已配,研究阶段一般用不到。

---

## 8. 命令速查

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
# 门禁(commit 前必绿)
$PY -m pytest tests/factor_research/ -q
$PY -m ruff check scripts/factor_research/ tests/factor_research/
$PY -m mypy scripts/factor_research/*.py
# 重建面板(如需;~5-13min,后台)
FEISHU_INTERACTIVE_ENABLED=false $PY -m scripts.factor_research.build_factor_panel --rebalance-freq 5
# IC 研究 / 等权基线回测
FEISHU_INTERACTIVE_ENABLED=false $PY -m scripts.factor_research.factor_ic_study
FEISHU_INTERACTIVE_ENABLED=false $PY -m scripts.factor_research.portfolio_backtest
# 测试集锁校验(应无异常)
FEISHU_INTERACTIVE_ENABLED=false $PY -c "from scripts.factor_research.locked_split import LockedSplit; s=LockedSplit.load(); print('test', s.test_dates[0], s.test_dates[-1], len(s.test_dates))"
# -> test 20250604 20260612 250
```

**下一步起手 = §6.1 写 `weight_search.py`(先给 BacktestResult 加 net_returns 字段)→ 跑出唯一终选权重 → §6.2 Phase 4 一次性测试。**
