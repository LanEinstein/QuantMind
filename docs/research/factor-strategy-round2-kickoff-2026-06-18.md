# 量化选股策略研究 — 第 2 轮 kickoff(2026-06-18)

> **这是给一个全新 session 的自包含任务书。** 你(接手的 Claude)没有上文对话。
> 第 1 轮(long-only 因子打分)在锁定测试集上 **FAIL**(没跑赢沪深300)。第 2 轮目标 =
> 用 **多空 / 基准相对(benchmark-relative)** 构造,争取在**全部 4 条判据**上 PASS。
> **工作协议(owner 强制,见 §E):先形成方案 → 与 codex 讨论 → 没问题后再执行。**
> 母任务书 `docs/research/factor-strategy-research-brief-2026-06-16.md`(目标/红线/成功判据,仍有效)。
> 第 1 轮完整结果 `docs/research/factor-strategy-result-2026-06-16.md`。

---

## 0. 开工前必读(按序)

1. 本文件全文。
2. `docs/research/factor-strategy-research-brief-2026-06-16.md`(母任务书:目标、硬性成功判据、红线)。
3. `docs/research/factor-strategy-result-2026-06-16.md`(第 1 轮 FAIL 报告 + 失效分析)。
4. `docs/research/factor-theory-survey-2026-06-16.md`(Phase 1 文献综述 = 因子设计权威依据)。
5. `~/.claude/projects/-home-ps-papers-QuantMind/memory/project-factor-strategy-research-2026-06-16.md`(跨 session 记忆)。
6. 已建代码 `scripts/factor_research/*.py`(§B 资产清单)。

**环境**:
```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
# 跑研究模块(相对 import):FEISHU_INTERACTIVE_ENABLED=false $PY -m scripts.factor_research.<mod>
# 跑测试(绝对 import):$PY -m pytest tests/factor_research/ -q
# 门禁:pytest 全绿 + ruff + mypy strict
```

---

## A. 因子档案(第 1 轮这套因子的完整记录 —— 可直接复用)

7 个 A 股因子,全部纯 stdlib、PIT 可复现、非有限值 fail-closed→None。定义在
`scripts/factor_research/factor_lib.py`(`FACTORS` 注册表)。窗口均为 20 日(ret_5d 为 5 日)。

| 因子 | 公式(原始值) | 机制 | 文献先验方向 | 吸引方向 |
|---|---|---|---|---|
| `ret_5d` | close[-1]/close[-6]−1 | 短期反转 | IC<0 | 低优(买近期输家) |
| `ret_20d` | close[-1]/close[-21]−1 | 1月反转(=现役"动量") | IC<0 | 低优 |
| `vol_20d` | pstdev(20日日收益) | 低波/IVOL | IC<0 | 低优 |
| `max_20d` | max(20日单日收益) | 反彩票(MAX) | IC<0 | 低优 |
| `ep_ttm` | 1/pe_ttm | 价值(E/P,非 B/M) | IC>0 | 高优 |
| `turn_20d` | mean(20日换手率) | 换手/情绪过度定价 | IC<0 | 低优 |
| `amihud_20d` | mean(|ret|/成交额)×1e9 | 流动性(Amihud) | IC<0 | 低优(偏好流动) |

**实测截面 rank IC(train_val 2015-01→2025-04,498 调仓日,20 日 horizon,t 值)** —— 全部与文献先验同号:
```
ret_5d  −0.034 (t −4.83)   ret_20d −0.070 (t −9.35)   vol_20d −0.097 (t −9.91)
max_20d −0.091 (t −12.03)  ep_ttm  +0.065 (t +6.27)   turn_20d −0.120 (t −11.52)
amihud  −0.022 (t −3.47)
```
完整多 horizon 表见 `data/factor_research/ic_study.md`。

