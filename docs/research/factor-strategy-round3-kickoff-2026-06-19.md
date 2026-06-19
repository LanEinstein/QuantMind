# 第 3 轮 kickoff prompt(新 session 直接粘贴 / 执行 R3-1→R3-6)

> 把下面「═══」之间的全文粘贴进新 session 即可。内容自包含;深层细节指向已落盘的方案/诊断/代码。

═══════════════════════════════════════════════════════════════════════

你是接手 QuantMind「量化选股策略研究 第 3 轮」的 Claude,无上文对话。
任务 = 执行 R3-1 → R3-6:**补 2 个零成本新正交 alpha 源**(盈余惊喜 SUE + 应计/资产增长),
用 R2-2 那套严格验证后重跑搜索 → 冻结 → 判定,**如实报 PASS/FAIL**。

──────────────────────────────────────────────────────────────────────
0. 为什么是这一轮(背景,最重要)
──────────────────────────────────────────────────────────────────────
round-2(benchmark-relative 增强指数)在锁定测试集上 **FAIL**:净 +21.58% / 夏普 1.80 /
回撤 6.11%,但 **超额 −0.26%**(CSI300 +21.84%)→ 四门 3 过 1 不过(beats_csi300 以 0.26pp 差)。
**诊断**:构造已对(beta≈1 跟住指数、size 漂移守住 −0.064),**缺的是正超额 alpha 源**。硬证据 =
R2-3 市场中性参考臂(alpha 上界)只有夏普 0.30 → 现有 carry 因子(反转/低波/价值/换手/流动性 +
roe/gpm/np_yoy/rev_yoy)alpha 本就温和,扣成本后净超额≈0。**修不出 alpha,只能加原料。**

owner 已拍板走 **路径 A**:补 2 个**零成本**新正交因子族,用 R2-2 协议验证,重跑全链。

──────────────────────────────────────────────────────────────────────
1. 开工前必读(按序)
──────────────────────────────────────────────────────────────────────
1. `docs/research/factor-strategy-round3-plan-2026-06-19.md` —— **本轮方案 SSoT**(因子定义/数据/阶段/红线)。冲突以它为准。
2. `docs/research/factor-strategy-round2-result-2026-06-19.md` —— round-2 FAIL 报告(失效分析 + 下一轮方向)。
3. `docs/research/factor-strategy-round2-r2-2-factor-diagnostics-2026-06-18.md` —— **R2-2 验证协议**(IC/中性化/共线/vintage 审计;|t|≥3 纳入门;carry 集来历)。
4. `docs/research/factor-strategy-round2-test-reuse-decision-2026-06-19.md` —— 测试集复用决策 + 4 条诚实保障。
5. memory:`MEMORY.md` + `project-factor-strategy-research-2026-06-16.md`(全 round 细节)+ `reference-tushare-entitlements-2026-06-19.md`(**Tushare 权限实测地图**)。
6. 已建代码(全在 `scripts/factor_research/`,离线/确定性/PIT/纯量化/LLM 零参与/import 隔离):
   - 摄取:`ingest_round2_data.py`(R2-1,字节存档+幂等+coverage)+ `TushareClient`(加端点处)。
   - 因子:`fundamentals_pit.py`(ann_date<d vintage 选值)/`industry_pit.py`/`neutralize.py`/`factor_lib.py`(R2 registry)/`build_factor_panel.build_panel_r2`/`r2_factor_diagnostics.py`/`factor_ic_study.py`。
   - 构造+评估+判定:`benchmark_relative.py` + `exposure_constraints.py`(constituent_only 已胜出)/`round2_search.py`+`config/research/round2_experiment_manifest.json`/`walk_forward_eval.py`/`full_engine_crosscheck.py`/`build_factor_panel.build_test_panel_r2`/`r2_locked_test.py`。

