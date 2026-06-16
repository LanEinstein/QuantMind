# 量化选股策略研究专项 — 新 session 任务书(2026-06-16)

> **这是给一个全新 session 的自包含任务书。** 你(接手的 Claude)没有上文对话的上下文,本文件提供全部背景、路径、命令、方法论、成功判据与红线。先完整读本文件,再读 §3 的必读清单,然后按 Plan 模式立项。

---

## 0. 任务与**硬性成功判据**(先读,最重要)

**目标**:为 A 股(沪深主板+创业板+ETF,short-term/中短线为主)产出一套**真实可证、样本外稳定盈利**的量化选股策略,用**大量真实历史数据 + 概率统计理论**推导,并在**留出的近期真实数据(锁定测试集)**上一次性验证。

**Owner 指令(2026-06-16,强制,覆盖旧框架)**:
> "我们的系统任务就是保证稳定盈利,绝不能『工程可闭合·盈利不保证·反过拟合门只减假阳性不造 alpha』。"

你会在 `docs/research/self-evolution-loop-closure-rqalpha-2026-06-14.md` 里读到上面那句被否决的旧框架。**如何正确执行 owner 指令(关键,别搞反)**:

- **目标 = 真实盈利**,把"样本外净收益为正且有风险调整优势"设为**通过/不通过的硬门**,而不是把"通过某个统计门"当目标。
- **反过拟合/样本外纪律不是借口,而是达成真实盈利的手段**:能让你被随机噪声骗的回测,正是上线后**必然亏钱**的那种。所以测试集神圣不可侵犯(§5),恰恰是为了"保证盈利"——保证的是*真盈利*,不是*纸面盈利*。
- **诚实的硬约束**:市场非平稳,任何人都无法用数学"证明未来必赚"。所以 owner 指令的可执行翻译 = **"只接受在从未见过的测试数据上、扣除真实成本后确实盈利的策略;找不到就扩大研究(更多因子族、更前沿方法)继续找;穷尽现代方法仍找不到就如实上报——但绝不靠偷看测试集制造一个假的通过。"**
- **绝对禁止**:为了"凑出盈利"而在测试集上反复迭代/调参/选择(data snooping / 测试集泄漏)。这会直接导致上线真亏,是对 owner 指令最严重的背叛。

**最终交付的成功判据(在锁定测试集上一次性评估)**:
1. 扣除真实 A 股成本(佣金 0.015%+5元底 / 印花税 0.1% 卖出 / 过户费 / 分板块滑点 / T+1 / 涨跌停 at-fill)后,**累计净收益 > 0**;
2. **跑赢沪深300**(累计超额 ≥ 0);
3. 风险可控(最大回撤 ≤ 一个事先声明的阈值,如 ≤15%;夏普 > 一个事先声明的阈值);
4. 这些指标在**测试集**(开发期从未触碰)上成立 —— 不是训练/验证集。
> 达不到就如实报告 + 提出下一轮研究方向;**不准**回头改测试集评估方式来"过线"。

---

## 1. 现状:线上策略是什么 + 缺口在哪

线上选股(`simulation_auto` 全自动路径,LLM 不参与选股、只判方向)当前是一套**人工拍权重的启发式因子排名**:

- **入口**:全市场 ~5526 只 → 硬排除四件套(新股≤30/次新≤180/流动性<2亿/单价>500/ST/科创/北交/可转债/历史<21)→ ~1971 存活。
- **因子**(`backend/screening/factors.py`,Alpha158 子集,纯 stdlib):`momentum_20d` / `ma_ratio_5_20` / `volatility_20d` / `rsi_14` / `avg_amount_20d`。
- **打分**(`backend/screening/screener.py` 的 `FACTOR_WEIGHTS`,**写死常量、未经统计验证**):

  | 因子 | 权重 | 方向 |
  |---|---|---|
  | momentum_20d | 0.40 | 高优 |
  | ma_ratio_5_20 | 0.25 | 高优 |
  | volatility_20d | 0.20 | 反向(低优) |
  | avg_amount_20d | 0.15 | 高优 |
  | rsi_14 | 0.00 | 仅展示不计分 |

  截面百分位 → 加权 composite → 降序 top-N(~100)→ CandidateSelector 出 shortlist 5。

