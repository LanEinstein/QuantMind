# 自进化闭环设计 dossier — rqalpha 权威回测引擎闭合量化参数进化环

> **日期**:2026-06-14
> **作者**:Claude Opus 4.8 (1M context)
> **状态**:**设计草案第一稿(待 owner 过目)** — 本文档不含生产代码;按协议(amendment-first + plan-first),owner 逐项审定后再落 amendment + plan.html 正式任务 + TDD。
> **触发**:#86 战略转向 — owner 拍板「先把含自进化在内的未完成功能开发好,打磨到可稳定盈利再投实盘」;识破当前自进化 22:00 cron 实际空转(DEGRADED)。
> **权威指针**:`docs/plan.html` SESSION_LOG #86 next。
> **设计依据**:R-001/R-002/AB-001..AB-008 已建代码(本文 §1 代码级盘点)+ `P2-2-amendment-2026-06-12`(sim 客观晋升)+ R0 §3(PIT 可复现)/§8(自进化人工 gate)。
>
> **⚠️ 修订横幅(2026-06-14 第二轮,owner 指示「借鉴 OSS 顶尖 harness + 与 codex 一同审查」)**:本 dossier 经 **3 路 OSS 调研(回测引擎 / 反过拟合搜索 / 差分与可复现工程)+ codex 对抗审查** 后**重大升级**。**§8(OSS 借鉴 + codex 批判 → 设计升级)+ §9(升级后决策与路线图)为最新权威,覆盖前文 §3.1/§3.4/§3.6/§5/§6 的对应判断**。前文保留作推理链与上下文;**凡冲突,以 §8/§9 为准**。对抗审查由 **codex-oracle agent + codex CLI second-opinion 双路**给出(codex CLI 首次 auth/broker busy,后台重试成功返回完整 JSON 批判),两路高度印证。

---

## 0. 摘要 + 诚实前提(先读)

**目标**:让自进化的「量化参数进化环」从空转变为真闭合 —— `发现候选参数 → rqalpha + 自有引擎回测 → 9 门客观判定 → 人工 pin → 重启 → 生效`,并能在模拟盘上反复打磨,直到出现**可测的、经反过拟合检验的**稳定盈利证据,owner 才有信心投真金白银到实盘。

**诚实前提(必须先讲清,贯穿全文)**:
1. **工程可闭合,盈利不保证**。本设计能把闭环的每一段焊死、可审计、可复现。但「是否存在真实 edge」是**经验性**问题,取决于策略本身,不是任何工程能凭空制造的。
2. **反过拟合门只减假阳性,不造 alpha**。9 门里的 deflated Sharpe(对累计试验次数去膨胀)、purged CV、bootstrap CI、rqalpha 差分 oracle、anti-gaming,全部作用是**降低「看起来好其实是运气/过拟合」的误判率**。它们让搜索诚实,不让搜索更聪明。
3. **机器只做诚实搜索**。进化引擎能做的=在白名单参数空间里系统地试、严格地验、如实地报(绝大多数夜晚的正确结果是「没有候选通过,不晋升」)。它**不能**保证找到盈利参数;它能保证的是**不自欺**——不会把噪声当 alpha 推给 owner。
4. **人工 gate 永不拆**。即使在 sim 域,本设计 P1 阶段坚持「客观 9 门判 PASS → 飞书通知 → 人工 pin(amendment + 重启)才生效」(redline「进化应用人工 gate」),比 AB-003 已建的 sim-auto-activation 更保守 —— 见 §6 决策点 ①。

> 一句话:**本 dossier 交付的是一台诚实的搜索机器和它的安全外壳,不是一张盈利保证书。**

---

## 1. 现状代码级盘点 —— 70% 已建 / 30% 缺失

(全部经本 session 直接读源码核实,文件:行号可点。)

### 1.1 已建且可用(自进化 ~70%)

| 组件 | 文件 | 作用 | 状态 |
|------|------|------|------|
| `LiveArtifactRegistry` | `backend/strategy_evolution/live_artifact_registry.py` | boot 从不可变 lockfile 载批准哈希集;实时路径拒未 pin;5 类 ArtifactKind;完全不可变 | ✅ R-001 |
| 生命周期 | `lifecycle.py` | 5 态 + allowlist 转移 + ACTIVE 必须 registry pin + append-only ledger | ✅ R-002 |
| 差分 oracle(纯函数侧) | `backtest_oracle.py:153 compare_equity_curves` / `:242 run_differential_check` | 共享日 \|diff\| ≤25bps + 散点 ≤5% 才 CONSISTENT;oracle 失败→ORACLE_UNAVAILABLE(非 pass、不抛) | ✅ R-002 |
| 反过拟合 | `anti_overfit.py` | purged k-fold + deflated Sharpe(Bailey-de Prado)+ DSR≥0.95 门 | ✅ R-002 |
| ExperimentRegistry | `experiment_registry.py` | append-only(含失败实验)+ content-addressed experiment_id + `count_trials(family)` 供多重检验校正 | ✅ AB-001 |
| **9 门客观晋升判官** | `objective_promotion.py:215 evaluate_promotion` | 纯函数,消费 `PromotionInputs` → `PromotionDecision`;分层窗口/bootstrap CI/DSR/8 门不劣化/anti-gaming/oracle 硬拒;**零 LLM** | ✅ AB-002 |
| PromotionIntent + activation 骨架 | `promotion_intent.py` / `activation.py` | append-only intent + content-addressed `ActivationManifest` + 原子 next_boot.lock + boot consume-once + 失败自动回退 | ✅ AB-003(**param 路径留空,见 §1.2**) |
| auto-demote | `demotion.py` | challenger vs incumbent counterfactual;相对超额 ≤-150bps 降级 | ✅ AB-004 |
| 冻结集 + 白名单 | `evolvable_params.py` | `FROZEN_NON_EVOLVABLE`(命中即 raise)+ `EVOLVABLE_WHITELIST`(15 参数,不可变 clamp + safety-adjacent 单调) | ✅ AB-005 |
| prompt 政策化 | `prompt_policy.py` | 禁类 lint + 字节捕获;GEPA 产物走同一晋升门 | ✅ AB-006 |
| harsh fill(撮合零件) | `harsh_fill_model.py:93 simulate_harsh_fill` | **单笔**严苛撮合(涨跌停不成交/ADV 5% 容量/价格冲击/次日延迟有利缺口钳/harsh-or-equal);零 broker import | ✅ AB-007 |
| EvolutionDispatcher(LLM 4 lane) | `services/evolution_dispatcher.py` | 4 lane:prompt/rag/risk_proposal/exemplar;走 ShadowChain | ✅ X-008 骨架 |
| ShadowChain | `services/shadow_chain.py` | 45 日 challenger 验证;`evaluate_challenger` 8 门;`ChallengerReplayer` Protocol | ✅ X-007(replayer 仅 Protocol) |
| 22:00 cron + sub-budget | `main.py:2835 _evolution_shadow_run_callback` | `reserve_evolution_run` 双层预算门已接 | ✅ AB-007 |
| Reflexion + exemplars | `reflexion.py` | 交易员行为进化离线半边;只 propose 永不自动应用 | ✅ T-004 |

### 1.2 缺失(让 22:00 cron 空转 DEGRADED 的 30%)

逐一对应本 dossier 后续 §3 的 6 个解法:

1. **rqalpha 回测引擎**:`backtest_oracle.py:341 RqalphaBacktestRunner.run` 恒抛 `OracleUnavailableError`(rqalpha 未装 + run harness 留给「Phase AB」未实现)→ 差分恒 `ORACLE_UNAVAILABLE`。→ **§3.1 + §3.2 + §3.3**
2. **确定性策略回测 harness(最大缺口,本 dossier 核心发现)**:`ChallengerReplayer`(`shadow_chain.py:361`)仅 Protocol;**没有任何模块**把「给定参数 + PIT 窗口 → 逐日重放确定性策略 → 产出权益曲线 + daily PnL + AcceptanceReport + anti-gaming 统计」串起来。现存的只是零件(`simulate_harsh_fill` 单笔 / `compare_equity_curves` 差分 / `evaluate_promotion` 判官),缺把它们串成引擎的那段。→ **§3.4**
3. **EvolutionDispatcher boot 接线**:`main.py:2864` 找 `app.state.evolution_dispatcher`,但 lifespan 启动期**从不设置**它 → 恒 `skipped_dispatcher_unwired`;即便挂上也「no tasks」(`:2883`)。→ **§3.5**
4. **task producer**:没有任何组件决定「每晚评估哪些候选参数」。→ **§3.5**
5. **param 运行时落地**:`activation.py:168` staging 拒 param-bearing manifest + `:288` apply 再拒(防御纵深);lockfile `new_lock`(`:314`)只写 `approved` 不写 `params`;**没有任何 runtime 消费端**读 `manifest.params`。15 个白名单参数当前散落在各模块的 dataclass 默认值 / YAML(`IntradayTriggerConfig`、`add_position` config、`intraday_calibration` tiers、`config/candidate_weights`、`config/allocation_policy.yaml`、`config/slot_rotation_policy.yaml`)。→ **§3.6**
6. **(并行)AC-003 value_score 真实数据未喂**:`line1_runner` value_score=None → 全 SHORT_TERM。属 P2,与本环正交。→ **§5 P2**

