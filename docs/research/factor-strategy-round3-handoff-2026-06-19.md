# 第 3 轮执行期接手文件(R3-1/2/3 代码已落 → 卡 owner-gate「开」→ R3-3run→R3-6)

> **新 session 直接读本文件即可无缝接手。** 本文件是「执行期 handoff」,区别于原始
> kickoff `factor-strategy-round3-kickoff-2026-06-19.md`(那是 R3-1 开工前的总任务书,仍有效作背景)。
> 写作纪律:exhaustive —— 完整命令 + 预期输出 + 待写代码的具体片段(future session 不必重新推导)。

═══════════════════════════════════════════════════════════════════════

## 0. 一句话状态 + 下一步

**R3-1 / R3-2 / R3-3 的代码全部写完、codex 全过(0 P0/P1,所有 P2 已修)、本地已 3 个 feature commit(未 push);297 个 factor_research 测试全绿 + ruff + mypy --strict + redline 全清。**
**唯一卡点 = R3-1 真摄取(owner-gated),owner 需说「开」。** 摄取完成后顺序执行:r3 面板 → R3-3 诊断真跑(定 carry 增补集)→ R3-4 搜索 → R3-5 冻结 → R3-6 判定。R3-4/R3-5/R3-6 的**代码尚未写**(数据派生常量须等真跑),本文件 §6 给了完整实施指引 + 代码片段。

**新 session 第一步:**
```bash
# 1) 确认你在本地有这 3 个 R3 commit(未 push):
cd /home/ps/papers/QuantMind && git log --oneline -3
#   预期:40255b3 R3-3 / 5678f45 R3-2 / 120ea82 R1-1  (origin/main 仍在 3e3ace9)
# 2) 确认数据现状(income/cashflow/balancesheet/namechange 尚未摄取):
/home/ps/anaconda3/envs/zhanglan/bin/python - <<'PY'
import json, collections; c=collections.Counter()
[c.update([json.loads(l)["endpoint"]]) for l in open("data/marketdata_pit/index.jsonl") if l.strip()]
print({k:v for k,v in sorted(c.items())})
PY
#   预期含:income_vip / cashflow_vip / balancesheet_vip / namechange  →  全部缺(R3-1 未真跑)
# 3) 向 owner 复述本文件 §4 的 dry-run 计划,请 owner 说「开」再真跑。
```

---

## 1. 背景(为什么这一轮)

round-2 benchmark-relative 增强指数在**冻结后才读**的锁定测试集上 **FAIL**:扣成本净 **+21.58%** /
夏普 **1.80** / 回撤 **6.11%**,但**超额 −0.26%**(CSI300 +21.84%)→ 四门 3 过 1 不过。诊断:**构造已对**
(测试期 beta≈1、size 漂移守住 −0.064、TE 5.37%),**缺的是正超额 alpha 源**(R2-3 市场中性参考臂 alpha
上界仅夏普 0.30)。**修不出 alpha,只能加原料。** 本轮 = 补 2 个零成本新正交 alpha 源:① 盈余惊喜 SUE
(零新数据,用已摄取的 `fina_indicator_vip.profit_dedt`)+ ② 应计/资产增长(income/cashflow/balancesheet_vip,
现有 token ¥0 可取);用 R2-2 协议严格验证(中性化 |t|≥3 + 低共线 + 机制注册,弱则如实丢)后重跑搜索→冻结→判定。
**owner 要真指数超额,补不出仍如实 FAIL。四门不放宽。**

必读背景(冲突时以本文件「已落实部分」为准):
- `docs/research/factor-strategy-round3-plan-2026-06-19.md` — 本轮方案 SSoT。
- `docs/research/factor-strategy-round3-kickoff-2026-06-19.md` — 原始任务书。
- `docs/research/factor-strategy-round2-result-2026-06-19.md` — round-2 FAIL 报告。
- `docs/research/factor-strategy-round2-r2-2-factor-diagnostics-2026-06-18.md` — R2-2 验证协议(|t|≥3 门、carry 集来历)。
- `docs/research/factor-strategy-round2-test-reuse-decision-2026-06-19.md` — 测试集复用决策 + 4 条诚实保障。
- memory:`MEMORY.md` + `project-factor-strategy-research-2026-06-16.md` + `reference-tushare-entitlements-2026-06-19.md`。

---

## 2. owner 已拍板的两个决策(本轮 scope 据此定,不要再问)

