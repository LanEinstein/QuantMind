# QuantMind 项目协作上下文

> 跨 session 接手 QuantMind 的"第一读"。**决策对齐期 P0 + P1 + P2 全完成 ✅**(2026-05-10 P2 收官 — P2-1/P2-3 superseded by P0-8/P1-6 §1.5;**P2-2 自进化 deferred to dedicated session — 用户 critical feedback 否决 "全锁不启用",自进化必须有但需深度调研**;P2-4 派生 P1-6 二次 amendment 27 类 AuditEventType);核心约束已稳定。下一站:**实施期 Phase A 启动**(代码迁移合并 P1-5 + P1-6 + P1-7 + P0-1 旧矩阵删除)。
>
> ⚠️ 本文件只提炼原则与红线,**不重复决策条文**;详细规约请直接读 `docs/decisions/` 下对应决策文档 §2 红线节。

---

## 1. 项目定位

QuantMind = 多 Agent 投研信号系统 + 模拟实盘验证 + 飞书人工执行。**永禁**真实券商账户的程序化、半自动、自动下单。

| 模式 | 名称 | 真实券商 API | 真实下单 |
|------|------|--------------|----------|
| A | `simulation_auto`(always-on 底座) | 不接 | 不下 |
| B | `feishu_interactive`(可叠加切换) | 不接 | 用户飞书手动执行后回报 |

两模式共用同一套 `InstructionPlan` / RiskEngine / 仓位计算 / 证据链 / `decision_ledger` / **MockBroker(唯一账户镜像)**。`FEISHU_INTERACTIVE_ENABLED` 是唯一运行时开关;旧 `AUTHORIZATION_MODE × QUANTMIND_PHASE` 矩阵在新代码视为非法标识符(`live_confirm`/`phase7_live`/`auto` 必须 grep 为空)。模式切换 = 账户生命周期事件(强制归档 + MockBroker 重置 + 飞书初始化对账,期间冻结买卖类路由),不是 flag toggle。

---

## 2. 核心红线(违反即停止)

### 2.1 LLM 角色边界

- **可写字段仅 4 类**:`InstructionPlan.reasoning` / `evidence_collection.content` / `agent_debate_records.{reasoning_text, conclusion}` / `risk_parameter_proposals.proposal_text`
- **严禁写**:决策字段(`volume` / `limit_price` / `valid_until` / `status` / `risk_summary` / `evidence_ids` / `debate_round_count` / `instruction_id` / `code` / `side`)、`RiskCheckSummary` 任一字段、`evidence_id` 命名、`cap_allocator` 字段、MockBroker 任一字段、`WatchlistPolicy`、`DataQualityState`/`ReconciliationTicket`/`AcceptanceReport`、`RiskConfig`
- **单调用 30s 硬超时 + 0 重试**;失败即 Agent 返 None 不重试
- **LLM 全停 ≥1h 触发系统级中断**;成本超 ¥20/日 hard ceiling 暂停所有 LLM 调用
- **LLM 严禁参与**:回报解析 / 对账 / 验收 / 数据质量判定 / RiskEngine 决策路径 / 飞书消息文本拼接(防 prompt injection,所有飞书消息必经 `renderer.py`)
- Pydantic strict + `extra="forbid"` + lint rule 三层守门;`extra="allow"`/`"ignore"` 红线违规

### 2.2 多 Agent 守门

- **4 必经 Agent**:`fundamental_analyst` + `technical_analyst` + `risk_officer` + `fund_manager`;任一缺失即 InstructionPlanBuilder 降级 HOLD
- **`fund_manager` 是唯一 BUY/SELL/HOLD 倡议者**;其他 Agent 仅作 evidence/debate/context 输入
- **双层守门**:`InstructionPlanBuilder` 五道早返 + `RiskEngine` 14-check;两层独立并行,任一失败即 REJECTED 或降级 HOLD
- `debate_round_count ≥ 1`(辩论必经);`risk_officer`(LLM)与 `RiskEngine` 14-check(确定性)双层防护互不替代
- `fund_manager_shadow_baseline` 永远不参与决策(`frequency: shadow_only`)

### 2.3 风险与配置不可改

