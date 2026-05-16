# QuantMind 跨 session 协作上下文

QuantMind = 多 Agent 投研信号 + 模拟实盘 + 飞书人工执行。**永禁**真实券商程序化下单。
决策对齐期 ✅ 全完成(P0+P1+P2);**下一站 = Phase A 实施期**(或 dedicated 计划文档 session)。
SSoT = `docs/plan.html`(61 任务,十一阶段 S/A-H/I/X)。红线条文 → 对应 `docs/decisions/{编号}.md` §2。

## 1. 项目进度管理协议(**强制,违反 = 违规实现**)

详细表格见 `docs/plan.html#protocol`,本节冲突以 plan.html 为准。

1. **开始前调查**:`grep` `docs/plan.html` 的 `#session-log` + `status: "doing"`/`status: "blocked"`,定位上次停在哪、在途任务。**确定本次活动 Phase**(最早一个含 `status="todo"` 的字母 Phase)以及该 Phase 内所有 `depends` 已满足的任务集 — **本次 session = 该 Phase 全套未阻塞任务,不是单子任务**(2026-05-12 用户锁定)。
2. **认领任务**:把该 Phase 当前可做的所有任务 `TASKS` 状态 `todo`→`doing` + 填 `session_date: "YYYY-MM-DD"`;依赖未完成改 `blocked` + `notes` 写 `blocked_by:`。
3. **完成任务**:每个任务自己一次 feature commit 后,状态改 `done` + 回填 `commit:`(真实 hash 7 位) + `notes:`(What+Why 1-2 句)。一个 commit / 一个任务,git 历史按任务粒度可读。
4. **结束 session**(Phase 全部 done 或确实无法继续后才结束):一次性 docs-only commit 把 `SESSION_LOG` 顶部追加一条覆盖整个 Phase 的 `{date, session: "#N phase-X full", owner, state_in, actions(每任务 + commit hash 枚举), commit, next}`;"修订记录"加一行。**不要**每个任务都跟一次 docs-only commit。
5. **改决策边界**:先新增 `docs/decisions/*-amendment-YYYY-MM-DD-{原因}.md`,再改代码;无 amendment 的行为差异 = 违规。

> 报告"完成"前:SSoT 必须已改 ✅ + 真实 commit hash + 报告里写改了什么。只改 localStorage 不算项目进度。

## 2. 核心红线(违反即停;细则见 `docs/decisions/`)

**2.1 模式(P0-1)** simulation_auto(always-on)+ feishu_interactive(可叠加);唯一开关 `FEISHU_INTERACTIVE_ENABLED`。旧 `AUTHORIZATION_MODE`×`QUANTMIND_PHASE` 矩阵 Phase A 一次性破坏式删除;grep 必空。切换 = 账户生命周期事件(归档 + MockBroker reset + 飞书初始化对账 + 期间冻结)。

**2.2 LLM 权限(P0-10)** 可写仅 4 类:`InstructionPlan.reasoning` / `evidence_collection.content` / `agent_debate_records.{reasoning_text,conclusion}` / `risk_parameter_proposals.proposal_text`。严禁写决策字段 / `RiskCheckSummary` / `evidence_id` 命名 / MockBroker / `WatchlistPolicy` / `DataQualityState` / `ReconciliationTicket` / `AcceptanceReport` / `RiskConfig` / 飞书消息文本。单调用 30s + 0 重试;LLM 全停 1h 系统中断;¥20/日 hard 全 LLM 暂停。严禁参与回报解析 / 对账 / 验收 / 数据质量 / RiskEngine。Pydantic strict + `extra="forbid"` + lint 三层守门。

**2.3 多 Agent 守门(P0-10)** 4 必经 Agent `fundamental_analyst` + `technical_analyst` + `risk_officer` + `fund_manager`,任一缺失即降级 HOLD。`fund_manager` 唯一 BUY/SELL/HOLD 倡议者;`debate_round_count ≥ 1` 必经;`fund_manager_shadow_baseline` 永不入决策路径。双层守门:Builder 五道早返 + RiskEngine 14-check 独立并行。

**2.4 风险 / 配置 / 路由(P0-7 / P0-9 / P1-5)** `RiskConfig` / `agent_models.yaml` / `watchlist_policy.yaml` / `broker.yaml` runtime 不可改 + hot-reload 全禁;修改 = git diff + amendment + 重启。`backend/api/{risk,watchlist,llm,agents,cost}*.py` 仅 GET;全后端仅 2 写端点(`POST /api/execution-reports` + `POST /api/reconciliation-tickets/{id}/decide`)。`backend/risk/` 严禁 `import backend.{llm,agents,mirofish,data}`,纯函数无 IO。仓位三连 单股 ≤15% / 总仓 ≤70% / 单次 ≤5 万;熔断 ≤5 单/日 + 日亏 -5% + 连亏 3 笔 + 60min 冷却(SELL 不熔断);universe 沪深主板+创业板+ETF,禁 ST/科创/北交/可转债,禁涨停 BUY / 跌停 SELL,long-only。Watchlist 锁 13 标的(4+3+3+3,必备 ETF `510300`/`510500`/`159949`);排除四件套在 Builder 第五道早返。