1. **R3-6 判定路径 = 既有锁定测试集第 3 次评测**(非等前向窗口)。报告须披露「**第 3 次评测,跨策略多重
   检验 3 次(round-1 / round-2 / round-3)**」。诚实四条全留:冻结-再读 firewall / 累计-N deflation /
   第 3 次评测披露 / **四门不放宽**(净>0 / 超额≥0 / MDD≤15% / 夏普≥0.5;IR/TE 仅披露)。
2. **namechange ST 提纯 = 本轮顺带接入**(已在 R3-1/R3-2 实现:R3-1 摄取 namechange,build_panel_r3 加 PIT ST 硬排除)。

---

## 3. 已完成(代码 + commit + 门禁,全部本地未 push)

| commit | Phase | 内容 | codex |
|---|---|---|---|
| `120ea82` | R3-1 | `backend/data/tushare_client.py` 加 `income_vip/cashflow_vip/balancesheet_vip(period)` + `namechange(start/end)`;`scripts/factor_research/ingest_round2_data.py` 加 `EP_INCOME/EP_CASHFLOW/EP_BALANCESHEET/EP_NAMECHANGE` + `ingest_statement` + `ingest_namechange`(年分页)+ `ingest_round3` 编排 + per-statement coverage + `--phase {round2,round3,all}` CLI | 0 P0/P1,2 P2 修 |
| `5678f45` | R3-2 | 新 `statements_pit.py`(`PeriodStatementPIT`:通用 PIT reader,`report_type='1'` 过滤 + ann_date vintage + `asof`/`as_known` + `statement_vintage_audit`)/ 新 `namechange_pit.py`(`NameChangePIT.is_st_asof`)/ `factor_lib.py` 加 `R3_FACTORS`(sue/accr/asset_growth)+ 纯函数 `earnings_surprise_sue`/`accruals_sloan`/`asset_growth`/`compute_statement_factors`/ `build_factor_panel.py` 加 `build_panel_r3`/`build_test_panel_r3`/`build_r3_inputs` + `--factor-set r3` + cohort PIT ST 排除 | 0 P0/P1,2 P2 修 |
| `40255b3` | R3-3 | 新 `r3_factor_diagnostics.py`(IC 原始+中性化 + 共线性 vs carry + vintage 审计 + carry 决策);纳入门 = 中性化 \|t\|≥3 + aligned + \|corr\|≤0.7 | 0 P0/P1,1 P2 修 |

**门禁(本地复跑应一致)**:`297 passed`(factor_research)+ 40(tushare_client),ruff/mypy --strict/redline 全清。
**round-2 全链 byte-unchanged**:`R2_FACTORS`/`R2_FACTOR_NAMES`/`build_panel_r2`/`build_test_panel_r2`/
`fundamentals_pit.py`/`r2_locked_test.py`/`round2_search.py`/`benchmark_relative.CARRY_FACTORS` 一律未改动语义
(`build_fina_coverage_manifests` 仅新增默认 `endpoint=EP_FINA` kwarg,默认行为不变)。

**已做的关键设计决策(偏离 kickoff 字面处,均质量/复现驱动,已 commit):**
- SUE 源 = `fina_indicator_vip.profit_dedt`(实测确认 fina 无净利级别字段,profit_dedt=YTD 扣非净利,更净且**真零新数据**)。
- 三表 PIT 走**新 `statements_pit.py`**(通用 + `as_known` 序列),`fundamentals_pit.py` byte-unchanged。
- 新因子走 **`R3_FACTORS`**(不动 `R2_FACTORS`);面板走 **`--factor-set r3`/`build_panel_r3`**(不动 r2 面板)。
- 机制 tag:`accr`=`quality_premium`(已注册);`sue`=`post_earnings_drift`、`asset_growth`=`asset_growth_anomaly`
  (**故意未注册** EconomicMechanism → 晋升 gate fail-closed until amendment,同 `growth_premium`;**未动 governance enum**)。

---

## 4. ⛔ owner-gate:R3-1 真摄取(等 owner 说「开」)

**`--dry-run` 已先验(无网络),输出:**
```
[dry-run] phase=round3 calendar 2779 td (20150105..20260612)
[dry-run] statements (income_vip, cashflow_vip, balancesheet_vip): 135 period snapshots (20150331..20260331)
[dry-run] namechange year snapshots: 37 (19901231..20260619; current year keyed by asof)
[dry-run] no network calls made.
```
即 **3 表 × 45 期 + 37 年 namechange ≈ 172 次全市场调用,¥0**(vip 财报现有 token 实测可取),仅官方 SDK /
IPv4-only / 字节存档+sha256+per-period coverage / 幂等续传(可中断重跑)/ 用 `~/.bashrc` 的真实 `TUSHARE_TOKEN`,
限速 400/min 下约几分钟。