- **`RiskConfig`/`PositionLimitsConfig`/`CircuitBreakerConfig`/`UniverseConfig`/`WatchlistPolicy`/`agent_models.yaml` runtime 不可改 + hot-reload 禁用**
- 阈值修改 = `git diff config/*.yaml` + `docs/decisions/{编号}-amendment-{date}-{原因}.md` + 进程重启三步;任何 hot-reload/setattr/monkey-patch 即红线违规
- **`backend/api/{risk,watchlist,llm,agents}*.py` 仅允许 GET 端点**(POST/PUT/PATCH/DELETE 红线违规)
- **`backend/risk/` 严禁 `import backend.{llm,agents,mirofish,data}`**;`stock_meta`/`daily_state` 由 InstructionPlanBuilder 装配传入,RiskEngine 纯函数无 IO
- **保守仓位三连**:单股 ≤15% / 总仓 ≤70% / 单次 ≤5 万元
- **中性日内熔断**:每日 ≤5 单 / 日亏 -5% / 连亏 3 笔 / 冷却 60min(SELL 默认不熔断)
- **中性 universe**:`sh_main`+`sz_main`+`chuangye`+`etf`;禁 ST / 科创(688x)/ 北交所(8x/92x)/ 可转债(11x/12x);禁涨停 BUY / 跌停 SELL
- 板块/ST 识别经 `backend/data/stock_metadata.py::classify_board` 单一真相源;严禁 RiskEngine 内重复实现

### 2.4 数据质量与情报

- **行情主备硬阈值**:staleness ≤5s / divergence ≤0.3% / `minimum_freshness_seconds_for_buy_sell=60`
- **全 watchlist 30s 个股快照**(`watchlist_market_snapshots` collection;填补 audit §6.2 缺口)
- **多域 5 源情报**:财经 2(`stock_news_em`+`stock_info_global_cls`)+ 时政 1(`news_cctv` 6h 阈值)+ 全球 2(`stock_info_global_em`+`stock_info_global_sina`);军事/社交舆论留 P1
- **MiroFish 是加分项不是核心**(平台底层是传统 AI 量化交易):事件驱动(severity≥HIGH;cap=20/日)+ 17:00 盘后复盘双路径;输出**仅入 `evidence_collection`,不入 `RiskCheckSummary`**
- **`DataQualityState` 早返第四种买卖类冻结来源**(不进 RiskEngine,Builder 层降级 HOLD,**不**暂停 simulation_auto)
- **5 类 `evidence_id` 前缀约定**:`NEWS-` / `MIROFISH-` / `MARKET-` / `RISK-` / `DEBATE-`
- `backend/data/{data_quality,divergence,staleness,suspension}.py` 严禁 `import backend.llm`
- 跨域不去重(同一事件多域评价是 MiroFish 输入价值);严禁实施期跨域去重

### 2.5 飞书接入

- **永禁 HTTPS 回调入站端口**;事件订阅只走 `lark-oapi` WebSocket 长连接(单实例,3s 内 ack)
- **自定义机器人 webhook 仅可发系统告警**,绝不发买卖指令 / 对账请求 / 澄清消息
- 第一阶段纯文本指令 + 严格正则解析回报(LLM 严禁参与回报路径)
- **`tenant_access_token` 不持久化**(仅内存);第一阶段不实现交互卡片
- 长连接断线时仍可发系统告警,但不可发买卖指令(ModeRouter fail-closed)
- `lark`/`feishu`/`larksuite` 关键字在 `backend/risk/` 严禁出现

### 2.6 InstructionPlan 与状态机

- **`instruction_id` 严格匹配** `^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$`(长度 33-34)
- **`InstructionSide` 集合冻结** `{BUY, SELL, HOLD}`(永禁 SHORT/COVER/MARGIN_BUY/REVERSE_REPO/ETF_SUBSCRIBE/ETF_REDEEM)
- **HOLD 永不路由不发飞书**(`is_routable()` 强制 False);`parse_ok=False` 强制降级 HOLD
- `valid_until > created_at` + 当日内 + ≤14:55 Asia/Shanghai
- BUY/SELL 必须 `volume`(100 整数倍)+ `limit_price`(>0);HOLD 必须二者 None
- **`risk_summary` 长度恒 14**(对应 RiskEngine 14-check)
- **状态机守门**:`DRAFT → VALIDATED → DISPATCHED → FILLED/EXPIRED/REJECTED/AMBIGUOUS`;严禁 `model_copy(update={"status":...})` 直接绕过
- **核心数据结构全 frozen Pydantic v2 strict**:`InstructionPlan` / `ExecutionReport` / `ReconciliationTicket` / `AcceptanceReport` / `RiskConfig` / `WatchlistPolicy` / `FundManagerOutput` / `AgentDebateRecord` / `EvidenceItem` / `RiskParameterProposal` / `EquityPoint` 等;就地 mutation 红线违规

### 2.7 回报解析与对账