**共线性(截面 rank corr,均值)**:`vol/max/turn` 高度相关(0.54–0.83)≈ 同一"散户过度定价"
因子,别重复计;`ret_5d/ret_20d` 0.42;**`ep_ttm` 与其余负相关(−0.2~−0.4)= 唯一正交分散源**。

**关键经验性结论(第 1 轮样本内)**:稳健赢家 = "避开投机/高波名"(低换手、低波);**极端单因子
top-5 会坑**(纯反转=接下跌的刀、纯价值极值=价值陷阱都亏);**广义截面 IC ≠ 极端 top-5 表现**,
复合+分散才稳。

**第 1 轮终选权重(long-only Sobol 搜索,已冻结,供对照)**:
```
ret_5d 0.0892  ret_20d 0.0182  vol_20d 0.1632  max_20d 0.1630
ep_ttm 0.2108  turn_20d 0.1729  amihud_20d 0.1826    (和=1)
```
全精度在 `data/factor_research/weight_search_result.json`。

**这套因子缺什么(第 2 轮可补)**:全是 **价值/防御/反转** 系 —— **没有质量(ROE/盈利稳定)、
成长、趋势/动量延续、分析师/资金流、行业动量** 等在牛市能跟上指数的因子族。Phase 1 文献综述
(`factor-theory-survey`)里还有候选族未落地。

---

## B. 可复用资产清单(别重造轮子)

| 模块/产物 | 用途 | 复用度 |
|---|---|---|
| `scripts/factor_research/factor_lib.py` | 7 因子 + 注册表(方向/机制/先验) | 直接复用 + 扩因子 |
| `scripts/factor_research/locked_split.py` | 神圣切分加载 + `assert_not_test` 守门(dates_sha256 防漂移) | **复用守门机制(但需新窗口,见 §C)** |
| `scripts/factor_research/build_factor_panel.py` | PIT/幸存无偏 因子面板构建(`build_panel` train_val / `build_test_panel` test 一次性);hfq 复权 future-invariant;排除四件套+底30%市值 | 直接复用;**第 2 轮多空需新增『每日全截面 + 基准权重』面板列**(market cap 已在,行业分类需补 PIT 来源) |
| `scripts/factor_research/portfolio_backtest.py` | net-of-cost 组合回测(`backtest`,`_benchmark_leg` horizon-exact 基准腿,`oriented_rank`,`net_returns`,`group_by_date`) | **需扩:多空 / 基准相对组合构造**(当前只有 long-only top-N) |
| `scripts/factor_research/stats_disclosure.py` | DSR(主门 0.95)/ PBO-CSCV / SPA / MinBTL | 直接复用(诚实门不变) |
| `scripts/factor_research/weight_search.py` | Sobol 预声明 N 单纯形搜索 + train-robust→val 择优 + 全池披露 | 模式复用(权重搜索框架);构造换成多空后 backtest 内核要改 |
| `scripts/factor_research/phase4_locked_test.py` | 一次性锁定测试 runner + 冻结权重 firewall + 4 判据 | 模式复用(换新窗口 + 新构造) |
| `data/marketdata_pit/`(2.9G,2779 td) | PIT 快照原始字节+sha256(daily/adj_factor/daily_basic/fund_daily) | **直接复用 —— 全市场+退市,2015-01→2026-06** |
| Mongo `quantmind.kline_daily`(11.37M 行) | 便利层 | 复用 |
| `data/factor_research/csi300_daily.csv` | 沪深300 基准(2779 行) | 复用 |
| `backend/backtest/`(event-loop 引擎)+ rqalpha oracle | T+1/涨跌停/分板块滑点 全引擎 + 差分校验 | **第 2 轮终选应过全引擎交叉确认** |

**import 隔离**:`scripts/factor_research` 可 import `backend.{marketdata_snapshot,backtest,strategy_evolution}`;
`backend.data.*` 需 per-line `# noqa: TID251`;**严禁** `backend.{llm,agents,mirofish}`。