──────────────────────────────────────────────────────────────────────
2. Tushare 权限现状(2026-06-19 已实测,**两个新因子全部 ¥0 可取**)
──────────────────────────────────────────────────────────────────────
现有 `TUSHARE_TOKEN`(~/.bashrc,≈5000+ 积分档)**已确认能取**(广度探针,见 memory + CLAUDE.md §2.5):
- 财务三表 vip 全市场:`cashflow_vip` / `balancesheet_vip` / `income_vip`(✅,应计/资产增长用)。
- 已在用:`fina_indicator_vip`(✅,盈余惊喜用,零新数据)。
- 副产物(本轮可顺带,未来轮主用):`namechange`(曾用名/ST 史 → **PIT ST 排除**,补 build_factor_panel 已知缺口)/`moneyflow`/`hk_hold`/`margin_detail`/`forecast_vip`/`express_vip`。
- **唯一不可用**:`report_rc`(券商分析师预测,需正式 **8000 积分**)→ **本轮不碰**。

──────────────────────────────────────────────────────────────────────
3. 两个新因子(定义 + 数据字段 + PIT 陷阱)
──────────────────────────────────────────────────────────────────────
**① 盈余惊喜 / PEAD(SUE)— 零新数据,用已有 `fina_indicator_vip`**
- 公式:`SUE_t = (Eq_t − Eq_{t−4}) / σ(ΔEq, 过去 ~8 季)`,`Eq` = **单季**归母净利。
- **PIT 陷阱(必处理)**:① A 股财报多为 **YTD 累计**口径 → 单季 `Eq = YTD_t − YTD_{t−1}`(Q1 = YTD);
  ② SUE 需**过去 ~8 季的 as-known 单季盈利序列** → `fundamentals_pit` 现在只返回单 period as-of 值,
  须新增类似 `history(code, decision_date, n_quarters)` 返回 ann_date<d gated 的季度序列;
  ③ 全程 ann_date<d(非 end_date),严禁用未公告季度。
- 方向 prior:`attractive_high=True`(高 SUE 好)。机制:盈余公告后漂移(反应不足)。

**② 应计(Sloan)+ 资产增长 — `cashflow/balancesheet/income_vip`,¥0**
- 应计(cash-flow 法,更稳):`ACCR = (净利 − 经营现金流) / 期初期末平均总资产`。
  字段:净利 `income_vip.n_income`(或 fina);经营现金流 `cashflow_vip.n_cashflow_act`;总资产 `balancesheet_vip.total_assets`。
  方向 prior:`attractive_high=False`(**低应计好** = 盈余质量高)。
- 资产增长:`AG = (总资产_t − 总资产_{t−4}) / 总资产_{t−4}`。方向 prior:`attractive_high=False`(**低增长好**)。
- **PIT 陷阱**:① 净利同样 YTD→单季/或用年度;② 平均总资产需期初(t−4 或上期)+ 期末两期取值,跨期都要 ann_date<d gated;③ 缺报→None fail-closed。
- 互相关需测(应计↔资产增长可能算一根「资产负债表质量」轴)。

> 三个新因子写进 `factor_lib.py` R2 registry(机制注册 + 方向 prior;round-1+R2 既有因子 byte-unchanged);
> growth_premium 那类 governance enum 门仍 fail-closed until amendment(同 R2-2,**不动 governance**)。

──────────────────────────────────────────────────────────────────────
4. 本次任务 = R3-1 → R3-6(每码模块:TDD → 门禁 → codex → feature commit)
──────────────────────────────────────────────────────────────────────
**R3-1 PIT 摄取扩充(owner-gated 重活;`--dry-run` 先验)**
- `TushareClient` 加 `cashflow_vip`/`balancesheet_vip`/`income_vip`(period 入参,只读;照 `fina_indicator_vip` 加法)。
- `ingest_round2_data.py` 加三表摄取:**仿 `fina_indicator_vip` 整段**(字节存档 + sha256 + 幂等续传 + 限速 RateLimiter + per-period coverage manifest,keyed on `tradable_asof(period)` 幸存无偏)。
- 真摄取前 `--dry-run` + redline 扫描;退市股 roster dtype=str(R2-1 已知坑);IPv4-only 出站。
- (可选,顺带)`namechange` 接入 → PIT ST 排除,补 build_factor_panel 缺口。