---

## 2. 核心架构判断:量化参数环 ≠ LLM artifact 环(两条独立轨道)

这是整个设计的地基,必须先讲清,否则会错接到现有 4 lane 上。

**现有 EvolutionDispatcher 的 4 lane(prompt/rag/risk_proposal/exemplar)全是 LLM artifact lane**:它们进化的是 prompt 文本 / RAG 文档 / 风险提案文本 / exemplar schema,验证方式是 `ShadowChain` 把 45 日决策轨迹**重新喂过 LLM**产出 AcceptanceReport。这条轨道天生**非确定性**(LLM temp、路由),决策回放是独立难题 → 路线图正确地把它放在 **P3**。

**P1 的量化参数环是另一条轨道,确定性**:
- 进化对象 = `EVOLVABLE_WHITELIST` 的 15 个数值旋钮(Line-2 止损止盈系数 / selector 权重 / value 槽配额 / theme tier 权重)。
- 每个候选参数集 = 一个**完整确定性策略变体**,可**严格回测**。
- 判官不是 LLM ShadowChain,而是已建的 **`ObjectivePromotionEngine.evaluate_promotion`(9 门,纯函数)** —— 它已经预留了 `ExperimentKind.THRESHOLD_PARAM` 档(15 交易日 / 30 样本双门槛,`objective_promotion.py:68`)。

| | LLM artifact 轨道(P3) | 量化参数轨道(P1,本 dossier) |
|---|---|---|
| 进化对象 | prompt/rag/risk-text/exemplar | 15 个白名单数值参数 |
| 验证 | ShadowChain 重放决策过 LLM | 确定性回测(自有 harness + rqalpha oracle) |
| 判官 | `evaluate_challenger`(8 门) | `evaluate_promotion`(9 门,含 oracle) |
| 确定性 | 否(决策回放难题) | 是(同输入 → bit-identical) |
| 成本瓶颈 | LLM token(¥100 cap) | 计算/wall-clock(Line-2 路径零 LLM) |

**结论**:P1 要建的不是「给现有 4 lane 补 replayer」,而是**新增第 5 条确定性量化参数 lane**(+ 它专属的回测 harness + task producer),直接喂 `evaluate_promotion`。现有 4 lane 原样不动,留给 P3。

> 推论(影响 §3.4 模块归属):量化参数 lane 的回测 harness 需要 import 确定性策略模块(`candidate_selector`/`slot_portfolio`/`monitoring`)+ `harsh_fill_model` + PIT 数据;而 `backend/strategy_evolution/` 有严格 import 隔离(禁 `backend.{api,broker,risk,llm,agents,mirofish,data}`,可用仅 `knowledge_graph`+`marketdata_snapshot`+rqalpha)。**因此回测 harness 不能放在 strategy_evolution 里** —— 见 §3.4。

---

## 3. 六个必答点逐一解决

### 3.1 rqalpha env 隔离 + 子进程 harness

**事实**:rqalpha 6.1.5 已在隔离 venv `/home/ps/rqalpha-smoke-venv` 验证可装可 import(自取 numpy 2.4.6 / pandas 2.3.3,**高于主 env**)。主 env 的 5774 测试依赖现有 numpy/pandas 版本,**绝不能**让 rqalpha 拖动主 env 依赖。

**方案:子进程调用隔离 venv,主 env 零污染。**

```
RqalphaBacktestRunner.run(spec: BacktestSpec)        [主 env, async]
  └─ 写 spec + PIT 数据导出(§3.2)到 temp 工作目录
  └─ asyncio.create_subprocess_exec(
         QUANTMIND_RQALPHA_VENV_PYTHON,   # 默认 /home/ps/rqalpha-smoke-venv/bin/python
         "-m", "<rqalpha_runner_entry>",  # 独立脚本,只在 venv 里跑
         "--spec", spec.json, "--out", result.json,
         timeout=<bounded>, cwd=<temp>)
  └─ 读 result.json → 解析成 BacktestRunResult(strategy_hash 自校验)
  └─ 任一失败(超时/非零退出/解析失败/哈希不符)→ OracleUnavailableError
        → run_differential_check 已有逻辑 → ORACLE_UNAVAILABLE(fail-closed,非 pass)
```

**关键约束**:
- **子进程入口脚本不 import 任何 `backend.*`**(它在 venv 里跑,venv 没有 backend 依赖)。它只读自包含的 spec.json + PIT 数据导出文件(§3.2),import rqalpha,跑回测,写 result.json。纯单向数据交换(JSON 文件),无共享内存。
- **venv 路径走 env 配置**(`QUANTMIND_RQALPHA_VENV_PYTHON`),缺省指向已验证路径;缺失/不可执行 → `OracleUnavailableError`(沿用 fail-closed)。owner 亲自装 venv/设 env(红线:env/启动 owner 亲为)。
- **超时 + 资源界**:子进程硬超时(回测一个 45 日窗口应秒级~分钟级);超时 kill → UNAVAILABLE。每晚候选数有上界(§3.5)。

**红线影响(R-002)**:
- 现有 redline `[R-002]` 把字符串 `rqalpha` 限制在 `backtest_oracle.py` 一个文件。新增子进程入口脚本会引入第二处 `rqalpha`。→ **需 amendment 把 R-002 redline 从「单文件」放宽为「显式 allowlist」**(`backtest_oracle.py` + `scripts/rqalpha_runner/*` 或 `backend/backtest/rqalpha_entry/*`),并保持 AST 契约测试「无主 env 模块 import 该入口脚本」。
- rqalpha **永不入实时路径**不变(仅 test-time oracle/replay;子进程只在 22:00 cron + 手动 replay 触发)。**永不 vendor、不抄代码**不变(Apache 2.0 非商用)。

**取舍**:子进程 = 唯一可行(版本冲突排除同 env import);代价是 JSON 序列化 + 进程启动开销(可接受,非高频)。

---

### 3.2 数据 bundle + PIT 复现红线

**问题**:rqalpha 回测要喂行情数据。数据从哪来,决定差分是否有意义 + 是否守 PIT 红线(R0 §3:回测/shadow/实时三路径**同源**,存原始字节 + checksum)。

**两个方案**:

**Option A — pin rqalpha 官方 bundle + checksum**
- rqalpha 自带 `rqalpha download-bundle`(落 `~/.rqalpha/bundle`)。pin 一个 bundle 版本 + 记 checksum。
- ✅ 简单,用 rqalpha 原生数据通路。
- ❌ **致命**:rqalpha bundle 是**米筐自己的数据**,与 QuantMind 实盘/shadow 用的 PIT 快照(`MarketDataSnapshot` / `kline_daily`)**不同源**。两引擎差分时,差异可能纯粹来自「读了不同的价格数据」,而非「执行逻辑不同」→ **差分失去意义**(恒 DIVERGENT 或假 CONSISTENT),且**违反 PIT 同源红线**。

**Option B — 导出 PIT 快照 → rqalpha 自定义数据源(推荐)**
- 主 env 侧从 `kline_daily` / `MarketDataSnapshot`(已是 PIT-pin + checksum + 可 replay)读出回测窗口的 bars + 复权因子,导出成**自包含、content-addressed 的数据文件**(parquet/csv + manifest 含 sha256 + 复权因子 artifact pin)到 temp。
- 子进程(venv)里的 rqalpha 用一个**自定义 `AbstractDataSource`**(代码在入口脚本里,读 parquet,**不 import backend**)喂这份导出数据。
- 导出文件的 sha256 记入 `DifferentialReport` / 实验记录 → **完全可 replay**(同 signal_id 复现同输入)。
- ✅ 两引擎看**bit-exact 同一份 PIT 数据** → 差分 = 真执行逻辑差异;PIT 同源红线守住。
- ❌ 工程量更大(实现 rqalpha 数据源接口 + 导出器)。

