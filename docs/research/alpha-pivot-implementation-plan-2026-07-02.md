# Alpha-pivot 实施计划(AP-0..AP-3 + gated FW/SR/SIM + PARKED)— 2026-07-02

> **状态**:三步法第三步产物(plan mode,owner 2026-07-02 批准)。**上位权威** = `docs/research/alpha-pivot-composite-spec-outline-2026-06-27.md`(spec v3,codex 2 轮对抗收敛);本文档只把 spec 落成**可执行任务**(精确文件名/命令/预期输出/门禁),口径冲突以 spec 为准。
> **执行方式**:后续 clean-context session(Sonnet 5)按 `docs/plan.html`(SSoT,2026-07-02 重构版)认领任务 → 本文档是每个任务的施工图。
> **作者**:Claude(Fable 5)· owner:dr.zhang

---

## §0 一句话

在 owner 已答的固定容器(≤5 集中 + 现金 buffer,P-E)里,把**预声明的 10 因子三块复合**(反转快腿 + 分析师修正 + 质量,先验符号、committed 权重)做成 **fixed prior spec**,先跑**收益盲 power 前置**判过门可达性,再进可复用竞技场过**反过拟合四门(不放宽)**;过门才 owner-gated 冻结 + look-once 前向。**FAIL 报 FAIL,绝不移球门。**

---

## §1 已答框定(不再 litigate)

| 维度 | 结论 | 数据依据 |
|---|---|---|
| 风险容器 | **≤5 集中 + 现金 buffer**(P-E);分散**伤害** | `slot-frontier-results-2026-06-27`(槽 5→50:MDD 56%→66%) |
| EXIT/de-risk overlay | **净有害,永不再建** | C1a / B1 / B2 / QGR-4 全 FAIL |
| 回撤控制 | **唯一杠杆 = 现金 buffer**(组合层降暴露) | buf40_5(40% gross / 60% 现金)MDD 56%→31% |
| 绑定约束 | **选股 alpha 质量**(非容器) | frontier 全配置 DSR 0.003–0.006 ≪ 0.95,eq_5 最高 0.0059 |
| 判据 | 绝对净盈 + 控回撤(MDD 仅披露)+ 四门不放宽 | `qgr-criterion-rebar-amendment-2026-06-27` |

---

## §2 owner 决策点落定(2026-07-02 拍板;评测前 hash,评测后绝不改)

| # | 决策 | 选定值 |
|---|---|---|
| 1 | 块间权重规则 | **信心/horizon 加权:反转 0.5 / 分析师 0.25 / 质量 0.25**(provenance 分级派生 ✅=2 分 🟡=1 分,非样本内拟合) |
| 2 | AP-0.5 power 系数 K | **K = 2**(go iff `SR_req ≤ 2·SR_ref`) |
| 3 | no-go 分支预承诺 | **本刀降纯诊断**(只跑 attribution IC 披露 + 相对纯反转 SPA,不申报四门、不烧 promotion trial)+ 数据证据整理上报 owner 决定是否重审 ≤5 前提 |
| 4 | provisional 分支预承诺(A4 胜 A0 但 DSR<0.95) | **git 冻结 spec,等 post-2026-06-12 处子前向窗口裁决**(与 round-4 冻结候选 `ffc1db3` 同队列) |
| 5 | 双容器 joint pass | eq_5(科学门)+ buf40_5(部署门)**都过四门**才声称 deployable;仅 eq_5 过 = 限定为 ranker science gate |
| 6 | fixed prior spec 诚实代价 | 全预声明因子**一律入** spec(即便 IC 弱/符号疑),IC 仅披露不改组成 |

---

## §3 AP-0 预声明 fixed prior spec(docs/ledger gate,豁免 codex 代码门)

### AP0-001 spec 常量模块

**新文件** `scripts/factor_research/alpha_pivot_spec.py`(frozen dataclass / tuple 常量,零 IO,零 `backend.{llm,agents,mirofish,risk}` import):

