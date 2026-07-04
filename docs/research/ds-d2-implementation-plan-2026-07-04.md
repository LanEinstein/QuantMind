# DS-D2 施工图:防御宇宙 × 反转排名(重定性协议,排名层生死一刀)

> **状态**:owner 2026-07-04 全授权(assessment §5 三决定)。上位 = `docs/decisions/qgr-certification-rearch-amendment-2026-07-04-dev-selection-forward-certification.md`(**必读:promotion 判据已改 —— dev=选拔/前向=认证,DSR 降披露**)+ `docs/handoff/fable5-strategic-assessment-2026-07-04.md`(论证)+ `defensive-candidate-D2-reversal-on-defensive-universe-2026-07-03.md`(原 spec,本图在其上加重定性协议)。
> **作者**:Claude(Fable 5)· 执行:Opus 4.8 · owner:dr.zhang
> **一句话**:在**一个 ablation、一个 bar source、一个窗口**里跑齐 A0 全宇宙反转、D2 防御宇宙反转、各自的暴露匹配随机 placebo → 同时回答「反转排名在书层到底有没有边(证据洞)」+「防御宇宙过滤是否修复熊市亏损」+「排名层是否配得上叠在 sleeve 上」;预注册三分支 (a)/(b)/(c),owner 判。

---

## §0 Opus 4.8 kickoff prompt(owner 直接粘贴)

```text
读 docs/research/ds-d2-implementation-plan-2026-07-04.md,按照它执行 DS-D2,整个任务一个 session 完成。

背景自举(按序,别假设,grep 核实):CLAUDE.md §1 协议/§2 红线 → docs/plan.html #current + SESSION_LOG 顶部
→ docs/decisions/qgr-certification-rearch-amendment-2026-07-04-dev-selection-forward-certification.md
(判据架构已改:dev=选拔门〔胜自身 placebo t≥2 joint + owner 三判据〕,DSR/SPA/RW 降为披露,认证移前向)
→ 施工图本体(含全部委托常量、臂表、命令、验收锚)。

授权范围(owner 2026-07-04 已批,越界=违规):DS-D2 dev 全窗回测可跑;账本 append 可写;spec 评测前
hash 冻结、评测中绝不改;DSR 预声明必 FAIL、照算照报;三分支 read 是诊断面,结果 present 给 owner 判,
不替 owner 选;push 不在授权内(commit 全落本地等 owner);sealed test 永不读;代码 commit 前 codex
前置门(修完全部 P0/P1/P2);FAIL 报 FAIL,不移球门。

关键验收锚(施工图 §6):ablation 里 A0 两臂必须字节复现 slot_frontier 已披露结果
(a0_eq_5 net +571,099 / a0_buf40_5 net +294,340,读 data/factor_research/slot_frontier_result.json
断言)。不匹配 = fail-closed 停下排查,严禁带着不一致继续跑或解读。

完成定义:spec+panel+ablation+tests 一个 feature commit(codex 过门后)→ 全窗跑完(setsid,~2-4h,
用 Monitor 轮询)→ 结果 doc(中文,含三分支落点表)→ plan.html DS-D2 done + SESSION_LOG → 中文汇报。
```

---

## §1 任务重定性(为什么这样跑)

| 原 D2(candidate doc) | 本图(amendment 后) |
|---|---|
| 目标:过四门晋级 | 目标:**裁决排名层存废**(四门中 DSR 预声明必 FAIL,照披露) |
| 对照 = frontier 已披露 A0 数字 | 对照 = **同 harness 重跑 A0 臂**(paired-t 需对齐的期收益序列)+ 字节锚验收 |
| placebo = D2 同宇宙随机 | + **A0 全宇宙随机 placebo**(补证据洞:A0 反转从未在书层过 placebo 检验;D1 ablation 模块 docstring 明文 DEFERRED,本图就是把那笔债还上) |
| 判据 = 四门 | 判据 = **选拔门**:胜自身暴露匹配随机 placebo(paired-t ≥ 2,eq_5 与 buf40_5 双容器 joint)+ 熊市累计 ≥ 0 + 股灾切片 + 净盈 > 0;MDD/DSR/SPA/RW 披露 |

**D2 相对 D1 的覆盖优势(预声明)**:D2 排名/过滤不用 accr(那是 D1 的两年报限制)→ 可排名窗从 **2015 初**起(dv_ratio 日频 2015 起、roe/gpm PIT 自首批报表起、quality 缺失走 dv_ratio 分支)→ **2015/2016 两大股灾切片可覆盖**(D1 空缺的),六切片齐。

## §2 交付物