**推荐:Option B**。PIT 可复现是 R0 硬红线(「严禁 hash-only」「三路径同源」),Option A 直接违反。
**落地节奏**:P1a 可先用一份**固定的小样本 PIT 导出**把子进程 harness + 摩擦 Mod(§3.3)调通并跑过校准门;**在量化参数环被信任用于晋升之前**,数据通路必须是 Option B(PIT 导出 + checksum + pin)。不允许 Option A 的 bundle 进入晋升判定路径。

> 决策点 ②(§6):确认 Option B + P1a 先用固定 PIT 导出调通的节奏。

---

### 3.3 摩擦 Mod 对齐 ≤25bps

**MockBroker 摩擦(CLAUDE.md §2.7,`config/broker.yaml` 单一真相源)**:
佣金 commission + 卖出印花税 stamp_tax + 分板块滑点 slippage_by_board(主板 1.5 / 创业 1.5 / 科创 3.5 / ETF 1.5 bp)+ 深市过户费 transfer_fee 0.00341% 双边 + min_commission;T+1;涨跌停 at-fill recheck(`price_limit_violation_at_fill`);ALL_OR_NONE。

**rqalpha 侧对齐(子进程 run config 的 Mod 集)**:

| MockBroker 摩擦 | rqalpha 对齐手段 | 备注 |
|---|---|---|
| 佣金 + min_commission | `mod_sys_simulation` 自定义 commission(或 `commission_multiplier`) | 按 broker.yaml 数值映射 |
| 卖出印花税 | A 股印花税卖出单边 tax | 对齐税率 |
| 分板块滑点 1.5/1.5/3.5/1.5 bp | 自定义 slippage decorator(按 order_book_id 分板块) | rqalpha 默认单一滑点模型 → 需自定义;**或**接受板块间差落在 25bps 容差内 |
| 深市过户费 0.00341% 双边 | rqalpha 默认不单列过户费 → 折进 commission Mod 或自定义 | |
| T+1 | rqalpha A 股 stock 账户原生 T+1 | 原生支持 |
| 涨跌停不成交 | rqalpha matching `price_limit` | 对齐 at-fill 拒成交语义 |
| ALL_OR_NONE | rqalpha 默认部分成交 → 自定义撮合或后处理 | 需校验 |

**校准门(P1a 一次性 acceptance test,非每跑)**:
取一个**固定已知策略**(如「等权买入固定 N 只持有 45 日」)同时跑 MockBroker shadow + rqalpha,断言 `compare_equity_curves` 返回 **CONSISTENT**(≤25bps over ≥95% 天)。不达标 → 调 Mod config 直到对齐;若某项系统性摩擦差实在压不进 25bps,要么收紧 Mod,要么**书面记录原因**并依赖 AB-002 gate 6 注释的既定立场:**oracle 是防御纵深层,不是首要正确性权威 —— 首要权威是 harsh-fill 模型 + 锁定的 acceptance 门**(`objective_promotion.py:346-350`)。

**要点**:25bps 容差正是为「两引擎合理的摩擦细节差」设计的(`backtest_oracle.py:46-50` 注释)。对齐目标不是 bit-exact 摩擦(跨引擎不可能),而是**校准策略上 ≥95% 天落在 25bps 带内**。

---

### 3.4 确定性策略回测 harness 架构(P1b 核心,最大工程量)

**本 dossier 最重要的发现**:`ChallengerReplayer` 不只是「实现一个 Protocol」,而是要**从零建一台确定性策略回测引擎**。现存只有零件,没有引擎。

#### 3.4.1 引擎要产出什么

`evaluate_promotion` 消费 `PromotionInputs`(`objective_promotion.py:130`),需要:
- `champion_report` / `challenger_report`(AcceptanceReport,8 门指标)
- `daily_excess`(challenger 减 incumbent 的逐日 PnL,**同 PIT 输入下的 counterfactual**)
- `anti_gaming`(平均敞口 / 信号数 / 月换手)
- `n_trials`(来自 `ExperimentRegistry.count_trials(family)`,含失败)
- `oracle_verdict`(rqalpha 差分,§3.1-3.3)

所以一个候选参数集的数据流:

```
候选参数集 (从 EVOLVABLE_WHITELIST 派生, §3.5 producer)
  │
  ├─ [自有 harness] incumbent 参数回测  → champion AcceptanceReport + daily PnL_incumbent + equity curve
  ├─ [自有 harness] challenger 参数回测 → challenger AcceptanceReport + daily PnL_challenger + equity curve + anti-gaming
  │        (两次回测同一 PIT 窗口、同一份数据导出 → counterfactual)
  ├─ daily_excess = PnL_challenger − PnL_incumbent
  ├─ [rqalpha 子进程] challenger 参数 oracle 回测 → compare_equity_curves(vs 自有 challenger 曲线) → OracleVerdict
  ├─ n_trials = ExperimentRegistry.count_trials(family)
  │
  └─ PromotionInputs → evaluate_promotion → PromotionDecision
         └─ promoted? → build_promotion_intent → ActivationManifest(params=候选) → 飞书通知 → 人工 pin (§3.6)
```

#### 3.4.2 自有 harness:逐日重放确定性策略

引擎主循环(对回测窗口每个交易日 T):
1. **只用 PIT 可见数据**(≤ T-1 收盘 + T 当日 bar,严守 PIT)喂确定性策略。
2. 跑确定性决策:Line-1 量化候选排序(`candidate_selector`,零 LLM 路径)+ 槽位轮动(`slot_portfolio`)+ Line-2 监控触发(`monitoring` 止损止盈,确定性零 LLM)。
3. 订单经 **`simulate_harsh_fill`**(AB-007)撮合(harsh-or-equal vs 实盘,by construction 保守)。
4. mark-to-market → 记 `EquityPoint` + 当日 PnL。
5. 窗口结束 → 喂 `AcceptanceService` 产出 AcceptanceReport(指标语义与生产 byte-identical)+ 算 anti-gaming 统计。

#### 3.4.3 模块归属(关键架构决策)

`backend/strategy_evolution/` 的 import 隔离禁止它 import 策略模块。因此:

> **新建 `backend/backtest/` 模块**(确定性回测 harness),归属如下:
> - **可 import**:`backend.candidate_selector` / `backend.slot_portfolio` / `backend.monitoring` / `backend.marketdata_snapshot`(PIT 取数)/ `backend.strategy_evolution.harsh_fill_model`(撮合零件)/ `backend.services.acceptance_report`。
> - **严禁 import**:`backend.{llm,agents,mirofish}`(P1 不回放 LLM)+ `backend.{api,broker}`(harsh_fill 已是 broker-free 撮合,实盘镜像不碰)。
> - **零 LLM、test-time/offline only、永不入实时路径**(新 redline,见 §4)。
> - `strategy_evolution` 只消费 harness 的**输出**(纯数据 `PromotionInputs`)→ 保持 `evaluate_promotion` 纯净。dispatcher(`backend/services`)做接线。

#### 3.4.4 范围收窄(诚实分级 —— 最重要的工程判断)

不是所有 15 个白名单参数都同等可回测。按「回测保真度」分级,**P1 从最保真的做起**:

**P1 第一批 —— Line-2 保护性止损止盈系数(最高保真,推荐先做)**
`line2.atr_stop_mult` / `r_multiple` / `time_stop_trade_days` / `drawdown_quantile` / `strength_sell_threshold`。
- ✅ **纯确定性、出场侧**:给定入场,出场逻辑完全确定,**不需要任何 LLM**。
- ✅ 生产的 Line-2 监控 runner **本就确定性零 LLM** → 回测出场逻辑 = 实盘出场逻辑,**bit-identical**,保真度最高。
- ✅ safety-adjacent + 「只紧不松」单调约束已在 `evolvable_params` 锁死 → 进化只可能让止损更早,**永不放松保护**(契合 redline)。
- ✅ 出场质量直接影响盈利(更好的止盈锁定 / 更早止损)。

