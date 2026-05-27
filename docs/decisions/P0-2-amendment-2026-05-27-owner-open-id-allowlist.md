# P0-2 修订 — 2026-05-27 入站 owner open_id allowlist(fail-closed,非破坏式收紧)

> **修订基准**: [P0-2 飞书自建应用 + 长连接 + webhook 兜底](./P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md)
> **关联**: [P0-2-amendment-2026-05-16 禁用 custom-bot / 告警群≠决策群](./P0-2-amendment-2026-05-16-custom-bot-disabled.md) /
> P0-4 回报解析 fail-closed 状态机 / P0-5 对账 / CLAUDE.md §2.6
> **修订日期**: 2026-05-27(U-E5 / 缺口 2 端到端真发前置)
> **触发**: U-E5 真发前安全核查发现 `backend/main.py::_feishu_dispatch` 入站只按 `chat_id`(决策群)过滤,
> **未按发送者过滤** —— 决策群里**任何成员**(被拉进群的他人、误入者)发出的、恰好匹配回报/对账正则的纯文本,
> 都会被吃进 parser → applier → MockBroker 镜像。owner 明确要求加入站 owner open_id allowlist,非 owner 回复
> fail-closed 丢弃 + audit(codex 计划评审同款"入站鉴权 + allowlist + 幂等")。

## 1. 修订前(P0-2 / P0-2-amendment-2026-05-16 原锁定)

- 入站唯一通道 = `lark-oapi` WebSocket 长连接,零公网 HTTPS 回调(永禁,不变)。
- `tenant_access_token` 仅内存,SDK 持有(不变)。
- 事件经 `EventDispatcherHandler`(`FEISHU_ENCRYPT_KEY` + `FEISHU_VERIFY_TOKEN` 鉴权)→ `FeishuEventReceiver`
  → `event_id`/`message_id` 去重 → `_feishu_dispatch`。
- `_feishu_dispatch` **仅** `message.chat_id != FEISHU_DECISION_CHAT_ID → 丢弃 + 日志`;**无发送者过滤**。
  匹配的纯文本一律进 `reconciliation_orch.handle_reply` → 失败再 `execution_orch.handle_feishu`。
- 告警群 `FEISHU_ALERT_CHAT_ID` ≠ 决策群 `FEISHU_DECISION_CHAT_ID`,买卖/对账/澄清只发决策群(不变)。

## 2. 修订后(本 amendment 锁定 —— 入站 owner allowlist 不变量)

### 2.1 新增凭证 `FEISHU_OWNER_OPEN_ID`(决策群里被授权下达回报/对账裁定的发送者白名单)
- 值 = owner 的 Feishu `open_id`(`ou_...`);支持**逗号分隔多个**(如 owner 多设备/多账号),解析为 frozenset。
- 来源 = shell env(`~/.bashrc`),与 LLM 3 + 飞书 5 同源管理。**不是机密**(open_id 非 token),
  故**不计入** secrets fingerprint 轮换体系,也**不入** LLM/飞书凭证池计数(`EXPECTED_POOL_SIZE` 仍 8 不变);
  但仍**严禁**写进 `.env`(沿用 P1-6 启动期 `secrets_validator` 禁忌前缀只针对 token 类,open_id 不在其列)。
- 与决策群 `FEISHU_DECISION_CHAT_ID` 互补:chat_id 锁"在哪个群",open_id 锁"群里谁说了算"。

### 2.2 入站三态判定(纯函数 `InboundGate`,可单测)
新增纯模块 `backend/integrations/feishu/inbound_gate.py`:frozen `InboundGate(decision_chat_id, owner_open_ids)`,
`classify(*, chat_id, sender_id) -> InboundVerdict`,三态:
- `ACCEPT` —— `chat_id == decision_chat_id` **且** `sender_id ∈ owner_open_ids` → 放行进 parser/applier;
- `DROP_WRONG_CHAT` —— `chat_id != decision_chat_id` → 丢弃(行为同修订前,日志);
- `DROP_NOT_OWNER` —— 决策群内但 `sender_id ∉ owner_open_ids` → **fail-closed 丢弃 + audit**,
  **绝不**进 parser、**绝不**触碰 applier/MockBroker 镜像。

`InboundGate.from_env(env)` fail-closed 构造:`decision_chat_id` 空 → `ValueError`;`owner_open_ids` 空集 → `ValueError`。

### 2.3 启动期 fail-fast(与决策群同款守门)
`feishu_interactive` 启用且 acceptance gate 通过后,在接 receiver 前 `InboundGate.from_env(os.environ)`:
- `FEISHU_OWNER_OPEN_ID` 未设/空 → **`SystemExit` 拒绝启动**(与"`FEISHU_DECISION_CHAT_ID` 未设拒启动"
  "`FEISHU_ALERT_CHAT_ID == FEISHU_DECISION_CHAT_ID` 拒启动"同级别防御)。
- 退化语义:即便误放行启动,空 allowlist 在 `classify` 里对**所有**发送者判 `DROP_NOT_OWNER`(fail-closed:
  宁可谁都收不进、也绝不让未授权回报改镜像)。两道叠加 = "无 allowlist 不上线"。

### 2.4 audit(复用已锁 34 类,无需新 AuditEventType → 无 P1-6 派生 amendment)
`DROP_NOT_OWNER` 写 `FEISHU_MESSAGE_RECEIVED` + `outcome=BLOCKED` + `actor=FEISHU_USER`
+ `reason_namespace="inbound_sender_not_allowlisted"`;payload 仅含 `sender_fingerprint`(SHA256[:8],
**不存原始 open_id、不存消息原文** —— 防注入 + 不把用户可控 id 泄进每条 audit 行,沿用 `events.py` 既有约束)。

## 3. 不变量(本 amendment **保留**的红线,逐条点名)

- 入站仅 `lark-oapi` WS,**永禁** HTTPS 回调入站(不变)。
- `encrypt_key` + `verify_token` 鉴权仍是第一道(SDK `EventDispatcherHandler`);allowlist 是**其后**的应用层授权,
  二者叠加不替代。
- `parse_ok=False` 仍强制 HOLD;`AMBIGUOUS` 仍**绝不**更新镜像;回报正则仍 strict(P0-4 不变)。
- 告警群≠决策群仍 fail-fast(P0-2-amendment-2026-05-16 不变);allowlist 是在"已在决策群"之上再加发送者闸。
- LLM 仍**绝不**参与入站解析/对账/鉴权(P0-10 不变);`InboundGate` 纯函数,零 `backend.{llm,agents,mirofish}` import。
- `tenant_access_token` 仅内存(不变)。

## 4. 影响面

- 改 `backend/main.py`(用 `InboundGate` 替换 `_feishu_dispatch` 内联 chat_id 检查 + 启动期校验)。
- 新增 `backend/integrations/feishu/inbound_gate.py`(纯函数,可单测)。
- 新增 `FEISHU_OWNER_OPEN_ID` env(owner 真发前与 `FEISHU_INTERACTIVE_ENABLED`/`FEISHU_DECISION_CHAT_ID` 同批设)。
- `simulation_auto`(默认)路径不受影响:receiver 仅在 `feishu_interactive` 启用 + acceptance 通过后才接线。
- 无破坏式:`simulation_auto` 下不需要 `FEISHU_OWNER_OPEN_ID`;现有测试不启 interactive lifespan 路径,不触发 fail-fast。
