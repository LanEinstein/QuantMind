# QuantMind 跨 session 协作上下文

> **🚀 新 session 开工**:先读 [`docs/SESSION-KICKOFF.md`](docs/SESSION-KICKOFF.md)(通用开工协议:检查上次节点 → 开工改状态 → 完成改 done+记录 → 末尾一句话指下一步),按它无缝衔接。

QuantMind = 多 Agent 投研信号 + 模拟实盘 + 飞书人工执行。**永禁**真实券商程序化下单。
决策对齐期 ✅(P0+P1+P2)+ 旧 S/A-H/J/I/X 基础设施实施期 ✅;**2026-05-24 起 = 双线重构 v2 实施期(Phase K-T)** — owner 判定旧"锁定 13 标的"定位不够,重定位为**双线架构(全市场量化选股 + 持仓异动监控)+ 本地知识图谱 + 自进化多 agent**,在已建 backend 上演进。
SSoT = `docs/plan.html`(原 S/A-H/J/I/X + 新增 Phase K-T 40 任务)。**双线重构总纲 = `docs/decisions/R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md` + 6 amendment(见 §2.0)**;红线条文 → 对应 `docs/decisions/{编号}.md` §2。

## 1. 项目进度管理协议(**强制,违反 = 违规实现**)

详细表格见 `docs/plan.html#protocol`,本节冲突以 plan.html 为准。

1. **开始前调查**:`grep` `docs/plan.html` 的 `#session-log` + `status: "doing"`/`status: "blocked"`,定位上次停在哪、在途任务。**确定本次活动 Phase**(最早一个含 `status="todo"` 的字母 Phase)以及该 Phase 内所有 `depends` 已满足的任务集 — **本次 session = 该 Phase 全套未阻塞任务,不是单子任务**(2026-05-12 用户锁定)。
2. **认领任务**:把该 Phase 当前可做的所有任务 `TASKS` 状态 `todo`→`doing` + 填 `session_date: "YYYY-MM-DD"`;依赖未完成改 `blocked` + `notes` 写 `blocked_by:`。
3. **完成任务**:每个任务自己一次 feature commit 后,状态改 `done` + 回填 `commit:`(真实 hash 7 位) + `notes:`(What+Why 1-2 句)。一个 commit / 一个任务,git 历史按任务粒度可读。
4. **结束 session**(Phase 全部 done 或确实无法继续后才结束):一次性 docs-only commit 把 `SESSION_LOG` 顶部追加一条覆盖整个 Phase 的 `{date, session: "#N phase-X full", owner, state_in, actions(每任务 + commit hash 枚举), commit, next}`;"修订记录"加一行。**不要**每个任务都跟一次 docs-only commit。
5. **改决策边界**:先新增 `docs/decisions/*-amendment-YYYY-MM-DD-{原因}.md`,再改代码;无 amendment 的行为差异 = 违规。

> 报告"完成"前:SSoT 必须已改 ✅ + 真实 commit hash + 报告里写改了什么。只改 localStorage 不算项目进度。

## 2. 核心红线(违反即停;细则见 `docs/decisions/`)

**2.0 双线重构 v2 amendment 总览(2026-05-24,总纲 R0)** Owner 有意推翻 4 条 + 新锁 2 条;安全地基(永禁真实下单 / 飞书人工 / 127.0.0.1 / LLM 不写决策 / RiskEngine 纯函数 / 人工 gate)**全留**。下面 §2.3/2.4/2.5/2.10/2.12 已内联 amendment 指针。
- **推翻 4**:P0-9 13 标的→全市场筛(`P0-9-amendment-2026-05-24`)/ P0-7 +budget-adaptive+concentration_exception(`P0-7-amendment-2026-05-24`)/ P0-8 MiroFish 加分项→Line1 建议核心仍 evidence-only(`P0-8-amendment-2026-05-24`)/ P0-10 4 固定→可进化团队+≥2 交易员(`P0-10-amendment-2026-05-24`)/ P2-2 deferred→主动发现+知识图谱(`P2-2-amendment-2026-05-24`)/ P1-7 事后→真·预留+fan-out cap(`P1-7-amendment-2026-05-24`)。
- **新红线 2**:① **PIT 数据可复现** — 全市场 `MarketDataSnapshot` 存**原始字节**+checksum(仿 BrokerSnapshot,严禁 hash-only)+ coverage manifest + SignalInputManifest(消费行血缘)+ 复权因子 artifact pin + 离线 bit-exact `replay <signal_id>`;回测/shadow/实时三路径同源。② **InstructionPlan 单一构造点** — 仅 `instruction_plan_builder` 可构造(`grep "InstructionPlan(" ⊆ {model, builder, tests}`);`side/volume/limit_price` 确定性派生,**永不**来自 LLM JSON;4 可写文本字段永不解析进数值订单字段(import 隔离证明不了字段值来源 → 升 provenance-clean + 对抗测试先写)。
- **MVP = Phase K+L+M+N** 双线端到端 on 快照(无自进化/无 MiroFish 核心/无全异动栈)。经 Codex 2 轮红队(Conditional GO)。