**R3-2 因子库扩 + PIT join**
- `fundamentals_pit.py`:暴露三表行项(ann_date<d vintage gating drop-in);**新增季度序列读取** `history(...)` 供 SUE。
- `factor_lib.py` R2 registry:加 `sue`/`accr`/`asset_growth`(机制 + 方向 prior)。
- `build_factor_panel.build_panel_r2`:`compute_fundamental_factors`(或新 helper)算三因子并出新列;`build_test_panel_r2` 同步(R3-6 用)。

**R3-3 R2-2 式诚实验证**
- `r2_factor_diagnostics.py` / `factor_ic_study.py` 扩:IC(原始+中性化)+ 共线性 vs carry 簇 + vintage 审计。
- **纳入门 = 中性化后 |t|≥3 + 与现有 carry 低共线 + 机制注册**;弱则**如实丢**(同 R2-2 丢 mom/trend/dist_high)。
- 产出 `docs/research/factor-strategy-round3-r3-factor-diagnostics-*.md`(诚实诊断)+ 更新 carry 集。

**R3-4 重跑搜索(扩充 carry 集)**
- 新 `config/research/round3_experiment_manifest.json`(carry 集加新因子 → `carry_factor_order` 扩;**N 重新声明=累计 deflation**;预声明全 DoF)。
- `round2_search.py` 复用(改 carry 集来源 + 新 manifest 路径;`constituent_only` 已胜出,可设默认或保 4 约束搜)。**注意**:`benchmark_relative.CARRY_FACTORS` 是全局消费点,扩它要确认 composite/中性化/test-panel 同步且 round-2 结果可复现(建议用 round-3 独立 carry 常量,不破 round-2)。
- 真跑(~20min)→ 选唯一策略 + DSR/PBO/SPA/哨兵全披露 → 诊断 doc。

**R3-5 引擎交叉确认 + git 冻结**
- `full_engine_crosscheck.py` 成本压力(摩擦单调 ✓;rqalpha UNAVAILABLE 诚实记录)。
- **git 冻结新策略**(填 `r2_locked_test.FROZEN_R2_*` 或新 round3 变体,3dp 钉死 + load 复验 fail-closed;**读 test 之前**)。记 commit hash。

**R3-6 判定(owner 选路径)**
- **诚实抉择(owner 拍)**:本测试集已被评 2 次 → **优先「冻结后等真前向窗口(2026-06-12+)第 3 次判定」**;若 owner 同意既有测试集第 3 次评测,报告须披露「**第 3 次评测,跨策略多重检验 3 次**」。
- 复用 `build_test_panel_r2` + `r2_locked_test`(firewall→读 test 一次→四门)→ `docs/research/factor-strategy-round3-result-*.md`。
- **四门不放宽**(净>0 / 超额≥0 / MDD≤15% / 夏普≥0.5);IR/TE 仅披露;补不出正超额**如实 FAIL + 下一轮方向**。