- **不引入 `user_reported_portfolios` 平行 collection**;MockBroker 是 `feishu_on` 时唯一镜像
- **回报解析严格正则 only**:5 种回报形态(已执行 / 部分执行 / 未执行 / 更正 / 盘后补录)精确正则;任何不通过即 AMBIGUOUS,**绝不更新 MockBroker**
- **绝不猜测 `instruction_id`**(不通过严格正则即 AMBIGUOUS);盘中 30 分钟超时发追问 1 次,`valid_until` 自动 EXPIRED;盘后补录与更正到当日 16:00 Asia/Shanghai cutoff
- **系统主动发起日终对账**(16:00 后,`RECON-{YYYYMMDD}-{seq}`);用户主动无 ticket_id 一律 AMBIGUOUS
- **偏差分级阈值**:cash 1.00 元 / volume 0%(严格相等)/ cost_price 0.01 元
- 超阈值创建 `reconciliation_ticket` fail-closed 等用户三选一(`对账采纳:用户回报` / `对账采纳:系统镜像` / `对账更正`)
- **OPEN/EXPIRED ticket 期间冻结买卖类路由**(与切换冻结 / 熔断冷却 / DataQualityState 早返 — 四种独立并行,任一为真即冻结)
- 公司行动第一阶段不支持自动处理,统一走"对账更正"
- **MockBroker 覆盖必经 `ReconciliationApplier::reset_to_snapshot`**;严禁直接 mutation `_cash`/`_positions`/`_trades`
- 澄清飞书 / 裁定卡严禁走备用 webhook(继承 §2.5)

### 2.8 验收门槛

- **45 交易日滚动窗口**(节假日表静态 YAML,不引入 akshare 节假日 API)
- **5 项稳定性硬门槛**:指令完整率 ≥95% / 回报解析准确率 ≥99% / 数据缺失率 ≤1% / LLM 超时率 ≤5% / 信号生成成功率 ≥95%
- **3 项策略硬门槛**:最大回撤 ≤8% / 累计 PnL ≥0 / 沪深 300(`000300.SH`)累计超额 ≥0
- 7 项观察指标**不参与** `can_switch_to_feishu_on()` 判断
- **系统级中断 5 类重置窗口**:行情连续断流 30min / LLM 全停 1h / MockBroker 损坏 / 状态机非法迁移 / 长连接 4h
- reconciliation 冻结日**暂停而非重置**;基准缺失 `benchmark_excess_return=0`(等价边界 PASS,不假阳性)
- **切换 `feishu_on` 必须前置 `acceptance.can_switch_to_feishu_on()` 校验**;严禁 env var/CLI 绕过
- 每日 16:00:30 系统生成 `acceptance_reports` 一条记录(`(report_date)` 唯一索引,upsert)
- 验收期内禁止用户绕过指标修改 MockBroker(否则 equity_curve 失真,PnL/回撤指标无意义)

### 2.9 Watchlist 与频率

- **13 codes 总数恒定**(沪主 4 + 深主 3 + 创业板 3 + 宽基 ETF 3:`510300`+`510500`+`159949`)
- 板块组成恒定 `sh_main:4 / sz_main:3 / chuangye:3 / etf:3`(任一改动必走 amendment)
- **3 必备 ETF 永锁** `510300`/`510500`/`159949`(禁行业/主题/跨境/商品/货币 ETF)
- **排除四件套**:新股 ≤30 / 次新 ≤180 交易日;流动性 <2 亿 / 单价 >500;**在 InstructionPlanBuilder 第五道早返,不进 RiskEngine 14-check**
- **fast/slow 双频**:slow 09:00 全 watchlist 多 Agent 辩论 + fast 09/11/13/15 盘中验证
- **每日 ≤5 单 cap**:`traditional_path_default_cap=4` + `event_path_reserved_cap=1`(14:30 后 event 未用可滑动给 traditional;`event_path_reserved_cap > 1` 永禁;event 路径用满即使 severity=CRITICAL 也不再发指令仅写 evidence)
- **严格 long-only**;ETF 套利预留 P1 但永锁 disabled(`config/broker.yaml.etf_arbitrage_enabled=false`)
- watchlist runtime 不可改;`backend/api/watchlist*.py` 仅 GET

### 2.10 安全 / 流程

- LLM key + 飞书凭证(`FEISHU_APP_ID/APP_SECRET/VERIFY_TOKEN/ENCRYPT_KEY/CUSTOM_BOT_WEBHOOK_URL/CUSTOM_BOT_SIGN_SECRET`)仅走 shell env(`~/.bashrc`),永不入 `.env`/git
- MongoDB / Redis 仅绑 `127.0.0.1`;前端密钥仅末四位脱敏 + `webhook_configured` 布尔
- 不自动跨阶段推进(每阶段末 STOP + summary);不自动 push,本地 commit 后等用户授权再推
- httpx 客户端必须 `local_address="0.0.0.0"`(host 无 IPv6 默认路由,AAAA-publishing host 如 dashscope 会静默卡死)

### 2.11 前端工作流(P1-5)