---

## C. 第 1 轮失效根因 + 第 2 轮必须解决的硬问题

**第 1 轮判定 FAIL**:TEST(2025-06→2026-06)净 +5.48% / 夏普 0.54 / 回撤 7.78%(3 门过)但
**超额 −16.36%**(CSI300 +21.84%)→ 跑赢指数门 FAIL。失效三因(诚实):
1. **DSR=0.066 预警兑现** —— val 超额 +69.68% 未延续到 OOS;反过拟合门正确预测脆弱。
2. **regime 错配** —— 防御型组合在大盘强势年跑输市值加权指数。
3. **long-only top-5 对指数基准无关** —— 5 只等权多头无法跟踪 300 只市值加权指数。

**第 2 轮必须正面解决的 3 个硬问题(这些正是要先形成方案 + 与 codex 讨论的核心)**:

### C-1. 干净 OOS 测试窗口从哪来?(最棘手,诚实约束)
- 第 1 轮 test 窗口(2025-06-04→2026-06-12)**已『烧掉』** —— 一次性用过,不可再用于任何迭代/评估;
  泄漏=作废。锁文件 `config/research/test_set_lock.json`。
- 且它**本就是最新数据**(数据止于 2026-06-12)。train_val(2015-01→2025-04)全程入了第 1 轮的
  IC 研究 + 权重搜索 → 这些年的因子行为已被"看过"。
- **没有简单干净的新窗口。** 候选方案(让新 session + codex 权衡,**别默认**):
  - (a) **嵌套 walk-forward / CPCV 全历史评估**(不靠单一 holdout;但每点技术上都"过过")+ 更严
    DSR/PBO + 零-edge 哨兵,接受"无纯 holdout"并如实披露;
  - (b) 从更早历史切一段第 1 轮**从未单独评估**的窗口作 round-2 test(弱:截面数据仍进过 round-1 搜索);
  - (c) **等新数据积累**(owner 已停服,可放数据前向累积一段再测);
  - (d) 重做开发、**从头**留出一段更早窗口全程封存(最干净但费数据)。
- **此决定影响整轮诚实性,必须在执行前与 owner/codex 敲定。**

### C-2. 多空 vs 基准相对 —— 哪个,且能否上线?
- QuantMind **永禁真实下单 + long-only + 散户**(CLAUDE.md 红线)。**真·多空(融券做空)A 股散户基本
  不可得** → 纯多空策略只能是**研究上界参考**,不可直接上线。
- **可上线/可落地的路径 = 基准相对 long-only(增强指数)**:相对 CSI300 成分权重做超低配 tilt
  (行业/市值中性化 + 因子超额加权),控制 tracking error,目标"跑赢指数"。这是与现役 long-only
  系统兼容的方向。
- 备选:**long 股票篮子 + short 股指期货(IF/IC/IM)对冲 beta** = 市场中性(机构可,QuantMind 模拟
  研究可探索,但落地受永禁下单约束)。
- **方案需明确**:研究做哪个(可两个都做:基准相对长多 = 落地候选;多空/中性 = 上界参考),
  以及成功判据是否仍是 owner 锁定的 4 条(net>0 / 跑赢 CSI300 / MDD≤15% / Sharpe≥0.5),
  基准相对策略可加 **信息比率(IR)/ tracking error** 作自然补充门。

### C-3. 补因子族 + 行业/市值中性化所需数据
- 现 7 因子全防御/价值/反转。要在牛市跟上指数,八成需 **趋势/动量延续、质量、成长** 等族
  (Phase 1 综述有候选)。
- 基准相对/中性化需 **PIT 行业分类** + **CSI300 成分股权重历史**。当前面板有 circ_mv(市值),
  **行业分类 PIT 来源 + 指数成分权重**需确认能否从 Tushare 取(`index_weight`/`stock_basic` 行业;
  注意 PIT/幸存无偏 + 字节存档红线)。