**真摄取命令(owner 说「开」后执行):**
```bash
FEISHU_INTERACTIVE_ENABLED=false /home/ps/anaconda3/envs/zhanglan/bin/python \
  -m scripts.factor_research.ingest_round2_data --phase round3
# 预期尾行:round3 ingest: ingested=~172 skipped=0 failed=0
#          round3 coverage: 135 period manifests, ... ;  exit 0
# 失败=非零退出 → 直接重跑(幂等,已存的 skip)。退市股 dtype 已处理(R2-1 坑)。
```
**真跑后立即做的字段健全性核查(重要,见 §7 风险①):**
```bash
FEISHU_INTERACTIVE_ENABLED=false /home/ps/anaconda3/envs/zhanglan/bin/python - <<'PY'
from backend.marketdata_snapshot.store import SnapshotStore
import io, pandas as pd
st=SnapshotStore("data/marketdata_pit")
for ep,col in [("income_vip","n_income"),("cashflow_vip","n_cashflow_act"),
               ("balancesheet_vip","total_assets")]:
    s=st.latest(vendor="tushare",endpoint=ep,trade_date="20231231")
    df=pd.read_csv(io.BytesIO(s.raw_payload),nrows=2)
    print(ep, col, "PRESENT" if col in df.columns else "*** MISSING ***",
          "| report_type" , "yes" if "report_type" in df.columns else "NO")
PY
# 三个字段必须 PRESENT 且 report_type yes。若某字段名不同 → 改 build_r3_inputs 的 fields=
# (statements_pit 缺字段会静默 None → 因子 None,fail-closed 但会无声拉低覆盖率)。
```

---

## 5. 预注册因子定义 + PIT 陷阱(已实现于代码;新 session 据此校验真跑结果)

- **SUE**(`factor_lib.earnings_surprise_sue`):`q_t`=单季扣非净利 = `profit_dedt`(YTD)同财年内差分(Q1=YTD);
  `Δq_t = q_t − q_{t-4}`(同季去年);`SUE = Δq_latest / stdev(trailing 8 Δq)`;需 ≥`SUE_MIN_DIFFS`(6)个 Δq
  否则 None;σ≤0 → None。`attractive_high=True`。
- **应计**(`accruals_sloan`,**年度**):`ACCR = (n_income − n_cashflow_act) / avg(TA_FYt, TA_FYt-1)`;用最近 as-known
  年报(12-31)的 NI/CFO + 该年与上年的 TA 均值。`attractive_high=False`(低应计好)。
- **资产增长**(`asset_growth`,**年度**):`AG = TA_FYt / TA_FYt-1 − 1`;两个**连续**年报(非连续→None)。`attractive_high=False`。
- accr/AG 都用 `compute_statement_factors` 里的 `_annual_pair`(取最近 1231 + 上年 1231,连续才算)。
- **PIT 陷阱(已处理)**:① report_type='1' 合并报表(空白/缺失 fail-closed 丢,只 fina 用 `report_type_filter=None`);
  ② vintage 取 `ann_date<决策日` 的最新一版(restatement after d 不用);③ YTD→单季差分(流量项);TA 是时点存量不差分;
  ④ namechange `start<=d<end`(空 end=open);PIT ST 名 = `ST/*ST/SST/S*ST` 前缀或含「退」。
- **诚实预期**:新因子若中性化 |t|<3 或 |corr|>0.7 → 如实丢(同 R2-2 丢 mom/trend);carry 增补集可能为空 → R3-6 仍可能 FAIL。

---

## 6. 执行期剩余步骤(精确命令 + 待写代码)

### 步骤 A — R3-1 真摄取(§4,owner-gated)

### 步骤 B — 建 r3 面板(train_val;**heavy,数分钟**;依赖 A)
```bash
FEISHU_INTERACTIVE_ENABLED=false /home/ps/anaconda3/envs/zhanglan/bin/python \
  -m scripts.factor_research.build_factor_panel --factor-set r3
# → data/factor_research/panel_train_val_r3.csv  (= round-2 列 + sue/accr/asset_growth + PIT ST 排除)
# 预期 rows≈round-2 面板量级(~326k 略减,ST 排除);打印 panel rows=... codes=... dates=...
# 健全性:三新列非空率(real run 后看;若 sue 全 None → 检查 profit_dedt 字段名/历史长度)。
```