**2.1 模式(P0-1)** simulation_auto(always-on)+ feishu_interactive(可叠加);唯一开关 `FEISHU_INTERACTIVE_ENABLED`。旧 `AUTHORIZATION_MODE`×`QUANTMIND_PHASE` 矩阵 Phase A 一次性破坏式删除;grep 必空。切换 = 账户生命周期事件(归档 + MockBroker reset + 飞书初始化对账 + 期间冻结)。

**2.2 LLM 权限(P0-10)** 可写仅 4 类:`InstructionPlan.reasoning` / `evidence_collection.content` / `agent_debate_records.{reasoning_text,conclusion}` / `risk_parameter_proposals.proposal_text`。严禁写决策字段 / `RiskCheckSummary` / `evidence_id` 命名 / MockBroker / `WatchlistPolicy` / `DataQualityState` / `ReconciliationTicket` / `AcceptanceReport` / `RiskConfig` / 飞书消息文本。单调用 30s + 0 重试;LLM 全停 1h 系统中断;¥100/日 hard 全 LLM 暂停(2026-05-26 P1-7-amendment ¥20→¥100,见 §2.10)。严禁参与回报解析 / 对账 / 验收 / 数据质量 / RiskEngine。Pydantic strict + `extra="forbid"` + lint 三层守门。

**2.3 多 Agent 守门(P0-10)** 4 必经 Agent `fundamental_analyst` + `technical_analyst` + `risk_officer` + `fund_manager`,任一缺失即降级 HOLD。`fund_manager` 唯一 BUY/SELL/HOLD 倡议者;`debate_round_count ≥ 1` 必经;`fund_manager_shadow_baseline` 永不入决策路径。双层守门:Builder 五道早返 + RiskEngine 14-check 独立并行。 **(2026-05-24 P0-10-amendment:4 必经保留 + 新增 ≥2 交易员 agent 人格卡 frozen git;fund_manager 仍唯一倡议方向;LangGraph 编排,RiskEngine/Builder 作纯节点 LLM 无边可写;InstructionPlan 单一构造点见 §2.0。)** **(2026-05-25 P0-10-amendment-line2:§2.3 = Line-1 LLM 选股路径;Line-2 持仓监控为独立**确定性零 LLM** 路径,SELL/ADD 方向由确定性 AnomalyDetector/AddPositionEvaluator 派生不经 fund_manager/辩论,经 builder 新增 `assemble_monitoring_plan` 确定性构造(单一构造点不破)+ 5 早返 + RiskEngine 14-check + 飞书人工;`debate_round_count=1`=确定性监控评估轮,`signal_id` 用 `LINE2-MON-` 前缀供 audit 区分。)** **(2026-05-26 P1-7-amendment:Line-1 每日 09:35 cron 按 `CandidateSelector` shortlist **顺序多候选辩论** —— 每只 `build_lead_context→run_shortlist(单元素辩论)→assemble_plan(单一构造点)→RouteCoordinator`,RiskEngine REJECTED/HOLD/DEGRADED/非-BUY 即 fallthrough 下一只,VALIDATED BUY 收集(basket,收齐所有);推翻旧"只辩 top-1 lead"/"一次辩论每日";fan-out 受 `max_debates_per_day`(默认8)+ 日 ¥100 真·预留双重 fail-closed bound;篮子额外受熔断 ≤5单/日 + check-10 bound。lead 涨停被拒=RiskEngine 正确履职→辩下一只,绝不绕过拒单。)**