| # | 文件 | 内容 |
|---|---|---|
| 1 | `scripts/factor_research/defensive_d2_spec.py` | 冻结常量 + `spec_hash()`(仿 `defensive_d1_spec.py` 逐模式) |
| 2 | `scripts/factor_research/defensive_d2_panel.py` | crowding panel 增列器(+`dv_ratio`/`roe`/`gpm`) |
| 3 | `scripts/factor_research/defensive_d2_ablation.py` | 11 臂 harness(仿 `defensive_d1_ablation.py`) |
| 4 | `tests/factor_research/test_defensive_d2_{spec,panel,ablation}.py` | 见 §7 |
| 5 | `data/factor_research/panel_train_val_d2.csv` + `defensive_d2_result.json` | artifacts(gitignored) |
| 6 | `docs/research/defensive-d2-results-YYYY-MM-DD.md` | 结果 doc(仿 D1 结构 + 三分支落点表) |
| 7 | plan.html DS-D2 done + SESSION_LOG + 修订记录 | SSoT 记账 |

## §3 T1 — `defensive_d2_spec.py`(冻结 spec;先建先 hash)

逐模式仿 `defensive_d1_spec.py`(纯常量、零 IO、dataclass frozen、`_canonical_payload` → SHA256,prose source 不入 hash)。committed 内容:

```python
CANDIDATE = "D2_reversal_on_defensive_universe"

# —— ranker:与 A0 字节一致,靠【复用】保证,不靠复制 ——
# D2 不重新实现 ranker;ablation 直接调 exit_veto_panel.build_ranker_table。
# spec 只镜像断言(tests 锁死):
RANKER_FACTORS = ("rev_1d", "max_5d", "turn_spike")   # == exit_veto_panel.RANKER_FACTORS
RANKER_IMPLEMENTATION = "exit_veto_panel.build_ranker_table"  # byte-identity by reuse

# —— 宇宙过滤(D2 相对 A0 的唯一改动;排名前二值 include/exclude,不入排名)——
@dataclass(frozen=True)
class D2UniverseFilter:
    vol_keep_max_quantile: float        # 0.60  仅 vol_20d 截面分位 ≤60% 进宇宙(剔最高波动 40%)
    max20d_lottery_exclude_quantile: float  # 0.90  max_20d 顶 decile 剔(彩票)
    dividend_min_percentile: float      # 0.50  dv_ratio ≥ 截面中位 …(分支一)
    roe_floor: float                    # 0.0   …或 roe>0 ∧ gpm 非底 decile(分支二)
    gpm_floor_quantile: float           # 0.10
    # 缺失规则(committed,fail-closed):dv_ratio 缺 → 分支一不通过;roe/gpm 任缺 → 分支二
    # 不通过;两分支皆不通过 → 剔除。vol_20d/max_20d 缺 → 剔除(不该发生,crowding panel 恒有)。

HORIZON = 5; REBALANCE_FREQ = 5                      # A0 parity(frontier 同值)
CONTAINERS = (("eq_5", 5, 100), ("buf40_5", 5, 8))   # 镜像 slot_frontier.FRONTIER 同名配置
PLACEBO_SEED = 20260704; PLACEBO_TOP_N = 5; BEATS_PLACEBO_T = 2.0
NEUTRALIZATION = ("industry_sw_l1", "log_circ_mv", "winsor_0.01", "min_obs_20")  # 全 panel 上做,过滤前

# —— 四门角色(amendment 2026-07-04)——
GATE_CALIBRATION = GateCalibration(
    dsr_threshold=0.95, ..., deflation_n=2418,        # D2 前账本 floor;照算
)
DSR_ROLE = "disclosure_only"   # amendment:预声明必 FAIL,照披露,不作 promotion 门
PROMOTION_GATES = ("beats_own_random_placebo_joint_t2", "bear_cum_nonneg",
                   "crash_slices_nonneg", "net_pnl_positive")   # 选拔门(owner 判)

# —— 预注册三分支(评测前入 hash;read = 诊断面,owner 判)——
DECISION_BRANCHES = (
    ("a", "d2_beats_own_placebo_joint AND owner_gates_improved -> ranking layer 入围,冻结送前向队列"),
    ("b", "NOT d2_beats_own_placebo_joint AND d2 容器仍呈 sleeve 风险画像 -> 排名层弃,sleeve-only"),
    ("c", "NOT a0_beats_own_placebo_joint -> 反转书层排名边被推翻,排名层死刑+定性修正;与(a)并存则如实披露矛盾"),
)
AMENDMENT = "qgr-certification-rearch-amendment-2026-07-04-dev-selection-forward-certification.md"
```

`spec_hash()` 打印落 SSoT + 结果 doc;**hash 之后到结果 doc 落盘之间,本模块任何字段不可改**。

## §4 T2 — `defensive_d2_panel.py`(增列器,非全量重建)

