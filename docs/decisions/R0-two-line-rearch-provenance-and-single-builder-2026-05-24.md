# R0 双线重构总纲 — PIT 数据可复现 + InstructionPlan 单一构造点(2026-05-24)

> **性质**: 新决策(非 amendment)。本文是 2026-05-24「双线重构」程序的**入口与总纲**,并锁定两条**新红线**(§3 PIT 数据可复现 / §4 InstructionPlan 单一构造点 provenance-clean)。
> **触发**: Owner 2026-05-24 判定旧「锁定 13 标的」定位「完全不够」,重定位为**两条线 + 本地知识图谱 + 自进化多 agent**(蓝图见 §1)。
> **方法**: Claude 三路并行调研(`docs/research/*.md`)+ Codex 2 轮红队头脑风暴(Conditional GO),结论固化于本文 §3/§4 两条新红线 + §2 的 6 份 amendment。
> **SSoT 分工**: **本文锁「决策边界」;`docs/plan.html` 新阶段(K–T)锁「任务清单」;`CLAUDE.md` 锁「跨 session 速查」。** 三者不重复:边界细则在此,任务粒度在 plan.html,日常红线摘要在 CLAUDE.md。

## 1. 背景与愿景(双线 + 知识库)

旧实施期(S/A–H/J/I/X)把平台做成「13 标的固定池 + 多 Agent 信号 + 模拟实盘 + 飞书人工执行」。Owner 2026-05-24 重定位为:

- **Line 1 选股**: 全 A 股 + ETF(5000+)量化初筛「买得起 × 能赚钱」→ 几十~百只候选 → 信息汇总文档(趋势 / 情绪 / 政治舆论 / 板块热度 / 关联板块 + 国内外指数 / 历史)作 **MiroFish** 输入 → MiroFish 输出「哪些板块大概率涨」→ 两报告合称**场外信息** → 场外信息 × 候选股 → 多 agent 辩论收敛 ≤5 只 → **≥2 交易员 agent** 结合预算定「何时买 / 买多少」→ 飞书。
- **Line 2 持仓管理**: 监控持仓盘面 / 量能 / **异动** → 飞书提醒卖出(给卖出价 / 量)、该补仓时提醒补仓。
- **本地知识库(知识图谱)**: 冷启动预装 现成量化策略 + 金融知识 + 基金经理 / 操盘手经验 skill + 规范回测设施;运行中 agent 不断发现 / 验证 / 淘汰策略并进化知识库;理论知识 owner 手动更新。

**安全地基永不动**(详见 §5):永禁真实券商程序化下单 + 飞书人工执行;模拟实盘单一镜像;LLM 不写决策字段 + RiskEngine 纯函数;全层 127.0.0.1;secrets shell-env;fail-closed 对账 / 验收。

## 2. 被推翻的 4 条已锁红线 → 6 份 amendment

Owner 2026-05-24 **有意推翻**以下 4 条(`AskUserQuestion` 全选确认);每条对应一份 amendment,**先写 amendment 再改代码**(§1.5 协议):

| # | 被改红线 | 从 | 到 | amendment |
|---|---------|----|----|-----------|
| 1 | P0-9 universe | 锁定 13 标的 | 全市场 5000+ 量化初筛 | `P0-9-amendment-2026-05-24-full-market-screening.md` |
| 2 | P0-7 仓位 | 百分比仓位三连(单股≤15%/总仓≤70%/单次≤5万) | + budget-adaptive 分层 + `concentration_exception` | `P0-7-amendment-2026-05-24-budget-adaptive-position.md` |
| 3 | P0-8/P0-9 MiroFish | 加分项·硬 cap=1·evidence-only·不入决策 | 升 Line 1 **建议核心**(仍 evidence-only,有界重排,不否决板块) | `P0-8-amendment-2026-05-24-mirofish-advisory-core.md` |
| 4 | P0-10 agents | 固定 4 必经 agent | 可进化多 agent 团队 + ≥2 交易员(fund_manager 仍唯一倡议,builder 仍确定性) | `P0-10-amendment-2026-05-24-evolvable-agent-team.md` |
| 5 | P2-2 自进化 | 保守 3 路径 + deferred + 严禁自动 mutate | 主动策略发现 + 知识图谱进化(**人工 gate 不变**) | `P2-2-amendment-2026-05-24-active-discovery-knowledge-graph.md` |
| 6 | P1-7 成本 | `cost_guard` 事后 trailing-stop | 真·预留硬上限 + `max_debates_per_day` + per-stage retry cap | `P1-7-amendment-2026-05-24-precall-reservation-fanout-cap.md` |