- **缺口**:因子集有学术出处(qlib Alpha158),但**权重是拍的**,线上路径**无任何 IC / 回测夏普 / 样本外 / 过拟合检验**把关。本专项就是用数据把"拍的权重/因子集"换成"在锁定测试集上证明确有正期望的策略"。

---

## 2. 可复用的既有基建(**别重造轮子**,这些已 done 但休眠)

| 模块/文件 | 用途 | 状态 |
|---|---|---|
| `backend/data/historical_ingest/` + `scripts/ingest_historical_pit.py` | AE-001 全市场+退市 PIT 历史摄取(字节稳定、幸存无偏、幂等续传、qfq 复权) | 代码 done,**未真跑大规模摄取**(owner-gated;Mongo `kline_daily` 曾=0) |
| `backend/backtest/`(event_loop/friction/portfolio/invariants/golden_vector/strategy/harness) | AE-004 确定性事件循环回测引擎(单调时钟前视即抛、Lean 整数分摩擦、T+1 next-open、守恒不变量) | done,库代码,未接 runtime |
| `backend/backtest/backtest_oracle.py`(rqalpha 子进程) | AE-002 差分校验 oracle(独立第二实现,≤25bps 一致门) | done;**rqalpha venv 在 `/home/ps/rqalpha-smoke-venv`,勿重装** |
| `backend/strategy_evolution/disclosure_stats.py` | DSR(Deflated Sharpe)+ PBO-CSCV + Hansen SPA + MinBTL 统计门 | done |
| `backend/strategy_evolution/quant_param_search.py` | Sobol 准随机搜索(替代贝叶斯)+ 确定性约束变换 | done |
| `backend/strategy_evolution/{quant_param_lane,sentinel,mechanism_registry,forward_shadow_mandate}.py` | 三阶段漏斗 + 零-edge 哨兵对照 + 经济机制白名单 + 45 日前向 shadow | done |
| `backend/screening/{factors,screener}.py` + `config/candidate_weights/v1.yaml` | 现役因子/打分(待替换的对象) | done(线上在用) |
| `backend/utils/decision_compare.py` + `backend/backtest/golden_replay` | AE-003 定点会计、实盘日重放=同源实证 | done |

**Tushare(唯一历史数据源)**:官方 `ts.pro_api`(`TUSHARE_TOKEN` 在 env;严禁 MCP/skill 进数据路径)。探针确认:`daily`≥8 年 / `index`≥11 年 / 5528 在市 + 326 退市 / `adj_factor` 可取。数据成本不设 ceiling。

---

## 3. 开工前必读(按序)

1. 本文件全文。
2. `docs/research/self-evolution-loop-closure-rqalpha-2026-06-14.md`(自进化回测 dossier——**注意 §0 已否决其"盈利不保证"框架**)。
3. `docs/decisions/P0-9-amendment-2026-05-24-full-market-screening.md`(筛选)+ `backend/screening/CLAUDE.md`。
4. `docs/decisions/P0-7-amendment-2026-05-30-portfolio-allocation.md` + `P0-7-amendment-2026-06-16-raise-deploy-fraction-sim-test.md`(分配)。
5. AE 五个 amendment:`P0-8-amendment-2026-06-14-bulk-historical-pit-ingestion` / `R-002-amendment-2026-06-14-rqalpha-subprocess-oracle` / `P2-2-amendment-2026-06-14-deterministic-backtest-harness` / `P2-2-amendment-2026-06-14-quant-param-evolution-loop` / `AB-003-amendment-2026-06-14-param-runtime-landing`。
6. `docs/decisions/R0-two-line-rearch-...-2026-05-24.md`(总纲 + 2 新红线:PIT 可复现 / 单一构造点)+ `CLAUDE.md` §2 / §2.0 / §2.0b。
7. `~/.claude/projects/-home-ps-papers-QuantMind/memory/MEMORY.md`(跨 session 记忆索引)。

---

## 4. Phase 1 — 前沿量化理论调研(**仅可信机构**)

用 `deep-research` skill / WebSearch / WebFetch,**provenance-gated**(只信:同行评审期刊、知名机构/院校、arXiv q-fin、SSRN、头部买方/卖方研究、官方文档;**不信**博客农场/营销稿/未署名内容)。并行多 agent 分主题:

- **横截面 alpha / 因子投资前沿**:Fama-French 之后的进展;AQR、Microsoft `qlib`(Alpha158/Alpha360)、WorldQuant 101 Alphas;近年顶刊(JF / RFS / JFE / JPM / RAPS)。
- **中短线/中频 alpha**:动量 vs 反转、换手/流动性、特质波动率、microstructure、限价簿信号;机器学习因子(梯度提升、正则化线性、神经网络的 IC 证据)。
- **A 股特异性**:T+1、涨跌停、散户驱动的短期反转、规模/换手/特质波动异象、北向资金、行业轮动;A 股因子有效性的实证文献。
- **诚实评估方法论(最关键的一类)**:Lopez de Prado《Advances in Financial ML》(purged/embargoed CV、CPCV、DSR、PBO);Harvey-Liu-Zhu《…and the Cross-Section of Expected Returns》(多重检验);Bailey-Lopez de Prado(DSR/PBO/MinBTL);Hansen SPA;White Reality Check;交易成本/容量/换手对净 alpha 的侵蚀。

**交付**:`docs/research/factor-theory-survey-2026-06-XX.md` —— 候选因子族清单(每个附:经济机制假说 + 文献出处 + 预期 IC 方向 + A 股适配性 + 已知失效条件)+ 推荐的评估协议。**对抗验证**:用 codex / 多 agent 复核每条主张的来源可信度。

---

## 5. Phase 2 — 数据 + **神圣的 train / validation / test 切分**

1. **真跑 AE-001 全历史 PIT 摄取**(owner 已停服,可放手摄取):先 `python scripts/ingest_historical_pit.py --dry-run` 验证,再实跑。scope = **2015-至今 + 全市场 + 退市股(幸存者无偏)**,qfq bit-exact。落 Mongo `quantmind` PIT 库 + snapshot store。**字节稳定 + checksum + 幂等续传**(断了能续)。
2. **切分(切完立刻锁死)**:
   - **TEST(测试集)= 最近的一段连续真实数据**(建议**最近 ~12 个月 / ~250 交易日**;最终窗口由 owner 拍板)。**切出来后立即"封存",开发全程一次都不看、不调参、不选择。**
   - **TRAIN + VALIDATION = 其余历史**;在其内部用 **purged + embargoed walk-forward / CPCV**(防时序泄漏:embargo 跨越持有期),严禁未来函数。
3. **测试集公约(写进报告,自我约束)**:测试集只在 §7 最终评估时**触碰一次**;任何"看了测试结果回头改策略"都使该测试集作废、必须换一段新的从未用过的数据。**这条就是 owner『保证盈利』在工程上的落地——它保证你报出来的盈利是真的。**

---

## 6. Phase 3 — 策略推导(**只用 train/validation**)

- 从现役 Alpha158 子集出发,按 Phase 1 文献**扩因子库**(动量/反转/波动/流动性/质量/微结构/资金流…)。
- 特征工程:截面标准化/去极值/中性化(行业、市值);缺失 fail-closed 不臆造(对齐现有 `factors.py` 的 `None` 语义)。
- 建模:从**透明、可解释、低自由度**起步(正则化线性 / 单调因子加权 / 浅层 GBDT);自由度越高越要用 DSR/PBO 折损。**优先经济机制可解释的因子**(过 `mechanism_registry` 白名单思路)。
- 选择信号:**IC / ICIR、换手与成本后净收益、validation 段 walk-forward 夏普、DSR、PBO、SPA**;务必接 **A 股真实摩擦**(复用 `backend/backtest/friction.py` + T+1 + 涨跌停 at-fill + 分板块滑点 + 费率);加**容量/换手约束**(短线尤其会被成本吃掉)。
- 用 `backend/backtest/` 引擎 + rqalpha oracle 差分校验(≤25bps);定点会计(`decision_compare`)。
- **零-edge 哨兵**(`sentinel.py`):跑一组无 alpha 的随机/打乱对照,确认你的流程不会把噪声评成"显著"。

**产出候选**:若干 content-addressed 候选策略(因子集 + 权重/模型 + 超参),各带 validation 段完整统计披露。**在 validation 上择优,不在 test 上。**