- `RANKER_FACTORS`:10 因子 + 先验符号(committed,绝不样本内改):
  - 反转块(✅有据,QGR-3 ⑦ 从零验存活):`rev_1d` −,`max_5d` −,`turn_spike` −
  - 分析师块(🟡谨慎,round-4 R4-4 正交子集;`tp_impl` 因 `tp`=利润总额歧义预声明剔除):`np_rev` +,`rev_diff` +,`cover_chg` +(源 `analyst_revision_pit.AnalystRevisionPIT`,`report_date < d` PIT)
  - 质量块(🟡谨慎,round-1/2 + AF-003 先验):`roe` +,`gpm` +,`ep_ttm` +,`accr` −(源 `fundamentals_pit.FundamentalsPIT`,`ann_date < d` vintage PIT;`accr` = `factor_lib.accruals_sloan`)
- `BLOCK_WEIGHTS = {"reversal": 0.5, "analyst": 0.25, "quality": 0.25}`(owner 决策 #1)
- `UNIVERSE_FILTERS`:底部确认 core-4 committed 阈值(QGR-3 ⑧ 原样:vol_dryup / 无破位 / 无困境 PIT-ST via namechange / 质量地板;cyq 成本带预声明剔除)+ 排除四件套 + at-limit 不可成交名剔除
- `CONTAINERS`:`("eq_5", 5, 100)` 与 `("buf40_5", 5, 8)`(即 5 槽 ×8% cap ≈ 40% gross / **60% 现金 buffer**,满足 P-E ≥40% floor)——**必须与 `slot_frontier.FRONTIER` 中同名配置字节一致**(测试断言)
- `GATE_CALIBRATION`:DSR≥0.95(deflation N=2417)/ PBO≤0.5 / SPA(Hansen)/ Romano-Wolf;CPCV purge+embargo ≥ 4(=horizon−1)
- `POWER_INPUTS`(owner 决策 #2 + codex R2-1):矩 = normal(skew=0, kurt=3)/ HAC = `honest_gates.hac_variance_inflation` lag=4 结构保守上界规则 / T = `onc_effective_n`(≈497 rebalance 重叠压缩)/ K=2 / SR_ref = frontier 已披露纯反转 eq_5 SR(零新 peek)
- `spec_hash() -> str`:canonical JSON(sort_keys)→ SHA256;确定性(测试断言两次调用相等 + 与快照常量相等)

**测试** `tests/factor_research/test_alpha_pivot_spec.py`:① 可导入、全 frozen;② 10 因子/符号/权重与本文档 §3 完全一致;③ 权重和=1;④ 容器与 `slot_frontier.FRONTIER` 逐字段相等;⑤ `spec_hash()` 确定性;⑥ redline grep 空。

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
$PY -m ruff check scripts/factor_research/alpha_pivot_spec.py && $PY -m mypy scripts/factor_research/alpha_pivot_spec.py
FEISHU_INTERACTIVE_ENABLED=false $PY -m pytest tests/factor_research/test_alpha_pivot_spec.py -q
grep -rn "import backend\.\(llm\|agents\|mirofish\|risk\)" scripts/factor_research/alpha_pivot_spec.py   # 必空
```

### AP0-002 账本预登记(先于任何结果 artifact)

用既有 `trial_ledger` API(`TrialRecord` 无独立 spec_hash 字段 → **hash 嵌入 `description`**;`trial_id` 是 design-only 内容寻址,幂等 re-append):

```python
from scripts.factor_research.trial_ledger import TrialLedger, TrialRecord
H = spec_hash()[:16]
L = TrialLedger.with_legacy("data/factor_research/mfi_trial_ledger.jsonl")
win = ("2015-02-09", "2025-04-25")   # train_val;sealed test 永不读
L.append(TrialRecord("AP", "diagnostics", "qgr.alpha_pivot.ic",
    f"analyst(3)+quality(4) x 4 horizons disclosure-only, spec={H}", 28, *win, "<UTC-ISO>", effective_n=28))
L.append(TrialRecord("AP", "ablation", "qgr.alpha_pivot.attribution",
    f"A1/A2/A3 single-add attribution arms, spec={H}", 3, *win, "<UTC-ISO>", effective_n=1))
L.append(TrialRecord("AP", "single", "qgr.alpha_pivot.composite",
    f"A4 fixed prior composite x dual containers (joint pass), spec={H}", 2, *win, "<UTC-ISO>", effective_n=1))
```

**验收(核验一行)**:AP-前基数实测 nominal **2412** / effective **2387**(2026-07-02 已核)→ 登记后:

```bash
$PY - <<'EOF'
from scripts.factor_research.trial_ledger import TrialLedger
L = TrialLedger.with_legacy("data/factor_research/mfi_trial_ledger.jsonl")
assert L.cumulative_nominal_trials() == 2445 and L.cumulative_effective_trials() == 2417, \
    (L.cumulative_nominal_trials(), L.cumulative_effective_trials())
print("ledger OK: 2445 /", L.cumulative_effective_trials())
EOF
```

A4 的 deflation N = `L.deflation_n_trials(onc_effective_n=<AP-2 实测>)` ≈ **2417**(取 max)。

---

## §4 AP-0.5 收益盲 power 前置(省债关键;零新 trial)

**新文件** `scripts/factor_research/alpha_pivot_power_precheck.py`。**不读任何 A4/panel 收益**(验收含 grep:不 import panel/backtest/bar_source 模块)。

**算法**(Bailey–LdP DSR 反解,全输入来自 `POWER_INPUTS` 预声明):
1. `SR0` = 期望最大伪 Sharpe(deflation 阈,N=2417,`variance_of_sr` 用 normal 矩 + HAC lag=4 保守上界 × IID `var_sr = (1+0.5·SR²)/T`,T=onc_effective_n≈497);
2. 反解 `SR_req`:使 `DSR(SR_req; N=2417, T, skew=0, kurt=3, HAC) = 0.95` 的最小 period-SR(数值求根,单调性测试守卫);年化披露 `SR_req_ann = SR_req·√(252/5)` 一并输出;
3. `SR_ref` = frontier 已披露纯反转 eq_5 annualized SR(从 `data/factor_research/`/frontier 结果 doc 读**已披露数字**,零新 peek);
4. **go iff `SR_req ≤ 2·SR_ref`**(K=2,owner 决策 #2)。

```bash
$PY -m scripts.factor_research.alpha_pivot_power_precheck --out data/factor_research/alpha_pivot_power.json
```

**输出**:`data/factor_research/alpha_pivot_power.json`(SR0/SR_req/SR_ref/K/go 布尔 + 全输入 echo)+ 结果 doc `docs/research/alpha-pivot-power-precheck-2026-07-XX.md`。
**测试** `tests/factor_research/test_alpha_pivot_power_precheck.py`:闭式 fixture(小 N 手算对照)+ SR_req 对 N 单调递增 + 对 T 单调递减 + go/no-go 边界。
**分支**:no-go → **owner gate**(预承诺 = 降纯诊断,§2 #3);go → AP-1/AP-2。

---

## §5 AP-1 panel 合并 + PIT 硬验收 + IC 披露

### AP1-001 panel 合并

扩展 `build_qgr_panel.py`(既有 ⑦ 快腿 + ⑧ 底部确认 + fundamentals 结构上新增两块列):
- 分析师列:`np_rev` / `rev_diff` / `cover_chg`(`AnalystRevisionPIT`,`report_date < d`;report_rc 范围分页坑已在 R4 处理);
- 质量列:`roe` / `gpm` / `ep_ttm` / `accr`(`FundamentalsPIT.asof`,`ann_date < d` vintage;`accr`=Sloan)。
防火墙不变:bar-read ⊆ train_val `20150209→20250425`。

### AP1-002 PIT 硬验收测试(红线级,codex R1-P1-5)

**新文件** `tests/factor_research/test_alpha_pivot_pit.py`,五硬测试:
1. 任一 decision date 不得见未来 `report_date`/`ann_date`(构造越界 fixture 断言被闸);
2. EOD close 特征只能 T+1 执行(执行日 > 特征日);
3. `ep_ttm`/`accr` 来自 vintage PIT,绝不混 vendor 当日 `pe_ttm` 重述口径;
4. 复权因子 as-of pin 不混未来;
5. `leak_probe` future-NaN 毒化扫描对新增列全绿。

```bash
FEISHU_INTERACTIVE_ENABLED=false $PY -m pytest tests/factor_research/test_alpha_pivot_pit.py -q   # 全绿才继续
```

### AP1-003 IC 披露 runner(纯披露,不改 spec)

**新文件** `scripts/factor_research/alpha_pivot_ic_disclosure.py`(模板 `factor_ic_study.py`):28 个预登记诊断(分析师 3 + 质量 4)× horizons {1,5,10,20}td,size+行业中性化(`neutralize.neutralize_panel`,删最小 30%)IC/ICIR/t + 先验符号兑现表 + vs 反转簇共线披露。
**enforcement**:runner 只读 spec 常量、**绝不写** spec;验收 grep:runner 无任何对 `alpha_pivot_spec.py` 的写路径 + 结果 doc 显式声明「IC 不改 spec 组成」。⚠️ IC t = 乐观 screen 非裁决(重叠窗自相关;round-1..4 全有强 IC 却 test FAIL 三次)。
**输出** `docs/research/alpha-pivot-ic-disclosure-2026-07-XX.md`(FAIL 报 FAIL)。

---

## §6 AP-2 committed 复合 + 竞技场四门

### AP2-001 复合 assembler

**新文件** `scripts/factor_research/alpha_pivot_composite.py`:每因子按先验符号对齐 → 截面 z-score(size+行业中性化后)→ 块内等权 → 块间 `BLOCK_WEIGHTS`(0.5/0.25/0.25)→ 复合分喂 `gate_backtest.PanelScoreProvider`。缺失值规则预声明:块内可用因子重归一;整块缺失 → 该块权重按剩余块比例重分(committed,写进 spec 常量)。
**测试**:符号对齐 / 权重和 / 确定性 / 权重只来自 spec 模块(无样本内拟合路径)。

### AP2-002 竞技场 runner

**新文件** `scripts/factor_research/alpha_pivot_arena.py`(模板 `slot_frontier.py`:argparse + `--ledger` 默认 `data/factor_research/mfi_trial_ledger.jsonl` + 分段 `[n/6]` 日志):

- **臂 × 容器**:A0 纯反转(对照,frontier 已测 eq_5 +571k/DSR 0.0059、buf40_5 +294k/0.0052;byte-exact 核对)/ A1 反转+底部确认过滤 / A2 反转+质量 / A3 反转+分析师 / **A4 全复合** —— 每臂在 **eq_5 与 buf40_5 各跑一套,只同容器内比较**;
- **baselines**:`baselines.run_baselines` B1-B5(随机 top-5 容器+过滤宇宙+约束匹配 / screener-momentum / 流动性 / ETF-only / CSI300-hold);
- **四门(A4,双容器)**:`honest_gates.deflated_sharpe_hac`(ONC+HAC,`n_trials = L.deflation_n_trials(onc_effective_n=实测)`≈2417)/ PBO-CSCV ≤0.5 / Hansen SPA(预声明 family + block bootstrap)/ `multi_strategy_compare` Romano-Wolf;真 CPCV(`cpcv.py`,purge+embargo≥4);
- **切片与披露**:制度分层 + 6 股灾切片 + **size-drift 量化暴露回归**(A4 篮子日收益 ~ log circ_mv + 行业;A4 相对 A0 的持仓平均 log-mv 分层;胜出可被 size/大盘暴露显著解释 → **FAIL 报 FAIL,增强指数残影不接受**);
- 引擎全留:`run_gate_backtest`(冻结引擎 + 前视即抛 + 涨跌停不可成交 + 分板块滑点 + ¥5 min 佣金 + 印花/过户 + T+1 + 5td 最短 + 7 弱势门轮动)。

**测试** `tests/factor_research/test_alpha_pivot_arena.py`:合成 mini-panel(臂接线 / 容器匹配 / 四门 plumbing / baseline 匹配规则),不跑全窗。

### AP2-003 全窗跑 + 结果 doc

⚠️ **全窗 PitBarSource ~40–50 min/容器组;harness 后台 bash 在 turn 边界被杀 → 必须 setsid 全脱离**:

```bash
mkdir -p logs && nohup setsid $PY -u -m scripts.factor_research.alpha_pivot_arena \
  --out data/factor_research/alpha_pivot_arena_result.json \
  > logs/alpha_pivot_arena.log 2>&1 < /dev/null &
# runner 内自配 structlog WARNING(静音 backend INFO 洪流);用 Monitor 工具轮询 completion,绝不用普通 run_in_background
```

**输出**:`data/factor_research/alpha_pivot_arena_result.json` + `docs/research/alpha-pivot-arena-results-2026-07-XX.md`(每臂×容器净盈/MDD/DSR/四门 + attribution 表 + 切片 + size-drift 回归 + 诚实分级 ✅/🟡/🔴 + §7 决策树落点)。

---

## §7 AP-3 决策树(预承诺,不移球门)

```
AP-0.5 power 前置(收益盲)
├─ no-go(SR_req > 2·SR_ref)→ 不烧 trial → 降纯诊断 + 数据上报 owner(重审 ≤5 前提?)   [owner 决策 #3]
└─ go → AP-2 竞技场,A4 双容器:
    ├─ 双容器过四门 + 严格胜 A0-A3/B1-B5 + 无 size-drift 假象
    │   → ✅ owner-gated 冻结(git)+ look-once 前向(处子 OOS)→ 兑现→go-live gate;不兑现→FAIL 报 FAIL
    ├─ 胜 A0 但 DSR<0.95(或仅 eq_5 过)
    │   → 🟡 FAIL 报 FAIL + 冻结等前向(与 ffc1db3 同队列)                              [owner 决策 #4]
    ├─ 胜出仅由 size-drift/增强指数残影解释 → 🔴 FAIL 报 FAIL(不接受)
    └─ 不胜 A0 → 🔴 H0 不被拒 → 报 owner:绑定约束或在 ≤5 前提/持仓机制/超短腿本身
```

---

## §8 FW / SR / SIM(owner-gated,SSoT 中 status=blocked)

- **FW-001 look-once 前向**:post-`2026-06-12` 处子数据,对象 = round-4 冻结候选 `ffc1db3`(分析师修正)+ AP 复合(若 AP-3 批准)。blocked_by:AP3-001 owner 批准 + 前向数据摄取 owner-gated + look-once 第 5 次极慎。模板:`round4_forward_test.py`/`forward_gate_test.py`。
- **SR-001 shadow replay**:45 交易日真管线 shadow(go-live gate,P0-6 协议)。blocked_by:FW-001 兑现 + owner。
- **SIM-001 ¥1万执行可行性**:整手/¥5 佣金地板/滑点现实下 ≤5 槽可执行性(双资本:¥100万证 alpha / ¥1万证执行)。blocked_by:SR-001 + owner。

## §9 PARKED 遗留承接(全 blocked,防丢失)

| id | 内容 | blocked_by / 指针 |
|---|---|---|
| PK-001 | 价值线运行期激活(AF-001..007 已建休眠,env-OFF byte-identical) | owner 激活决策;`docs/research/value-sleeve-af-handoff-2026-06-22.md` |
| PK-002 | 主旋律维度(theme map 已冻 `4e97db2`) | owner 2026-06-27 决策 #2 后置 |
| PK-003 | production-hardening deferred #10-13 | owner;`docs/handoff/production-hardening-handoff-2026-06-23.md` §0b |
| PK-004 | push backlog(2026-06-22 以来本地 commit 一摞) | push = owner-gated |

---

## §10 红线(全留,违反即停)

sim 暂停 · 永禁真实下单 · 离线 · 仅 Tushare 官方 SDK · PIT 字节存档+checksum · **train_val only(`20150209→20250425`,sealed test 永不读;真 OOS = owner-gated look-once,第 5 次极慎)** · 防火墙 bar-read⊆train_val · **研究/评测零 LLM** · size/行业中性化删最小 30% · **反过拟合四门不放宽** · **非清零账本不清零(AP 后 nominal 2445 / effective 2417),fixed prior spec 防 mining 债(禁 grid/best-of/inclusion screen/样本内改符号权重)** · 不接 moneyflow 主路径 · 北向仅历史 · 不做 L2 · **永不再建 held-EXIT/regime/避顶部 overlay** · 不碰 backend value-sleeve(AF-*)/冻结引擎字节/RiskEngine/单一构造点 · governance enum 不动 · codex 前置门 · FAIL 报 FAIL · **push/摄取/live 激活/look-once = owner-gated** · 报告中文/代码 commit 英文。

## §11 工作流门禁(每个编码任务)

1. TDD 先写测试;非 risk 覆盖 >70%。
2. 逐文件 `ruff check` + `mypy`;基线 `FEISHU_INTERACTIVE_ENABLED=false $PY -m pytest tests/factor_research -q`(接手时 **688 passed**,只增不减)。
3. redline grep(`docs/plan.html#gates` 研究域扫描)全空/全绿。
4. **codex commit 前置门**(代码任务;撞限流 → `/code-review high`;docs/ledger-only 豁免)。
5. 一任务一 feature commit(英文 conventional);完成后回填 `docs/plan.html` TASKS(`done` + 真实 hash + notes)——**报告完成前 SSoT 必须已改**。
6. Phase 全 done 后一次 docs-only commit 追加 SESSION_LOG 条目。