> ④⑤ 共同体现 [[feedback_self_evolution_must_have]]:自进化必须有,但 Owner 锁「人工 gate + 飞书审批 + 重启生效」自主度(§3 进化自主度问题),agent 永不自动改决策路径或风控。⑥ 是把 CLAUDE.md §2.2「¥20/日 hard 暂停」从文字落到代码(Codex 发现现状是事后 trailing-stop)。

## 3. 新红线 A — PIT 数据可复现(point-in-time provenance)

**动机**: universe 从 13 只手挑 → 5000+ 全市场每日扫描,同一份数据要喂 **回测 / 45 日 shadow 验证 / 实时信号解释** 三个消费者。无可复现快照,则三者皆可能「自信地错」,且 P0-6 验收门在验证噪声。A 股是最坏环境(幸存者偏差、除权歧义、财报重述、供应商回填修正)。现有 `DataQualityProvider` 只查新鲜度(staleness/divergence),**不查历史可复现性**。

**红线条文**(全市场数据进入任何回测 / shadow / 实时路径前必须满足):

1. **存原始字节,不只哈希**。新增 `MarketDataSnapshot`,**append-only**,**仿** `backend/broker/persistence/snapshots.py` 的 `BrokerSnapshot` 模式(存完整 canonical payload + checksum + **用前校验** verify-before-adopt);**严禁**发明只存 `sha256` 的变体。一个哈希在原始字节不可独立取回时(供应商重述 / 保留期过期 / 解析依赖升级)无法复现。字段含:`snapshot_id` / `fetch_time_utc` / `trade_date` / `vendor` / `raw_payload`(字节或 content-addressed URI)+ `size` + `encoding` + `compression` / `raw_payload_sha256` / `schema_version`。
2. **覆盖语义(coverage manifest)**。同一 `trade_date` 多次抓取都合法但语义不同。必须记:`granularity`(intraday / EOD / bar-size)、session 窗、`endpoint` + `params`、`requested_universe` vs `delivered_universe`、`missing_symbols`、`completeness_status`、as-of / watermark。仅 `row_count` 会让「部分 universe 抓取」静默冒充「全 universe」。
3. **消费行血缘(SignalInputManifest)**。新增按 `signal_id` 键的 `SignalInputManifest` = `{snapshot_ids, 消费表/主键或 row-hashes, feature_code_version, config_hashes, join/filter params}`。即便快照不可变,`replay <signal_id>` 若只读「整张快照」会重建出比原始 run **更宽**的特征矩阵(join / filter / 幸存者 / lookback 窗之后)。这是「精确复现特征输入」的落点。
4. **复权 pin 到 artifact,不是字符串**。`adjust_policy`(qfq / hfq / raw,**按用途**:因子 / 回测用 qfq,可负担性 / 下单价用 raw)不足以重建复权 OHLCV。必须 pin:**复权因子表 artifact**(存字节,非版本标签)+ 公司行动原始行 + 复权算法版本 + 数值精度 + 舍入规则。分红 / 拆股修正会在不改 policy 字符串的情况下改掉复权特征。
5. **离线 bit-exact replay**。`replay <signal_id>` 必须在**无网络**下,从存储的原始字节 + pin 的 config 哈希,**逐 bit 重建**当时喂给该信号的特征输入。回测 / shadow / 实时三条路径**都**按 `snapshot_id` 读数据。供应商修正(尤以 `fina_indicator_vip` 静默重述)= 新 append-only 快照版本,**靠保留旧字节兜底**。