**2.5 数据情报(P0-8)** 主备 staleness ≤5s / divergence ≤0.3% / freshness ≥60s。全 watchlist 30s 个股快照;多域 5 源(财经 2 + 时政 1 + 全球 2),跨域不去重。MiroFish 加分非核心:事件 cap=1 + 17:00 复盘双路径;输出仅入 `evidence_collection`(MIROFISH- 前缀)不入 `RiskCheckSummary`。`evidence_id` 5 前缀 `NEWS-`/`MIROFISH-`/`MARKET-`/`RISK-`/`DEBATE-`。

**2.6 飞书(P0-2 / P0-4)** 永禁 HTTPS 回调入站;事件订阅仅 `lark-oapi` WebSocket(3s ack);`tenant_access_token` 仅内存。备用 webhook 仅发系统告警,**绝不**发买卖 / 对账 / 澄清。纯文本 + 严格正则;不通过 = AMBIGUOUS 绝不更新 MockBroker;严禁猜 `instruction_id`。所有飞书消息必经 `renderer.py`(防 prompt injection)。

**2.7 InstructionPlan / 账本(P0-3 / P0-4 / P0-5 / P1-2.A-C)** `instruction_id` 严格 `^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$`。HOLD 永不路由不发飞书;`parse_ok=False` 强制 HOLD;状态机 `DRAFT→VALIDATED→DISPATCHED→FILLED/EXPIRED/REJECTED/AMBIGUOUS`,`model_copy(update={"status":...})` 违规。核心结构全 frozen Pydantic v2 strict + `extra="forbid"`。16:00 系统主动发对账(`RECON-{YYYYMMDD}-{seq}`);阈值 cash 1 元 / volume 0% / cost 0.01 元;超阈值 ticket fail-closed 三选一。**5 种买卖冻结独立并行**:切换 / OPEN ticket / 熔断冷却 / DataQualityState / EOD pipeline;永禁聚合 `frozen=true`。MockBroker 单一镜像,覆盖必经 `ReconciliationApplier::reset_to_snapshot`,直接 mutation `_cash`/`_positions`/`_trades` 违规。撮合 ALL_OR_NONE + 涨跌停 at-fill recheck(`price_limit_violation_at_fill` ≠ engine `limit_up_block`)+ 分板块滑点 1.5/1.5/3.5/1.5 bp + 深市过户费 0.00341% 双边。持久化 hybrid delta + EOD snapshot + checksum 失败拒自动恢复 + append-only 8 红线。30s intraday MTM + per-position `EquityPoint`;三级回退 Redis≤60s → Mongo≤300s → degraded;禁 cost_price fallback。

**2.8 验收(P0-6)** 45 交易日滚动 + 静态 `config/holidays.yaml`(不引入 akshare 节假日 API)。5 稳定性 指令完整率 ≥95% / 回报准确率 ≥99% / 数据缺失 ≤1% / LLM 超时 ≤5% / 信号生成 ≥95%;3 策略 最大回撤 ≤8% / PnL ≥0 / 沪深 300 累计超额 ≥0。5 类 P0 系统级中断重置;reconciliation freeze 暂停而非重置。切换 `feishu_on` 必经 `acceptance.can_switch_to_feishu_on()`,严禁 env/CLI 绕过。

**2.9 安全 / 可观测(P1-6 + P0-2-amendment-2026-05-16)** LLM 3 key + 飞书 5 凭证(`FEISHU_APP_ID`/`FEISHU_APP_SECRET`/`FEISHU_VERIFY_TOKEN`/`FEISHU_ENCRYPT_KEY`/`FEISHU_ALERT_CHAT_ID`)全 `~/.bashrc`;**P0-2 amendment 2026-05-16 锁定:owner 飞书租户禁用 custom-bot,凭证池 6→5,告警通道走自建应用同款 OpenAPI 发到 FEISHU_ALERT_CHAT_ID;`FEISHU_CUSTOM_BOT_*` 永禁存在,出现即 warning + audit**。`.env` 严禁 `LLM_KEY/FEISHU_*/FEISHU_ALERT_*` 前缀;启动期 `secrets_validator` fail-fast。fingerprint = SHA256[:8],严禁 plaintext / 末四位;5 类强制轮换 + 12 月 warning。全层 127.0.0.1 only(Backend + Vite + Mongo + Redis + Nginx),远程仅 SSH tunnel;不加本机 auth middleware;httpx 出站 `local_address="0.0.0.0"`(IPv4 only egress)。Mongo `audit_events` TTL 180 天 + JSONL 30 天双写,34 类锁定(类 5 evolution 7 类 actor=SYSTEM/SCHEDULER);LLM 严禁写 audit;调试事件走 `logs/quantmind.jsonl`。gitleaks pre-commit 强制,严禁 `--no-verify`。

