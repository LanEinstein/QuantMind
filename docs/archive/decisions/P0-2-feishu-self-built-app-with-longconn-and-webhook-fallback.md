# P0-2 — 飞书接入形态

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P0-2 |
| 决策日期   | 2026-05-09 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联 audit | `docs/quantmind_project_audit_2026-05-07.md` §7 / §16 |
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` P0-2 |
| 依赖决策   | `docs/decisions/P0-1-simulation-base-feishu-overlay.md`(尤其 §1.3.1) |
| 替代       | 旧 CLAUDE.md 中"P0-2 = 免费数据栈+财联社+巨潮"作废(属于旧方向) |

## 决策摘要

QuantMind 飞书接入采用**双通道架构**:

1. **主路径**:企业自建应用 + 机器人能力,通过 `im/v1/messages` 主动发买卖指令/对账请求/澄清消息,通过订阅 `im.message.receive_v1` 事件接收用户回报。事件订阅采用**长连接(WebSocket via 官方 `lark-oapi` Python SDK 的 `lark.ws.Client()`)**,**零公网入站**,仅依赖 IPv4 出口能访问飞书开放平台。
2. **备用通道**:保留一个独立的飞书自定义机器人 webhook(单向)作为"系统告警逃生通道"。当自建应用长连接失联或 token 失效时,系统告警类消息(行情断流 / LLM 不可用 / MockBroker 异常 / 长连接掉线本身)仍可推送到群,但**绝不发买卖指令**。

第一阶段(所有 P0 锁定后首次实施)消息形态为**纯文本 + 严格回报模板**。交互卡片(`card.action.trigger` 回调按钮等)留待长连接 + 文本回报闭环稳定后,作为 amendment 引入。

密钥与网络边界:**零公网入站**。`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_VERIFY_TOKEN` / `FEISHU_ENCRYPT_KEY` / `FEISHU_CUSTOM_BOT_WEBHOOK_URL` / `FEISHU_CUSTOM_BOT_SIGN_SECRET` 仅走 shell env(与 LLM key 同策略),永不入 `.env`、永不入 git。HTTPS 回调路径明确不在本决策范围内,如未来确实需要,走 amendment(`P0-2-amendment-...`)。

## 1. 决策具体内容

### 1.1 主路径:企业自建应用 + 长连接

**接入要素**(实施期细化模块边界由 audit §7.3 + 本节落地):

| 要素 | 选择 | 备注 |
|------|------|------|
| 飞书应用类型 | 企业自建应用(机器人能力) | 不使用商店应用、不使用 ISV |
| 凭证 | `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | shell env;首次实施时由用户在飞书开发者后台创建并提供 |
| 主动发消息 API | `POST /open-apis/im/v1/messages` | `receive_id_type=chat_id` 主用 |
| 发送内容类型(P1) | `text`(第一阶段)+ `interactive`(后续 amendment) | 第一阶段所有买卖指令、对账请求、澄清消息走 `text` |
| 事件订阅 | `im.message.receive_v1` | 接收 @机器人 消息 + 用户回报;**不订阅**所有群消息(权限最小化) |
| 事件传输方式 | **长连接 WebSocket** | 通过 `lark-oapi` Python SDK 的 `lark.ws.Client()` |
| Python SDK | `lark-oapi`(LarkSuite 官方 OAPI Python SDK) | 实施期通过 `requirements.txt` 锁定具体版本;签名/加密相关 schema 在 `backend/integrations/feishu/schemas.py` 镜像一份用于解析 |
| token 管理 | `tenant_access_token` 自动刷新(SDK 已内置) | 二次自检兜底:刷新失败 → 切换告警渠道为备用 webhook + fail-closed 不发买卖指令 |
| `chat_id` 来源 | 用户首次启用 `feishu_interactive` 时由前端配置项(或 .env 非密项)注入 | 实施期由 `config/feishu.yaml` 中 `target_chat_ids` 列表承载;允许多群但每条 InstructionPlan 只发到主群 |

**长连接客户端约束(架构层硬要求)**:

- 单实例运行(集群模式下消息随机推单 client 是飞书行为,**禁止水平扩多实例长连接 worker**;真要扩展用主备失活切换,实施期再做)
- 事件处理 **3 秒内完成**否则会触发飞书重推;因此事件处理函数必须只做"入队 + ack",真实解析/落库异步进行
- 长连接进程独立于 FastAPI app(可以是同进程内 asyncio 后台任务,也可以是独立 worker;实施期决定),但**不能阻塞 API 响应**
- 长连接断线重连 + 健康探针由 SDK 提供;监控层(后续 P2-4 决策的告警渠道之一)必须能在断线时通过备用 webhook 通知

**契合 P0-1 §1.3.1**:

- "首条飞书:平台主动发账户初始化对账消息" → 由 `FeishuMessenger.send_text(chat_id, content)` 通过 `im/v1/messages` 完成
- "解析与确认:ExecutionReportParser 解析回报" → 由长连接 worker 收到 `im.message.receive_v1` → 入队 → `ExecutionReportParser` 异步消费(实施期细节属 P0-4 决策范围)
- "切换期间冻结买卖类 InstructionPlan" → 长连接 worker 状态机由 `mode_transition.py`(P0-1 §3.2)统筹,不在 P0-2 范围内重复约束

### 1.2 备用通道:自定义机器人 webhook(单向告警)

**职责严格隔离**:

| 通道 | 允许内容 | 禁止内容 |
|------|---------|---------|
| 自建应用主通道 | 买卖指令、对账请求、澄清消息、回报追问、所有系统告警(默认) | — |
| 自定义机器人 webhook 备用 | **仅系统告警**(行情断流 / LLM 全部不可用 / MockBroker 异常 / **长连接断线本身** / token 刷新失败) | 任何买卖指令、对账请求、澄清消息(违规属于红线) |

**为什么保留备用 webhook 而不彻底简化**:

- 长连接掉线 / `tenant_access_token` 刷新失败时,主通道可能短时间无法发任何消息;此时若关键告警(例如 MockBroker 数据损坏、模拟亏损暂停线触发、长连接掉线本身)只能写日志,人工很可能在收盘后才发现
- 自定义机器人 webhook 与自建应用是**完全独立**的飞书产品形态(不共享 token、不共享授权、不共享群权限),不会同时失效
- 现有 `backend/monitoring/alerter.py` 已经具备"webhook 单向发 + 限流 + 失败时不在日志中暴露 webhook URL"的能力,只需把它从"通用 webhook"具体化为"飞书自定义机器人 webhook"(签名校验逻辑由实施期决定加不加,P0-2 不强制)

**安全要求(P0-2 锁定的最小约束)**:

- 自定义机器人 webhook **仅出站**,不需要 IP 白名单、不需要关键词;HmacSHA256 签名是否启用属实施期偏好,本决策不强制
- webhook URL 与 sign secret 只走 shell env(`FEISHU_CUSTOM_BOT_WEBHOOK_URL` / `FEISHU_CUSTOM_BOT_SIGN_SECRET`)
- 备用通道**不构成账户镜像状态源**:即使主通道全挂,系统也不能因为发不出指令而启动"用户去前端手工录入"的旁路(那是 P0-2 已排除的临时 webhook+前端录入方案,见 §4.3)

### 1.3 第一阶段消息形态:纯文本 + 严格回报模板

**第一阶段范围**:所有 P0 锁定后首次实施期 + simulation_auto 验收期(P0-6 决策)。

| 消息类型 | 第一阶段形态 | 后续演进路径 |
|---------|-------------|-------------|
| 买卖指令(InstructionPlan) | 纯文本,字段集见 P0-3 决策、模板见 P0-3 产出物 | 稳定后可 amendment 加 `interactive` 卡片版本,文本与卡片**同时**发送(双发同一 `instruction_id`,飞书侧用户选用任一种回报) |
| 对账请求(切换初始化 + 日终对账) | 纯文本,模板见 P0-1 §1.3.1 + P0-5 决策 | 同上 |
| 澄清/追问消息 | 纯文本 | 第一阶段不上卡片 |
| 系统告警 | 纯文本(主通道 / 备用 webhook 都纯文本) | 第一阶段不上卡片;告警渠道全清单留 P2-4 |
| 用户回报 | 纯文本,语法见 P0-4 决策 | 加卡片回调按钮(已执行/部分/未执行)留后续 amendment |

**为什么先不上卡片**:

- P0-3 InstructionPlan 字段集尚未锁定,卡片模板必须等 P0-3 锁定后才能定型;现在就上卡片会导致 P0-2 实施被反向阻塞
- `card.action.trigger` 回调引入额外的去重/状态机/超时分支(回调和 `im.message.receive_v1` 文字回报可能同时到达同一 `instruction_id`),解析与对账风险加大
- 纯文本路径 + P0-4 严格回报模板已足够覆盖 P0-1 §1.3.1 全部场景;先把最简通路打通再做交互增强

### 1.4 密钥与网络入站边界(高层方向)

**P0-2 锁定的约束**:

1. **零公网入站端口**:QuantMind 主机不开任何对外 HTTPS/HTTP 入站端口供飞书回调使用;长连接由本机出口主动发起,符合 IPv4-only egress 红线(httpx 模式 `local_address="0.0.0.0"` 的同源原则)
2. **凭证只走 shell env**:
   - `FEISHU_APP_ID`
   - `FEISHU_APP_SECRET`
   - `FEISHU_VERIFY_TOKEN`(预留,长连接路径 SDK 内部使用;若实施期发现 SDK 不需要可不设)
   - `FEISHU_ENCRYPT_KEY`(预留,长连接路径若启用消息加密时使用)
   - `FEISHU_CUSTOM_BOT_WEBHOOK_URL`
   - `FEISHU_CUSTOM_BOT_SIGN_SECRET`(可选;若启用 HmacSHA256 签名时使用)
3. **永不入 git**:`.env` 中只放 `FEISHU_INTERACTIVE_ENABLED` 等非密配置;所有飞书凭证 grep `.env*` / `*.yaml` / `*.json` 必须为空(实施期通过 lint rule 持续校验)
4. **前端不展示完整密钥**:`/api/run-mode/state` 等接口若返回飞书状态,只能返回 `app_id_masked`(末四位)、`webhook_configured` 布尔等脱敏字段
5. **`tenant_access_token` 不持久化**:由 SDK 在内存维护;每次重启重新获取
6. **回调端点延后**:HTTPS 回调路径**不在 P0-2 范围内**。如未来发现长连接稳定性不足,需要 HTTPS 回调作冗余,必须新建 `P0-2-amendment-{date}-https-callback.md` 重新评估,在 amendment 中重新讨论 `Verification Token`、`Encrypt Key`、replay 防护、签名校验、IP 白名单等具体策略(那时可能也并入 P1-6)

P1-6 的具体职责(秘钥存放介质、密钥轮换周期、是否启用前端登录、Feishu 消息记录保留期等)在本决策不展开。

### 1.5 飞书接入与账户生命周期事件的衔接(强约束)

P0-1 §1.3 把"模式切换"定义为账户生命周期事件,P0-2 必须支持其全部子步骤。本节明确两条强约束:

1. **`feishu_off → feishu_on` 切换初始化对账**:
   - 平台**必须**通过自建应用主通道发首条对账消息(不能用备用 webhook,因为备用 webhook 收不到回报)
   - 长连接 worker **必须**在切换流程开始前已建立连接并健康(健康探针:近 N 秒收到过 ping/pong);否则切换流程失败回滚
   - 解析两次失败后放弃切换 + 通知用户:通知通过主通道发(优先)或备用 webhook 发(主通道也挂时)
2. **`feishu_on → feishu_off` 退出**:
   - 路径 A(已清仓退出):用户飞书发"退出真实交易 资产已全部提现"指令 → 长连接 worker 接收 → ExecutionReportParser 解析 → MockBroker 校验通过后归档
   - 路径 B(保留长持监控):用户在前端"手动初始状态设定"页面录入 → **不经过飞书**;但归档完成后系统通过主通道发一条"退出真实交易,模式切换为 simulation_auto"确认消息

### 1.6 不在 P0-2 范围内的明确清单

为防止 P0-2 决策无限蔓延,以下明确**不**在本决策范围内,各自留给对应决策点或实施期:

| 不在 P0-2 范围 | 归属 |
|---------------|------|
| InstructionPlan 字段集与文本模板 | P0-3 |
| 用户回报语法、ambiguous fail-closed 规则 | P0-4 |
| 日终对账模板、偏差阈值 | P0-5 |
| 风控参数(单股仓位/亏损暂停线/单次金额) | P0-7 |
| 告警事件全清单(哪些必须主通道/哪些备用 webhook 也要发) | P2-4 |
| 是否每周导出券商成交单人工对账 | P0-5 |
| 卡片模板 / `card.action.trigger` 回调状态机 | 后续 amendment |
| 密钥轮换周期、前端登录、消息记录保留期 | P1-6 |
| Kimi thinking 是否参与回报解析 | P1-4 + P1-8 |
| `lark-oapi` 具体版本号 | 实施期 `requirements.txt` 锁定 |

## 2. 红线 / 边界(立即生效)

P0-2 落地后这些立即成为代码硬约束:

1. **永久禁止 HTTPS 回调入站端口**(任何 `/api/integrations/feishu/events` HTTP 接收端点的实现必须先走 `P0-2-amendment-...` 决策)
2. **自定义机器人 webhook 仅可发系统告警,绝不发买卖指令 / 对账请求 / 澄清消息**;违规即红线违规(实施期由 lint rule + 集成测试守门)
3. **飞书凭证只走 shell env**:`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_VERIFY_TOKEN` / `FEISHU_ENCRYPT_KEY` / `FEISHU_CUSTOM_BOT_WEBHOOK_URL` / `FEISHU_CUSTOM_BOT_SIGN_SECRET` **永不入 `.env`、永不入 git、永不在前端完整展示**
4. **长连接 worker 只能单实例运行**;水平扩多实例(都建立长连接)属红线违规(飞书消息随机推单 client,会丢消息)
5. **长连接事件处理函数 3 秒内必须返回 ack**;真实解析/落库走异步队列(实施期定具体队列实现)
6. **`tenant_access_token` 不持久化**:不写入 MongoDB / Redis / 文件;只在内存
7. **第一阶段不实现交互卡片**(`interactive` msg_type / `card.action.trigger` 回调实现属实施期外的范围,加它必须先走 amendment)
8. **长连接断线时**:可继续发系统告警(走备用 webhook),但**不可发买卖指令**(因为发出后无法接收回报,会触发 P0-1 §1.5 的 expired 路径,产生大量噪声;由 ModeRouter 在长连接失活态下 fail-closed 拒绝路由买卖类 InstructionPlan)
9. **备用 webhook URL 与 sign secret 不在前端任何形式展示完整值**(脱敏到末四位)
10. **`lark` / `feishu` / `larksuite` 关键字在 `backend/risk/` 子树严禁出现**(继承 P0-1 红线 §8 的 "risk 不依赖外部集成" 原则)

## 3. 影响范围(留给 implementation 阶段)

后续实施任务清单(不在 P0-2 决策内,等所有 P0 锁定后由新执行计划编排):

### 3.1 新增项(代码级)