**落点**: `backend/marketdata_snapshot/`(模块 0,先于一切、可独立测;详见 plan.html Phase K)。

## 4. 新红线 B — InstructionPlan 单一构造点 + provenance-clean

**动机(Codex round-2 纠正)**: import 隔离(模块 X 禁 `import backend.risk`)是**错的边界**——它证明不了「LLM 输出不能决定字段值」。一个 import 干净的模块照样能写 `InstructionPlan(volume=int(llm_json["qty"]), limit_price=float(llm_json["px"]), side=llm_json["action"])`。`frozen + strict + extra="forbid"`(`instruction.py:84`)只挡 schema 形状违规与构造后 mutation,**挡不住**干净 parser 把 LLM 产的整数抄进 `volume`。P0-10 红线是关于**字段值**,旧防护(import 边界)是关于**import 边)**,二者不交。

**真正的安全资产**: 全代码 `InstructionPlan` **只有 2 个构造点**(model 自身 + `backend/services/instruction_plan_builder.py`;`grep "InstructionPlan(" ⊆ {model, builder, tests}`,已核实)。

**红线条文**:

1. **单一构造点**。`InstructionPlan` **只能由 `instruction_plan_builder` 构造**。新增构造站点 = 违规。`scripts/redline-check.sh` 加子检:`grep -rn "InstructionPlan(" backend/` 结果集 ⊆ `{models/instruction.py, services/instruction_plan_builder.py}`(测试目录除外)。
2. **决策字段确定性派生**。`side / volume / limit_price / stock_code` 等可执行字段必须有**确定性、非 LLM 来源**:由 RiskEngine + 组合状态 + sizing 规则派生,**永不**来自 LLM JSON。
3. **4 个 LLM 可写字段隔离**。P0-10 positive list 的 4 个字段(`InstructionPlan.reasoning` / `evidence_collection.content` / `agent_debate_records.{reasoning_text,conclusion}` / `risk_parameter_proposals.proposal_text`)是**展示 / 审计专用**,**严禁**被下游解析进任何数值订单字段。
4. **fund_manager 仅倡议方向**。fund_manager(P0-10 §2.3 唯一 BUY/SELL/HOLD 倡议者)的输出**只能**作为方向倡议种子;`volume / limit_price / 整手舍入`由 builder 计算。
5. **对抗测试先写(RED first)**。给 builder 喂一条 `proposal_text = "BUY 5000 shares of 600519 at limit 1800"` 的辩论记录,断言产出的 `InstructionPlan.volume` **由 sizing 规则派生而非 5000**,且 `proposal_text` **从不**流入任何数值字段。这条测试证明 import 门证明不了的属性。
6. **provenance-clean 优先于 import-clean**。import 隔离子检**保留**(良好卫生),但安全边界从「import-clean」**升级**为「provenance-clean + 单一构造点 + 对抗测试」。

**落点**: `backend/candidate_selector/` + `backend/agents_team/`(Phase M);对抗测试随模块同生。

## 5. 安全地基保留清单(本次**不动**)

1. 永禁真实券商程序化下单;信号 → 飞书 → **用户手动执行**(P0-1 / CLAUDE.md 总纲)。
2. MockBroker 单一镜像;覆盖必经 `ReconciliationApplier::reset_to_snapshot`(P0-5 / P1-2.A)。
3. LLM 不写决策字段 + `RiskEngine` 纯函数 IO-free + 双层守门(builder 早返 + 14-check)(P0-10 / §4 强化)。
4. 全层 127.0.0.1 + SSH tunnel 远程 + secrets shell-env + gitleaks(P1-6)。
5. fail-closed 对账 / 验收;5 种买卖冻结独立并行(P0-5 / P0-6)。
6. config runtime 不可改 + hot-reload 禁用 + 改动走 amendment + git diff + 重启(P0-7 / P0-9 / P0-10 / P1-7)。
7. audit append-only + LLM 严禁写 audit(P1-6)。