**2.4 风险 / 配置 / 路由(P0-7 / P0-9 / P1-5)** `RiskConfig` / `agent_models.yaml` / `watchlist_policy.yaml` / `broker.yaml` runtime 不可改 + hot-reload 全禁;修改 = git diff + amendment + 重启。`backend/api/{risk,watchlist,llm,agents,cost}*.py` 仅 GET;全后端仅 2 写端点(`POST /api/execution-reports` + `POST /api/reconciliation-tickets/{id}/decide`)。`backend/risk/` 严禁 `import backend.{llm,agents,mirofish,data}`,纯函数无 IO。仓位三连 单股 ≤15% / 总仓 ≤70% / 单次 ≤5 万;熔断 ≤5 单/日 + 日亏 -5% + 连亏 3 笔 + 60min 冷却(SELL 不熔断);universe 沪深主板+创业板+ETF,禁 ST/科创/北交/可转债,禁涨停 BUY / 跌停 SELL,long-only。Watchlist 锁 13 标的(4+3+3+3,必备 ETF `510300`/`510500`/`159949`);排除四件套在 Builder 第五道早返。 **(2026-05-24 P0-9-amendment:13 标的 → 全市场主板+创业板+ETF universe 规则;排除四件套前移 `screening` 硬排除,Builder 早返保留为最后防御;科创/北交/ST/可转债仍永禁。P0-7-amendment:+budget-adaptive 分层 Micro<¥2k 仅 ETF / `NO_COMPLIANT_TRADE` 一等 outcome / ETF `concentration_exception` RiskEngine 独立再校验;Normal≥¥10k 三连不变。)**

**2.5 数据情报(P0-8)** 主备 staleness ≤5s / divergence ≤0.3% / freshness ≥60s。全 watchlist 30s 个股快照;多域 5 源(财经 2 + 时政 1 + 全球 2),跨域不去重。MiroFish 加分非核心:事件 cap=1 + 17:00 复盘双路径;输出仅入 `evidence_collection`(MIROFISH- 前缀)不入 `RiskCheckSummary`。`evidence_id` 5 前缀 `NEWS-`/`MIROFISH-`/`MARKET-`/`RISK-`/`DEBATE-`。 **(2026-05-24 P0-8-amendment:MiroFish 升 Line1 建议核心,仍 evidence-only by-construction;确定性 `CandidateSelector` 出候选,MiroFish 仅合格集内有界重排 ≤1 分位、永不否决板块、top-N 后保 ≥3 量化名额、缺席量化兜底。PIT 数据可复现见 §2.0 新红线①。**数据源(P0-8-amendment-2026-05-24-tushare-data-source,K-001 落地)**:新增 Tushare Pro 全市场扫描层 = 官方 Python SDK only(`ts.pro_api`,严禁 MCP/skill 进运行时数据路径);adata/akshare 实时主备 + 新闻 5 源不变 + akshare/baostock/adata 兜底;`TUSHARE_TOKEN` 异质凭证不入 LLM 3+飞书 5 池(EXPECTED_POOL_SIZE 仍 8),数据成本不设 ceiling。模块 0 `backend/marketdata_snapshot/`(K-001..K-006 done)= 存原始字节+checksum / coverage / 消费行血缘 / 复权 pin / 离线 replay,纯模块零 backend.* 子包 import。)**

**2.6 飞书(P0-2 / P0-4)** 永禁 HTTPS 回调入站;事件订阅仅 `lark-oapi` WebSocket(3s ack);`tenant_access_token` 仅内存。备用 webhook 仅发系统告警,**绝不**发买卖 / 对账 / 澄清。纯文本 + 严格正则;不通过 = AMBIGUOUS 绝不更新 MockBroker;严禁猜 `instruction_id`。所有飞书消息必经 `renderer.py`(防 prompt injection)。