---

## D. 第 2 轮目标 & 红线(继承第 1 轮,全部不变)

**目标**:用多空/基准相对构造,产出在**锁定(新)测试集**上**全部 4 判据 PASS** 的策略;
达不到如实报 FAIL + 下一轮方向。**判据(owner 锁定,除非 owner 改口径)**:扣真实成本后
① 净收益>0 ② 跑赢沪深300(累计超额≥0)③ 最大回撤≤15% ④ 年化夏普≥0.5,**四条全过才 PASS**。

**红线(违反即停,全继承)**:
1. **测试集神圣**:开发期零触碰 round-2 test;一切读日期经 `LockedSplit.assert_not_test`;泄漏=作废换窗口。
2. **离线 only**:不碰 `simulation_auto` 实时路径;不接线上 `FACTOR_WEIGHTS` 不经 owner gate;永禁真实下单。
3. **PIT/幸存无偏/无前视**:扩数据(行业/成分权重)须存原始字节+checksum + 幸存无偏 + walk-forward purge+embargo。
4. **数据源**:仅 Tushare 官方 SDK;`TUSHARE_TOKEN` 不入 LLM/飞书凭证池。
5. **LLM 不进数值策略**:权重/打分/构造全确定性;LLM 只用于文献综述。
6. **诚实**:严禁 data-snooping 凑过线;FAIL 报 FAIL;样本内/外严格区分;DSR/PBO/SPA/哨兵全报。
7. **codex-review 前置门**:含代码任务 commit 前过 codex-review(现已恢复额度),修完 P0/P1/P2;docs 豁免;import 隔离。
8. **git**:每模块一 feature commit;**push 受 owner auth 门控**(commit 落本地);`M CLAUDE.md` 是 owner 在途改动**别碰**。

---

## E. 工作协议(owner 强制 —— 先方案、再 codex、后执行)

**严格三段,不可跳步**:

1. **形成方案(plan,不写实现代码)**:进 Plan 模式,通读 §0 必读清单,产出一份**书面方案**覆盖:
   - C-1 干净 OOS 窗口的**明确选择 + 理由**(这是诚实性的核心,须最先定);
   - C-2 研究构造(基准相对长多 / 多空 / 中性化,哪个为主、哪个为参考、能否落地);
   - C-3 要补的因子族 + 所需新数据(行业 PIT / 指数成分权重)及其 PIT/幸存无偏摄取方案;
   - 评估协议(walk-forward/CPCV、DSR/PBO/SPA/哨兵、成本模型、IR/TE 是否加门);
   - 分阶段任务拆解 + 风险 + 工作量。
   - 方案写到 `docs/research/factor-strategy-round2-plan-2026-06-XX.md`。

2. **与 codex 讨论(强制门)**:把方案交 codex 对抗评审(codex 额度 2026-06-18 已恢复;按
   `feedback_codex_review_invocation_quantmind` / codex-review skill;codex 不可用时回退 `/code-review high`
   或多 agent 对抗)。重点让 codex 挑:**C-1 是否真诚实(有没有偷偷 data-snoop)**、多重检验是否
   到位、基准相对构造有无 look-ahead、成本/容量是否现实、判据是否被悄悄放宽。**修完 codex 的
   P0/P1/P2 异议,方案定稿。**

3. **执行(codex 没问题后才开始)**:TDD + 门禁(pytest+ruff+mypy strict)+ 每模块 codex-review +
   feature commit。最后一次性读 round-2 test(同第 1 轮的冻结-firewall 纪律:策略先 git 冻结再读 test)→
   判 PASS/FAIL → 写结果报告 + owner-gated 上线建议。

> **再次强调**:owner 要的是**真盈利/真指数超额**,不是过一个门。反过拟合是手段。**严禁**为凑过线
> 而碰测试集或改判据口径。达不到就如实 FAIL + 下一轮方向。