- **MVP 7 页 + Phase B 4 页 + 决策闭环 4 分组永锁**:MVP = Dashboard / 系统状态 / InstructionPlan 池 / Portfolio / 用户回报录入 / 对账裁定 / 验收报告;Phase B 收尾 = Agent 辩论 / 数据质量 / 飞书消息历史 / 成本拆解;一级菜单 = 运行状态 / 决策与指令 / 账本与成交 / 复盘与验收 + 设置只读折叠;新增/删除/合并必走 P1-5-amendment
- **写入接口立即下线 + Phase A 一次性破坏式删除**:`backend/api/{risk,watchlist,llm,agents,settings,trading,analysis}*.py` 所有 POST/PUT/PATCH/DELETE handler 删除;前端 RiskCenter / Settings / ApprovalQueue / OrderList / AgentDebate 写入按钮 + axios 一并清理
- **仅允许 2 个前端写入端点**:POST `/api/execution-reports`(用户回报录入)+ POST `/api/reconciliation-tickets/{ticket_id}/decide`(对账三选一裁定);其他全部禁写
- **WS `/ws/market` 单通道扩展 12 类消息**:已有 6 类(index_update/signal/news/status/position_update/circuit_breaker_update)+ 新增 8 类(instruction_plan_update/broker_event/equity_point_update/data_quality_breach/freeze_source_update/ticket_update/acceptance_report_ready/feishu_message_received)+ 删除 2 类(auth_mode_change/approval_update);SSE `/api/analysis/stream` 仅保留 LLM 流式
- **用户回报双路径同一 ExecutionReportApplier 入口**:飞书主路径 + 前端备用录入页;前端 JS 正则镜像与后端 `backend/execution/regex_patterns.py` 单一真相源保持一致(单元测试断言);镜像不一致即前端 fail-closed 阻止提交
- **5 冻结源全局 StatusBar 顶部常驻 5 独立状态点**:`freeze_source_switch` / `freeze_source_ticket_open` / `freeze_source_circuit_breaker_cooldown` / `freeze_source_data_quality` / `freeze_source_eod_pipeline_freeze`;**永禁聚合为单一 frozen=true**;独立"系统状态"页全量展示
- **三层决策拦截 reason — InstructionPlan 池详情抽屉三 tab**:Builder 早返(6 检查项)/ RiskEngine 14-check(逐项)/ MockBroker at-fill(终态 + cost_breakdown);命名空间 `price_limit_violation_at_fill`(broker)≠ `limit_up_block`/`limit_down_block`(engine)便于 audit 区分
- **Performance + 验收报告页分离永锁**:Performance 走 broker_snapshots+equity_points(可视化);验收报告走 acceptance_reports(决策表格 5 稳定性 + 3 策略硬门槛 + can_switch_to_feishu_on() 布尔)
- **P1-5 暂不加本机认证(P1-6 处置)**:前端永不存储任何凭证到 localStorage / sessionStorage / cookie;Vite 默认 `host: '127.0.0.1'`(不允许 `'0.0.0.0'`)
- **Simulation.vue 保留为 P1-5 范围外**:MiroFish 多 Agent 演化可视化展示价值后续阶段细化;P1-5 阶段不改造不重点投入;菜单不展示

### 2.12 安全与可观测性(P1-6)