**2.7 InstructionPlan / 账本(P0-3 / P0-4 / P0-5 / P1-2.A-C)** `instruction_id` 严格 `^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$`。HOLD 永不路由不发飞书;`parse_ok=False` 强制 HOLD;状态机 `DRAFT→VALIDATED→DISPATCHED→FILLED/EXPIRED/REJECTED/AMBIGUOUS`,`model_copy(update={"status":...})` 违规。核心结构全 frozen Pydantic v2 strict + `extra="forbid"`。16:00 系统主动发对账(`RECON-{YYYYMMDD}-{seq}`);阈值 cash 1 元 / volume 0% / cost 0.01 元;超阈值 ticket fail-closed 三选一。**5 种买卖冻结独立并行**:切换 / OPEN ticket / 熔断冷却 / DataQualityState / EOD pipeline;永禁聚合 `frozen=true`。MockBroker 单一镜像,覆盖必经 `ReconciliationApplier::reset_to_snapshot`,直接 mutation `_cash`/`_positions`/`_trades` 违规。撮合 ALL_OR_NONE + 涨跌停 at-fill recheck(`price_limit_violation_at_fill` ≠ engine `limit_up_block`)+ 分板块滑点 1.5/1.5/3.5/1.5 bp + 深市过户费 0.00341% 双边。持久化 hybrid delta + EOD snapshot + checksum 失败拒自动恢复 + append-only 8 红线。30s intraday MTM + per-position `EquityPoint`;三级回退 Redis≤60s → Mongo≤300s → degraded;禁 cost_price fallback。

**2.8 验收(P0-6)** 45 交易日滚动 + 静态 `config/holidays.yaml`(不引入 akshare 节假日 API)。5 稳定性 指令完整率 ≥95% / 回报准确率 ≥99% / 数据缺失 ≤1% / LLM 超时 ≤5% / 信号生成 ≥95%;3 策略 最大回撤 ≤8% / PnL ≥0 / 沪深 300 累计超额 ≥0。5 类 P0 系统级中断重置;reconciliation freeze 暂停而非重置。切换 `feishu_on` 必经 `acceptance.can_switch_to_feishu_on()`,严禁 env/CLI 绕过。

**2.9 安全 / 可观测(P1-6 + P0-2-amendment-2026-05-16)** LLM 3 key + 飞书 5 凭证(`FEISHU_APP_ID`/`FEISHU_APP_SECRET`/`FEISHU_VERIFY_TOKEN`/`FEISHU_ENCRYPT_KEY`/`FEISHU_ALERT_CHAT_ID`)全 `~/.bashrc`;**P0-2 amendment 2026-05-16 锁定:owner 飞书租户禁用 custom-bot,凭证池 6→5,告警通道走自建应用同款 OpenAPI 发到 FEISHU_ALERT_CHAT_ID;`FEISHU_CUSTOM_BOT_*` 永禁存在,出现即 warning + audit**。`.env` 严禁 `LLM_KEY/FEISHU_*/FEISHU_ALERT_*` 前缀;启动期 `secrets_validator` fail-fast。fingerprint = SHA256[:8],严禁 plaintext / 末四位;5 类强制轮换 + 12 月 warning。全层 127.0.0.1 only(Backend + Vite + Mongo + Redis + Nginx),远程仅 SSH tunnel;不加本机 auth middleware;httpx 出站 `local_address="0.0.0.0"`(IPv4 only egress)。Mongo `audit_events` TTL 180 天 + JSONL 30 天双写,34 类锁定(类 5 evolution 7 类 actor=SYSTEM/SCHEDULER);LLM 严禁写 audit;调试事件走 `logs/quantmind.jsonl`。gitleaks pre-commit 强制,严禁 `--no-verify`。