**P1 第二批 —— selector 权重 / value 槽配额 / theme tier 权重(中保真,带显式 caveat)**
`selector.weight_*`(5,sum=1 组约束)/ `allocation.value_slot_quota` / `theme.tier*_weight`(序约束)。
- ⚠️ 这些改变**候选排序/配置**。实盘里排序喂 LLM 辩论,由 fund_manager 决定 BUY。确定性回测**无法回放 LLM 辩论** → 必须用**确定性代理入场规则**(如「买入排序 top-N,经 RiskEngine 14-check + 排除四件套」)。
- ⚠️ **诚实限制**:代理入场 ≠ 实盘 LLM-gated 入场。在代理规则下晋升的权重,是**在代理路径上**被验证,不是实盘路径。dossier 必须如实标注,且这类晋升的「生效」更要靠人工 pin 把关。
- 取舍:仍有价值(权重是量化资格层的真实旋钮),但保真度低于 Line-2,放第二批。

> 决策点 ③(§6):确认 P1 先做 Line-2 止损止盈系数(纯确定性),selector/allocation 权重作 P1 第二批 + 代理入场 caveat。

#### 3.4.5 与 ShadowChain 的关系

`ShadowChain`(`shadow_chain.py:382`)是为 LLM lane 设计的(替 bootstrap CI 展示)。量化参数环**直接调 `evaluate_promotion`**(它内部已调 `evaluate_challenger` + bootstrap CI + DSR + oracle),**不绕 ShadowChain**。可选:量化 harness 也实现 `ChallengerReplayer` Protocol 形状以复用 CI 展示,但**权威判定是 `evaluate_promotion`**。建议保持简单:量化 lane 自己的回测引擎 + 直喂 `evaluate_promotion`,不强塞进 ShadowChain。

---

### 3.5 EvolutionDispatcher boot 接线 + task producer

#### 3.5.1 boot 接线(补 `app.state.evolution_dispatcher` 缺口)

- 在 `main.py` lifespan 启动期构造量化参数 lane 所需组件(回测 harness + ExperimentRegistry + oracle runner + producer),挂到 `app.state.evolution_dispatcher`(或新 `app.state.quant_param_evolution`)。
- 现有 4 LLM lane 的接线**保持原样**(它们的 replayer 仍空,留 P3)。

#### 3.5.2 task producer(每晚评估哪些候选)

**新增确定性 `ParamExperimentProducer`**:
- 从 `EVOLVABLE_WHITELIST` 当前生效值出发,按**确定性搜索策略**派生候选变体:
  - 起步建议:**坐标下降 / 当前值邻域的有界网格**(每参数在 clamp 内取若干档),或**固定种子采样器**。
  - **确定性**:不用 wall-clock 随机;用 seed + 实验序号派生扰动(契合「回测可复现」+ 工作流脚本禁 `Math.random`/`Date.now` 同理)。
- 每个候选 → `ExperimentRegistry` 注册(content-addressed,**含失败**,供 `count_trials` 多重检验校正;同 design 幂等 skip)。
- **预算/界**:量化 Line-2 回测**零 LLM** → ¥100 sub-budget 主要拦 LLM,这条 lane 的真实界是 **wall-clock/计算**:每晚候选数硬上界 + 子进程超时(§3.1)。`reserve_evolution_run` 仍包住(留痕一致),但要新增「每夜候选上界」配置。
- producer 必须**如实 log 被丢弃的候选**(若有上界截断)—— 不静默截断(契合 plan.html「no silent caps」精神)。

#### 3.5.3 22:00 cron 流(补 `:2883` 的「no tasks」)

```
_evolution_shadow_run_callback:
  reserve_evolution_run (已建)
  → producer.next_candidates(seed, 上界)            [新]
  → for 候选: harness 回测(incumbent+challenger)+ oracle 差分   [新, §3.4]
  → evaluate_promotion → PromotionDecision (已建)
  → if promoted: build_promotion_intent + ActivationManifest(params) [已建+§3.6]
                 + write_next_boot_lock(params) + 飞书通知人工 pin   [§3.6 解除拒]
  → audit 留痕(成功/失败/无候选均如实,不再恒 DEGRADED)
```

---

### 3.6 param 运行时落地(补 `activation.py:288` 缺口)

**当前缺口**:lockfile 只写 `approved`(5 类哈希),不写 `params`;两处硬拒 param-bearing manifest;无 runtime 消费端。

**设计(P1c)**:

1. **lockfile schema v1→v2**:`live_artifacts.lock.json` 增可选 `params: {name: value}` 块(已在 `ActivationManifest.params` 建模,只是 apply 时 `new_lock` 字典丢弃了它,`activation.py:314`)。v2 读兼容 v1(无 params = 空 = 全默认)。
2. **新建 boot 期 `RuntimeParamStore`(不可变)**:boot 一次性从 live lockfile 的 `params` 载入 → **再过一遍 `validate_param_set`(fail-closed:clamp 违反/冻结集命中 → 拒 boot 或回落默认 + 大声 audit)** → 不可变快照。缺失 = 空 = **与今天 byte-identical**。
3. **注入消费端**:用既有注入模式把 `RuntimeParamStore` 注入消费模块构造点 —— `IntradayTriggerConfig` / `intraday_calibration` tiers / `candidate_selector` 权重 / `allocation` 配额 / `theme` tier 权重在 runner 构造时从 store 取值(`store.get(name, code_default)`,叠在现有默认之上)。**store 为空时取 code_default = 现状不变**。
4. **解除两处拒**:`write_next_boot_lock`(`:168`)+ `apply_pending_activation`(`:288`)的 param ValueError 替换为:apply 把 `manifest.params` 写进 lockfile v2 + boot health assert 重载验证。**保留防御纵深**:apply 前 `validate_param_set` 再校验一次;boot 载入再校验一次。
5. **单调红线(safety-adjacent 防多步放松)**:`evolvable_params` 的「只紧不松」当前 vs `current` 参数比较。**风险**:多次晋升让 current 漂移,可能一步步放松到接近 clamp_max。**锁定**:safety-adjacent 参数的单调约束基线 = **冻结代码默认值**(不可变 baseline),而非上次进化值;且 clamp_max 本身已是被审定的硬上界 → 双重兜底。→ 需在 amendment 写死,对抗测试先写。

**红线影响**:这是完成 AB-003 预留的 param 路径(AB amendment 已写「lands with the AB experiment harness」)。需一份聚焦 amendment 记 lockfile schema v2 + RuntimeParamStore + 解除两处拒 + 单调-vs-默认纪律。

---

## 4. 红线影响 + 需要的 amendment 清单(amendment-first)

**安全地基红线全留**(永禁真实下单 / 飞书人工 / 127.0.0.1 / LLM 不写决策 / 单一构造点 / PIT 可复现 / fail-closed / 进化应用人工 gate)—— 本设计无一触碰,且多处强化(单调-vs-默认、PIT 导出 checksum、人工 pin)。

**需新增/修订的 amendment**(实施前置门):

| # | amendment | 边界变化 | 对应 §|
|---|---|---|---|
| A1 | `R-002-amendment-2026-06-14-rqalpha-subprocess-runner`(或并入下条) | rqalpha = 新依赖落地;子进程执行;R-002 redline 从单文件放宽为显式 allowlist(+ runner 入口脚本);venv env 配置 | §3.1 |
| A2 | `P2-2-amendment-2026-06-14-deterministic-backtest-harness` | 新 `backend/backtest/` 模块 import allowlist + 零 LLM + test-time/offline-only + 永不入实时路径(新 redline + AST 契约) | §3.4 |
| A3 | `P2-2-amendment-2026-06-14-quant-param-evolution-loop` | 新第 5 条量化参数 lane + ParamExperimentProducer + 每夜候选上界 + boot 接线 | §3.5 |
| A4 | `AB-003-amendment-2026-06-14-param-runtime-landing` | lockfile schema v1→v2(params 块)+ RuntimeParamStore + 解除 staging/apply 两处拒 + 单调-vs-默认纪律 | §3.6 |
| A5 | (决策点①若选)`P2-2-amendment-2026-06-14-quant-param-human-pin` | P1 量化参数即便 sim 域也要人工 pin(比 AB-003 sim-auto 更保守) | §6 ① |

> 可按实施分期合并(如 A1+A2+A3 同期 P1a/P1b,A4 同期 P1c)。具体拆分 owner 定。

---

## 5. 路线图分期(dossier 批准后;每期 amendment-first + TDD + codex 前置门)