**2.10 成本预算(P1-7)** LLM only:日 ¥20 hard(唯一全 LLM 熔断)+ 月 ¥440 soft(50/80/100% 三节点,100% 不停)+ Kimi 日 ¥4。数据 / 运维不设 ceiling。`cost_guard` + `SoftDegradeManager` 严禁 `import backend.{llm,agents,mirofish,data}`。软触发 ¥14=70% 优先关 Kimi escalation;严禁削减 4 必经 Agent / 降 fast 频次 / 全 deepseek-only。告警仅飞书 + audit + Phase B 成本拆解面板;严禁 SMTP / Slack / Discord;Alerter dedup_15min。

**2.11 前端(P1-5)** MVP 7 + Phase B 4 页 + 决策闭环 4 分组永锁;Simulation.vue 留代码不进菜单。WS 12 类(原 6 + 新 8 - 删 2 `auth_mode_change`/`approval_update`);SSE 仅 LLM 流式。用户回报双路径同 `ExecutionReportApplier`,前端 JS 正则镜像与 `regex_patterns.py` 单一真相源,不一致 fail-closed。三层 reason 抽屉 3 tab(Builder / Engine / Broker)命名空间区分。前端不存任何**凭证**到 `localStorage`/`sessionStorage`/`cookie`(UI 偏好如 focusMode 不违规)。

**2.12 自进化(P2-2,实施期 deferred)** 启用 3 路径:DSPy GEPA 离线 prompt(≤¥5/次)+ RAG provenance-gated 白名单(arxiv/semanticscholar/openreview/白名单 GitHub releases/akshare changelog)+ FinMem exemplars(≤3/prompt)。严禁 7 路径:fine-tune / online learning / RLHF / DPO / continual SFT / 自动 mutate config / 新 LLM provider / LLM 自动决策权。全人工 gate + 飞书主动通知;shadow 45 日完全沿用 P0-6;文件式 prompt registry + git + restart;严禁 MLflow/LangSmith。BrokerScheduler 第五 cron `evolution_shadow_run` 22:00 mon-fri;Phase A/B 实施期严禁写任何自进化代码。

## 3. 工程原则

- 注释 / commit 英文;UI 文档中文;public function 必须 type hints + docstring(WHY 而非 WHAT)。
- 不可变结构优先(frozen dataclass / NamedTuple / Pydantic frozen);文件 200-400 行典型 / 800 上限;函数 <50 行;嵌套 <4 层。
- 测试金字塔 + ruff 全绿才允许 commit;非 risk >70%,risk >95%。**测试通过 ≠ 闭环可用**(audit 反面教材 1139 绿但 RiskEngine 不接订单;断言要覆盖被谁调用、贯穿到哪)。
- Codex review = **手动调用,绝不自动**(2026-05-12 用户锁定):commit 前的本地门禁 = pytest + ruff + redline-check + 前端 type-check + vitest 全绿,**不**包括 codex;Claude 绝不主动跑 `/codex-review` / `codex review --uncommitted` / `codex exec` / `scripts/run-codex-review.sh`。用户显式说"跑 codex"/"/codex-review" 时再跑;那时仍按 codex-review skill 流程跑(1 cycle 起步,major 用户可指定 5 轮 R1-R5,输出 `docs/reviews/{task_id}-codex-review-summary.md`)。绿测试 ≠ 完美但够提交;codex 留给用户主动质检整 Phase 的批量改动。
- fail-closed for data corruption / fail-open for infra glitches;完整升级路径优先,不为省工作量妥协可用性。

## 4. 重要文档

| 路径 | 用途 |
|------|------|
| **`docs/plan.html`** | **实施 SSoT — 任务清单 + Session Log + 维护协议** |
| `docs/decisions/` | 18 决策 + 6 amendments;红线细则 §2 |
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
```

LLM key:`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY`。
飞书:`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_VERIFY_TOKEN` / `FEISHU_ENCRYPT_KEY` / `FEISHU_ALERT_CHAT_ID`(告警群 open_chat_id;P0-2-amendment-2026-05-16 锁定:owner 飞书租户禁用 custom-bot,告警通道走自建应用同款 OpenAPI 不走 webhook;`FEISHU_CUSTOM_BOT_*` 永禁存在)。