- **Secrets 仅 shell env 单源永锁**:LLM 3 key + 飞书 6 凭证 + 未来扩展凭证全 `~/.bashrc`;`.env` 永禁含 LLM_KEY/FEISHU_* prefix;不引入 sops/age/Vault/keyring(单实例项目过度工程)
- **凭证 5 类强制轮换 + 12 月 warning**:① 泄露/疑似 ② 团队变动 ③ provider 告警 ④ 12 月到期(warning 不强制 exit)⑤ P2 升级前;轮换 = 编辑 ~/.bashrc + source + systemctl restart + 启动期日志确认 fingerprint;**严禁 hot-reload**(继承 P0-7+P0-10);凭证 fingerprint=SHA256(value)[:8] 严禁 plaintext 写持久化通道
- **飞书 6 凭证 P1-6 仅锁约束实际配置延迟到 feishu_interactive 启用前**:启动期 fail-fast 仅在 `FEISHU_INTERACTIVE_ENABLED=true` 时校验飞书凭证(默认 false 不影响 simulation_auto)
- **凭证泄露三步应急**:① 立即轮换 + provider revoke ② git history 排查(git log -p -S 前 8 字符 + filter-repo + force push 仅本人确认 + 已 push 公网视为永久泄露)③ 影响评估(audit_events 反查 + 飞书控制台 + cost_guard 异常飙升);**docs/runbook/secrets-incident-response.md 必须存在**
- **gitleaks pre-commit hook 强制**:`.pre-commit-config.yaml` + gitleaks v8.18+ + `.gitleaks.toml` 覆盖 `sk-*`/`FEISHU_*`/`DEEPSEEK_API_KEY`/`DASHSCOPE_API_KEY`/`MOONSHOT_API_KEY`;严禁 `--no-verify` 跳过
- **启动期 secrets_validator fail-fast**:`backend/services/secrets_validator.py` 在 `main.py:lifespan` 同步调用;失败即 `exit(1)` + 写 audit `secrets_validator_blocked` + ERROR;校验 .env 不含 LLM_KEY/FEISHU_* + process env LLM key 格式 + FEISHU_INTERACTIVE_ENABLED=true 时校验飞书 6 凭证
- **IP 全层严锁 127.0.0.1 only 永锁**:Backend uvicorn 显式 `--host 127.0.0.1` + Frontend Vite `host:'127.0.0.1'`(F-001 必修当前 `'0.0.0.0'` 历史违规)+ Nginx `listen 127.0.0.1:80/443` 显式补充 + 远程访问仅 SSH tunnel(SSH 是身份认证层);严禁 LAN 段开放 + 0.0.0.0 + iptables 控制(冲突 P0-2 永禁 HTTPS 入站红线);httpx 出站 `local_address="0.0.0.0"` 不冲突入站(出站和入站不同方向)
- **不加任何本机 auth middleware 永锁**(履行 P1-5 §2 红线 11 P1-6 处置承诺):Backend FastAPI 不挂 `Depends(get_current_user)` / API key / Bearer token / JWT;Frontend axios 不插任何 `Authorization` / `Bearer` / cookie / `localStorage` / `sessionStorage`;WebSocket + SSE 不要求 token 参数;不引入单 token 文件 / OAuth / mTLS
- **Mongo audit_events 180 天 TTL + JSONL 30 天双写永锁**:audit_events collection append-only insert-only(继承 P1-2.A broker_events 8 项红线);schema 由 `backend/audit/models.py` AuditEvent frozen Pydantic v2 strict + extra='forbid' 锁定 10 字段(event_id/timestamp/event_type/actor/actor_detail/resource_type/resource_id/payload/outcome/correlation_id/reason_namespace);Mongo failure 时 fail-open(JSONL 兜底 + warning 不阻主路径);**严禁 LLM 写 audit_events**(继承 P0-10 §2 红线 1)
- **`AuditEventType` enum 锁定 22 类 event_type 永锁**:类 1 两写入端点(2)+ 类 2 模式切换+5冻结源+生命周期(11)+ 类 3 凭证生命周期+飞书收发(7)+ 类 4 异常+拦截(8);任何新增/删除/重命名必走 `P1-6-amendment`;调试性事件(LLM raw / RiskEngine trace / 行情快照 / cron 心跳 / Redis cache)严禁入 audit 走 `logs/quantmind.jsonl`
- **凭证类 audit 仅写 fingerprint 严禁 plaintext + 严禁末四位**(防关联推断;比 P1-5 §2 红线 14 末四位脱敏更严)
- **Audit 查询入口仅 CLI + GET API 永锁**:`scripts/query_audit.py` CLI + `backend/api/audit.py` GET `/api/audit/events`(仅 GET 符合 P1-5 §2 红线 1+2);**前端不占 P1-5 11 页名额**(后期需加查询界面必须先走 P1-5-amendment + P1-6-amendment 双批准)

### 2.13 成本预算(P1-7)