**2.10 成本预算(P1-7)** LLM only:日 ¥100 hard(唯一全 LLM 熔断;2026-05-26 amendment ¥20→¥100)+ 月 ¥440 soft(50/80/100% 三节点,100% 不停;不变)+ Kimi 日 ¥4(不变)。数据 / 运维不设 ceiling。`cost_guard` + `SoftDegradeManager` 严禁 `import backend.{llm,agents,mirofish,data}`。软触发 ¥70=70% 优先关 Kimi escalation;严禁削减 4 必经 Agent / 降 fast 频次 / 全 deepseek-only。告警仅飞书 + audit + Phase B 成本拆解面板;严禁 SMTP / Slack / Discord;Alerter dedup_15min。 **(2026-05-24 P1-7-amendment:¥/日 hard 从事后 trailing-stop 升**真·预留**(preflight 预留+拒超+对账)+ `max_debates_per_day` + per-stage retry cap + 所有 LLM 含 Line-2 异动写同一 `llm:usage:{utc_date}` 计数器。**2026-05-26 P1-7-amendment**:日 hard ¥20→¥100(唯一熔断阈值上调;soft 0.7 比例不变=¥70;月 ¥440 + Kimi ¥4 不变);**推翻"一次辩论/每日 shortlist 非 per candidate"** → Line-1 按 shortlist 顺序**多候选辩论**(每候选 1 次 4-agent,REJECTED→fallthrough 下一只),fan-out 改由 `max_debates_per_day`(默认 8)+ 日 ¥100 真·预留双重 fail-closed bound 承担;收集语义=basket(收齐所有 VALIDATED BUY,额外受熔断 ≤5单/日 + check-10 bound)。)**

**2.11 前端(P1-5)** MVP 7 + Phase B 4 页 + 决策闭环 4 分组永锁;Simulation.vue 留代码不进菜单。WS 12 类(原 6 + 新 8 - 删 2 `auth_mode_change`/`approval_update`);SSE 仅 LLM 流式。用户回报双路径同 `ExecutionReportApplier`,前端 JS 正则镜像与 `regex_patterns.py` 单一真相源,不一致 fail-closed。三层 reason 抽屉 3 tab(Builder / Engine / Broker)命名空间区分。前端不存任何**凭证**到 `localStorage`/`sessionStorage`/`cookie`(UI 偏好如 focusMode 不违规)。

**2.12 自进化(P2-2,实施期 deferred)** 启用 3 路径:DSPy GEPA 离线 prompt(≤¥5/次)+ RAG provenance-gated 白名单(arxiv/semanticscholar/openreview/白名单 GitHub releases/akshare changelog)+ FinMem exemplars(≤3/prompt)。严禁 7 路径:fine-tune / online learning / RLHF / DPO / continual SFT / 自动 mutate config / 新 LLM provider / LLM 自动决策权。全人工 gate + 飞书主动通知;shadow 45 日完全沿用 P0-6;文件式 prompt registry + git + restart;严禁 MLflow/LangSmith。BrokerScheduler 第五 cron `evolution_shadow_run` 22:00 mon-fri;Phase A/B 实施期严禁写任何自进化代码。 **(2026-05-24 P2-2-amendment:deferred 解除 → Phase R 主动策略发现 + 知识图谱(SQLite+NetworkX,双时态+SUPERSEDES)+ `LiveArtifactRegistry`(startup 只认批准哈希,对抗测试先写);人工 gate + 45 日 shadow + 飞书 + git+restart 不变;7 禁不变。MVP 阶段 K-N 仍不写自进化代码。)**

## 3. 工程原则

- 注释 / commit 英文;UI 文档中文;public function 必须 type hints + docstring(WHY 而非 WHAT)。
- 不可变结构优先(frozen dataclass / NamedTuple / Pydantic frozen);文件 200-400 行典型 / 800 上限;函数 <50 行;嵌套 <4 层。
- 测试金字塔 + ruff 全绿才允许 commit;非 risk >70%,risk >95%。**测试通过 ≠ 闭环可用**(audit 反面教材 1139 绿但 RiskEngine 不接订单;断言要覆盖被谁调用、贯穿到哪)。
- Codex review = **代码任务强制前置门禁**(2026-05-24 用户锁定,推翻 2026-05-12 手动-only):**但凡有代码编写的任务,commit 之前先跑 codex-review,修复完所有 P0/P1/P2(CRITICAL/HIGH/MEDIUM)bug 后再 commit + push**。本地门禁(pytest + ruff + redline-check + 前端 type-check + vitest 全绿)是前置必要条件但不充分;codex-review 是 commit 前的最后一道门。按 codex-review skill 跑(1 cycle 起步,major 用户可指定 5 轮 R1-R5,输出 `docs/reviews/{task_id}-codex-review-summary.md`),修完 P0/P1/P2 后再提交。**例外**:docs-only / 配置文档 / SSoT 记账 commit 不需 codex("有代码编写的任务"才触发)。codex CLI 不可用时须上报 owner 拿决策,**严禁**静默跳过推未审代码。绿测试 ≠ commit-safe(印证 [[feedback_codex_findings_real]])。
- fail-closed for data corruption / fail-open for infra glitches;完整升级路径优先,不为省工作量妥协可用性。