> 协议:每期一组任务、TDD(对抗测试先写 RED→GREEN)、commit 前过 codex-review 修完 P0/P1/P2;绿测试 ≠ commit-safe。env/启动/真发/push owner 亲为。

**P1a — rqalpha 集成(§3.1+§3.2+§3.3)**
- `RqalphaBacktestRunner` 子进程 harness + 入口脚本(venv-only,零 backend import)。
- PIT 数据导出器(Option B:kline_daily/MarketDataSnapshot → content-addressed parquet + checksum + 复权 pin)+ rqalpha 自定义数据源。
- 摩擦 Mod config 映射 + **校准门**(固定策略两引擎 CONSISTENT)。
- 交付物:对一个固定策略,`run_differential_check` 返回 CONSISTENT(非恒 UNAVAILABLE)。

**P1b — 确定性回测 harness + 量化参数 lane(§3.4+§3.5)**
- 新 `backend/backtest/` 逐日重放引擎(**先 Line-2 止损止盈系数**,纯确定性)→ 产 AcceptanceReport + daily PnL + anti-gaming + equity curve。
- `ParamExperimentProducer`(确定性候选派生 + 上界 + 注册)。
- EvolutionDispatcher 量化 lane + boot 接线(补 `app.state`)+ 22:00 cron 串起(补「no tasks」)。
- 交付物:22:00 cron 真跑一个 Line-2 参数候选 → evaluate_promotion 出 PromotionDecision(不再 DEGRADED 空转)。

**P1c — param 运行时落地(§3.6)**
- lockfile schema v2 + `RuntimeParamStore` + 消费端注入 + 解除两处拒 + 单调-vs-默认。
- 交付物:**量化参数进化环闭合** —— 候选→回测→9 门→(人工 pin)→重启→runtime 真生效(end-to-end 测试 + bit-identical-when-empty 回归)。

**P2 — AC-003 value_score 真实数据接线**(正交,策略变强而非环本身)
- event-study / Amihud 容量 / KG 共振 / 公告日基本面 真实数据喂入 → VALUE 分型激活 → 价值槽可填。

**P3 — LLM prompt 进化 + rqalpha 兼作过拟合 oracle**
- 决策回放难题(temp=0 pin 或 record-replay)。现有 4 LLM lane 的 ChallengerReplayer 实现。
- 之后:开 simulation_auto 跑**带可用进化环**的自动盘,观察盈利(诚实前提:观察,非保证)。

---

## 6. 决策点(请 owner 拍板)

① **【最重要】sim 域量化参数是否坚持人工 pin?**
   - AB-003 已建 sim-auto-activation(客观 9 门 PASS → 受控重启自动生效,无人工)。
   - 但 #86 路线图文字 + redline「进化应用人工 gate」要求人工 pin。
   - **建议**:P1 阶段(环刚闭合、未建立信任)**坚持人工 pin**(9 门 PASS → 飞书通知 → owner 审 + amendment + 重启)。信任建立后 owner 可再决定是否开 sim-auto。→ 倾向选「人工 pin」。

② **数据通路 Option B(PIT 导出 + checksum)确认?** 建议 B(A 违反 PIT 红线);P1a 先用固定 PIT 导出调通 harness/摩擦,晋升路径只认 B。→ 倾向选「B」。

③ **P1 参数范围:先 Line-2 止损止盈系数(纯确定性、最高保真),selector/allocation 权重作第二批(代理入场 caveat)?** → 倾向选「Line-2 先行」。

④ **回测 harness 归属:新建 `backend/backtest/` 模块(隔离外置,保 strategy_evolution 纯净)?** → 倾向选「新建 backend/backtest/」(strategy_evolution import 隔离不可破)。

⑤ **amendment 拆分粒度:A1-A5 合并为几份?**(建议 A1+A2 同 P1a,A3 同 P1b,A4 同 P1c,A5 视 ① 结果)。

---

## 7. 诚实前提再申明

- 本环闭合后,**绝大多数夜晚的正确结果是「无候选通过 9 门,不晋升」**。这是特性不是 bug —— 反过拟合门就是用来挡住「运气好看的噪声」的。
- 任何晋升候选都**只是「在我们的回测 + oracle 下、经反过拟合检验后、相对当前参数有统计显著超额」**,不等于实盘会赚。代理入场(selector 权重那批)的晋升更要打折看。
- **人工 pin 是最后一道清醒阀**:9 门是机器的诚实,人工 pin 是人对「这值不值得上」的最终判断。两者都不拆。
- 「打磨到可稳定盈利」是一个**经验性观察目标**,不是工程可交付的承诺。本 dossier 交付的是「能诚实地做这个观察、且不会自欺」的机器与外壳。

---

## 附:本 dossier 核实过的源码锚点(可点)

- `backend/strategy_evolution/backtest_oracle.py`(差分 oracle + `RqalphaBacktestRunner` 恒 UNAVAILABLE:341-360)
- `backend/strategy_evolution/objective_promotion.py`(9 门 `evaluate_promotion`:215;THRESHOLD_PARAM 档:68)
- `backend/strategy_evolution/evolvable_params.py`(15 白名单 + 冻结集 + 单调约束)
- `backend/strategy_evolution/activation.py`(param 两处拒:168 / 288;lockfile 写丢 params:314)
- `backend/services/shadow_chain.py`(`ChallengerReplayer` 仅 Protocol:361)
- `backend/services/evolution_dispatcher.py`(4 LLM lane,无量化 lane)
- `backend/main.py`(22:00 cron + dispatcher 未接线:2835-2887)
- `backend/strategy_evolution/harsh_fill_model.py`(`simulate_harsh_fill` 单笔撮合零件:93)
- `config/live_artifacts.lock.json`(当前仅 approved,3 prompt_version pin)
- rqalpha 隔离 venv:`/home/ps/rqalpha-smoke-venv`(6.1.5,已验证 import)

---

## 8. OSS 借鉴 + codex 对抗审查 → 设计升级(2026-06-14 第二轮,**权威,覆盖前文冲突项**)

### 8.0 方法

owner 指示「借鉴开源社区顶尖 harness 构造策略 + 与 codex 一同构思决策与审查」。本节据:
- **3 路并行 OSS 调研**:① 回测引擎架构(vectorbt/backtrader/zipline-reloaded/nautilus_trader/qlib/Lean);② 反过拟合验证 + 参数搜索(de Prado/mlfinlab 方法论、CPCV/PBO/DSR、Optuna/Nevergrad/Ax、walk-forward、White RC/Hansen SPA、文件式实验跟踪);③ 跨实现差分测试 + 可复现/PIT 工程(差分测试范式、共因失效、浮点确定性、子进程跨 venv、content-addressing)。
- **codex 双路对抗审查**:codex-oracle agent 推理 + codex CLI second-opinion(JSON,confidence=high)。两路独立得出**高度一致**结论。

### 8.1 借鉴的 OSS 回测引擎设计(clean-room 借鉴,**几乎零 vendor**)

> **核心结论:harness = 已有件(`harsh_fill_model`/`backtest_oracle`/`marketdata_snapshot`/`slot_portfolio`/`cost_calculator`)的确定性逐日装配器,不是新引擎。** 借设计、不抄码。

| 引擎 | 借鉴的设计模式 | 许可证 | vendor? |
|------|---------------|--------|---------|
| **nautilus_trader** ⭐ | ① `NautilusKernel` **共享组件** + 只换时钟/执行 adapter → 三路径同源的现成答案;② 单调 `ts_init` 事件主循环(`_advance_time` 在数据进 exchange 前推进时钟 → look-ahead 物理不可能);③ 确定性 ID + 种子化 FillModel | LGPL-3.0 | ❌ 借 kernel 设计在 Python 重写(Rust 太重) |
| **zipline-reloaded** | ① **待决单→下一 bar 成交屏障**(`open_orders`,bar t 的单在 t+1 成交 = T+1 by construction);② 独立 PIT 复权库(原始价 + 单独 adjustments,查询时 as-of 应用) | Apache-2.0 | ❌ 借设计,clean 重写 |
| **qlib** ⭐ | ① **`Ref` 符号 lint**:负向时移(看未来)= look-ahead bug,只许在 label/评估端 → **可进 redline-check(AST)**;② A 股 Exchange 涨跌停**方向门** + 整手向下取整;③ 修订感知 PIT 库(按公告日取"截至那天已知版本",根除数据泄漏) | **MIT** | ✅ 可选 vendor `.bin`/PIT/Exchange 片段(MIT 合法) |
| **Lean** | 单方法 reality model(`IFillModel`/`ISlippageModel`/`IFeeModel`)+ 单工厂(`IBrokerageModel`)装配 → 把我们 §2.7 摩擦栈收进一个 A 股 brokerage-profile,harness 与 MockBroker 共用 | Apache-2.0 | ❌ C# 仅借哲学 |
| **vectorbt** | `execute_order_nb(state, order)→(state, fill)` **纯不可变 reducer** 形态(契合不可变红线) | Apache-2.0 **+ Commons Clause** | ❌ 绝不 vendor(Commons Clause + 默认 look-ahead 语义有害) |
| **backtrader** | index-0=当前的缓冲(未来物理不在内存)+ 默认 t+1 成交 + 显式 cheat 标志 | **GPL-3.0** | ❌ 绝不(病毒式) |