- `backend/integrations/feishu/__init__.py`
- `backend/integrations/feishu/client.py` — 自建应用 token 管理 + `im/v1/messages` 发送 + 重试 + 限流(参考 audit §7.3 推荐结构)
- `backend/integrations/feishu/longconn.py` — `lark-oapi` 长连接 worker 启动/关闭/健康探针;事件入队
- `backend/integrations/feishu/events.py` — `im.message.receive_v1` 事件 schema 解析 + 入队接口
- `backend/integrations/feishu/dedupe.py` — `event_id` / `message_id` 去重(MongoDB 一份 + 内存 LRU)
- `backend/integrations/feishu/schemas.py` — Pydantic 模型(事件、消息、回报)
- `backend/integrations/feishu/renderer.py` — InstructionPlan / 对账请求 / 澄清消息 → 文本(第一阶段不渲染卡片)
- `backend/integrations/feishu/fallback_webhook.py` — 自定义机器人 webhook 单向发,**仅 ALERT_TYPES 子集允许调用**(代码层硬限制:函数签名只接受 `AlertEvent`,不接受 `InstructionPlan`)
- `backend/api/run_mode.py` 的切换流程中加入"长连接健康探针校验"步骤(在 P0-1 §1.3.1 #1 归档前)

### 3.2 修改项

- `backend/monitoring/alerter.py`:从"通用 webhook"具体化或拆分出"飞书自定义机器人 webhook 发送器"(可能保留通用 sender 抽象,新增 feishu_webhook_sender 实现)
- 启动断言(P0-1 §1.8)新增:若 `FEISHU_INTERACTIVE_ENABLED=true`,则 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 必须存在且非空;否则 `SystemExit`

### 3.3 配置项

- `.env` 新增(非密):无;所有飞书相关都是密钥,走 shell env
- `~/.bashrc` 新增 export(密钥示例,具体值用户提供):
  ```bash
  export FEISHU_APP_ID=cli_...
  export FEISHU_APP_SECRET=...
  # 长连接路径若 SDK 需要(实施期确认):
  export FEISHU_VERIFY_TOKEN=...
  export FEISHU_ENCRYPT_KEY=...
  # 备用通道:
  export FEISHU_CUSTOM_BOT_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/...
  export FEISHU_CUSTOM_BOT_SIGN_SECRET=...   # 可选,启用签名时
  ```
- `config/feishu.yaml`(新):
  - `target_chat_ids`:主群 chat_id 列表(允许多群,但买卖指令只到主群)
  - `longconn`:retry_backoff、heartbeat_timeout 等
  - `fallback_webhook`:enabled、cooldown
  - `rate_limits`:per_chat_qps_soft、per_app_rps_soft(低于飞书硬上限,留 30% 余量)

### 3.4 依赖项

- `requirements.txt` 新增 `lark-oapi`(版本号实施期锁定)
- 不引入 `httpx` 之外的 HTTP 客户端(`lark-oapi` 内部用 `requests` 还是 `aiohttp` 由 SDK 自身决定;若与 IPv4-only egress 红线冲突,实施期需 monkey-patch 或 SDK 配置 `local_address="0.0.0.0"`,这是已知风险点,实施期必须验证)

### 3.5 文档同步(本决策落地立即执行)

- `CLAUDE.md` §1.3 进度行(P0-2 ✅)
- `CLAUDE.md` §2.1 P0-2 行(状态 + 决策文档列)
- `CLAUDE.md` §3.1 红线节同步本文 §2 的红线(尤其 §2.1-§2.3、§2.7-§2.9)
- `CLAUDE.md` §3.4 操作速查的"飞书 key"占位符替换为本决策 §3.3 的 env var 列表
- `MEMORY.md` 索引新增 `project_run_mode_p0_2.md` 条目
- 新建 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_2.md`

## 4. 决策依据

### 4.1 audit 引用

- audit §7.1 确认仓库目前**无任何**飞书 SDK 依赖、无 `backend/integrations/feishu/`,只有 `backend/monitoring/alerter.py` 通用单向 webhook
- audit §7.2 调研结论:双向交互必须企业自建应用 + 机器人能力 + `im.message.receive_v1` 订阅 + 长连接或 HTTPS 回调
- audit §7.3 推荐模块结构与本决策 §3.1 完全一致(`client.py` / `events.py` / `schemas.py` / `renderer.py` / `parser.py` / `dedupe.py`)
- audit §16 提供的官方文档入口在 2026-05 仍有效,本决策已通过 WebFetch 二次核验(见 §4.3)

### 4.2 代码事实抽检(2026-05-09 复核)

- `backend/monitoring/alerter.py:80-82` 读取 `ALERT_WEBHOOK_URL` 通用 webhook,无飞书业务知识,可作为备用通道的演化起点
- `backend/monitoring/alerter.py:135-150` 失败时不在日志中暴露 webhook URL — 该实践直接复用到 `fallback_webhook.py`
- `grep -rn "lark\|feishu\|larksuite" backend/ tests/ frontend/`(排除 `alerter.py`、frontend node_modules、tests)结果为空 — 飞书集成是真正的从零新增,不存在历史代码遗留风险
- `backend/integrations/` 目录在仓库中**不存在** — `backend/integrations/feishu/` 是全新子树,可按 audit §7.3 推荐结构整体规划

### 4.3 联网调研复核(2026-05-09)

通过 WebFetch + WebSearch 验证 audit §16 官方入口在 2026-05 的现状:

| 调研主题 | 关键结论 | 来源 |
|---------|---------|------|
| 自定义机器人 webhook 能力边界 | 仅单向推送,**明确不能响应用户消息或卡片回调**;频控 100/min, 5/s;支持 HmacSHA256 签名 / IP 白名单 / 关键词三种安全 | https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot |
| `im/v1/messages` 主动发 | `receive_id_type` 支持 `chat_id` / `open_id` / `union_id` / `user_id` / `email`;`msg_type` 支持 `text` / `post` / `interactive` 等;频控 50 RPS / 1000 RPM,单群 5 QPS | https://open.feishu.cn/document/server-docs/im-v1/message/create |
| 事件订阅模式 | 长连接(WebSocket)无需公网入站,只需出口能访问公网;HTTPS 回调需公网域名 + Verification Token + Encrypt Key + replay 防护 | https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/choose-a-subscription-mode |
| 长连接 Python SDK | 官方 `lark-oapi`,提供 `lark.ws.Client(APP_ID, APP_SECRET)`;3 秒内必须处理完事件否则重推;集群部署消息随机推单 client | https://feishu.apifox.cn/doc-7518429 |

### 4.4 用户选择记录(2026-05-09 决策对话)

| 问题 | 选择 |
|------|------|
| 飞书接入核心形态? | **自建应用主路径 + 自定义机器人备用** — 业务消息走自建应用双向闭环,系统告警保留独立逃生通道 |
| 事件接收方式? | **长连接(WebSocket via lark-oapi)** — 与 IPv4-only egress 红线兼容,零公网入站 |
| 第一阶段是否上交互卡片? | **纯文本指令 + 严格回报模板** — 卡片留待 P0-3/P0-4 锁定后通过 amendment 引入 |
| 密钥与网络入站边界? | **零公网入站,仅出口** — App ID/Secret/Verify Token/Encrypt Key 仅 shell env;HTTPS 回调路径在 P0-2 范围外,需要时走 amendment |

### 4.5 与 P0-1 的契合点对照

| P0-1 条款 | P0-2 承载方式 |
|-----------|--------------|
| §1.3.1 #1 归档当前 MockBroker | 不需要飞书参与 |
| §1.3.1 #2 重置 MockBroker | 不需要飞书参与 |
| §1.3.1 #3 平台主动发账户初始化对账消息 | `FeishuMessenger.send_text(chat_id, content)` via 自建应用主通道 + `im/v1/messages` |
| §1.3.1 #4 解析与确认 | 长连接 worker 收 `im.message.receive_v1` → 入队 → P0-4 决策范围内的 ExecutionReportParser 异步消费 |
| §1.3.1 #5 初始化 MockBroker 到真实状态 | 不需要飞书参与 |
| §1.3.1 #6 状态切换 | 长连接健康探针通过 + 解析成功 → `mode_transition.py` 完成切换 |
| §1.4 切换期间冻结买卖类 InstructionPlan | ModeRouter 在长连接未健康 / 切换中态下 fail-closed |
| §1.5 on 时未回报的 InstructionPlan | 长连接 worker 收回报 → 异步 → MockBroker;超时机制 P0-4 决策 |
| §2.6 feishu_off 时只发系统告警 | 主通道(自建应用)与备用(自定义机器人)都强制只发告警类 |
| §2.7 feishu_on 时未回报不更新 MockBroker | 长连接收不到回报即不入队,自然不更新 |

### 4.6 替代方案与拒绝理由

| 候选方案 | 拒绝原因 |
|---------|---------|
| 纯自建应用单通道(无备用 webhook) | 长连接掉线 / token 刷新失败时关键告警可能只能落日志,违反"严重故障必须可被人感知"的运维准则 |
| 临时 Webhook + 前端手工录入回报 | 违反 P0-1 §1.3.1(平台必须主动发对账消息**且**自动解析回报);本质上把 feishu_interactive 降级为"前端确认模式",与 2026-05-08 项目方向重构冲突 |
| HTTPS 回调主路径 | 需要公网域名 + 反向代理 + 签名/加密/replay 防护一整套基础设施;与 IPv4-only egress 红线和"本机/内网部署"偏好冲突;任何 P 级缺项都会让事件被丢弃或被伪造 |
| 长连接 + HTTPS 回调双跑做冗余 | 引入两套去重路径与状态机,与 P0-2 阶段"锁高层方向、不锁实现细节"目标冲突;需要冗余时走 amendment |
| 第一阶段全卡片(指令 + 回报均卡片) | P0-3 InstructionPlan 字段集尚未锁定,卡片模板必须等 P0-3;现在就上卡片会反向阻塞 P0-2 落地 |
| 第一阶段文本指令 + 卡片回报按钮 | 同时维护文字回报与卡片回调两条路径,去重/状态机/超时分支翻倍;无明确收益 |

## 5. 后续动作 (checklist)

> 本决策本身定稿不触发实施工作。以下条目仅记录"P0-2 锁定后下一步要做什么",真实落地排期等所有 P0 全部锁定后由新执行计划统一编排。

### 5.1 立刻完成的状态同步(本 PR 内随决策一起提交)

- [x] 写入本决策文档(`docs/decisions/P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md`)
- [ ] 更新 `CLAUDE.md` §1.3:P0-2 状态从 ⏳ 改为 ✅,链接本文件
- [ ] 更新 `CLAUDE.md` §2.1:P0-2 行 决策文档列填本文件路径,备注列收紧为决策结果摘要
- [ ] 更新 `CLAUDE.md` §3.1 红线节:把本文 §2 红线 1-10 同步进去(若与 P0-1 重叠保留更严格的)
- [ ] 更新 `CLAUDE.md` §3.4:飞书 key 占位符替换为本决策 §3.3 的完整 env var 列表 + 注解"P0-2 已锁定"
- [ ] 更新 `MEMORY.md` 索引:新增 `project_run_mode_p0_2.md` 条目
- [ ] 新建 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_2.md`
- [ ] commit 本决策文档 + CLAUDE.md/MEMORY.md 同步更新(单 PR);**等用户授权再 commit**;不自动 push

### 5.2 依赖本决策的下游 P0/P1 决策

- **P0-3 InstructionPlan 字段集**:决定文本模板的具体字段集;与本决策 §1.3 第一阶段纯文本路径直接耦合
- **P0-4 飞书回报语法**:决定长连接 worker 解析阶段的状态机 + ambiguous fail-closed 行为(本决策 §1.1 已约定 worker 仅做 ack + 入队,真实解析在 P0-4 范围)
- **P0-5 账户对账机制**:决定日终对账文本模板,以及解析失败时的容差/异常路径(本决策 §1.5 已约束初始化对账走主通道)
- **P0-6 simulation_auto 验收标准**:决定何时允许第一次切换 `feishu_off → feishu_on`;长连接稳定性纳入验收门槛之一
- **P1-3 飞书消息形态**:本决策已锁第一阶段纯文本;P1-3 进一步锁第二阶段卡片演进路径与触发节点
- **P1-4 回报解析策略**:LLM 是否参与 ambiguous 回报的澄清问题生成
- **P1-6 安全/密钥/访问边界**:本决策已锁"零公网入站 + shell env",P1-6 细化具体存储介质 / 轮换 / 前端登录 / 消息保留期
- **P2-4 告警渠道**:决定哪些事件必须主通道 + 备用 webhook 双发,哪些只主通道

### 5.3 实施期(所有 P0 锁定后)

- [ ] 按 §3.1-§3.4 编写 implementation 任务列表;`backend/integrations/feishu/` 整个子树新增,与 P0-1 中 `run_mode.py` / `mode_transition.py` 同 PR 或紧邻 PR 落地
- [ ] 该 PR 走 codex review 5 轮 hard gate(major 级:新引入外部 SDK + 网络通道 + 凭证管理)
- [ ] 测试覆盖:
  - 长连接 worker 启停 + 健康探针 + 断线重连
  - `im/v1/messages` 发送(text)+ 限流(soft cap)+ 失败回退到备用 webhook 的"是否允许"判断(系统告警允许,买卖指令不允许)
  - `im.message.receive_v1` 事件 schema 解析 + 去重(`event_id` / `message_id`)
  - 备用 webhook 在收到 `InstructionPlan` 入参时**应抛 TypeError**(代码层硬限制 — 编译期就阻止,而非运行时校验)
- [ ] 静态检查:lint rule 阻止 `lark` / `feishu` / `larksuite` 在 `backend/risk/` 子树出现;阻止飞书凭证名出现在 `.env*` / `*.yaml` / `*.json`
- [ ] 验证 `lark-oapi` 与 IPv4-only egress 红线兼容性:若 SDK 内部 HTTP 客户端不支持 `local_address` 配置,需要 monkey-patch 或 SDK 配置注入;若都不行,需走 amendment 决定 fallback

---

_本文件定稿,不再就地修改。如需调整,新建 `P0-2-amendment-{日期}-{原因}.md`。_