- **LLM only 预算永锁**:LLM 总日 hard ¥20(继承 P0-10 §1.4 不变 — 唯一全 LLM 停摆触发器)+ 月 soft ¥440 = 22 工作日 × ¥20(静态固定按自然月不依赖 holidays.yaml)+ Kimi 单独 daily cap ¥4(20% 总日预算);**数据/运维不设 ceiling**(akshare/adata/baostock 全免费;MongoDB/Redis 自托管 127.0.0.1);4 阈值修改 = git diff + amendment + 重启 + 严禁 hot-reload(继承 P0-7 §2 红线 14);派生 P0-10 amendment(¥20 hard 锁定不变扩 monthly + Kimi 子维度)
- **月节点 50%/80%/100% 渐进升级永锁**:50%(¥220)仅 audit `monthly_budget_50pct_reached`;80%(¥352)audit + 飞书 warning;100%(¥440)audit + 飞书 critical + cost_breakdown 附件;**月预算 100% 严禁触发停摆 LLM**(月度波动属正常仅警告)+ **严禁 25%/75%/120% 额外节点**(过度密集打扰)+ Redis SET NX 幂等(同一节点重复调用仅触发 1 次);Alerter dedup_15min cooldown 防轰炸
- **Kimi daily cap = ¥4 单独锁永锁**:`backend/services/cost_guard.py::assert_kimi_cap_allows()` 在 Kimi 调用前必经;触发即 raise `KimiDailyCapExceededError`(仅暂停 Kimi 不暂停 deepseek + qwen);**deepseek + qwen 严禁单独 cap**(防失 failover 弹性 — deepseek 灭后 qwen + kimi 总额 ¥10 不够 4 必经 Agent 全跑);Kimi `¥0.0084/k output` 是 deepseek 42 倍;¥4 cap = ~47 笔 escalation/日 buffer 覆盖正常 < 5 次场景
- **软触发(日 ¥14=70%)优先关 Kimi escalation 路径永锁**:`backend/services/soft_degrade_manager.py::SoftDegradeManager.activate_kimi_escalation_block()` 由 cost_guard 在 soft breach 时调用 + fund_manager tiered routing 强制走 triage qwen;**严禁同时关闭 4 必经 Agent 任一**(继承 P0-10 §2 红线 2)+ **严禁降低 fast 频次 09/11/13/15 → 09/13**(11/15 突发响应延迟不可接受)+ **严禁全模型降级 deepseek-only**(中文金融领域 qwen 优势丧失 + 需走 P0-10-amendment);每日 00:00 BrokerScheduler 1st cron reset 软触发标志位
- **告警通道仅飞书 + audit + Phase B 成本拆解面板永锁**:`Alerter.webhook_url = FEISHU_CUSTOM_BOT_WEBHOOK_URL`(继承 P0-2 §2.5 备用 webhook 仅告警);**严禁 SMTP/Slack/Discord 第二告警通道**(违反 P1-6 §1.1 凭证池仅 LLM 3 + 飞书 6 锁状态);**严禁 soft breach 发飞书**(违反 P1-5 低噪声原则);Phase B 成本拆解面板 = `backend/api/cost.py` GET `/api/cost/breakdown` + `frontend/src/views/CostBreakdown.vue` 5min polling(P1-5 §2 红线 1 锁 4 Phase B 页之一);**严禁 POST/PUT/PATCH/DELETE 在 backend/api/cost.py**(继承 P1-5 §2 红线 1+2)
- **`AuditEventType` 扩 22 → 26 类永锁**(派生 P1-6 amendment):新增 `monthly_budget_50/80/100pct_reached` + `kimi_daily_cap_4cny_breached` 4 类均归类 4(异常 + 拦截事件)`reason_namespace='cost_budget_threshold'` `actor=SYSTEM` `resource_type='cost_budget'`;cost_guard 写 audit 仅 SYSTEM/SCHEDULER actor + 严禁 frontend_user/feishu_user actor 写 cost_budget event(防伪造);cost_breakdown 响应严禁 plaintext API key fingerprint(by_provider key 用 'deepseek'/'qwen'/'kimi' 字面量)
- **`BudgetState` frozen Pydantic v2 strict + extra="forbid" 三层守门永锁**:升级后字段集 = (daily_*) 6 + (monthly_*) 5 + (kimi_*) 3 + (kimi_escalation_blocked) 1 = 15 字段;严禁就地 mutation;严禁 `extra="allow"`/`"ignore"`;hot-reload 禁用(继承 P0-3 §2 红线 12 + P0-7 §2 红线 14)
- **`backend/services/cost_guard.py` + `backend/services/soft_degrade_manager.py` 严禁 import `backend.{llm,agents,mirofish,data}` 永锁**(继承 P0-10 §2 红线 1 LLM 字段权限矩阵):cost_guard 是基础设施层 LLM 是上层调用方;LLM 写引用反向依赖即破隔离;`SoftDegradeManager.activate_kimi_escalation_block` 仅由 cost_guard 调用(防 LLM/agents 模块绕过预算守门主动触发降级);严禁 frontend 暴露写入端点

### 2.14 P2 收官(2026-05-10)