**harness 设计源头映射(一句话)**:主循环 = nautilus `_advance_time` 单调事件循环 + zipline `open_orders` 下一 bar 成交屏障;PIT = qlib `Ref` lint + Lean frontier 时钟纯函数 + 我们 `marketdata_snapshot` as-of;撮合 = 我们 `harsh_fill_model` + `cost_calculator` 按 Lean 单工厂组织、补 qlib 涨跌停方向门/整手;同源 = nautilus `NautilusKernel` 共享组件 + 换时钟/换执行 adapter(**实盘永远飞书人工,绝不接 nautilus LiveExecutionClient — 撞永禁真实下单红线**);可复现 = vectorbt 纯 reducer + 确定性排序 + pin 版本。

### 8.2 借鉴的反过拟合/搜索/可复现方法(库 + 许可证)

| 用途 | 选用 | 许可证 | 拒用 |
|------|------|--------|------|
| 参数搜索 | **scipy.stats.qmc**(Sobol/LHS,固定 `Generator(seed)`) | BSD-3 | Optuna-TPE / Ax-BoTorch(自适应 → N 不可数、更快过拟合) |
| CPCV 多路径(**降为披露指标**) | skfolio `CombinatorialPurgedCV` / timeseriescv | BSD-3 / MIT | **mlfinlab(已转闭源专有,不可用)** |
| PBO(**披露**) | 自实现 ~30 行 CSCV / AidanAkdogan 版 | 自有 / MIT | pypbo(AGPL-3) |
| DSR/PSR/MinBTL | 自实现公式 / eslazarev / qf-lib | 自有 / MIT / Apache-2.0 | mlfinlab(专有) |
| 多重比较 SPA(基准=现役) | **arch**(`SPA(studentize=True, bootstrap='stationary', seed=)`) | NCSA(商用安全) | — |
| 实验跟踪 | **内容寻址 JSON + git**(可选叠 DVC) | 自有 / Apache-2.0 | **MLflow / LangSmith / Aim / W&B(红线/需服务)** |

要点:**mlfinlab 已闭源** → CPCV/PBO/DSR 一律用 BSD/MIT 等价库或按公开论文自实现 ~公式。文件式 content-addressed JSON+git 与我们 PIT 可复现 / append-only audit 红线完全同构。

### 8.3 codex 对抗审查抓出的真实缺陷 + 修正(逐条;**覆盖前文**)

> 两路 codex 一致;前文 §3 多处被推翻。下表「原判断」指本 dossier 前文。

**① 【high,覆盖 §3.4.4】事件节奏奇偶性 —— Line-2 30s 盘中 vs 回测逐日是头号漂移源。**
- 缺陷:我原把「Line-2 止损止盈系数」列为 P1 最高保真先做项 —— **错**。Line-2 全在 30s 盘中 runner(分批锁盈/ATR 移动止损/drawdown 止损,路径依赖)。**日线回测只有 OHLC,没有 09:31 那一笔打穿阈值的事件** → 用日线近似一个 30s 事件流 = 一个**未经校验的新决策层**;同日既止盈又止损日线无法判先后 → 仲裁假设直接改成交、复利放大。
- 修正(codex 给「事件契约」二选一):**(A)** 回测在**实盘决策节奏**跑(Line-2 用 1min/tick PIT 重放);**或 (B)** 把 Line-2 盘中监控**正式归类为「非 alpha 的保护性风险监控」**(它本就「只紧不松」永不造 alpha)→ **其参数不走自动晋升环**,改由独立 invariant 测试守护。**二者必择一并在代码强制边界。** → 见 §9 决策点 ⑥ + codex 开放问题①。

**② 【high,覆盖 §3.1】oracle 契约必须拆成两条 lane —— 别让「防版本差」架空 oracle。**
- 缺陷:我原提「主引擎产整数化订单流喂 rqalpha,rqalpha 只撮合记账」防 NEP 50 版本翻转 —— 但这把 rqalpha **从策略 oracle 降级成对账器**,而对账我们已有 harsh-fill + MockBroker 单镜像 + 16:00 对账 → rqalpha 增量近零,**架空了它抓决策 bug 的核心价值**。且真正会翻转布尔门的是**主引擎自身**升级 numpy,与 rqalpha 无关 —— 我把两个正交问题混了。
- 修正(两条正交措施,不互相吃):
  - **措施 A(决策稳定性,与 oracle 无关)**:**主引擎所有门控阈值比较一律走定点/整数域**(钱到分、价到分、量到股、比率定精度/Decimal)+ 新 redline lint(决策路径裸 `float ==`/`<`/`>` 阈值比较须经 `decision_compare(a,b,ulp_guard)` 包装)。**彻底消除「主引擎升 numpy 翻转决策」,不牺牲 oracle。**
  - **措施 B(oracle 仍独立,分两 lane,codex J2)**:**Lane-1 订单流对账 oracle**(rqalpha 重放整数订单流,离散成交/记账**零容差**);**Lane-2 golden-vector 策略决策 oracle**(在主引擎、pinned runtime、定点比较下校验 features/signals/risk-decisions/order-intents 的金标准向量)。决策层别交给 rqalpha 跨版本重做,但也别不验 —— 用 golden-vector 验。
  - 补:closed-form **不变量断言**作廉价「第三 oracle」破 N=2 共因盲区(见 ④)。

**③ 【high,覆盖 §3.5/§5 门设计】门做减法不做加法 + 批量 vs 逐候选结构冲突。**
- 缺陷:PBO/SPA 是**批量**联合统计,与现有「逐候选注册+9 门」结构冲突;且 CPCV-PBO + purged k-fold + DSR **重复惩罚同一过拟合** → 小样本 A 股下功效打到地板 → **几乎永不晋升(工程做完却空转)**。
- 修正:
  - **批量成为一等 registry 对象**(不可变候选集 + 共享数据窗 + 共享 null);**两阶段**:阶段1 批量门(MinBTL 准入 → CPCV-PBO/SPA 选 basket winner)→ 阶段2 逐候选门(oracle、anti-gaming)→ 阶段3 **冻结前向 shadow**(见 ⑥)。
  - **只让一个统计过拟合门有否决权**:保 **DSR 为主门**;**CPCV-PBO 降为披露指标**;purged k-fold 是产 OOS 序列的**方法非门**。加 **MinBTL(准入,廉价高价值)**+ **SPA(基准=现役 pinned 参数,语义=防把噪声当代际改进,独特不重复)**。

**④ 【medium→关键,覆盖 §3.x 差分】N=2 共因盲区 + golden-master 偷取消独立性。**
- 缺陷:「主引擎当 golden master、rqalpha 当跟班」前提是 master 一定对 —— 若主引擎决策有 bug,rqalpha 贴近 master 就 PASS,**bug 被一致化**;且两引擎**共享 PIT 数据/日历/复权 pin** → 差分对这些 A 股最易错处**完全盲**(两边读同一份脏数据)。
- 修正:**决策层对称差分**(任一方决策分叉即 DIVERGENT,不预设谁对;数值层才用容差);加**封闭式不变量断言**(现金守恒 = Σ买入+Σ卖出+期末现金=期初,整数零容差;持仓守恒;费用=显式公式重算;单股≤15%/总仓≤70% 每权益点重验)—— 不依赖任何框架版本,抓「两引擎一起错」;25bps **绝对地板从「零交易日基线」数据驱动标定**,不拍脑袋;**逐组件 signed-difference 归因**(哪项摩擦致发散)作晋升前强制项。