## 6. 收敛技术栈(Codex 砍胖后;全 MIT/Apache,全本地)

| 层 | 选型 | 备注 |
|---|------|------|
| Agent 编排 | **LangGraph**(MIT) | RiskEngine / Builder 作纯函数节点,LLM 无边可写 |
| Agent 蓝本 | **读** TradingAgents(Apache-2.0)角色 / prompt 形,LangGraph **原生写**(不作依赖) | 其 LLM 风控团队整个换成纯 Python RiskEngine |
| 回测 | **rqalpha** 唯一权威 A 股执行回测(test-time 差分 oracle,MockBroker 仍单一镜像)+ **vectorbt** 仅因子快筛 | qlib 推迟;rqalpha/vectorbt 是 NOASSERTION,vendoring 前读 LICENSE |
| 冷启动因子 | qlib **Alpha158/360**(~600,MIT)+ GTJA-191 / WorldQuant-101(按论文重写) | 编码成 KG 节点 |
| 知识图谱 | **SQLite + NetworkX** 起步;LightRAG **离线只读**先行 | LadybugDB / LightRAG-live / GraphRAG 推迟(Kùzu 被 Apple 收购归档) |
| 异动 | MVP 仅 **z-score / 布林** + 一个无监督检测器 | IsolationForest / HMM / ruptures / OFI 按需加 |
| 仓位 | 固定分数(Van Tharp)+ ATR 移动止损;**禁马丁格尔、熊市禁补** | 全过 RiskEngine + 飞书人工 |
| 数据源 | 全市场扫描 = **Tushare Pro 5000 档** + `daily_vip` / `fina_indicator_vip`(全市场单次拉取)+ akshare/baostock/adata 兜底 + 新闻 | 数据成本不设 ceiling(P1-7) |

> **数据调用方式锁定(2026-05-24 owner 确认)**:Tushare 走**官方 Python SDK**(`ts.pro_api(token)` → `pro.daily/daily_basic/fina_indicator_vip/...`),**确定性后端调用**(`backend/data/tushare_client.py`,`asyncio.to_thread` 包同步调用),原始 payload 喂 `MarketDataSnapshot`(§3)。**严禁 MCP server / agent-skill 等"LLM 推理时取数"模式进运行时数据路径** —— 它们撞 4 条红线:§3 PIT 可复现(LLM 临时取数无法快照/replay)、§4 LLM-数据隔离(`screening`/`marketdata_snapshot` 禁 import LLM)、L-002 全市场纯量化筛 0 LLM、P1-7 ¥20/日成本(5000 标的走 LLM 工具循环成本荒谬)。MCP 顶多作开发期交互探查的可选工具,不进产品代码路径。

详见 `docs/research/{coldstart-knowledge-and-backtest,agent-architecture-self-evolution,knowledge-graph-and-anomaly-detection}.md`。

## 7. 8 阶段程序 + 模块图 + MVP(任务细节见 plan.html Phase K–T)

| Phase | 子模块(各自 dir + CLAUDE.md) | 内容 | 红线动作 |
|---|---|---|---|
| **K 数据地基** | `backend/marketdata_snapshot/` | PIT 快照(§3)+ SignalInputManifest + 离线 replay | 本文 §3 |
| **L 选股地基** | `backend/screening/` + `backend/budget_policy/` | 全市场纯量化筛 + BudgetTierPolicy + `NO_COMPLIANT_TRADE` | P0-9 + P0-7 amend |
| **M 候选+辩论** | `backend/candidate_selector/` + `backend/agents_team/` | 确定性 CandidateSelector(≥3 量化名额)+ LangGraph 4 必经 agent 单轮 + cost 硬上限 | 本文 §4 + P1-7 amend |
| **★MVP gate** | — | 双线端到端(BUY + SELL)on 快照;无自进化 / 无 MiroFish 核心 / 无全异动栈 | — |
| **N 监控** | `backend/monitoring/` | Line-2 z-score/布林异动 → 飞书 SELL + 补仓;suspension 接入 | — |
| **O MiroFish 核心** | `backend/mirofish/`(扩展) | 升建议重排器(仍 evidence-only,有界 ≤1 分位,不否决板块) | P0-8 amend |
| **Q 知识图谱** | `backend/knowledge_graph/` | SQLite+NetworkX KG + 冷启动因子 / 启发式 + LightRAG 离线 | — |
| **R 自进化** | `backend/strategy_evolution/` | 发现 / 验证 / 淘汰 + LiveArtifactRegistry + 45 日 shadow + 人工 gate | P2-2 amend |
| **T 交易员+全栈** | `backend/agents_team/` + `backend/monitoring/`(扩展) | ≥2 交易员 agent(人格卡)+ 全异动栈 + rqalpha 回测 | P0-10 amend |