- **P2-1 MiroFish 范围 superseded by P0-8 永锁**:不重新评估;不引入"日常每只股票都跑"路径;详见 `docs/decisions/P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md` §1
- **P2-2 自进化机制边界 deferred to dedicated session 永锁**(用户 critical feedback):用户原话"自进化功能是必须要有的可以引入'自我进化后必须经过模拟盘验证'以及状态回滚但绝对不能完全禁止;大模型如果没有持续学习/持续适应新变化/持续追踪最前沿量化以及金融交易思路的能力就一定无法长久立于不败之地;具体采用怎样的策略我们可以单开一个 session 仔细调研讨论";关键约束 = 自进化输出必须经过模拟盘验证(类似 P0-6 acceptance 框架)+ 必须有状态回滚机制(类似 P1-2.A reset_to_snapshot 思路);**实施期 Phase A/B/B-finale 严禁写任何自进化代码**直到 dedicated session 锁定;严禁单方面在其他场景做自进化决策;严禁把"hot-reload 禁用 + LLM 严禁写决策字段"理解为"自进化永远禁止"(用户已明确不同意);Critical feedback memory: `~/.claude/projects/-home-ps-papers-QuantMind/memory/feedback_self_evolution_must_have.md`
- **P2-3 移动端/远程访问 superseded by P1-6 §1.5 永锁**:不开发独立移动端 App;PC 浏览器仅本机/SSH tunnel;移动端依赖飞书交互(继承 P0-1 §1.3)
- **P2-4 告警渠道维持 P1-7 §1.7 锁定 + 派生 P1-6 二次 amendment**:`AuditEventType` enum 22 → 26 → 27 类(P1-6 + P1-7 + P2-4 三层 amendment 累积);新增 `EXECUTION_REPORT_PARSE_FAILED`(归类 4 异常 + 拦截事件;reason_namespace='execution_report_ambiguous';actor=FEISHU_USER 或 FRONTEND_USER;outcome=FAILURE;payload={raw_text, regex_attempt_results, parse_error_kind};继承 P0-4 严格正则失败即 AMBIGUOUS 节奏);**澄清飞书走 P0-2 §1.2 主路径长连接 + P0-4 §1 五模板预写死永锁;严禁走 P0-2 §2.5 备用 webhook**(继承"备用 webhook 仅可发系统告警绝不发买卖指令/对账请求/澄清消息"红线);告警通道整体维持 P1-7 §1.7 仅飞书 + audit + Phase B 成本拆解面板;严禁 SMTP/Slack/Discord 第二通道(继承 P1-6 §1.1 凭证池仅 LLM 3 + 飞书 6 锁状态)

---

## 3. 工程原则

- 注释 / commit message 英文;UI 文本与文档中文;public function 必须 type hints + docstring(WHY,不是 WHAT)
- 不可变数据结构优先(`@dataclass(frozen=True)` / `NamedTuple` / Pydantic frozen);文件 200-400 行典型 / 800 上限;函数 <50 行;嵌套 <4 层
- TaskCreate/TaskUpdate 全程跟踪,`in_progress` 严格只挂 1 个,完成立刻 mark completed;报告"完成"前必须先把 SSoT/决策表更新好 + 填真实 commit hash + 报告里说明改动
- 测试金字塔 + ruff 全绿才允许 commit;非 risk 模块覆盖率 >70%,risk 模块 >95%
- **测试通过 ≠ 闭环可用**:断言要覆盖"被谁调用、贯穿到哪",不能只测自身行为(audit 已揭示 1139 绿 + RiskEngine 不接订单的反面教材)
- **Codex review hard gate**:major 5 轮 R1-R5,minor R1+R3 两轮,输出存 `docs/reviews/{task_id}-r{N}-{topic}.md`;触发前 `git pull` 同步 `LanEinstein/CCodexSkill`
- fail-closed for data corruption / fail-open for infra glitches:NaN/Inf/负值在数据层 + 守门层双层校验;Redis ConnectionError 让 scheduler 兜底通过
- 完整升级路径优先:不为省工作量妥协系统可用性
- Handoff 文档要详尽:计划/SSoT 文档要含完整代码片段、精确命令、预期输出,不能只列大纲

---

## 4. 重要文档

| 路径 | 用途 |
|------|------|
| `docs/quantmind_project_audit_2026-05-07.md` | 全景盘点 — 接手第一份必读(2026-05-08 重写) |
| `docs/quantmind_owner_decision_points_2026-05-07.md` | 决策清单(2026-05-08 重写) |
| `docs/decisions/` | 已锁定决策定稿 + 派生 amendment;红线条文详细规约请直接读对应文件 §2 |
| `docs/reviews/` | codex review 报告 + 阶段 summary |
| `/home/ps/.claude/projects/-home-ps-papers-QuantMind/memory/MEMORY.md` | 跨 session 自记忆索引 |
| `/home/ps/.claude/rules/` | 全局规范(coding-style / git / testing / security / performance / agents / hooks / patterns) |

---

## 5. 操作速查