**输入** `data/factor_research/panel_train_val_crowding.csv`(A0 panel,5d 节奏,497 个 rebalance 日,已含 `vol_20d`/`max_20d`/ranker 三因子/`industry_l1`/`log_circ_mv`/`at_*_limit_d`)。**缺的只有 `dv_ratio`/`roe`/`gpm` 三列** —— 在 crowding panel 自己的日期×代码格点上补算(**不可**直接 merge `panel_train_val_defensive_d1.csv`:那是 20d 节奏,日期不对齐)。

**做法(复用,不发明)**:先读 `defensive_d1_panel.py`,复用它已测过的 PIT 原语 ——
- `dv_ratio`:同 `_ingest_day_d1` 的 `daily_basic` 读法(date=d 当日快照,PIT 无前视);
- `roe`/`gpm`:同 D1 的 fundamentals PIT 路径(`ann_date < d` 门,asof 语义,与 AF-003/D1 一字不差);
- 逐 date 补列 → left-merge 回 crowding panel → 写 `data/factor_research/panel_train_val_d2.csv`(原列全保留 + 3 新列;行数不变,merge 后逐行 assert)。
- **firewall**:全部日期过 `LockedSplit.assert_all_not_test` + 非 train_val 即 fail-closed(仿 D1 [1/6] 块)。
- **coverage 披露**:按年统计 dv_ratio 覆盖率 / quality(roe∧gpm)覆盖率 / 过滤后防御宇宙均值大小,进结果 doc(预期 2015 年 quality 覆盖爬坡,由 dv_ratio 分支兜住)。

预期运行 ~10–25 min(497 个日期的 daily_basic + fundamentals asof;远轻于 D1 的 20min 全量:无 beta/accr/statement 计算)。CLI 仿 D1:`--snapshot-root data/marketdata_pit --lock config/research/test_set_lock.json --out ...`。

## §5 T3 — `defensive_d2_ablation.py`(11 臂,单 bar source)

骨架逐块仿 `defensive_d1_ablation.py`(load→firewall→neutralize→tables→bar source→arms→ledger→DSR→SPA/RW→regime/crash→read),差异点:

1. **中性化 = slot_frontier 一字不差**(同因子列表〔ranker 3 因子 + crowding 轴〕、`MIN_OBS=20`、`WINSOR=0.01`),在**全 panel(过滤前)**上做 —— 这是 A0 字节锚的前提。
2. **两张 ranker 表**:
   ```python
   a0_table = xv.build_ranker_table(neut)                            # 全宇宙(= slot_frontier 路径)
   d2_table = xv.build_ranker_table(apply_d2_universe_filter(neut))  # 唯一差异 = 过滤
   ```
   `apply_d2_universe_filter` 按 §3 committed 阈值逐 date 在 RAW 列上筛行(仿 `defensive_d1_ranker.apply_exclusion_gates` 的 per-date quantile 写法 + §3 缺失规则)。**断言**:d2 宇宙 ⊆ a0 宇宙;d2 与 a0 的 rebalance 日期集相等且每日非空(空 date = fail-closed,报涨跌停/覆盖异常)。
3. **单 bar source**:universe = `xv.panel_universe(a0_table) + CSI300`(⊇ d2 宇宙),daily window 由 a0 日期 resolve(仿 D1 `_resolve_window`,allowed = train_val ∪ embargo)。
4. **11 臂**(横 = 容器 eq_5/buf40_5):

   | 臂 | scores | health | 意义 |
   |---|---|---|---|
   | `a0_{c}` | a0_table | a0 overrides | 全宇宙反转(**字节锚**) |
   | `d2_{c}` | d2_table | d2 overrides | 候选 |
   | `placebo_random_a0_{c}` | `random_top_n_scores(xv.universe_by_day(a0_table), seed, 5)` | 默认 | **证据洞臂** |
   | `placebo_random_d2_{c}` | 同上 on d2_table | 默认 | D2 选拔主对照 |
   | `placebo_sizematched_d2_{c}` | 仿 D1 `size_matched_scores`(d2_table) | 默认 | 防御过滤≠size 倾斜控制 |
   | `csi300_hold` | — | — | beta 门 |

   placebo 一律与所控容器**同 slots/cap 暴露匹配**(D1 codex P1 教训,直接沿用其实现)。
5. **账本**:`ledger_n_trials(family="ds.d2_reversal_on_defensive", round_label="ds-d2", ledger_date="2026-07-04", persist=smoke_periods is None)`,candidate arms = d2 两臂(a0/placebo 是 baseline/hurdle 不入债;a0 已在 `qgr.slot_frontier` family 计过)。
6. **DSR**:全臂照算(`deflated_sharpe_hac`,`lag=_overlap_lag(5,5)`),**披露**;`dsr_pass` 字段保留但 read 注明 `role=disclosure_only`(amendment)。
7. **read(诊断面)**:每 d2 容器 `vs_own_random_t / vs_sizematched_t / vs_a0_paired_t / bear_cum / crash_slices / net / MDD`;`a0_vs_own_random_t`(两容器 + joint bool);`d2_beats_own_placebo_joint`;按 §3 `DECISION_BRANCHES` 输出 `branch_read = {"a": bool, "b": bool, "c": bool}` + `note="DIAGNOSTIC — owner judges per amendment 2026-07-04"`。