### 步骤 C — R3-3 诊断真跑 → 定 carry 增补集(依赖 B)
```bash
FEISHU_INTERACTIVE_ENABLED=false /home/ps/anaconda3/envs/zhanglan/bin/python \
  -m scripts.factor_research.r3_factor_diagnostics
# → docs/research/factor-strategy-round3-r3-factor-diagnostics-2026-06-19.md
# 读「## 6. Carry decision」的 R3_CARRY = 'ret_5d, ..., rev_yoy[, 幸存新因子]'。记下幸存集。
```
**这是 docs/诊断产物(无新代码)→ 不需 codex。** 据真实 IC/共线性如实定 carry(可能 0/1/2/3 个新因子幸存)。

### 步骤 D — R3-4 重跑搜索(**需写代码**;依赖 C 的幸存集)
**待写改动:**
1. `benchmark_relative.py` 加常量(**不动 `CARRY_FACTORS`**):
   ```python
   # round-3 carry = round-2 eleven + R3-3 survivors (填入 C 步真实幸存集).
   R3_CARRY_FACTORS: tuple[str, ...] = (*CARRY_FACTORS, "sue", "accr")  # 例:若 sue/accr 幸存
   ```
   并加进 `__all__`。
2. `round2_search.py` 参数化 carry-耦合函数,默认 `carry: tuple[str,...] = CARRY_FACTORS`(**默认保 round-2 byte 行为**)。
   需改的耦合点(共 ~8 处,逐一加 `carry` 参数 / 传递):`load_manifest(path, *, carry=CARRY_FACTORS)`(断言改成
   `order != tuple(carry)`)、`build_weight_vectors(manifest, *, carry=...)`(dim 校验)、`build_candidates(manifest, *, carry=...)`、
   `_weights_dict(w, *, carry=...)`、`_shuffle_neut(panel, seed, *, carry=...)`、`_sentinel_val_irs(..., carry=...)`、
   `search(..., *, carry=CARRY_FACTORS)`(贯穿 + `main` 里 `neutralize_panel(panel, list(carry))`)。
   **加回归测试**:`test_round2_search.py` 跑默认(carry=CARRY_FACTORS)仍选 `constituent_only` + 同权重(round-2 复现守门)。
   > 备选:克隆 `round3_search.py`(更稳但 ~692 行重复)。**推荐参数化 + 回归守门**(DRY + round-2 复现)。
3. 新 `config/research/round3_experiment_manifest.json`(克隆 round2 manifest,改):
   - `carry_factor_order` = R3_CARRY(eleven + 幸存),`weight_simplex.dim` = `len(R3_CARRY)`;
   - 重算 `cells=36`(4 constraint × 3 k × 3 a_max 不变)、`weights_per_cell=1+n_sobol`、`n_trials_total=cells×weights_per_cell`
     (= **重新声明的累计 N**,DSR/PBO 据此 deflate;`n_sobol` 可保 16);
   - `spa_baselines` 不变(passive/round1/momentum);`honesty.test_reuse` 注明第 3 次评测。
   - `constituent_only` 已在 R2 胜出 → 可保 4-constraint 搜以诚实完整(推荐),或 owner 指定只搜 constituent_only。
4. 真跑:
   ```bash
   FEISHU_INTERACTIVE_ENABLED=false /home/ps/anaconda3/envs/zhanglan/bin/python \
     -m scripts.factor_research.round2_search --carry r3 \
     --manifest config/research/round3_experiment_manifest.json \
     --panel data/factor_research/panel_train_val_r3.csv \
     --out data/factor_research/round3_search_result.json
   # (main 需加 --carry {r2,r3} 开关映射到 CARRY_FACTORS / R3_CARRY_FACTORS)
   # → 选唯一策略 + DSR/PBO/SPA/哨兵/CPCV 全披露(~20min)。
   ```
5. 门禁 + codex(含代码)→ feature commit。诊断 doc 记开发证据(≠判定)。