**⑤ 【medium,覆盖 §3.5 搜索】「Sobol 比贝叶斯诚实」是错误归因 + 过拟合搬家。**
- 缺陷:诚实**只来自「N 预声明且固定」**(固定网格一样诚实);Sobol 优势是**低维投影覆盖**,非诚实。过拟合被搬到三处未设防:(a) **搜索边界的人工选择**(我们那些 clamp 全是历史手调出来的 → 在过拟合盆地里均匀采样,DSR/PBO 抓不到,**在统计框架上游**);(b) 重跑选择性(换 seed/扩边界必须计入累计 N、永不重置);(c)「选哪些点预声明」的一次性研究者自由度。
- 修正:重述三条独立理由(预声明固定 N→DSR 的 N 精确;Sobol→低维投影覆盖;拒贝叶斯→N 不可数);**搜索边界冻结 + 改边界=amendment + 计入「研究者自由度日志」**;**累计 N 跨 session 永不重置**(从 registry 直接算,换 seed/重跑=显式 registry 事件);**先算约束后有效维度**(sum=1 单纯形 + 单调 cumsum 后有效维可能仅 6-8)再定 Sobol 点数;**对晋升候选用 sensitivity slices**,不宣称 15 维全覆盖。

**⑥ 【critical,两路 codex 一致的头号风险】最大风险是「虚假信心」,不是误触实盘。历史门再多也不够。**
- 缺陷:系统可能工程完整却**朝「通过越来越精致的历史门」优化** → 严谨统计 × 失真输入(④Line-2 日线近似)= **高置信度的错误**,制造 owner 误以为「过了这么多门就有 edge」的自欺。
- 修正(**单点最该先做**):**晋升的主门 = 冻结的前向 shadow**(predeclared metrics + 日历时间下限 + **期间零参数编辑**;候选不得因「通过历史门」就上线)。**历史回测 + rqalpha + 统计门一律降为「前置预筛」**;真正决定能否晋升的是**实盘路径上的 45 日冻结前向 shadow(复用既有 P0-6,但提升为主门)**。**令人安心的是:我们已建的 45 日 shadow(P2-2 沿用 P0-6)正是这道主门 —— 升级 = 把它从「众多门之一」提为「不可绕的主门」,统计门退居预筛。**

### 8.4 升级后的设计决策汇总(authoritative)

1. **晋升架构** = 三阶段漏斗:**预筛**(历史回测 + rqalpha 双 lane + 统计预筛:MinBTL 准入 + DSR 主门 + CPCV-PBO/SPA/PBO 披露)→ **逐候选验**(anti-gaming + 不变量)→ **主门:45 日冻结前向 shadow**(零参数编辑 + predeclared)→ **人工 pin**(amendment + 重启)。
2. **回测 harness** = nautilus/zipline/qlib/Lean 设计借鉴的确定性事件循环;**Line-2 盘中必须 1min/tick 重放,否则归类为非-alpha 风险监控不进环**(决策点 ⑥)。
3. **oracle 双 lane** = 订单流对账(整数零容差)+ golden-vector 策略决策(主引擎定点)+ 封闭不变量(破 N=2)。主引擎门控全定点化 + redline lint。
4. **搜索** = scipy QMC Sobol/LHS 预声明固定 N;边界冻结 amendment 化;累计 N 永不重置;有效维度先算。
5. **诚实地基** = §8.5。
6. **数据通路** = Option B(PIT 导出 content-addressed + checksum,借 qlib PIT / ArcticDB snapshot / zipline 独立复权);两 env numpy/pandas/BLAS 版本指纹入 manifest;`OMP_NUM_THREADS=1`;子进程稳健清单(§9 附)。

### 8.5 反自欺机制(新增,直接服务「打磨到可稳定盈利」诚实前提)

codex 两路都强调:这台机器**结构上倾向制造虚假信心**;owner 自己点破「反过拟合门只减假阳性、不造 alpha」。三个对冲钩子:
1. **null-edge sentinel 对照组**:每批 Sobol 候选**掺入已知无 edge 的哨兵参数**(随机阈值 / shuffle 信号 / 现役镜像扰动)。**门放行任何 sentinel → 门坏了;sentinel 永拒但真候选也永拒 → 空间里没 alpha。** 把「机器是否有用」变成可观测命题。
2. **机制假设人工门(非统计)**:每个晋升候选须附一句「这个参数为何应在样本外继续有效」的经济机制(动量延续/均值回归/流动性溢价…);无机制的纯数据胜出 = 默认过拟合,拒。统计门防不了「数据窥探出的伪规律」,只有机制约束能。
3. **诚实仪表盘**:owner pin 前必看「累计试验数 N / edge 样本外衰减 / sentinel 通过率 / 距上次晋升天数」。**设计目标不是让 owner 相信机器,而是让机器持续给 owner 不相信它的理由。**

### 8.6 codex 开放问题 → 必须先回答(决定环能否成立)

1. **哪些实盘决策真依赖 30s Line-2 数据?**(决定 Line-2 是 alpha 还是 risk-only → 决定它进不进环)
2. **PIT + 幸存者偏差过滤后,有多少年历史 / 多少 regime?**(决定 MinBTL 可行性 —— 历史太短则环**统计上欠功效**,此时「诚实地说『无法验证』」就是正确输出,不该硬造晋升)
3. **参数空间/特征/数据窗的编辑算不算 trial?**(研究者自由度记账 —— 不记则人工可绕过所有门调参)

---

## 9. 升级后的决策点(给 owner 拍板)+ 路线图

### 9.1 决策点(覆盖前文 §6)

> **✅ 决策已定(2026-06-14 owner 卡片拍板)**:
> - **① 晋升主门 = 45 日冻结前向 shadow**;历史回测+rqalpha+统计门全降为**预筛**。
> - **③ Line-2 盘中 30s 止损止盈 = 归类「非-alpha 保护性风险监控」,不进自进化环**;用 invariant 测试守护(不走参数晋升)。
> - **④ P1 首个进环参数组 = selector/allocation 权重**(selector 5 权重 sum=1 / value 槽配额 / theme tier 权重);日线节奏 + 确定性代理入场只做预筛,真正验证靠实盘冻结 shadow 主门。
> - **⑧ 历史数据 → 已实测(2026-06-14,见 §11):当前 `kline_daily`/`financial_data` 全空,系统无任何历史 PIT 价格数据(仅 ~5 周 go-live 实时快照、8 watchlist 码 + CSI300)**。owner「≥5 年多 regime」是**目标非现状**;Tushare 摄取**可行性已探针确认**(token 工作、daily≥8yr/index≥11yr、326 只退市可建幸存者无偏 universe、adj_factor 权限 OK)→ **新增 P1-DATA 历史摄取为硬前置**(见 §11 + §9.2)。
> - **②/⑤/⑥/⑦ 按推荐推进(owner 未否决)**:② sim 域仍**人工 pin**(比 AB-003 sim-auto 保守);⑤ 数据 **Option B**(PIT 导出+checksum);⑥ 门集 **DSR 主门 + MinBTL 准入 + SPA〔基准=现役〕,CPCV-PBO/PBO 降披露**;⑦ 回测 harness **新建 `backend/backtest/`**。
> - **⑨ codex 开放问题③(trial 记账)按推荐定**:参数空间/特征/数据窗的**编辑一律计入 trial**,累计 N **跨 session 永不重置**(从 registry 直接算;换 seed/扩边界=显式 registry 事件)—— 防人工绕门调参,契合反自欺前提。owner 可后续否决。

（以下为拍板前的决策点原文,保留作上下文。）

① **【最重要】晋升主门 = 45 日冻结前向 shadow,历史门降为预筛?**(codex critical)→ **✅ 已定:是**。

② **sim 域量化参数坚持人工 pin?**(原 §6①)→ **✅ 按推荐:是**。

③ **Line-2 盘中参数:1min/tick 重放进环,还是归类非-alpha 风险监控不进环?**(codex ① + 开放问题①)→ **✅ 已定:归类风控,不进环**。

④ **P1 首个进环的参数组选谁?** → **✅ 已定:selector/allocation 权重**(代理入场预筛 + 冻结 shadow 主门)。

⑤ **数据通路 Option B(PIT 导出 + checksum)**(原 §6②)→ **✅ 按推荐:B**。