```bash
# 后端启动(实施期 Phase A 完成代码迁移后,旧 env var 必空)
FEISHU_INTERACTIVE_ENABLED=false \
  /home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app --port 8000

# 前端(避开 Open WebUI 占用的 3000)
cd frontend && npm run dev   # listens on :9276

# 测试
/home/ps/anaconda3/envs/zhanglan/bin/pytest -q --cov=backend --cov-fail-under=70
pytest -q backend/risk --cov=backend/risk --cov-fail-under=95
cd frontend && npm run type-check && npm run test -- --run && npm run build

# 红线静态检查(精选)
grep -rn "from backend.\(llm\|agents\|mirofish\|data\)" backend/risk/
grep -rn "from backend.risk\|RiskConfig" backend/llm/ backend/agents/ backend/mirofish/
grep -rn "@router.\(post\|put\|patch\|delete\)" backend/api/{risk,watchlist,llm,agents}*.py
grep -rn "from backend.llm" backend/data/{data_quality,divergence,staleness,suspension}.py
grep -rn "MiroFish.*RiskCheckSummary\|RiskCheckSummary.*MiroFish" backend/
grep -rnE "InstructionPlan\.(volume|limit_price|valid_until|status|risk_summary)\s*=" backend/agents/ backend/llm/
grep -rnE "for retry in range|for _ in range\(retry_count" backend/llm/router.py
grep -rn "etf_arbitrage_enabled" config/broker.yaml | grep -v "false"
grep -rn "enable_hot_reload" config/agent_models.yaml | grep -v "false"
grep -rn "live_confirm\|phase7_live\|AUTHORIZATION_MODE\|QUANTMIND_PHASE" backend/  # 实施期 Phase A 完成后必空
grep -rnE "@router\.(post|put|patch|delete)" backend/api/{risk,watchlist,llm,agents}*.py backend/api/settings/ backend/api/trading/ backend/api/analysis/  # P1-5 Phase A 完成后必空(排除 execution_reports + reconciliation_tickets/{id}/decide 唯二例外)
grep -rn "ApprovalQueue\|auth-mode\|/api/risk/config\|/api/settings/llm-config\|/api/trading/approve\|/api/trading/reject\|/api/trading/cancel" frontend/src/  # P1-5 Phase A 完成后必空
grep -rn "auth_mode_change\|approval_update" frontend/src/composables/useWebSocket.ts  # P1-5 Phase A 完成后必空(2 类删除消息)
grep -rn "localStorage\.\|sessionStorage\.\|document\.cookie" frontend/src/  # P1-5 红线 11:前端不允许存储任何凭证
grep -rnE "DEEPSEEK_API_KEY|DASHSCOPE_API_KEY|MOONSHOT_API_KEY|FEISHU_(APP_ID|APP_SECRET|VERIFY_TOKEN|ENCRYPT_KEY|CUSTOM_BOT_WEBHOOK_URL|CUSTOM_BOT_SIGN_SECRET)" .env .env.example  # P1-6 红线 1:.env 不得含 LLM_KEY/FEISHU_*
grep -rnE "(host|listen)\s*[=:]\s*['\"]?0\.0\.0\.0" backend/ frontend/ deploy/ docker-compose*.yml | grep -v "local_address"  # P1-6 红线 8:严禁绑 0.0.0.0(httpx local_address 例外)
grep -rnE "Bearer|Authorization|JWT|@app\.middleware\(.*auth" backend/api/ frontend/src/ | grep -v "lark-oapi\|lark_oapi"  # P1-6 红线 10:不加任何本机 auth middleware
grep -rn "from backend.audit\|audit_store\.write" backend/llm/ backend/agents/  # P1-6 红线 16:LLM 严禁写 audit_events
grep -rn "from backend.\(llm\|agents\|mirofish\|data\)" backend/services/cost_guard.py backend/services/soft_degrade_manager.py  # P1-7 红线 12:cost_guard + SoftDegradeManager 严禁 import LLM/agents/mirofish/data
grep -rn "activate_kimi_escalation_block" backend/llm/ backend/agents/  # P1-7 红线 14:SoftDegradeManager.activate_kimi_escalation_block 仅由 cost_guard 调用
grep -rnE "@router\.(post|put|patch|delete)" backend/api/cost.py  # P1-7 红线 8:backend/api/cost.py 仅 GET 严禁 POST/PUT/PATCH/DELETE
grep -cE "_DEFAULT_(DAILY_BUDGET_RMB|SOFT_CEIL_PCT|MONTHLY_BUDGET_RMB|KIMI_DAILY_CAP_RMB)\s*=\s*[0-9]" backend/services/cost_guard.py  # P1-7 红线 11:cost_guard.py 4 常量定义必为 4(daily ¥20+soft 0.7+monthly ¥440+kimi ¥4)
```

LLM key 走 shell env:`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY`。
飞书 key:主路径 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`(预留 `FEISHU_VERIFY_TOKEN` / `FEISHU_ENCRYPT_KEY`);备用 `FEISHU_CUSTOM_BOT_WEBHOOK_URL` / `FEISHU_CUSTOM_BOT_SIGN_SECRET`。
`.env` 仅放非密配置(`MONGODB_URI` / `QUANTMIND_DAILY_BUDGET` 等)。
