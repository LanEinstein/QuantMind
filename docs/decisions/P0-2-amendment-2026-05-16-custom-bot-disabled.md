# P0-2 修订 — 2026-05-16 自定义机器人在飞书租户级被禁用 / 告警路径走自建应用同款 OpenAPI

> **修订基准**: [P0-2 自建应用 + 长连接 + 自定义机器人备用](./P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md)
> **附加约束**: [P1-6 §1.1 凭证池](./P1-6-secrets-loopback-audit.md) + [P1-7 §1.7 告警渠道](./P1-7-cost-budget-llm-only.md)
> **修订日期**: 2026-05-16(SESSION_LOG #14 启动当日)
> **触发条件**: Owner(项目负责人 = 顶级飞书管理员)在 2026-05-16 凭证 onboarding 阶段确认:
>
> 1. 在飞书管理后台 `admin.feishu.cn` 三处可能位置(数据安全 / 应用安全 / 机器人管理)+ 搜索框都找不到"自定义机器人"开关;
> 2. 飞书 App 内"群机器人 → 添加机器人"列表搜索 `Webhook` / `自定义` / `匿名` / `Custom Bot` 全无结果;
> 3. 这是该飞书组织的版本或租户策略级行为(非 owner 一人能改),**不申请放开**(没意义,且违反"减少凭证池外延"安全姿势)。
>
> **决议**: 不强行启用自定义机器人;调整凭证池 6 → 5,告警通道走自建应用同款 OpenAPI 发到指定告警群。

## 1. 修订前(P0-2 + P1-6 + P1-7 原锁定状态)

### 1.1 双通道架构(原)

| 通道 | 凭证 | 用途 |
|------|------|------|
| 主路径(自建应用) | `FEISHU_APP_ID` + `FEISHU_APP_SECRET` + `FEISHU_VERIFY_TOKEN` + `FEISHU_ENCRYPT_KEY` | lark-oapi WebSocket 收发买卖/对账/澄清(P0-3 / P0-4 / P0-5) |
| 备用 webhook(自定义机器人) | `FEISHU_CUSTOM_BOT_WEBHOOK_URL` + `FEISHU_CUSTOM_BOT_SIGN_SECRET` | **仅**发系统告警(P0-2 §2.5;严禁发买卖/对账/澄清) |

### 1.2 P1-6 §1.1 凭证池(原)

锁定 LLM 3 + 飞书 6 = 9 个凭证条目,**严禁**扩张到 SMTP / Slack / Discord / 其他渠道(P1-7 §1.7 红线)。

### 1.3 P1-7 §1.7 告警渠道(原)

仅 **飞书(备用 webhook) + audit + Phase B 成本拆解面板**;`Alerter.dedup_15min`;**严禁**第二通道(SMTP / Slack / Discord / 邮件 / 短信)。

## 2. 修订后(本 amendment 锁定状态)

### 2.1 凭证池 6 → 5

删除:
- `FEISHU_CUSTOM_BOT_WEBHOOK_URL` ❌
- `FEISHU_CUSTOM_BOT_SIGN_SECRET` ❌

新增:
- `FEISHU_ALERT_CHAT_ID` — 自建应用要发往的告警群 `open_chat_id`(格式 `^oc_[a-f0-9]{24}$`)。Owner 手动建一个专用群,把 QuantMind 自建应用拉进群,把群 `open_chat_id` 写入 `~/.bashrc`。

保留(不变):
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_VERIFY_TOKEN` / `FEISHU_ENCRYPT_KEY`

**新凭证池总数**:LLM 3 + 飞书 5 = 8(净减 1)。

### 2.2 告警通道:走自建应用 OpenAPI

`backend/monitoring/alerter.py`:
- 删除 `webhook_url` / `sign_secret` 字段(连同 HMAC-SHA256 签名代码)。
- 新增 `feishu_client: FeishuClient` 依赖注入(F-001 同款,持有 `tenant_access_token` 内存态)。
- 新增 `alert_chat_id: str` 字段,从 `os.environ["FEISHU_ALERT_CHAT_ID"]` 读取。
- 发送实现:`feishu_client.send_message(chat_id=alert_chat_id, content=...)`,走 `POST /open-apis/im/v1/messages?receive_id_type=chat_id`。
- 消息体必经 `backend/integrations/feishu/renderer.py` 渲染(P0-2 §2.5 + CLAUDE.md §2.6 红线 9 — 严禁 LLM 拼接飞书文本)。
- 重试 / dedup 策略不变(`Alerter.dedup_15min` 继承 P1-7 §1.7)。

### 2.3 通道独立性损失 + 兜底

**损失**: 告警与主决策路径共用 `tenant_access_token` + 同款 OpenAPI。若自建应用整体不可用(token 续期失败 / 飞书 OpenAPI 全停 / 网络中断),告警**也**发不出。这违反原 P0-2 "备用通道独立于主路径"的设计意图。

**兜底**(已锁定不变,均能在主路径故障时仍工作):
1. `audit_events` Mongo 集合 — append-only 落地,180 天 TTL(P1-6 §1.8)。
2. `logs/quantmind.jsonl` + `logs/audit.jsonl` — 文件系统落地,30 天双写(P1-6 §1.8)。
3. Operator SSH 上机 grep `logs/audit.jsonl` — 远程访问已走 SSH tunnel(P1-6 §1.5),凭证管控独立。

**结论**: 兜底 3 层都不依赖飞书,operator 可在飞书完全不可达时拿到所有 critical 告警。可接受。

### 2.4 P1-7 §1.7 红线兼容性

P1-7 §1.7 原文:"告警**仅**飞书 + audit + Phase B 成本拆解面板"。

**修订后**: 自建应用 OpenAPI(`open.feishu.cn/open-apis/im/v1/messages`)**就是**飞书路径,P1-7 红线**不破**。不引入 SMTP / Slack / Discord / 任何第三方渠道。

### 2.5 P1-6 §1.1 红线兼容性

P1-6 §1.1 原文:"凭证池仅 LLM 3 + 飞书 6,封闭"。

**修订后**: LLM 3 + 飞书 5(净减 1)= **更严格**的封闭凭证池。`FEISHU_ALERT_CHAT_ID` 不是凭证(不是密钥,是公开的群 ID 标识),原则上不属于"凭证"范畴,但为了管理统一也写进 `~/.bashrc` 并通过 secrets_validator 检查存在性。**不破红线**。

## 3. 实施期任务调整

### 3.1 H-001 secrets_validator(P0)

修订前:校验 6 个飞书凭证(`FEISHU_INTERACTIVE_ENABLED=true` 时全数必填,否则全可选)。

修订后(本 amendment):
- 4 个自建应用凭证(`FEISHU_APP_ID/APP_SECRET/VERIFY_TOKEN/ENCRYPT_KEY`):`FEISHU_INTERACTIVE_ENABLED=true` 时**必填**;`=false` 时可选。
- `FEISHU_ALERT_CHAT_ID`:`FEISHU_INTERACTIVE_ENABLED=true` 时**必填**;`=false` 时可选(主路径未启用时告警也走不动飞书)。
- `FEISHU_CUSTOM_BOT_WEBHOOK_URL` / `FEISHU_CUSTOM_BOT_SIGN_SECRET`:**严禁存在**(即使有也忽略 + warning + audit 记录"unexpected_legacy_feishu_custom_bot_credential")。
- `.env` 禁忌前缀扫描**新增** `FEISHU_ALERT_*`(同等保护)。
- gitleaks 规则**移除** `FEISHU_CUSTOM_BOT_WEBHOOK_URL` / `FEISHU_CUSTOM_BOT_SIGN_SECRET` 的 pattern。

### 3.2 F-006 alerter(P1 → P0,提前到主路径必备)

修订前:`backend/integrations/feishu/alerting.py` 用 `FEISHU_CUSTOM_BOT_WEBHOOK_URL` + HMAC-SHA256 签名发 webhook;告警类型白名单清晰;dedup_15min。

修订后:
- 文件改名为 `backend/monitoring/alerter.py`(归到通用监控模块,不再"feishu 集成"包内)。
- 内部使用 `backend/integrations/feishu/client.py:FeishuClient.send_message(chat_id, content)` 调 OpenAPI。
- 消息体必经 `renderer.py` 渲染。
- 告警类型白名单 + dedup_15min + 不发买卖 / 对账 / 澄清 三条红线**保留**。

### 3.3 F-001 ~ F-005 影响

均不变。F-001 ~ F-005 的主路径(收发买卖 / 对账 / 澄清)用同款自建应用 + lark-oapi WebSocket,跟告警通道选型独立。

### 3.4 audit_events 影响

`AuditEventType` 不需要变(`SECRETS_VALIDATOR_BLOCKED` / `FEISHU_MESSAGE_SENT` / `FEISHU_ALERT_SENT` 等已有类别覆盖新场景)。

## 4. 红线清单(本 amendment 之后)

1. 凭证池**严格** = LLM 3(`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY`)+ 飞书 5(`FEISHU_APP_ID` / `APP_SECRET` / `VERIFY_TOKEN` / `ENCRYPT_KEY` / `ALERT_CHAT_ID`),**总 8 条**。任何新增凭证必须再发 amendment。
2. `FEISHU_CUSTOM_BOT_*` 前缀**严禁**出现在 `.env` / `~/.bashrc` / 代码任意处。secrets_validator 启动期扫到立即 warning + audit。
3. `~/.bashrc` 仍是凭证唯一来源(P1-6 §1.1 不变)。`.env` 严禁 `LLM_KEY/FEISHU_*` 前缀(扩展到 `FEISHU_ALERT_*`)。
4. `Alerter` 严禁 `import backend.{llm,agents,mirofish,data,risk}`(继承 P0-10 + P1-7);**新允许** `import backend.integrations.feishu.client`(走自建应用 OpenAPI)。
5. `Alerter.send` 严禁拼接 LLM 输出到消息文本;**必经** `renderer.py`(P0-2 §2.5 + CLAUDE.md §2.6 红线 9)。
6. 告警消息**必含** `[QuantMind-ALERT]` 前缀 + 告警类型 + audit_event_id;**严禁**包含 `instruction_id` / 持仓数据 / 用户 PII / 敏感金额。
7. `FEISHU_ALERT_CHAT_ID` 群里**禁止**自建应用接收任何用户消息(主路径群与告警群分离;若 owner 在告警群发消息,客户端**不回应**,服务端**不解析**;若用户误发买卖指令到告警群,丢弃 + warning + audit。
8. `feishu_interactive=false` 时 alerter **不发飞书**,仅落 audit + JSONL。`feishu_interactive=true` 切换前由 `AcceptanceService.can_switch_to_feishu_on()` 校验通过(P0-6 §2 红线 5 不变)。
9. `FeishuClient.tenant_access_token` 仅内存,**严禁**持久化 / 落日志 / 写 audit;过期重取(继承 P0-2 §2.1)。
10. 告警群 `chat_id` 不算"凭证"但走 secrets fingerprint 体系(SHA256[:8])落 audit;**严禁**明文写日志。
11. 主路径自建应用机器人和告警群可以是**同一个机器人 / 同一个 app**,但告警群必须是**单独的群**(不和买卖 / 对账 / 澄清群共用);至少 1 个 operator 在群里。
12. gitleaks pattern 集合**净减**(删 `FEISHU_CUSTOM_BOT_*`),但 `FEISHU_APP_SECRET` / `FEISHU_VERIFY_TOKEN` / `FEISHU_ENCRYPT_KEY` / `FEISHU_ALERT_CHAT_ID` 全留 + 新增 `FEISHU_ALERT_CHAT_ID` 的 `oc_[a-f0-9]{24}` pattern。
13. `redline-check.sh` 新增子检 `.env` 严禁 `FEISHU_ALERT_*` 前缀(继承 P1-6 §1.1 扩展)。
14. P0-2 §2.5 红线"备用 webhook 仅发系统告警,绝不发买卖 / 对账 / 澄清"**改写**为:"告警通道(自建应用同款 OpenAPI 发往 `FEISHU_ALERT_CHAT_ID` 群)只发系统告警,绝不发买卖 / 对账 / 澄清"。文字虽改,**精神不变**:告警群与决策群隔离 + 告警内容白名单。
15. 未来某天 owner 找到办法启用自定义机器人 → 走**反向 amendment** 把这俩凭证补回 + 告警通道切回独立 webhook,**不是无声切换**;独立通道是更安全的最终形态,但不阻塞当前实施。

## 5. 修订记录追加

`docs/plan.html` 修订记录已同步追加。下一次 SESSION_LOG 条目应**首先**引用本 amendment(`P0-2-amendment-2026-05-16-custom-bot-disabled.md`)再开始 H-001 / F-001 实施。