⑥ **门集**:DSR 主门 + MinBTL 准入 + SPA(基准现役),CPCV-PBO/PBO 降披露?(codex ③)→ **✅ 按推荐:是**。

⑦ **回测 harness 归属 = 新建 `backend/backtest/`**(原 §6④)→ **✅ 按推荐:是**。

⑧ **必答 codex 开放问题②**:PIT + 幸存者过滤后历史年数/regime 数?→ **✅ 已定:owner 确认 ≥5 年多 regime(P1a 实测复核)**。

### 9.2 升级后路线图(golden-replay-first 重排;每期 amendment-first + TDD + codex 前置门)

- **P1-DATA(新,**硬前置**,§11 实测后新增)**:**历史 PIT 数据摄取**(Tushare bulk:`daily`+`adj_factor`+`daily_basic`+`stock_basic`〔含退市 list_status=D〕+`index_daily`+`fund_daily`)→ 存 `kline_daily` + marketdata_snapshot 原始字节+checksum+复权因子 pin(R0 §3 红线)+ **幸存者无偏 universe**(纳入退市码历史,补 codex 开放问题②)+ PIT as-of 读(借 qlib PIT)。≥5 年(建议 2015-present 含 2015 牛熊+2018 熊+2019-21 牛+2022-24 震荡 = 多 regime)。数据成本不设 ceiling(§2.5),但受 Tushare 每分钟频限 → 摄取 job 须限速 + 断点续传。**此前 selector/allocation 搜索环无数据可跑。**
- **P1-0(codex Top-3 地基,P1-DATA 后)**:**golden replay 测试** —— 用真实跑过的实盘日(`BrokerSnapshot` 原始字节 + `audit_events`)重放,断言 harness 权益曲线贴合实盘记录。**这是「同源」唯一实证 + 回测可信的地基;不过此关,后续门一门都不加。** + 决策阈值定点化 + `decision_compare` redline lint(措施 A)。
- **P1a — rqalpha 集成(§3.1-3.3 + §8.4.6)**:子进程隔离 venv + PIT 导出 Option B + 摩擦 Mod 对齐校准门 + 双 env 版本指纹 + 单线程 + 子进程稳健清单。
- **P1b — 确定性 harness + 双 lane oracle(§8.4.2-3)**:`backend/backtest/` 事件循环 + golden-vector 策略 oracle + 订单流对账 oracle + 封闭不变量;**Line-2 按决策点 ③ 处理**。
- **P1c — 搜索 + 批量 registry + 门(§8.4.1/8.4.4 + §8.5)**:scipy QMC 预声明搜索 + 批量一等 registry + 三阶段门 + sentinel + 机制门 + 诚实仪表盘。
- **P1d — param 运行时落地(§3.6)**:lockfile schema v2 + RuntimeParamStore + 解除两处拒 + 单调-vs-默认。
- **量化参数进化环闭合** = 发现→预筛(回测+oracle+统计)→逐候选验→**45 日冻结前向 shadow**→人工 pin(含机制门)→重启→生效。
- **P2 / P3** 同前文 §5。

### 9.3 codex Top-3(两路一致,作实施优先序)

1. **形式化事件模型与决策节奏**(回测/shadow/实盘消费同一逻辑事件)—— 先于一切。
2. **rqalpha 拆成对账 oracle + 新增 golden-vector 策略决策测试** —— 别让防版本差架空 oracle。
3. **晋升从「逐候选过历史门」改为「不可变批量注册 + 冻结前向 shadow 验证」** —— 在加更多统计门 / 扩搜索机器之前做。

---

## 10. 诚实前提(终版重申)

- 这台机器的最高价值是**诚实地告诉 owner「搜索空间里有没有 edge」** —— 包括「没有」。绝大多数夜晚正确输出 = 不晋升;历史短则可能长期诚实地不晋升(这不是 bug)。
- **严谨统计 × 失真输入 = 高置信度的错误**(codex 头号风险)→ 故地基(golden replay + 事件节奏奇偶 + 冻结前向 shadow)先于一切统计门。
- 别把机器设计成「想方设法 promote 点什么」;设计成「持续给 owner 不相信它的理由」(sentinel + 机制门 + 诚实仪表盘 + 人工 pin)。
- 「打磨到可稳定盈利」是**经验性观察目标**,非工程承诺。本 dossier 交付的是「能诚实做这个观察、且结构上抵抗自欺」的机器与外壳。

---

## 11. PIT 历史数据实测结论(2026-06-14,owner 指示「先实测再起草 amendment」)

> 直接查运行实例 Mongo(`quantmind` db @127.0.0.1:27017,系统 STOPPED 但容器 up)+ Tushare 只读探针。

### 11.1 存量数据 = 几乎为零(回测无米下锅)

| collection | 量 | 说明 |
|---|---|---|
| **`kline_daily`** | **0** | **历史日线 OHLCV 完全空 —— 代码注释早已写「never fed / no feeder yet」** |
| `financial_data` | 0 | 无基本面 |
| `index_prices` | 9 | 仅 CSI300,9 个交易日(2026-04-29→05-21) |
| `market_realtime` | 12867 | 实时报价快照,**仅 3 码**,2026-05-01→06-04(~5 周,盘中非日线) |
| `watchlist_market_snapshots` | 4376 | 30s watchlist 快照,**仅 8 码,2 天**(2026-06-03→06-04) |
| `news_articles` | 15887 | 新闻文本(非价格),2026-05-29→06-14 |
| `equity_points_archive` | 957 | go-live 后 MTM 权益点 |

盘上文件:`data/line1_frames`(52M,实盘选股 frame)/`data/models`(1.2G)/`data/knowledge_graph`(560K);**无任何全市场历史 bar**。

**结论:系统当前只有 go-live(2026-06-01 前后)以来 ~5 周、≤8 码 + CSI300 的实时/盘中快照。无历史日线、无多年、无多 regime、无退市码(幸存者有偏)、无基本面。owner「≥5 年多 regime」是目标,不是已存现状。**

### 11.2 但摄取可行性 = 已确认(Tushare 探针)

`TUSHARE_TOKEN` 已配(len 56,~/.bashrc)+ `tushare 1.4.29` 主 env 可 import + `backend/data/tushare_client.py` 已覆盖所需端点 + `database.save_kline/query_kline` 已建。探针(只读,5 calls):

| 探针 | 结果 |
|---|---|
| `stock_basic(L)` 在市 | **5528** 只 |
| `stock_basic(D)` 退市 | **326** 只(delist_date 早至 1999)→ **可建幸存者无偏 universe** |
| `daily 600519 2018` | ✅ → **≥8 年个股历史可达** |
| `index_daily 000300 2015` | ✅ → **≥11 年指数历史可达** |
| `adj_factor 2018`(高 tier) | ✅ → **复权因子可得,PIT 复权可做** |
| `daily(trade_date)` 全市场 | 一次 call ~5528 行/交易日 → bulk 摄取 = 每交易日一次 call |

**结论:owner「≥5 年多 regime」可达**(建议 2015-present,涵盖 2015 牛市+股灾 / 2018 熊 / 2019-21 牛 / 2022-24 震荡 = 多 regime),且可纳 326 退市码做幸存者无偏(补 codex 开放问题②)。代价:数据成本不设 ceiling(§2.5),但 Tushare 每分钟频限 → 摄取 job 须限速 + 断点续传 + PIT 存储(原始字节+checksum+复权 pin,R0 §3)。

### 11.3 对计划的影响

1. **新增 P1-DATA 历史摄取为硬前置**(§9.2)——在 selector/allocation 搜索环能跑之前必须先摄取。golden replay(P1-0)可先用现有少量实盘日(line1_frames + watchlist 快照 + equity 点 + BrokerSnapshot)。
2. **MinBTL 门可行**:≥5 年 → 历史长度足以支撑预声明 N 的多重检验(若只 5 周则环统计上空转,现确认不会)。
3. **amendment 影响**:bulk 历史摄取 + 幸存者无偏 universe + PIT 大规模存储,需独立 amendment(K-001 Tushare amendment 上加 bulk-historical 子条)。
4. **✅ 摄取 scope 已定(2026-06-14 owner 卡片)**:**时间深度 = 2015-present(~11 年)**;**universe = 全市场 5528 在市 + 326 退市(幸存者无偏)**。数据成本不设 ceiling,受 Tushare 频限 → 限速 + 断点续传。