### 步骤 E — R3-5 成本压力 + git 冻结(**需写代码**;依赖 D)
1. 成本压力:`full_engine_crosscheck.cross_check` 已 carry-agnostic,直接复用(panel 用 R3_CARRY 中性化 + R3 选出的
   weights/constraint/k/a_max);写 `round3_crosscheck_result.json`(摩擦单调 ✓;rqalpha UNAVAILABLE 诚实记录)。
   可在 `full_engine_crosscheck.main` 加 `--search-result`/`--carry` 或写薄 r3 runner。
2. 新 `round3_locked_test.py`(克隆 `r2_locked_test.py`,改 4 处):
   - `FROZEN_R3_*` 常量 = D 步 `round3_search_result.json` 的 constraint/k/a_max/cap + 权重(**3dp 钉死**);
   - `CARRY_FACTORS` → `R3_CARRY_FACTORS`(neutralize_panel + 权重集校验);
   - `build_test_panel_r2` → `build_test_panel_r3`(用 `build_r3_inputs(store, root, last_period_date=test_end)`);
   - `_print_verdict` 披露文案改「**3RD evaluation**(round-1/2/3 各 1 次)」。
   - `load_frozen_strategy` firewall(load 复验 fail-closed,权重集 + 3dp)逻辑保留。
   **`r2_locked_test.py` 保持 byte-unchanged**(round-2 冻结产物完整)。
3. 门禁 + codex → **填好 `FROZEN_R3_*` 后先 feature commit(记 hash)→ 再读 test**(冻结-再读 firewall)。

### 步骤 F — R3-6 判定(既有测试集第 3 次评测;依赖 E 的冻结 commit)
```bash
FEISHU_INTERACTIVE_ENABLED=false /home/ps/anaconda3/envs/zhanglan/bin/python \
  -m scripts.factor_research.round3_locked_test
# firewall→build_test_panel_r3 读 test 一次→四门;→ data/factor_research/round3_locked_test_result.json
```
产出 `docs/research/factor-strategy-round3-result-2026-06-19.md`:四门 PASS/FAIL + 失效/成功分析 +
**第 3 次评测披露(跨策略多重检验 3 次)** + DSR/PBO/SPA/哨兵开发证据。补不出正超额 → **如实 FAIL + 下一轮方向**
(分析师上修 `report_rc` 需充 8000 积分;或资金流 `moneyflow`/`hk_hold`/事件 `forecast_vip`/`express_vip` 因子族 — 见 round-3 plan §7)。

---

## 7. 风险 / 必查项(诚实)

1. **字段名风险(必查,§4 核查脚本)**:`statements_pit` 缺字段 → 静默 None → 因子 None。真跑后核 `n_income`/
   `n_cashflow_act`/`total_assets`/`profit_dedt` 存在 + 非空率合理;sue 需 ~12 季历史 → 早年/次新股 None 正常。
2. **可能仍无 alpha**:参考臂上界夏普 0.30 是硬约束;carry 增补集可能为空 → R3-6 仍 FAIL。不假设有效。
3. **第 3 次评测代价**:本测试集已被 round-1 + round-2 评测 2 次,本轮第 3 次;报告显式披露,之后慎再迭代本测试集。
4. **r3 面板 build 慢**:每 code 每调仓 4 次 statement `as_known` 查找;若过慢可考虑预聚合(当前实现可接受,先实测)。

---

## 8. 红线(全继承,违反即停)

1. **测试集神圣**:开发期(B-E)零碰 test;唯一读 test = R3-6 `round3_locked_test`(策略先 git 冻结);基准侧开发期
   `<test_start`;一切日期经 `LockedSplit.assert_(all_)not_test`。`--factor-set r3 --mode test` 通用 CLI **已拒**(只 R3-6 runner 可读)。
2. **PIT/幸存无偏/无前视**:三表 `ann_date<d` + report_type=1 + vintage;SUE 用已公告季度;namechange `start<=d<end`;缺报 fail-closed→None。
3. **数据源仅 Tushare 官方 SDK**;`TUSHARE_TOKEN` 不入 LLM/飞书池;IPv4-only(`local_address="0.0.0.0"`);**不引 akshare 进研究 PIT 路径**。
4. **LLM 不进数值策略**;LLM 只用于文献。**离线 only**;不碰 simulation_auto;不接线上 FACTOR_WEIGHTS 不经 owner gate;永禁真实下单。**不动 governance EconomicMechanism enum。**
5. **诚实**:无 data-snooping;DSR/PBO/SPA/哨兵全报;四门不放宽;FAIL 报 FAIL;开发证据≠判定。
6. **import 隔离**:`scripts/factor_research` 可 import `backend.{marketdata_snapshot,backtest,strategy_evolution}`;`backend.data.*` 须 per-line `# noqa: TID251`;**严禁** `backend.{llm,agents,mirofish}`。
7. **codex 前置门**:含代码任务 commit 前过 `codex review --uncommitted </dev/null`(撞额度回退 `/code-review high`),修完 P0/P1/P2;docs/配置/记账 commit 豁免。**新文件须先 `git add -N` 才进 codex 的 `git diff`。**
8. **git**:每模块一 feature commit;**push 受 owner auth 门控(commit 落本地)**;`M CLAUDE.md` 若 owner 在途别碰别 stage。