## §6 验收锚(fail-closed,先于一切解读)

1. **A0 字节锚**:读 `data/factor_research/slot_frontier_result.json`,断言 `a0_eq_5` 与其 `eq_5` 臂、`a0_buf40_5` 与其 `buf40_5` 臂 **net P&L / MDD 逐字节相等**(disclosed:eq_5 **+571,099 / 56.0%**;buf40_5 **+294,340 / 31.4%**)。不等 = 中性化/窗口/引擎配置漂移,停下排查,严禁继续。
2. 守恒 `conservation_ok` 全臂 ✅;bar-read 窗口 `assert_all_not_test`。
3. smoke(`--smoke-periods 6`,persist off)全 11 臂跑通 + read 结构完整,才允许全窗。

## §7 T4 — tests(先写,RED→GREEN)

- `test_defensive_d2_spec.py`:hash 确定性;`RANKER_FACTORS == exit_veto_panel.RANKER_FACTORS`(drift 守卫);容器镜像 frontier 同名配置;阈值/seed/分支表等于 committed 值;`deflation_n==2418`;`DSR_ROLE=="disclosure_only"`。
- `test_defensive_d2_panel.py`:合成帧上过滤逻辑(quantile 边界;缺 dv_ratio 走 quality 分支;缺 quality 走 dv 分支;双缺剔除;vol/max 缺剔除);merge 行数守恒;缺列 KeyError fail-closed。
- `test_defensive_d2_ablation.py`:**关键测试 = no-op 过滤恒等**(阈值放空的 filter → `build_ranker_table` 输出与 A0 表 frame-equal,证「唯一差异=过滤」);d2⊆a0 宇宙断言触发;日期集不等 fail-closed 触发;branch_read 纯函数真值表;paired-t 已知值。

全套过后:`$PY -m pytest tests/factor_research -q`(基线 762 passed,只增不减)+ `ruff check` + mypy 绿 → **codex 前置门**(`codex review --uncommitted`;超时回退 `/code-review high`)修完 P0/P1/P2 → **一个 feature commit**。

## §8 运行(全窗)

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
export FEISHU_INTERACTIVE_ENABLED=false
# 1) panel 增列(~10-25min,前台可跑)
$PY -m scripts.factor_research.defensive_d2_panel --out data/factor_research/panel_train_val_d2.csv
# 2) smoke(必过再全窗;persist off)
$PY -m scripts.factor_research.defensive_d2_ablation --smoke-periods 6 --out /tmp/claude-d2-smoke.json
# 3) 全窗(11 臂 × 497 reb × 2484 daily;预算 ~2-4h;必须 setsid 全脱离,turn 边界不死)
mkdir -p logs && setsid nohup $PY -m scripts.factor_research.defensive_d2_ablation \
  --out data/factor_research/defensive_d2_result.json > logs/d2_ablation_run.log 2>&1 &
# 进度:structlog INFO + log 里 [N/6] 块标(print 块缓冲,重定向下不实时 flush —— D1 已知坑,
# 用 Monitor 轮询 logs/d2_ablation_run.log + 结尾 DONE echo 判完成,别傻等 stdout)
```

## §9 结果 doc + 记账(完成定义)

`docs/research/defensive-d2-results-YYYY-MM-DD.md` 仿 D1 结构:装置 → 逐臂主表(11 臂:net/MDD/暴露/DSR/熊市累计/关键 paired-t)→ **四大对照读数**(① a0 vs a0-random〔证据洞裁决〕② d2 vs d2-random〔选拔主门〕③ d2 vs a0〔宇宙因果:熊市累计从 −0.22 改善?六股灾切片?MDD?〕④ d2 vs sizematched)→ 选拔门/披露门落点表 → **三分支落点**(a/b/c 布尔 + 机制解读)→ 诚实 caveat(train_val only;DSR 预声明 FAIL 照报;coverage)。**FAIL 报 FAIL;不因结果改 spec/阈值/分支。** 随后:plan.html DS-D2 done(commit hash 回填)+ SESSION_LOG 一条 + docs commit。**push 不做**(owner-gated)。

## §10 红线速查(违反即停)

train_val only(sealed test 物理不可达)· spec hash 后绝不改 · 账本只增 · size/行业中性化删最小 30%(panel 已内建)· 研究零 LLM · PIT 存档禁重下(本任务零摄取)· push/摄取/look-once owner-gated · codex 代码前置门 · FAIL 报 FAIL · 报告中文/代码 commit 英文。