──────────────────────────────────────────────────────────────────────
5. 红线(全继承,违反即停)
──────────────────────────────────────────────────────────────────────
1. **测试集神圣**:开发期(R3-1..R3-5)零碰 test;唯一读 test = R3-6 认可路径,策略须先 git 冻结;基准侧开发期 <test_start;一切日期经 `LockedSplit.assert_not_test`。
2. **PIT/幸存无偏/无前视**:三表用 ann_date<d(非 end_date)+ vintage 审计;SUE 用已公告季度 + 单季差分;缺报 fail-closed→None。
3. **数据源仅 Tushare 官方 SDK**;`TUSHARE_TOKEN` 不入 LLM/飞书池;IPv4-only(`local_address="0.0.0.0"`);**不引 akshare 进研究 PIT 路径**。
4. **LLM 不进数值策略**(因子/打分/中性化/搜索全确定性);LLM 只用于文献。
5. **离线 only**;不碰 simulation_auto;不接线上 FACTOR_WEIGHTS 不经 owner gate;永禁真实下单。
6. **诚实**:无 data-snooping;DSR/PBO/SPA/哨兵全报;开发证据≠判定;四门不放宽;FAIL 报 FAIL。
7. **codex 前置门**:含代码任务 commit 前过 `codex review --uncommitted </dev/null`,修完 P0/P1/P2;**撞额度回退 `/code-review high`**(别跳别等)。docs/配置/记账 commit 豁免。import 隔离:`scripts/factor_research` 可 import `backend.{marketdata_snapshot,backtest,strategy_evolution}`,`backend.data.*` 须 per-line `# noqa: TID251`,**严禁** `backend.{llm,agents,mirofish}`。
8. **git**:每模块一 feature commit;**push 受 owner auth 门控**(commit 落本地);`M CLAUDE.md` 若 owner 在途**别碰别 stage**(除非 owner 明确让你改)。

──────────────────────────────────────────────────────────────────────
6. 环境 + 命令
──────────────────────────────────────────────────────────────────────
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
跑研究模块:`FEISHU_INTERACTIVE_ENABLED=false $PY -m scripts.factor_research.<mod>`
跑测试:`FEISHU_INTERACTIVE_ENABLED=false $PY -m pytest tests/factor_research/ -q`(当前 219 绿,新增须保持全绿)
门禁:`$PY -m ruff check scripts/factor_research tests/factor_research` / `$PY -m ruff format <仅你改的文件>`(别 format 整目录) / `$PY -m mypy --strict scripts/factor_research` / `bash scripts/redline-check.sh`
codex:`codex review --uncommitted </dev/null`(撞额度回退 `/code-review high`;codex 后台易 stdin deadlock,前台跑 + `</dev/null`)
rqalpha venv(勿重装):`/home/ps/rqalpha-smoke-venv`
Tushare 探针(IPv4 shim + `os.environ["TUSHARE_TOKEN"]` 绝不打印)模板见 memory `reference-tushare-entitlements-2026-06-19`。

──────────────────────────────────────────────────────────────────────
7. 工作流(强制)
──────────────────────────────────────────────────────────────────────
先进 Plan 模式列子任务(R3-1..R3-6 各模块)→ ExitPlanMode 批准后:
每模块 TDD(先写失败测试)→ 实现 → 门禁全绿 → codex(撞额度回退 /code-review high)修完 P0/P1/P2 → feature commit。
**R3-1 真摄取是 owner-gated 重活**:`--dry-run` 先验,等 owner「开」再真跑。
完成后:改 round-3 方案进度段 + memory(MEMORY.md + project 文件)+ 中文报告 + 一句话指下一步。

──────────────────────────────────────────────────────────────────────
8. 完成定义
──────────────────────────────────────────────────────────────────────
R3-1 三表 PIT 摄取(字节+coverage+幸存无偏);R3-2 三因子 + PIT join(SUE 季度序列 + 单季差分正确);
R3-3 R2-2 式验证选出 carry 增补集(弱则诚实丢);R3-4 重跑搜索选唯一策略(全披露);
R3-5 成本压力 + git 冻结;R3-6 判定(owner 选前向/既有,四门 + 诚实披露报告);
全程 TDD+门禁+codex 绿;feature commit 落本地;记账 + 一句话指下一步。
**owner 要真指数超额,补不出仍如实 FAIL + 下一轮方向(如分析师上修需充 8000 积分,或资金流/事件因子)。**

═══════════════════════════════════════════════════════════════════════
