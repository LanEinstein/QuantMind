# QuantMind 项目协作上下文

> 跨 session 接手 QuantMind 的"第一读"。决策对齐期 P0 阶段已完成,核心约束已稳定。下一站:P1 决策对齐(从 `P1-2 MockBroker 持久化与实时估值` 起步)。
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
```

LLM key 走 shell env:`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY`。
飞书 key:主路径 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`(预留 `FEISHU_VERIFY_TOKEN` / `FEISHU_ENCRYPT_KEY`);备用 `FEISHU_CUSTOM_BOT_WEBHOOK_URL` / `FEISHU_CUSTOM_BOT_SIGN_SECRET`。
`.env` 仅放非密配置(`MONGODB_URI` / `QUANTMIND_DAILY_BUDGET` 等)。