---

## 9. 环境 + 命令速查

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
# 测试 / 门禁
FEISHU_INTERACTIVE_ENABLED=false $PY -m pytest tests/factor_research/ -q   # 当前 297 绿(只增不减)
$PY -m ruff check scripts/factor_research tests/factor_research
$PY -m ruff format <仅你改的文件>                                          # 别 format 整目录
$PY -m mypy --strict scripts/factor_research
bash scripts/redline-check.sh
# codex(前台 + </dev/null 防 stdin deadlock;撞额度 → /code-review high)
git add -N <新文件> && codex review --uncommitted </dev/null
# rqalpha venv(勿重装):/home/ps/rqalpha-smoke-venv
```
- 报告用中文、推理用英文、代码/commit 英文。
- commit 末尾保留 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`(本轮 3 个 R3 commit 已含)。

---

## 10. 关键文件地图(R3 新增/改动)

| 文件 | 角色 | R3-4/5/6 在哪扩 |
|---|---|---|
| `backend/data/tushare_client.py` | 加 4 端点(income/cashflow/balancesheet_vip + namechange) | — |
| `scripts/factor_research/ingest_round2_data.py` | `ingest_round3` + `--phase` | — |
| `scripts/factor_research/statements_pit.py` | `PeriodStatementPIT`(asof/as_known)+ `statement_vintage_audit` | — |
| `scripts/factor_research/namechange_pit.py` | `NameChangePIT.is_st_asof` + `namechange_snapshot_keys`(空 fail-closed) | — |
| `scripts/factor_research/factor_lib.py` | `R3_FACTORS` + `earnings_surprise_sue`/`accruals_sloan`/`asset_growth`/`compute_statement_factors` | — |
| `scripts/factor_research/build_factor_panel.py` | `build_panel_r3`/`build_test_panel_r3`/`build_r3_inputs` + `--factor-set r3` + cohort ST 排除 | R3-6 用 `build_test_panel_r3`+`build_r3_inputs` |
| `scripts/factor_research/r3_factor_diagnostics.py` | IC+共线+vintage+carry 决策 | C 步真跑 |
| `scripts/factor_research/benchmark_relative.py` | `CARRY_FACTORS`(round-2,**不改**)+ `composite_score`(已 carry-agnostic) | **R3-4 加 `R3_CARRY_FACTORS`** |
| `scripts/factor_research/round2_search.py` | 搜索(DSR/PBO/SPA/哨兵/CPCV) | **R3-4 参数化 carry** |
| `scripts/factor_research/full_engine_crosscheck.py` | 成本压力(carry-agnostic) | R3-5 复用 |
| `scripts/factor_research/r2_locked_test.py` | round-2 冻结+四门(**byte-unchanged**) | **R3-5 克隆为 `round3_locked_test.py`** |
| `config/research/round2_experiment_manifest.json` | round-2 frozen manifest | **R3-4 克隆为 `round3_experiment_manifest.json`** |

---

## 11. 完成定义

R3-1 真摄取(字节+coverage+幸存无偏)→ r3 面板(SUE 季度差分 + accr/AG 年度 + ST 排除)→ R3-3 验证选 carry
增补集(弱则诚实丢)→ R3-4 重跑搜索选唯一策略(全披露)→ R3-5 成本压力 + git 冻结(读 test 之前 commit,记 hash)→
R3-6 既有测试集第 3 次评测(四门 + 第 3 次评测披露报告)。全程 TDD + 门禁 + codex 绿;feature commit 落本地(push 待 owner);
末尾更新 round-3 plan 进度段 + memory(MEMORY.md + project 文件)+ 中文报告 + 一句话指下一步。
**owner 要真指数超额,补不出仍如实 FAIL + 下一轮方向。**

═══════════════════════════════════════════════════════════════════════