---

## 7. Phase 4 — **一次性诚实测试**

- 把 Phase 3 在 validation 上选定的**唯一最终策略**(若要比较 ≤3 个,须事先声明并用 SPA/Bonferroni 校正多重检验),在**锁定测试集**上**跑且只跑一次**。
- 报告(扣真实成本):累计净收益、年化、夏普、最大回撤、沪深300 累计超额、胜率、换手、容量、分年/分市况稳健性。
- 对照 §0 成功判据判定 **PASS / FAIL**。
- **PASS** → 进 Phase 5。**FAIL** → **如实写 FAIL**,分析失效原因,提出下一轮研究(新因子族/新数据/新方法),**换一段全新未用数据**作下一轮的测试集。**严禁**改测试集口径凑过线。

---

## 8. Phase 5 — 交付物 + 上线接线(**owner-gated**)

- **研究报告** `docs/research/factor-strategy-result-2026-06-XX.md`:方法、数据切分、validation 与 test 全部指标、PASS/FAIL、局限与风险、下一步。
- **策略产物**:因子集 + 权重/模型(content-addressed),可被 `LiveArtifactRegistry` 按批准哈希 pin。
- **回测代码**:复用 `backend/backtest/` + `strategy_evolution/`,新增的研究脚本放 `scripts/`(离线、import 隔离)。
- **上线建议**:是否/如何把新权重接到线上 `FACTOR_WEIGHTS` / `candidate_weights/vN.yaml`。**接线本身 = owner-gated**:走 amendment + `LiveArtifactRegistry` 批准 + 45 日前向 shadow + 人工 pin + 重启。**本专项不直接改线上选股路径。**

---

## 9. 红线 / 护栏(违反即停)

1. **离线研究 only**:不碰 `simulation_auto` 实时交易路径;不接线上 `FACTOR_WEIGHTS` 不经 owner gate;永禁真实下单。
2. **测试集神圣**:开发期零触碰;泄漏=作废重来(§5/§7)。
3. **PIT 可复现 + 幸存无偏 + 无前视**:摄取存原始字节+checksum;退市股必含;walk-forward 带 purge+embargo;成本净收益。
4. **数据源**:仅 Tushare 官方 SDK;严禁 MCP/skill 进数据路径;`TUSHARE_TOKEN` 不入 LLM/飞书凭证池。
5. **LLM 不进数值策略**:权重/打分/选股全确定性;LLM 只能用于 Phase 1 文献综述本身(且 provenance-gated)。
6. **代码任务照 CLAUDE.md**:commit 前过 codex-review(撞额度回退 `/code-review high`),修完 P0/P1/P2;docs-only 豁免;import 隔离(`backend/screening`、`backend/strategy_evolution`、`backend/backtest` 严禁 import `backend.{llm,agents,mirofish}`)。
7. **诚实**:FAIL 就报 FAIL;不确定就说不确定;绝不为迎合"保证盈利"而美化/造假回测。

---

## 10. 操作速查

```bash
# Python 环境
/home/ps/anaconda3/envs/zhanglan/bin/python
/home/ps/anaconda3/envs/zhanglan/bin/pytest -q --cov=backend --cov-fail-under=70

# 历史摄取(先 dry-run)
/home/ps/anaconda3/envs/zhanglan/bin/python scripts/ingest_historical_pit.py --dry-run

# rqalpha oracle venv(勿重装)
/home/ps/rqalpha-smoke-venv

# 出站 IPv4 only(否则 dashscope 等 AAAA 源静默 stall):httpx local_address="0.0.0.0"
# 任何跑后端/测试:FEISHU_INTERACTIVE_ENABLED=false
# Mongo: 127.0.0.1:27017 db=quantmind ; Redis 127.0.0.1:6379
```

**第一步建议**:进 Plan 模式 → 读 §3 清单 → 用 `deep-research` 起 Phase 1 文献综述(并行多主题 agent)→ 同时 `--dry-run` 验证 AE-001 摄取链路 → 产出"研究计划 + 数据切分方案(含拟定测试窗口,待 owner 确认)"给 owner 审,再进 Phase 2 实摄取。