**模块依赖序**: K(模块 0,可独立测)→(L 的 screening + budget_policy 并行)→ M(candidate_selector + agents_team)→ N(与 M 并行)→ O → Q → R(依赖 Q)→ T。

**MVP 定义**(Owner 2026-05-24 确认为第一个里程碑): 全市场纯量化筛 → `BudgetTierPolicy` → top-N → **单轮 4 必经 agent** 辩论 → RiskEngine 14-check → 飞书 BUY;+ 持仓 z-score/布林异动 → 飞书 SELL。全跑在含 raw bytes + SignalInputManifest 的版本化快照上。

## 8. Codex 2 轮 vetted 关键约束(plan.html 任务必须设计进去)

- **预算真相**: 单次 4-agent 辩论 ~¥0.4,deepseek 0.2 / qwen 1.0 / kimi 2.1/8.4 RMB/M(`fallback.py:48`)。¥20/日对单例路径 20-40× 余量。真杀手是**乘法 fan-out**(¥0.8 × 20 候选 = ¥16)→ 钉「**一次辩论 / 每日 shortlist,不是 per candidate**」+ `max_debates_per_day` 常量(P1-7 amend)。Line-1 一日一次(09:00),Line-2 纯量化轮询、LLM 仅触发式(去重 + 日上限),且写**同一** `llm:usage:{utc_date}` 计数器。
- **MiroFish 建议 vs 决策边界**(§4 配套): MiroFish 仅写 `evidence_collection`(by-construction 不变);纯 Python `CandidateSelector` 在固定 git 版本权重下读证据出候选;**资格纯量化**;MiroFish 只能在合格集内**有界重排(≤1 分位)**、top-N 截断后仍**保 ≥3 量化名额**、**永不否决板块**、缺席有量化兜底。LLM 仅在确定性预筛出 ~50-100 小包后才介入,超固定候选数不调 LLM。
- **`concentration_exception`**(P0-7 amend): 由 BudgetTierPolicy 上游纯函数设,RiskEngine **独立再校验**(非绕过);需定第 15 check 还是改锁定的 14(`risk_summary` min=max=14 是 schema 常量)。
- **T+1 已建**: `mock_broker.py:592` advance_trading_day + `available_volume`(:392 拒超额 SELL)+ RiskEngine Check 4(:331)。Line-2 SELL 读 `available_volume`(已结算),非总持仓。
- **LiveArtifactRegistry**(R 阶段): startup 从不可变 config 载入批准哈希集 `{strategy_code_hash, feature_def_hash, prompt_version_hash, anomaly_model_hash, rag_index_version}`;实时路径**拒绝**任何不在集内的哈希;**无 runtime 路径**加哈希。对抗测试先写(种入未批准高 Sharpe 策略 → 断言实时 selector 不可读 / 不可执行;且**有效但未 pin** 的哈希也被拒)。

## 9. 修订记录追加

`docs/plan.html` 修订记录 + SESSION_LOG 将同步追加。下一批 SESSION_LOG 条目应**首先**引用本文(`R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md`)及 6 份 amendment,再开始 Phase K 实施。本文 §3 / §4 两条新红线将写入 `CLAUDE.md §2`,并加入 `scripts/redline-check.sh` 子检。