## 4. 重要文档

| 路径 | 用途 |
|------|------|
| **`docs/plan.html`** | **实施 SSoT — 任务清单(原 S/A-H/J/I/X + 新 K-T)+ Session Log + 维护协议** |
| **`docs/decisions/R0-two-line-rearch-...-2026-05-24.md`** | **双线重构 v2 总纲 + 2 新红线(PIT 可复现 / 单一构造点);先读** |
| `docs/decisions/` | 决策 + amendments(含 2026-05-24 R0 + 6 amendment);红线细则 §2 + §2.0 |
| `docs/research/` | 双线重构调研 dossier(冷启动+回测 / agent 架构 / 知识图谱+异动) |
| `docs/quantmind_project_audit_2026-05-07.md` | 接手第一份必读(2026-05-08 重写) |
| `docs/reviews/` | codex review + 阶段 summary |
| `~/.claude/projects/-home-ps-papers-QuantMind/memory/MEMORY.md` | 跨 session 自记忆索引 |
| `~/.claude/rules/` | 全局规范 |

## 5. 操作速查

```bash
FEISHU_INTERACTIVE_ENABLED=false /home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app --port 8000
cd frontend && npm run dev   # :9276(Phase A 后必锁 127.0.0.1)
/home/ps/anaconda3/envs/zhanglan/bin/pytest -q --cov=backend --cov-fail-under=70
pytest -q backend/risk --cov=backend/risk --cov-fail-under=95
cd frontend && npm run type-check && npm run test -- --run && npm run build

# 红线扫描精选(完整版见 plan.html#gates)
grep -rn "AUTHORIZATION_MODE\|QUANTMIND_PHASE\|live_confirm" backend/                          # Phase A 后必空
grep -rnE "@router\.(post|put|patch|delete)" backend/api/                                       # 仅留 2 写端点
grep -rn "from backend\.\(llm\|agents\|mirofish\|data\)" backend/risk/ backend/services/cost_guard.py
grep -rnE "host\s*[=:]\s*['\"]?0\.0\.0\.0" frontend/vite.config.ts deploy/                       # 必空
grep -rnE "DEEPSEEK_API_KEY|DASHSCOPE_API_KEY|MOONSHOT_API_KEY|FEISHU_" .env .env.example       # 必空
grep -rnE "FEISHU_CUSTOM_BOT_(WEBHOOK_URL|SIGN_SECRET)" backend/ tests/ .env* 2>/dev/null      # 必空 (P0-2-amendment-2026-05-16)
# 双线重构 v2(2026-05-24)新红线扫描
grep -rn "InstructionPlan(" backend/ | grep -vE "models/instruction\.py|instruction_plan_builder\.py"   # 必空 (R0 单一构造点;tests 除外)
grep -rnE "total_codes.*13|watchlist_size_must_equal" config/                                   # Phase L 后必空 (P0-9-amendment 全市场)
grep -rn "import backend\.\(llm\|agents\|mirofish\)" backend/screening/ backend/budget_policy/ backend/candidate_selector/ backend/marketdata_snapshot/  # 必空 (纯量化隔离)
```

LLM key:`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY`。
飞书:`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_VERIFY_TOKEN` / `FEISHU_ENCRYPT_KEY` / `FEISHU_ALERT_CHAT_ID`(告警群 open_chat_id;P0-2-amendment-2026-05-16 锁定:owner 飞书租户禁用 custom-bot,告警通道走自建应用同款 OpenAPI 不走 webhook;`FEISHU_CUSTOM_BOT_*` 永禁存在)。
