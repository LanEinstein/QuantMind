# P1-6 — Secrets shell env 单源 12 月事件驱动轮换 + 全层 127.0.0.1 only 边界 + 不加本机认证 + 审计 Mongo+JSONL 双写 4 类事件强制写

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P1-6 |
| 决策日期   | 2026-05-10 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联 audit | `docs/quantmind_project_audit_2026-05-07.md` §6(安全与可观测性现状)|
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §P1-6 — secrets 轮换 / IP 白名单 / 审计日志 |
| 范围说明   | 本决策为 **P1 决策对齐路径 A 第五份**(P1-2.A/B/C 三子全锁 + P1-5 后第二份非 P1-2 决策);锁定 secrets 存储升级路径 + 凭证轮换策略 + 飞书凭证落地时机 + 凭证泄露应急流程 + IP 白名单边界 + 本机访问认证方式 + 审计日志存储与保留 + 审计事件类型清单 + audit 查询入口 |
| 依赖决策   | `docs/decisions/P0-1-simulation-base-feishu-overlay.md`(§1.3 模式切换 = 账户生命周期事件需写 audit + §2 红线 6 旧 AUTHORIZATION_MODE 矩阵实施期一次性破坏式删除)+ `docs/decisions/P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md`(§1.2 永禁 HTTPS 入站 + §2.5 飞书 6 凭证仅走 shell env 永不入 .env/git + 备用 webhook 仅告警)+ `docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md`(§2 红线 12 frozen Pydantic strict + extra="forbid")+ `docs/decisions/P0-4-execution-report-parser-strict-regex-and-fail-closed-state-machine.md`(§3.1 ExecutionReportApplier 单一入口须可被 audit)+ `docs/decisions/P0-5-daily-reconciliation-fail-closed-tickets.md`(§1.5 reconciliation_ticket 三选一裁定须可被 audit)+ `docs/decisions/P0-6-acceptance-45-day-rolling-stability-and-strategy-gates.md`(§1.1 45 交易日滚动窗口决定 audit_events TTL 下限)+ `docs/decisions/P0-7-risk-redlines-position-circuit-universe-llm-immutability.md`(§2 红线 14 RiskConfig runtime 不可改 + 熔断 cooldown 状态变迁须 audit)+ `docs/decisions/P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md`(§2 红线 7 DataQualityState 早返冻结须 audit)+ `docs/decisions/P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md`(§1.4 agent_models.yaml runtime 不可改 + ¥20 hard ceiling + §2 红线 1 LLM 字段权限矩阵 → LLM 严禁写 audit)+ `docs/decisions/P1-2.A-persistence-hybrid-snapshot-and-broker-scheduler.md`(§1.4 broker_events append-only insert-only 8 项红线 → audit_events 同款约束)+ `docs/decisions/P1-2.B-mtm-30s-equity-points-data-quality-on-demand.md`(§1.7 DataQualityProvider per-stock evaluate 异常须 audit)+ `docs/decisions/P1-2.C-matching-allornone-defensive-limitcheck-tiered-slippage-transfer-fee.md`(§1.1 三层 reason 命名空间区分 → audit reason 字段携带命名空间)+ `docs/decisions/P1-5-frontend-workflow-mvp-7-pages-readonly-first-write-strict-bounded.md`(§2 红线 11 P1-5 暂不加本机认证 P1-6 处置 + 前端不允许存储任何凭证 + Vite host 127.0.0.1 + §2 红线 14 末四位脱敏 + §2 红线 1 MVP 7 + Phase B 4 共 11 页名额不动)|
| 派生 amendment | (无破坏式;新增 backend/audit/* 模块 + scripts/query_audit.py + 新增 GET /api/audit/events 端点 + 修复 Vite host '0.0.0.0' → '127.0.0.1' 历史违规;P1-5 §2 红线 11 在本决策完成"P1-6 处置"承诺即"不加本机认证")|
| 替代       | 当前 secrets 状态:LLM key 已在 ~/.bashrc + .env 仅非密配置 ✅;但 Vite vite.config.ts host: `'0.0.0.0'` 实际违反 P1-5 §2 红线 11;backend uvicorn 启动未显式 --host;0 凭证轮换脚本;0 应急 playbook;0 gitleaks pre-commit hook;0 audit Mongo collection;0 启动期 secrets validator;0 本机认证(P1-5 已铺垫"P1-6 处置"未决) |

## 决策摘要

QuantMind P1-6 安全与可观测性采用 **secrets 仅走 shell env 单源 + git 钩子防护 + 启动期 fail-fast 三件套 + 12 月最长保质期事件驱动 5 类强制轮换 + 飞书 6 凭证仅锁约束实际配置延迟到 feishu_interactive 启用前 + 三步应急 playbook + 全层严锁 127.0.0.1 only(backend/frontend Vite/MongoDB/Redis/Nginx)+ 远程访问仅走 SSH tunnel + 不加本机认证(127.0.0.1 边界 + SSH tunnel 已足够)+ 审计 Mongo audit_events 180 天 TTL + JSONL 30 天双写 + 4 类事件强制写 audit + 后端 CLI + GET API 查询不加前端页面** 架构,完成 P1 决策对齐路径 A 第五份决策:

1. **Secrets 存储:shell env 单源 + git 钩子防护 + 启动期 fail-fast 三件套**:LLM key(`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY`)+ 飞书 6 凭证(`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_VERIFY_TOKEN` / `FEISHU_ENCRYPT_KEY` / `FEISHU_CUSTOM_BOT_WEBHOOK_URL` / `FEISHU_CUSTOM_BOT_SIGN_SECRET`)+ 未来增加凭证(若引入 MongoDB/Redis auth、备份加密 key 等)**全部走 `~/.bashrc` 单一真相源**;`.env` 仅放非密配置(`MONGODB_URI` / `REDIS_URL` / `LOG_LEVEL` / `BROKER_MODE` 等);`.gitignore` 已覆盖 `.env` + `*.key`;**git 钩子防护** = `.pre-commit-config.yaml` 引入 `gitleaks` v8.x + 自定义 rules 覆盖 `sk-*` / `FEISHU_*` / `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY` pattern,hook 失败阻止 commit;**启动期 fail-fast** = 新建 `backend/services/secrets_validator.py` 在 `main.py:lifespan` 启动时扫描 `.env` 文件不得含 LLM_KEY/FEISHU_* + 扫描 process env LLM key 必须 ≥30 字符且匹配 provider 格式,失败即 `exit(1)` 不启动。轮换 = 编辑 `~/.bashrc` + `source ~/.bashrc` + `systemctl restart quantmind-backend.service` + 启动期日志确认新 key fingerprint。**不引入 sops / age / 1Password CLI / Vault / OS keyring**(单实例项目过度工程 / 增加学习成本 / 单点故障)。

2. **凭证轮换:事件驱动 + 12 月最长保质期 5 类强制触发**:5 类强制轮换触发条件 = ① **凭证泄露/疑似泄露**(任一形式:git 误提交、日志意外打印、第三方泄露、provider 通知 key 异常活动)② **团队成员变动**(离职/加入;即使个人项目也保留此条以备 P2 团队化)③ **provider 通知 key 异常活动**(DeepSeek/DashScope/Moonshot 控制台告警 + 飞书开放平台告警)④ **12 个月自然到期**(超期由启动期 secrets_validator 输出 warning + audit_events 记录,不强制 exit 但显示告警)⑤ **升级 P2 真实账户前**(simulation_auto → live 升级前必须全凭证轮换)。**非强制场景不主动轮换**(避免运维负担)。**凭证 fingerprint = SHA256(value)[:8] 写 audit_events / 启动期日志,严禁 plaintext 写任何持久化通道**(继承 P1-5 §2 红线 14 末四位脱敏精神,fingerprint 模式更严:不可由末四位反推前四位关联识别)。轮换流程同 §1 步骤。**不引入季度强制 / 月度强制 / 永不到期任意一种**(季度月度对单实例项目过度运维;永不到期长期不变累积风险)。

3. **飞书 6 凭证落地时机:P1-6 仅锁约束,实际 ~/.bashrc 配置延迟到 feishu_interactive 启用前**:**P1-6 决策仅锁定**:6 凭证存储方式(shell env 单源)+ 轮换策略(12 月事件驱动)+ 应急流程(三步)+ fingerprint 模式 + 启动期 fail-fast 行为(`backend/feishu/client.py` 启动期 fail-fast **仅在** `FEISHU_INTERACTIVE_ENABLED=true` 时触发,默认 `false` 不影响 simulation_auto 启动)。**实际 `~/.bashrc` 配置不强制现在做**(因 P1-2.A/B/C + P1-5 焦点是 simulation_auto 底座 + 前端;飞书 P0-2 已锁但实际启用日期取决于 `acceptance.can_switch_to_feishu_on()` 通过 = 45 交易日滚动窗口 + 5 稳定性硬门槛 + 3 策略硬门槛全部 PASS,可能 P2 末)。**与 P0-1 §1.3 "模式切换 = 账户生命周期事件不是 flag toggle" 节奏一致**:实际配置在 `feishu_interactive` 启用前一周内完成,避免凭证过期 + 集成测试不充分。**不引入 P1-6 lock 时同步配置全 6 凭证**(启用日期未定可能配过期再轮换浪费)+ **不引入差异化 4+2 配置**(只写型 4 凭证现在配,长连接相关 2 凭证延迟,增加复杂度记忆成本高)。

4. **凭证泄露应急:三步应急 playbook + gitleaks pre-commit + 启动期 secrets_validator 三件套**:**三步应急 playbook** `docs/runbook/secrets-incident-response.md`:① **立即轮换泄露凭证**(更新 `~/.bashrc` + `source` + `systemctl restart quantmind-backend.service` + provider 控制台 revoke 旧 key)② **git history 排查**(`git log -p --all -S '<泄露 key 前 8 字符>'` + 若误提交则 `git filter-repo` 重写历史 + force push 仅本人确认后执行;**若已 push 到公网仓库视为永久泄露不可挽回必须 revoke**)③ **影响评估**(audit_events 反查 24h 内调用 + 飞书凭证泄露则检查 feishu 控制台消息历史 + 成本反查 `backend/services/cost_guard` 异常飙升)。**gitleaks pre-commit hook**:`.pre-commit-config.yaml` 加 gitleaks v8.x + custom rules 覆盖 `sk-*` / `FEISHU_*` / `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY` pattern;hook 失败阻止 commit。**启动期 secrets_validator** `backend/services/secrets_validator.py` 在 `main.py:lifespan` 启动时:(a) 扫描 `.env` 文件不得含 LLM_KEY/FEISHU_* prefix(防误塞)(b) 扫描 process env LLM key 必须 ≥30 字符且匹配 provider 前缀(`sk-` for DeepSeek / `sk-` for Moonshot / 32 hex for DashScope);(c) 失败即 `exit(1)` 不启动 + 写 audit_events + 写 logs/quantmind.jsonl ERROR。**不仅 gitleaks 无 fail-fast**(启动期 .env 误塞 secret 仍会泄露)+ **不仅 fail-fast 无 gitleaks**(commit 阶段无防护误提交后只能事后清理)+ **不仅手工 playbook**(防御层薄弱违反 fail-closed for data corruption 原则)。

5. **IP 白名单:全层严锁 127.0.0.1 only + 远程访问仅 SSH tunnel**:**Backend** `uvicorn` 显式 `--host 127.0.0.1` 写入 `deploy/quantmind-backend.service` + CLAUDE.md §5 操作速查命令;**Frontend** `vite.config.ts` `host: '127.0.0.1'`(**修复 P1-5 §2 红线 11 历史违规** — 当前实际 `'0.0.0.0'`,本决策实施期 F-NNN 任务必修);**MongoDB / Redis** 保持 `docker-compose.yml` 127.0.0.1 绑定(已 ✅);**Nginx** upstream `127.0.0.1:8000` + listen 显式补充 `127.0.0.1:80` / `127.0.0.1:443`(当前未显式绑定 IP);**远程访问严格走 SSH tunnel**(`ssh -L 9276:127.0.0.1:9276 user@host` + `ssh -L 8000:127.0.0.1:8000 user@host`),SSH 本身是身份认证层,不需在应用层重复;**Httpx 出站客户端必须 `local_address="0.0.0.0"`**(继承 CLAUDE.md §2.10 红线 11,host 无 IPv6 默认路由)— 注意 httpx 出站绑定 0.0.0.0 不冲突 backend 入站 127.0.0.1 only(出站和入站不同方向)。**不引入 LAN 段开放 Vite 移动设备访问**(LAN 任意设备可访问未认证 UI = 不可接受风险 + 违反 P1-5 §2 红线 11 严格意旨)+ **不引入全 0.0.0.0 + iptables 控制**(单机过度复杂 + iptables 漏配即全凭证泄露 + 与 P0-2 "HTTPS 回调入站端口永禁" 红线冲突)。

6. **本机访问认证:不加(127.0.0.1 边界 + SSH tunnel 已足够)**:**127.0.0.1 only 含义** = 仅本机进程可达 = OS 用户隔离已是身份认证层;**远程访问** = SSH tunnel 强制(SSH 本身是身份认证层);**QuantMind 是个人单机项目** = 不适用多人共用 OS 账号场景;**加认证 = 重复防护 + 引入凭证管理复杂度 + 与 P1-5 §2 红线 11 "前端不存凭证" 冲突**(token / cookie / Authorization header 任一插入都触发);**前端 axios 不插任何 Authorization header / Bearer token / cookie**(继承 P1-5 §2 红线 11);**后端 FastAPI 不挂任何 auth middleware / Depends(get_current_user) / API key 校验**;**不引入单 token 文件认证**(鸡蛋问题:未认证者如何拿到 token = 要么后端提供 GET 端点违反认证逻辑要么 cookie 违反 P1-5)+ **不引入 OAuth/OIDC / mTLS**(单人项目过度工程 + OIDC 增加 provider 依赖 + mTLS 证书轮换复杂)。**P1-5 §2 红线 11 "P1-6 处置"承诺在本决策履行 = 不加本机认证**。

7. **审计日志:Mongo audit_events 180 天 TTL + JSONL 30 天双写**:**新建 MongoDB `audit_events` collection**(append-only insert-only 不可变,继承 P1-2.A §1.4 broker_events 8 项红线);**schema 字段**(由 `backend/audit/models.py` `AuditEvent` frozen Pydantic v2 strict + extra="forbid" 锁定):
   - `event_id` (UUIDv4 字符串)
   - `timestamp` (UTC ISO8601)
   - `event_type` (`AuditEventType` enum;锁定值见 §1.8)
   - `actor` (`feishu_user` / `frontend_user` / `system` / `scheduler` / `cli`)
   - `actor_detail` (可选;feishu user_id / frontend session_id / scheduler job_name / cli script_name)
   - `resource_type` (`instruction_plan` / `execution_report` / `reconciliation_ticket` / `mode_switch` / `freeze_source` / `circuit_breaker` / `secret` / `feishu_message` / `risk_check` / `eod_pipeline` 等)
   - `resource_id` (对应 resource 的 ID;若无则 None)
   - `payload` (frozen Pydantic 嵌套对象;**凭证类仅写 fingerprint=SHA256[:8] 严禁 plaintext**;**飞书消息 raw_text 完整记录因继承 P0-3 §2.5 LLM 严禁拼接飞书消息文本无 prompt injection 风险**)
   - `outcome` (`success` / `failure` / `blocked` / `degraded`)
   - `correlation_id` (跨调用串;同一 InstructionPlan 生命周期或同一 ticket 裁定全部串起)
   - `reason_namespace` (可选;命名空间区分用,如 `'limit_up_block'` (RiskEngine) vs `'price_limit_violation_at_fill'` (MockBroker) 继承 P1-2.C §2 红线 11)
   
   **TTL index 180 天**(基于 P0-6 45 交易日 acceptance 滚动窗口 × 4 倍安全余量,可复盘 acceptance 期间全部决策);**索引** = `(timestamp DESC)` + `(event_type, timestamp DESC)` + `(actor, timestamp DESC)` + `(correlation_id)` + `(resource_type, resource_id)`;**Mongo ops failure 时 fail-open**(JSONL 写 `audit_persistence_failed` warning;不阻主路径;遵循 fail-closed for data corruption ·vs· fail-open for infra glitches 原则);**JSONL 文件并行双写**作为 infra glitch 备份(`logs/audit.jsonl` 独立于 `logs/quantmind.jsonl`,日轮转 30 天保留),**Mongo 故障期间用户回报 / 模式切换 / 飞书发送等关键事件由 JSONL 兜底**;**严禁 LLM 写 audit_events**(继承 P0-10 §2 红线 1 LLM 字段权限矩阵)。**不引入仅 Mongo 不双写**(Mongo 故障即 audit 丢失 + Mongo 自身故障事件无法记 audit)+ **不引入仅 JSONL 365 天**(难查询 / 不能关联 broker_events / reconciliation_tickets 堆栈分析)+ **不引入仅现有 quantmind.jsonl 30 天**(< 45 交易日 acceptance 窗口决策复盘丢历史 + 与 fail-closed for data corruption 原则不符)。

8. **审计事件类型清单:4 类事件强制写 audit_events + 调试性事件不入**:`AuditEventType` enum 锁定:
   - **类 1 — 两唯二写入端点调用**(`execution_report_submitted` / `reconciliation_ticket_decided`):actor=feishu_user/frontend_user + raw_payload + outcome + correlation_id 串起上下游 broker_events / ticket_state_change
   - **类 2 — 模式切换 + 冻结源 + 生命周期事件**(`feishu_interactive_toggled` / `mockbroker_reset` / `freeze_source_switch_changed` / `freeze_source_ticket_open_changed` / `freeze_source_circuit_breaker_changed` / `freeze_source_data_quality_changed` / `freeze_source_eod_pipeline_changed` / `advance_day_executed` / `eod_pipeline_succeeded` / `eod_pipeline_failed` / `recovery_snapshot_created`):before/after state + trigger_reason
   - **类 3 — 凭证生命周期 + 飞书收发事件**(`secrets_validator_passed` / `secrets_validator_blocked` / `key_fingerprint_changed`(轮换检测)/ `feishu_main_message_sent` / `feishu_webhook_alert_sent` / `feishu_message_received`):凭证类仅写 fingerprint;飞书 raw_text 完整记录(无 prompt injection 风险因继承 P0-3 §2.5 LLM 严禁拼接飞书消息文本)
   - **类 4 — 异常 + 拦截事件**(`state_machine_illegal_transition` / `risk_engine_check_rejected` / `builder_early_return` / `mockbroker_price_limit_violation_at_fill` / `data_quality_breach` / `reconciliation_ticket_open_or_expired` / `llm_call_timeout_30s` / `daily_cost_ceiling_20cny_breached`):reason 携带命名空间(继承 P1-2.C §2 红线 11)
   
   **调试性事件不入 audit**(LLM raw response / RiskEngine 全路径 trace / 每次行情快照写入 / 每次 LLM 调用原始响应 / 每次 cron 心跳):走 `logs/quantmind.jsonl`;180 天 TTL 下存储爆炸 + audit 信噪比下降。

9. **Audit 查询入口:后端 CLI 脚本 + GET API,不加前端页面**:**CLI 脚本** `scripts/query_audit.py`(按 `--event-type` / `--actor` / `--time-range` / `--correlation-id` 查询 + JSONL 输出 + 支持 `--format=table` / `--format=jsonl`);**GET API** `backend/api/audit.py` `GET /api/audit/events`(支持 query params + 分页;**仅 GET 符合 P1-5 §2 红线 1+2 严禁 POST/PUT/PATCH/DELETE 在 backend/api/* 除两唯二例外**);**前端不占 P1-5 MVP 7 + Phase B 4 共 11 页名额**(后期 Operator 页若需加查询界面需走 P1-5 amendment 后再加;P1-5 11 页名额不动);**不引入前端独立"审计查询"页**(超出 P1-5 锁状态 + 个人项目 CLI 足够)+ **不引入仅 JSONL+jq 查询**(与 §1.7 Mongo audit_events 存在但无访问入口架构不一致)。

10. **第一阶段排除项**:多人 RBAC 权限分级(P2-3 范围;单人项目)/ 加密文件 secret 管理(sops/age/Vault P2-3 团队化时再评估)/ 远程跨网访问(P2-3;P1-6 仅本机 SSH tunnel)/ 审计事件实时告警通道(P1-7 预算扩 + 告警阈值合并处理)/ 审计 ML 异常检测(P3 范围)/ 多 Mongo replica geo-distribution(P2-3)/ 审计区块链不可篡改存证(过度工程)/ 飞书凭证 6 项立即配置(§1.3 延迟到 feishu_interactive 启用前)/ HTTPS 入站接收飞书事件(继承 P0-2 §2 红线 1)/ webhook 直接发买卖指令(继承 P0-2 §2.5)。

## 1. 决策具体内容

### 1.1 Q1 — Secrets 存储升级路径(shell env 单源 + git 钩子防护 + 启动期 fail-fast 三件套)

#### 1.1.1 存储方式

| 凭证类别 | 名称 | 存储位置 | 当前状态 |
|----------|------|----------|----------|
| LLM | `DEEPSEEK_API_KEY` | `~/.bashrc` | ✅ 已配置 |
| LLM | `DASHSCOPE_API_KEY` | `~/.bashrc` | ✅ 已配置 |
| LLM | `MOONSHOT_API_KEY` | `~/.bashrc` | ✅ 已配置 |
| 飞书 | `FEISHU_APP_ID` | `~/.bashrc` | ⏳ 延迟到启用前(§1.3)|
| 飞书 | `FEISHU_APP_SECRET` | `~/.bashrc` | ⏳ 延迟到启用前(§1.3)|
| 飞书 | `FEISHU_VERIFY_TOKEN` | `~/.bashrc` | ⏳ 延迟到启用前(§1.3)|
| 飞书 | `FEISHU_ENCRYPT_KEY` | `~/.bashrc` | ⏳ 延迟到启用前(§1.3)|
| 飞书 | `FEISHU_CUSTOM_BOT_WEBHOOK_URL` | `~/.bashrc` | ⏳ 延迟到启用前(§1.3)|
| 飞书 | `FEISHU_CUSTOM_BOT_SIGN_SECRET` | `~/.bashrc` | ⏳ 延迟到启用前(§1.3)|
| 数据库 (若引入 auth) | `MONGODB_USERNAME` / `MONGODB_PASSWORD` | `~/.bashrc` | 当前 127.0.0.1 only 无 auth |
| Redis (若引入 auth) | `REDIS_PASSWORD` | `~/.bashrc` | 当前 127.0.0.1 only 无 auth |
| 备份加密 (P2 启用) | `BACKUP_ENCRYPTION_KEY` | `~/.bashrc` | 未启用 |

`.env` 文件**仅放非密配置**:`MONGODB_URI` / `REDIS_URL` / `LOG_LEVEL` / `BROKER_MODE` / `MOCK_INITIAL_CAPITAL` / `QUANTMIND_DAILY_BUDGET` / `FEISHU_INTERACTIVE_ENABLED` 等。

#### 1.1.2 git 钩子防护(`.pre-commit-config.yaml` + gitleaks)

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.0
    hooks:
      - id: gitleaks
        args: ["protect", "--config=.gitleaks.toml", "--staged", "--verbose"]
```

```toml
# .gitleaks.toml
[extend]
useDefault = true

[[rules]]
id = "deepseek-api-key"
description = "DeepSeek API key"
regex = '''(?i)(deepseek[_-]?api[_-]?key|sk-[a-z0-9]{32,})'''
tags = ["llm", "deepseek"]

[[rules]]
id = "dashscope-api-key"
description = "DashScope API key"
regex = '''(?i)(dashscope[_-]?api[_-]?key|sk-[a-z0-9]{32,})'''
tags = ["llm", "dashscope"]

[[rules]]
id = "moonshot-api-key"
description = "Moonshot Kimi API key"
regex = '''(?i)(moonshot[_-]?api[_-]?key|sk-[a-z0-9]{32,})'''
tags = ["llm", "moonshot"]

[[rules]]
id = "feishu-credential"
description = "Feishu credential"
regex = '''(?i)(feishu_(app_id|app_secret|verify_token|encrypt_key|custom_bot_webhook_url|custom_bot_sign_secret))'''
tags = ["feishu"]
```

`pre-commit install` 安装本地 hook;commit 时自动扫描 staged diff;失败阻止 commit。

#### 1.1.3 启动期 fail-fast(`backend/services/secrets_validator.py`)

```python
# backend/services/secrets_validator.py
"""WHY: 启动期 fail-fast 防 .env 误塞 secret + process env 不完整启动。
在 main.py:lifespan 启动期调用,失败 exit(1) 不启动。"""

from pathlib import Path
import os
import re
import sys
from dataclasses import dataclass

FORBIDDEN_ENV_KEY_PREFIXES = (
    "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "MOONSHOT_API_KEY",
    "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_VERIFY_TOKEN",
    "FEISHU_ENCRYPT_KEY", "FEISHU_CUSTOM_BOT_WEBHOOK_URL",
    "FEISHU_CUSTOM_BOT_SIGN_SECRET",
)

LLM_KEY_PATTERNS = {
    "DEEPSEEK_API_KEY": re.compile(r"^sk-[A-Za-z0-9]{32,}$"),
    "MOONSHOT_API_KEY": re.compile(r"^sk-[A-Za-z0-9]{32,}$"),
    "DASHSCOPE_API_KEY": re.compile(r"^[a-zA-Z0-9-]{32,}$"),
}

@dataclass(frozen=True)
class SecretsValidationResult:
    passed: bool
    blocked_reasons: tuple[str, ...]
    fingerprints: dict[str, str]  # name -> sha256[:8]

def validate_secrets() -> SecretsValidationResult:
    """启动期同步调用。失败由调用方决定 exit(1) 或记 audit。"""
    blocked: list[str] = []
    fingerprints: dict[str, str] = {}
    
    # (a) 扫描 .env 文件不得含 LLM_KEY / FEISHU_* prefix
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for prefix in FORBIDDEN_ENV_KEY_PREFIXES:
                if line.startswith(f"{prefix}=") or line.startswith(f"{prefix} ="):
                    blocked.append(f"forbidden_secret_in_dotenv:{prefix}")
    
    # (b) 校验 process env LLM key (3 个 LLM key 必须都已配置)
    for key_name, pattern in LLM_KEY_PATTERNS.items():
        value = os.environ.get(key_name, "")
        if not value:
            blocked.append(f"missing_llm_key:{key_name}")
            continue
        if not pattern.match(value):
            blocked.append(f"invalid_llm_key_format:{key_name}")
            continue
        fingerprints[key_name] = _fingerprint(value)
    
    # (c) FEISHU_INTERACTIVE_ENABLED=true 时校验飞书 6 凭证
    if os.environ.get("FEISHU_INTERACTIVE_ENABLED", "false").lower() == "true":
        for fkey in (
            "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_VERIFY_TOKEN",
            "FEISHU_ENCRYPT_KEY", "FEISHU_CUSTOM_BOT_WEBHOOK_URL",
            "FEISHU_CUSTOM_BOT_SIGN_SECRET",
        ):
            value = os.environ.get(fkey, "")
            if not value:
                blocked.append(f"missing_feishu_credential:{fkey}")
                continue
            fingerprints[fkey] = _fingerprint(value)
    
    return SecretsValidationResult(
        passed=len(blocked) == 0,
        blocked_reasons=tuple(blocked),
        fingerprints=fingerprints,
    )

def _fingerprint(value: str) -> str:
    """SHA256(value)[:8] — 不可逆 + 8 字符长度足以区分轮换检测。"""
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()[:8]
```

`main.py:lifespan` 调用:

```python
# backend/main.py
from contextlib import asynccontextmanager
from backend.services.secrets_validator import validate_secrets
from backend.audit.store import audit_store

@asynccontextmanager
async def lifespan(app):
    result = validate_secrets()
    if not result.passed:
        # 写 audit + 写 logs/quantmind.jsonl ERROR + exit(1)
        await audit_store.write(
            event_type="secrets_validator_blocked",
            actor="system",
            payload={"blocked_reasons": list(result.blocked_reasons)},
            outcome="blocked",
        )
        logger.error("secrets_validator_blocked", reasons=result.blocked_reasons)
        sys.exit(1)
    
    # 成功 - 写 audit + 启动期日志显示 fingerprint
    await audit_store.write(
        event_type="secrets_validator_passed",
        actor="system",
        payload={"fingerprints": result.fingerprints},
        outcome="success",
    )
    logger.info("secrets_validator_passed", fingerprints=result.fingerprints)
    yield
```

#### 1.1.4 排除选项

- **sops + age**:加密文件入 git;但单实例项目过度工程 + age 私钥泄露 = 全凭证泄露(把鸡蛋放一篮)+ 增加学习成本;**否决**
- **1Password CLI / HashiCorp Vault**:专业 secret manager;但单点故障(op/vault 不可用即启动失败)+ 不符合 QuantMind 单机轻量定位;**否决**
- **OS keyring (Linux Secret Service / gnome-keyring)**:免明文存储;但需 GUI 会话(headless server 受限)+ 增加 keyring 依赖;**否决**

### 1.2 Q2 — 凭证轮换:事件驱动 + 12 月最长保质期 5 类强制触发

#### 1.2.1 5 类强制轮换触发条件

| 触发类型 | 描述 | 检测方式 |
|----------|------|----------|
| ① 凭证泄露/疑似泄露 | git 误提交 / 日志意外打印 / 第三方泄露 / provider 通知 | 人工识别 + gitleaks 扫描 + provider 控制台告警 |
| ② 团队成员变动 | 离职 / 加入(P2 团队化预留) | 人工流程触发 |
| ③ provider 通知 key 异常活动 | DeepSeek/DashScope/Moonshot/飞书 控制台告警 | provider 邮件/控制台告警监控 |
| ④ 12 个月自然到期 | 超过 12 个月未轮换 | 启动期 secrets_validator 检测(基于 audit_events `key_fingerprint_changed` 最早记录推算)|
| ⑤ 升级 P2 真实账户前 | simulation_auto → live 升级前必须全凭证轮换 | 决策对齐时强制 |

**12 月到期为 warning 不强制 exit**:超期检测后启动期日志显示 `WARNING: <key_name> not rotated for >365 days, last_rotation=<timestamp>` + 写 audit_events `key_rotation_overdue`;不阻止启动(避免突发事件导致系统宕机)。**强制轮换由 5 类触发条件决定,12 月仅为提醒**。

#### 1.2.2 轮换流程

1. 编辑 `~/.bashrc` 替换 key value(provider 控制台先生成新 key,旧 key 暂保留 + revoke 推迟到新 key 验证后)
2. `source ~/.bashrc` 当前 shell 生效
3. `systemctl restart quantmind-backend.service`(无 hot-reload,继承 P0-7 §2 红线 14 + P0-10 §1.4)
4. 启动期日志确认新 key fingerprint(对比 audit_events 历史 fingerprint 验证轮换成功)
5. provider 控制台 revoke 旧 key

#### 1.2.3 凭证 fingerprint 模式(SHA256[:8])

- **写位置**:`audit_events.payload.fingerprints` + `logs/quantmind.jsonl` 启动日志
- **不可逆**:SHA256 单向 + 8 字符前缀 = 仅用于轮换检测(对比新旧 fingerprint 是否变化),不可由 fingerprint 反推 key 内容
- **比 P1-5 末四位脱敏更严**:末四位可能被关联推断(同一 provider key 前缀模式固定);fingerprint 完全无关联性

#### 1.2.4 排除选项

- **季度强制轮换**:对单实例项目过度运维 + provider rate limit 风险(高频换 key 触发 provider 反欺诈);**否决**
- **月度强制轮换**:同上 + 运维不可持续;**否决**
- **永不到期**:长期不变累积风险;**否决**

### 1.3 Q3 — 飞书 6 凭证落地时机:P1-6 仅锁约束实际配置延迟到 feishu_interactive 启用前

#### 1.3.1 P1-6 仅锁约束(本决策完成范围)

- 6 凭证存储方式(shell env 单源,§1.1.1 表格 ⏳ 行)
- 轮换策略(12 月事件驱动,§1.2)
- 应急流程(三步,§1.4)
- fingerprint 模式(SHA256[:8],§1.2.3)
- 启动期 fail-fast 行为(`backend/feishu/client.py` 启动期 fail-fast **仅在** `FEISHU_INTERACTIVE_ENABLED=true` 时触发)

#### 1.3.2 实际配置延迟到 feishu_interactive 启用前一周内

- **启用前置条件**:`acceptance.can_switch_to_feishu_on()` 通过 = 45 交易日滚动窗口 + 5 稳定性硬门槛 + 3 策略硬门槛全部 PASS(继承 P0-6)
- **启用前一周配齐**:避免凭证过期 + 集成测试不充分 + 长连接 ack 测试 + 备用 webhook 告警测试
- **配置流程**:飞书开放平台创建自建应用 → 6 凭证生成 → 写入 `~/.bashrc` → `source` + 重启 → 启动期 fail-fast 通过 → 集成测试

#### 1.3.3 默认行为(simulation_auto 阶段)

- `FEISHU_INTERACTIVE_ENABLED=false`(默认)
- secrets_validator 启动期**不**校验飞书 6 凭证(仅校验 LLM 3 key)
- `backend/feishu/client.py` 启动期**不**初始化(避免 fail-fast 阻止 simulation_auto 启动)
- `backend/feishu/scheduler.py` 长连接**不**启动

#### 1.3.4 排除选项

- **P1-6 lock 时同步配置全 6 凭证**:启用日期未定可能配过期再轮换浪费 + 与 P0-1 §1.3 模式切换 = 账户生命周期事件节奏不一致;**否决**
- **差异化 4+2 配置**(只写型 4 凭证现在配长连接 2 凭证延迟):增加复杂度记忆成本高;**否决**

### 1.4 Q4 — 凭证泄露应急:三步应急 playbook + gitleaks pre-commit + 启动期 secrets_validator 三件套

#### 1.4.1 三步应急 playbook(`docs/runbook/secrets-incident-response.md`)

**Step 1 — 立即轮换泄露凭证**

```bash
# 1.1 编辑 ~/.bashrc 替换泄露 key (provider 控制台先生成新 key)
vim ~/.bashrc

# 1.2 source 生效
source ~/.bashrc

# 1.3 重启服务 (Backend 进程自动加载新 env)
sudo systemctl restart quantmind-backend.service

# 1.4 启动期日志确认新 fingerprint
journalctl -u quantmind-backend.service -n 50 | grep secrets_validator_passed

# 1.5 provider 控制台 revoke 旧 key
# DeepSeek: https://platform.deepseek.com/api_keys
# DashScope: https://dashscope.console.aliyun.com/apiKey
# Moonshot: https://platform.moonshot.cn/console/api-keys
# 飞书: 飞书开放平台 → 应用管理 → 凭证与基础信息 → 重置 App Secret
```

**Step 2 — git history 排查**

```bash
# 2.1 搜索泄露 key 是否曾在 git history (取前 8 字符防完整泄露二次)
LEAKED_PREFIX="<泄露 key 前 8 字符>"
git log -p --all -S "$LEAKED_PREFIX" --source --remotes

# 2.2 若误提交则 git filter-repo 重写历史
# 警告:force push 仅本人确认后执行
# 警告:若已 push 到公网仓库视为永久泄露不可挽回必须 revoke
pip install git-filter-repo
git filter-repo --replace-text <(echo "$LEAKED_PREFIX==>***REDACTED***")

# 2.3 force push (仅本地仓库或本人个人 fork 才允许)
git push --force-with-lease origin main
```

**Step 3 — 影响评估**

```bash
# 3.1 audit_events 反查 24h 内调用
python scripts/query_audit.py \
  --time-range "24h" \
  --actor system \
  --event-type "feishu_main_message_sent,feishu_message_received" \
  --format jsonl

# 3.2 飞书凭证泄露则检查 feishu 控制台消息历史
# 飞书开放平台 → 应用管理 → 数据中心 → 消息发送统计

# 3.3 成本反查 backend/services/cost_guard 异常飙升
python scripts/query_audit.py \
  --time-range "7d" \
  --event-type "daily_cost_ceiling_20cny_breached" \
  --format table
```

#### 1.4.2 gitleaks pre-commit hook(继承 §1.1.2 配置)

#### 1.4.3 启动期 secrets_validator(继承 §1.1.3 实施)

#### 1.4.4 排除选项

- **仅 gitleaks pre-commit + 应急 playbook**(无启动期 fail-fast):.env 误塞 secret + 启动 → 静默泄露到日志 / process listing;**否决**
- **仅启动期 fail-fast + 应急 playbook**(无 gitleaks):git commit 阶段无防护 + 误提交后只能事后清理;**否决**
- **仅手工 playbook 无任何自动检测**:防御层薄弱 + 与项目 fail-closed for data corruption 原则不符;**否决**

### 1.5 Q5 — IP 白名单:全层严锁 127.0.0.1 only + 远程访问仅 SSH tunnel

#### 1.5.1 全层 IP 绑定矩阵

| 服务 | 当前状态 | P1-6 锁定状态 | 修改位置 |
|------|----------|--------------|----------|
| Backend (uvicorn) | 未显式绑 | `127.0.0.1` | `deploy/quantmind-backend.service` ExecStart `--host 127.0.0.1` + CLAUDE.md §5 操作速查 |
| Frontend (Vite dev server) | `0.0.0.0` ❌ | `127.0.0.1` | `frontend/vite.config.ts` `server.host = '127.0.0.1'`(F-001 必修)|
| MongoDB (docker-compose) | `127.0.0.1:27017` ✅ | `127.0.0.1` | 无修改 |
| Redis (docker-compose) | `127.0.0.1:6379` ✅ | `127.0.0.1` | 无修改 |
| Nginx (反代) | `listen 80/443` 未显式绑 IP | `127.0.0.1:80` / `127.0.0.1:443` | `deploy/nginx-quantmind.conf` `listen 127.0.0.1:80` + `listen 127.0.0.1:443` |
| Httpx 出站 | `local_address="0.0.0.0"` ✅ | `local_address="0.0.0.0"`(出站不冲突入站)| 无修改(继承 CLAUDE.md §2.10 红线 11)|

#### 1.5.2 远程访问 SSH tunnel 工作流

```bash
# 远程开发机访问本机 QuantMind 前端 + 后端 + MongoDB
# (注意:仅本人或授权人员通过 SSH 公钥认证后可建立 tunnel)

# 前端
ssh -L 9276:127.0.0.1:9276 user@quantmind-host
# 浏览器访问 http://127.0.0.1:9276

# 后端 API
ssh -L 8000:127.0.0.1:8000 user@quantmind-host
# 浏览器/curl 访问 http://127.0.0.1:8000

# MongoDB (运维查询用)
ssh -L 27017:127.0.0.1:27017 user@quantmind-host
# mongosh mongodb://127.0.0.1:27017
```

**SSH tunnel 选择理由**:SSH 本身是身份认证层(公钥 + 二次因素);加密传输内置;无需在应用层重复认证;符合"不加本机认证"决策(§1.6)。

#### 1.5.3 排除选项

- **Backend/DB 127.0.0.1;Frontend Vite LAN 段开放**:LAN 任意设备(手机/平板/邻居电脑)可访问未认证 UI = 不可接受风险 + 违反 P1-5 §2 红线 11 严格意旨;**否决**
- **全 0.0.0.0 + iptables 控制源 IP**:单机过度复杂 + iptables 漏配即全凭证泄露 + 与 P0-2 "HTTPS 回调入站端口永禁" 红线冲突;**否决**

### 1.6 Q6 — 本机访问认证:不加(127.0.0.1 边界 + SSH tunnel 已足够)

#### 1.6.1 不加认证的依据

- **127.0.0.1 only 含义** = 仅本机进程可达 = OS 用户隔离已是身份认证层(用户必须先登录 OS 才能访问)
- **远程访问** = SSH tunnel 强制(SSH 本身是身份认证层)
- **QuantMind 是个人单机项目** = 不适用多人共用 OS 账号场景
- **加认证 = 重复防护 + 引入凭证管理复杂度 + 与 P1-5 §2 红线 11 "前端不存凭证" 冲突**(token / cookie / Authorization header 任一插入都触发)

#### 1.6.2 实施约束

- **后端 FastAPI 不挂任何 auth middleware**:无 `Depends(get_current_user)` / API key 校验 / Bearer token 解析
- **前端 axios 不插任何 Authorization header / Bearer token / cookie**(继承 P1-5 §2 红线 11)
- **WebSocket `/ws/market` + SSE `/api/analysis/stream` 不要求任何 token 参数**
- **lint rule**:`grep -rn "Bearer\|Authorization\|JWT\|@app.middleware" backend/api/ frontend/src/ ` 在 P1-6 实施期后必空(除 lark-oapi SDK 内部出站调用 Authorization header 不算)

#### 1.6.3 P2 团队化升级路径(本决策不锁,仅备忘)

- 若 P2 引入团队多人:增加 OAuth/OIDC + nginx auth_request 层(在 nginx 而非应用层加,避免后端业务逻辑混入认证)
- 若 P3 引入移动端原生 App:增加 mTLS 客户端证书 + 双向 TLS

#### 1.6.4 排除选项

- **单 token 文件认证**:鸡蛋问题 - 未认证者如何拿到 token = 要么后端提供 GET 端点违反认证逻辑要么 cookie 违反 P1-5 §2 红线 11;**否决**
- **OAuth/OIDC 集成**:单人项目过度工程 + 增加 OIDC provider 依赖 + 单点故障;**否决**
- **mTLS 客户端证书**:证书轮换复杂 + 单人使用过重;**否决**

### 1.7 Q7 — 审计日志:Mongo audit_events 180 天 TTL + JSONL 30 天双写

#### 1.7.1 `backend/audit/models.py` schema 锁定

```python
# backend/audit/models.py
"""WHY: audit_events 不可变 schema;LLM 严禁写;凭证类仅 fingerprint 不写 plaintext。"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class AuditEventType(str, Enum):
    """锁定 22 类 audit event_type — 任何新增必走 P1-6-amendment。"""
    
    # 类 1 — 两唯二写入端点调用
    EXECUTION_REPORT_SUBMITTED = "execution_report_submitted"
    RECONCILIATION_TICKET_DECIDED = "reconciliation_ticket_decided"
    
    # 类 2 — 模式切换 + 冻结源 + 生命周期事件
    FEISHU_INTERACTIVE_TOGGLED = "feishu_interactive_toggled"
    MOCKBROKER_RESET = "mockbroker_reset"
    FREEZE_SOURCE_SWITCH_CHANGED = "freeze_source_switch_changed"
    FREEZE_SOURCE_TICKET_OPEN_CHANGED = "freeze_source_ticket_open_changed"
    FREEZE_SOURCE_CIRCUIT_BREAKER_CHANGED = "freeze_source_circuit_breaker_changed"
    FREEZE_SOURCE_DATA_QUALITY_CHANGED = "freeze_source_data_quality_changed"
    FREEZE_SOURCE_EOD_PIPELINE_CHANGED = "freeze_source_eod_pipeline_changed"
    ADVANCE_DAY_EXECUTED = "advance_day_executed"
    EOD_PIPELINE_SUCCEEDED = "eod_pipeline_succeeded"
    EOD_PIPELINE_FAILED = "eod_pipeline_failed"
    RECOVERY_SNAPSHOT_CREATED = "recovery_snapshot_created"
    
    # 类 3 — 凭证生命周期 + 飞书收发
    SECRETS_VALIDATOR_PASSED = "secrets_validator_passed"
    SECRETS_VALIDATOR_BLOCKED = "secrets_validator_blocked"
    KEY_FINGERPRINT_CHANGED = "key_fingerprint_changed"
    KEY_ROTATION_OVERDUE = "key_rotation_overdue"
    FEISHU_MAIN_MESSAGE_SENT = "feishu_main_message_sent"
    FEISHU_WEBHOOK_ALERT_SENT = "feishu_webhook_alert_sent"
    FEISHU_MESSAGE_RECEIVED = "feishu_message_received"
    
    # 类 4 — 异常 + 拦截事件
    STATE_MACHINE_ILLEGAL_TRANSITION = "state_machine_illegal_transition"
    RISK_ENGINE_CHECK_REJECTED = "risk_engine_check_rejected"
    BUILDER_EARLY_RETURN = "builder_early_return"
    MOCKBROKER_PRICE_LIMIT_VIOLATION_AT_FILL = "mockbroker_price_limit_violation_at_fill"
    DATA_QUALITY_BREACH = "data_quality_breach"
    RECONCILIATION_TICKET_OPEN_OR_EXPIRED = "reconciliation_ticket_open_or_expired"
    LLM_CALL_TIMEOUT_30S = "llm_call_timeout_30s"
    DAILY_COST_CEILING_20CNY_BREACHED = "daily_cost_ceiling_20cny_breached"


class AuditActor(str, Enum):
    """锁定 5 类 actor。"""
    FEISHU_USER = "feishu_user"
    FRONTEND_USER = "frontend_user"
    SYSTEM = "system"
    SCHEDULER = "scheduler"
    CLI = "cli"


class AuditOutcome(str, Enum):
    """锁定 4 类 outcome。"""
    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"
    DEGRADED = "degraded"


class AuditEvent(BaseModel):
    """frozen + strict + extra='forbid' 三层守门(继承 P0-3 §2 红线 12)。"""
    
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
    )
    
    event_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime  # UTC
    event_type: AuditEventType
    actor: AuditActor
    actor_detail: str | None = None
    resource_type: str  # 'instruction_plan' / 'execution_report' / 'mode_switch' 等
    resource_id: str | None = None
    payload: dict[str, Any]  # 凭证类仅写 fingerprint;严禁 plaintext
    outcome: AuditOutcome
    correlation_id: str | None = None
    reason_namespace: str | None = None  # 区分 'limit_up_block' vs 'price_limit_violation_at_fill'
```

#### 1.7.2 `backend/audit/store.py` 双写抽象

```python
# backend/audit/store.py
"""WHY: Mongo audit_events 主存储 + JSONL 文件兜底备份。
Mongo failure 时 fail-open(不阻主路径,符合 fail-open for infra glitches 原则)。"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection

from backend.audit.models import AuditEvent, AuditEventType, AuditActor, AuditOutcome

logger = logging.getLogger(__name__)


class AuditStore:
    """双写抽象 — Mongo 主 + JSONL 备份。"""
    
    def __init__(
        self,
        mongo_collection: AsyncIOMotorCollection,
        jsonl_path: Path = Path("logs/audit.jsonl"),
    ):
        self._mongo = mongo_collection
        self._jsonl_path = jsonl_path
        self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    
    async def write(
        self,
        *,
        event_type: AuditEventType | str,
        actor: AuditActor | str,
        resource_type: str,
        payload: dict[str, Any],
        outcome: AuditOutcome | str = AuditOutcome.SUCCESS,
        actor_detail: str | None = None,
        resource_id: str | None = None,
        correlation_id: str | None = None,
        reason_namespace: str | None = None,
    ) -> None:
        """同步写 Mongo + JSONL;Mongo 失败仅写 JSONL + 记 warning。"""
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc),
            event_type=AuditEventType(event_type) if isinstance(event_type, str) else event_type,
            actor=AuditActor(actor) if isinstance(actor, str) else actor,
            actor_detail=actor_detail,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=payload,
            outcome=AuditOutcome(outcome) if isinstance(outcome, str) else outcome,
            correlation_id=correlation_id,
            reason_namespace=reason_namespace,
        )
        
        # JSONL 写(本地文件,几乎不可能失败)
        with self._jsonl_path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
        
        # Mongo 写(infra glitch 时 fail-open)
        try:
            await self._mongo.insert_one(event.model_dump(mode="json"))
        except Exception as exc:
            logger.warning(
                "audit_persistence_failed",
                event_id=str(event.event_id),
                event_type=event.event_type.value,
                error=str(exc),
            )
            # 不 raise — fail-open for infra glitches
```

#### 1.7.3 Mongo `audit_events` collection 索引

```javascript
// MongoDB audit_events 集合索引
db.audit_events.createIndex({ "timestamp": -1 })
db.audit_events.createIndex({ "event_type": 1, "timestamp": -1 })
db.audit_events.createIndex({ "actor": 1, "timestamp": -1 })
db.audit_events.createIndex({ "correlation_id": 1 })
db.audit_events.createIndex({ "resource_type": 1, "resource_id": 1 })

// TTL 180 天自动过期
db.audit_events.createIndex({ "timestamp": 1 }, { expireAfterSeconds: 15552000 })
```

#### 1.7.4 排除选项

- **仅 Mongo 不双写 JSONL**:Mongo 故障时 audit 丢失 + Mongo 自身故障事件无法写 audit;**否决**
- **仅 JSONL 365 天不走 Mongo**:难查询 + 不能关联 broker_events / reconciliation_tickets 堆栈分析;**否决**
- **仅现有 logs/quantmind.jsonl 30 天不加任何新存储**:30 天 < 45 交易日 acceptance 窗口决策复盘丢历史 + 与 fail-closed for data corruption 原则不符;**否决**

### 1.8 Q8 — 审计事件类型清单:4 类事件强制写 audit + 调试性事件不入

#### 1.8.1 4 类事件清单(对应 §1.7.1 22 个 event_type)

**类 1 — 两唯二写入端点调用**(对应 P1-5 §2 红线 5 仅 2 写入端点)
- `execution_report_submitted`:POST `/api/execution-reports` 调用入口;actor=`feishu_user` 或 `frontend_user`
- `reconciliation_ticket_decided`:POST `/api/reconciliation-tickets/{id}/decide` 调用入口

**类 2 — 模式切换 + 冻结源 + 生命周期事件**(对应 P0-1 §1.3 模式切换 + P1-2.A §1.6.2 EOD chain + 5 冻结源)
- `feishu_interactive_toggled`:`FEISHU_INTERACTIVE_ENABLED` toggle
- `mockbroker_reset`:模式切换强制归档
- `freeze_source_*_changed`(5 冻结源各一):switch / ticket_open / circuit_breaker / data_quality / eod_pipeline
- `advance_day_executed`:next_trading_day 09:00 切换
- `eod_pipeline_succeeded` / `eod_pipeline_failed`:EOD chain 16:00 → 16:00:35
- `recovery_snapshot_created`:条件自动恢复或 MANUAL_REVIEW 创建

**类 3 — 凭证生命周期 + 飞书收发**
- `secrets_validator_passed` / `secrets_validator_blocked`:启动期 fail-fast 结果
- `key_fingerprint_changed`:轮换检测(对比 audit_events 历史 fingerprint)
- `key_rotation_overdue`:12 月超期 warning
- `feishu_main_message_sent` / `feishu_webhook_alert_sent` / `feishu_message_received`:发送+接收完整记录

**类 4 — 异常 + 拦截事件**
- `state_machine_illegal_transition`:InstructionPlan 状态机非法迁移
- `risk_engine_check_rejected`:14-check 任一拦截
- `builder_early_return`:5 道早返任一拦截
- `mockbroker_price_limit_violation_at_fill`:撮合 at-fill 防御性涨跌停拦截
- `data_quality_breach`:DataQualityState 4 阻断 breach 任一
- `reconciliation_ticket_open_or_expired`:OPEN/EXPIRED ticket 状态变迁(冻结买卖类路由触发)
- `llm_call_timeout_30s`:LLM 30s 硬超时
- `daily_cost_ceiling_20cny_breached`:¥20 hard ceiling 触发

#### 1.8.2 调试性事件不入 audit(走 logs/quantmind.jsonl)

- LLM raw response(每次 LLM 调用原始返回,数据量大)
- RiskEngine 14-check 全路径 trace(每次 check 详细参数)
- 每次行情快照写入(30s × 13 codes × 4h trading = 6240 records/day)
- 每次 cron 心跳(BrokerScheduler / DataScheduler / AnalysisScheduler 各 N 次/分钟)
- 每次 Redis cache hit/miss

**理由**:audit 信噪比 + 180 天 TTL 下存储爆炸 + 调试需求走 quantmind.jsonl 30 天足够。

#### 1.8.3 实施期挂载点

| 模块 | 挂载点 | 写入 event_type |
|------|--------|----------------|
| `backend/api/execution_reports.py` | POST handler | `execution_report_submitted` |
| `backend/api/reconciliation_tickets.py` | POST `/decide` handler | `reconciliation_ticket_decided` |
| `backend/feishu/scheduler.py` | toggle 接收 | `feishu_interactive_toggled` |
| `backend/broker/scheduler.py` | EOD chain 各步骤 | `eod_pipeline_*` + `mockbroker_reset` + `recovery_snapshot_created` |
| `backend/services/freeze_state.py` | 5 冻结源状态变迁 hook | `freeze_source_*_changed` |
| `backend/services/secrets_validator.py` | 启动期 + 轮换检测 | `secrets_validator_*` + `key_fingerprint_changed` + `key_rotation_overdue` |
| `backend/feishu/client.py` | 发送/接收 hook | `feishu_*_sent` / `feishu_message_received` |
| `backend/risk/engine.py` | 14-check 拦截 | `risk_engine_check_rejected` |
| `backend/llm/instruction_builder.py` | 5 道早返 | `builder_early_return` |
| `backend/broker/mock_broker.py` | at-fill 拦截 | `mockbroker_price_limit_violation_at_fill` |
| `backend/data/data_quality.py` | breach 触发 | `data_quality_breach` |
| `backend/services/cost_guard.py` | ¥20 触发 | `daily_cost_ceiling_20cny_breached` |
| `backend/llm/router.py` | 30s 超时 | `llm_call_timeout_30s` |

### 1.9 Q9 — Audit 查询入口:后端 CLI + GET API 不加前端页面

#### 1.9.1 CLI 脚本(`scripts/query_audit.py`)

```python
# scripts/query_audit.py
"""WHY: 个人单机运营查询 audit 入口;支持时间范围/类型/actor/correlation 过滤。
用法示例:
  python scripts/query_audit.py --time-range 24h --event-type secrets_validator_blocked
  python scripts/query_audit.py --correlation-id <uuid> --format jsonl
  python scripts/query_audit.py --actor feishu_user --time-range 7d --format table
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorClient


def parse_time_range(s: str) -> datetime:
    """24h / 7d / 30d → datetime cutoff."""
    if s.endswith("h"):
        return datetime.now(timezone.utc) - timedelta(hours=int(s[:-1]))
    if s.endswith("d"):
        return datetime.now(timezone.utc) - timedelta(days=int(s[:-1]))
    raise ValueError(f"invalid time range: {s}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--time-range", help="24h / 7d / 30d")
    parser.add_argument("--event-type", help="comma-separated event types")
    parser.add_argument("--actor", help="feishu_user/frontend_user/system/scheduler/cli")
    parser.add_argument("--correlation-id", help="filter by correlation_id")
    parser.add_argument("--format", choices=["table", "jsonl"], default="table")
    args = parser.parse_args()
    
    client = AsyncIOMotorClient("mongodb://127.0.0.1:27017")
    coll = client["quantmind"]["audit_events"]
    
    query = {}
    if args.time_range:
        query["timestamp"] = {"$gte": parse_time_range(args.time_range)}
    if args.event_type:
        query["event_type"] = {"$in": args.event_type.split(",")}
    if args.actor:
        query["actor"] = args.actor
    if args.correlation_id:
        query["correlation_id"] = args.correlation_id
    
    cursor = coll.find(query).sort("timestamp", -1).limit(1000)
    
    async for doc in cursor:
        if args.format == "jsonl":
            print(doc)
        else:
            print(f"{doc['timestamp']} | {doc['event_type']:<40} | {doc['actor']:<15} | {doc['outcome']}")


if __name__ == "__main__":
    asyncio.run(main())
```

#### 1.9.2 GET API(`backend/api/audit.py`)

```python
# backend/api/audit.py
"""WHY: GET only audit 查询;符合 P1-5 §2 红线 1+2 严禁 POST/PUT/PATCH/DELETE。"""

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.audit.store import audit_store

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/events")
async def list_audit_events(
    time_range: Annotated[str | None, Query(description="24h / 7d / 30d")] = None,
    event_type: Annotated[list[str] | None, Query()] = None,
    actor: Annotated[str | None, Query()] = None,
    correlation_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    skip: Annotated[int, Query(ge=0)] = 0,
):
    """列出 audit_events;支持时间范围/类型/actor/correlation 过滤;最多 1000 条/次。"""
    query = {}
    if time_range:
        cutoff = _parse_time_range(time_range)
        query["timestamp"] = {"$gte": cutoff}
    if event_type:
        query["event_type"] = {"$in": event_type}
    if actor:
        query["actor"] = actor
    if correlation_id:
        query["correlation_id"] = correlation_id
    
    cursor = audit_store.collection.find(query).sort("timestamp", -1).skip(skip).limit(limit)
    return {"events": [doc async for doc in cursor], "total_returned": limit}
```

#### 1.9.3 不加前端页面的依据

- **P1-5 §2 红线 1**:MVP 7 + Phase B 4 共 11 页清单永锁;新增需走 P1-5-amendment
- **个人项目 CLI 足够**:本人运营 = 本机 SSH + CLI 查询自然路径
- **GET API 预留未来接入**:若 P2 团队化或 Operator 增加,可在 P1-5 amendment 后新增前端页面消费 GET /api/audit/events
- **不冲突 §1.6 不加本机认证**:GET API 仅 127.0.0.1 可达 = OS 用户隔离 = 同 §1.6 论证

#### 1.9.4 排除选项

- **后端 CLI + GET API + 前端新增独立"审计查询"页**:超出 P1-5 锁状态 + 个人项目过度工程 + 领域需求未验证;**否决**
- **仅 logs/quantmind.jsonl + jq 查询**:Mongo audit_events 存在但无付点查询入口架构不一致(要么不走 Mongo 要么提供查询);**否决**

### 1.10 P1-6 第一阶段排除项汇总

- 多人 RBAC 权限分级(P2-3 范围;单人项目)
- 加密文件 secret 管理(sops/age/Vault P2-3 团队化时再评估)
- 远程跨网访问(P2-3;P1-6 仅本机 SSH tunnel)
- 审计事件实时告警通道(P1-7 预算扩 + 告警阈值合并处理)
- 审计 ML 异常检测(P3 范围)
- 多 Mongo replica geo-distribution(P2-3)
- 审计区块链不可篡改存证(过度工程)
- 飞书凭证 6 项立即配置(§1.3 延迟到 feishu_interactive 启用前)
- HTTPS 入站接收飞书事件(继承 P0-2 §2 红线 1)
- Webhook 直接发买卖指令(继承 P0-2 §2.5)
- token / cookie / Authorization 任何前端凭证存储(继承 P1-5 §2 红线 11)
- 审计前端独立页(§1.9.3 不超出 P1-5 11 页名额)

## 2. 红线(P1-6)

> 以下条款一律以 P1-6 决策为准。**违反即视为红线违规**;实施期 grep / lint rule 应自动检测违规。

1. **Secrets 仅走 shell env 单源永锁**:LLM 3 key + 飞书 6 凭证 + 未来增加凭证(MongoDB/Redis auth、备份加密 key 等)全部仅存 `~/.bashrc`;`.env` 永禁含 LLM_KEY / FEISHU_* prefix(由启动期 secrets_validator 强制校验);严禁入 `git`(由 `.gitignore` `.env` + `*.key` 覆盖 + gitleaks pre-commit hook 双层防护)。lint rule grep 必空:`grep -rnE "DEEPSEEK_API_KEY|DASHSCOPE_API_KEY|MOONSHOT_API_KEY|FEISHU_(APP_ID|APP_SECRET|VERIFY_TOKEN|ENCRYPT_KEY|CUSTOM_BOT_WEBHOOK_URL|CUSTOM_BOT_SIGN_SECRET)" .env .env.example`。

2. **凭证轮换 5 类强制触发条件永锁**:① 凭证泄露/疑似泄露 ② 团队成员变动 ③ provider 通知 key 异常活动 ④ 12 个月自然到期(warning 不强制 exit)⑤ 升级 P2 真实账户前。**不引入季度强制 / 月度强制 / 永不到期任意一种**。轮换流程仅可:编辑 `~/.bashrc` + `source` + `systemctl restart` + 启动期日志确认 fingerprint;严禁 hot-reload(继承 P0-7 §2 红线 14 + P0-10 §1.4)。

3. **凭证 fingerprint = SHA256(value)[:8] 永锁;严禁 plaintext 写任何持久化通道**:写位置仅 `audit_events.payload.fingerprints` + `logs/quantmind.jsonl` 启动日志;严禁完整 key value 写日志/审计/前端展示;前端展示一律末四位脱敏 + `webhook_configured` 布尔(继承 P1-5 §2 红线 14)。

4. **飞书 6 凭证 P1-6 仅锁约束实际配置延迟到 feishu_interactive 启用前**:本决策仅锁定:存储方式 + 轮换策略 + 应急流程 + fingerprint 模式 + 启动期 fail-fast 行为;**实际 ~/.bashrc 配置不强制现在做**;启动期 fail-fast 仅在 `FEISHU_INTERACTIVE_ENABLED=true` 时校验飞书 6 凭证(默认 false 不影响 simulation_auto 启动);启用前一周内配齐 + 集成测试。

5. **gitleaks pre-commit hook 强制永锁**:`.pre-commit-config.yaml` 引入 gitleaks v8.18+ + `.gitleaks.toml` custom rules 覆盖 `sk-*` / `FEISHU_*` / `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY` pattern;`pre-commit install` 安装本地 hook;hook 失败阻止 commit;严禁 `--no-verify` 跳过(继承 git-workflow.md 强约束)。

6. **启动期 secrets_validator fail-fast 永锁**:`backend/services/secrets_validator.py` 在 `main.py:lifespan` 启动期同步调用;失败即 `exit(1)` + 写 audit_events `secrets_validator_blocked` + logs/quantmind.jsonl ERROR;校验内容:(a) `.env` 不得含 LLM_KEY/FEISHU_* prefix (b) process env LLM 3 key 必须存在且匹配 provider 格式 (c) `FEISHU_INTERACTIVE_ENABLED=true` 时校验飞书 6 凭证。

7. **凭证泄露三步应急 playbook 强制**:`docs/runbook/secrets-incident-response.md` 必须存在;Step 1 立即轮换 + Step 2 git history 排查 + Step 3 影响评估;`git filter-repo` 重写历史前必须本人确认;**若已 push 到公网仓库视为永久泄露不可挽回必须 revoke 旧 key**。

8. **IP 全层严锁 127.0.0.1 only 永锁**:Backend (`uvicorn --host 127.0.0.1`) + Frontend Vite (`server.host = '127.0.0.1'`) + MongoDB (docker-compose 127.0.0.1) + Redis (docker-compose 127.0.0.1) + Nginx (`listen 127.0.0.1:80/443`);**严禁任何服务绑 0.0.0.0 / LAN 段 IP / 公网 IP**;httpx 出站 `local_address="0.0.0.0"` 不冲突入站 127.0.0.1 only(出站和入站不同方向)(继承 CLAUDE.md §2.10 红线 11)。lint rule grep 必空:`grep -rnE "(host|listen)\s*[=:]\s*['\"]?0\.0\.0\.0" backend/ frontend/ deploy/ docker-compose*.yml`(httpx local_address 例外)。

9. **远程访问仅走 SSH tunnel 永锁**:严禁公网入站任何形式(HTTPS / TCP / 端口转发);SSH 公钥认证 + 二次因素;`ssh -L <port>:127.0.0.1:<port>` 标准用法;严禁外部反代直接转发(违反 P0-2 §2 红线 1)。

10. **本机访问不加任何 auth middleware 永锁**:Backend FastAPI 不挂 `Depends(get_current_user)` / API key 校验 / Bearer token 解析 / JWT 中间件;Frontend axios 不插任何 `Authorization` / `Bearer` / cookie / `localStorage` / `sessionStorage` 凭证(继承 P1-5 §2 红线 11);WebSocket `/ws/market` + SSE `/api/analysis/stream` 不要求任何 token 参数。lint rule grep 必空:`grep -rnE "Bearer|Authorization|JWT|@app\.middleware\(.*auth" backend/api/ frontend/src/`(lark-oapi SDK 内部出站例外)。

11. **MongoDB `audit_events` collection append-only insert-only 永锁**:严禁任何 update/delete/replace 操作(继承 P1-2.A §1.4 broker_events 8 项红线);schema 由 `backend/audit/models.py` `AuditEvent` frozen Pydantic v2 strict + extra="forbid" 锁定;严禁就地 mutation(继承 P0-3 §2 红线 12)。

12. **`AuditEventType` enum 锁定 22 类 event_type**:任何新增/删除/重命名必走 `P1-6-amendment-{date}-{原因}.md`;严禁 magic string `event_type` 写入 audit(必须用 enum)。

13. **audit_events TTL 180 天永锁**:索引 `{ "timestamp": 1 }` `expireAfterSeconds: 15552000`;基于 P0-6 45 交易日 acceptance 滚动窗口 × 4 倍安全余量;严禁缩短到 < 90 天(决策复盘丢历史)。

14. **JSONL `logs/audit.jsonl` 30 天保留并行双写永锁**:作为 infra glitch 备份;独立于 `logs/quantmind.jsonl`;Mongo failure 时所有 audit 事件由 JSONL 兜底;Mongo + JSONL 双写顺序:JSONL 先写(本地文件几乎不可能失败)+ Mongo 异步写(失败仅 warning 不 raise)。

15. **Mongo audit failure fail-open 永锁**:Mongo `insert_one` 失败时仅 logs/quantmind.jsonl 写 `audit_persistence_failed` warning;**不 raise 不阻止主路径**;符合 fail-open for infra glitches 原则(与 fail-closed for data corruption 区分);data corruption(NaN/Inf/负值)由数据层 + 守门层双层校验;Mongo connection error 是 infra glitch 兜底通过。

16. **LLM 严禁写 audit_events 永锁**(继承 P0-10 §2 红线 1 LLM 字段权限矩阵):`backend/audit/store.py::AuditStore.write` 严禁被 `backend/llm/` / `backend/agents/` 任何模块导入或调用;lint rule grep 必空:`grep -rn "from backend.audit\|audit_store\.write" backend/llm/ backend/agents/`。

17. **4 类事件强制写 audit + 调试性事件不入永锁**:类 1(2 写入端点)+ 类 2(模式/冻结/生命周期)+ 类 3(凭证/飞书收发)+ 类 4(异常/拦截)各 event_type 强制挂载 `AuditStore.write` 调用(对照 §1.8.3 实施期挂载点表);调试性事件(LLM raw response / RiskEngine trace / 行情快照 / cron 心跳 / Redis cache)严禁入 audit_events;走 `logs/quantmind.jsonl` 30 天。

18. **凭证类 audit 仅写 fingerprint 严禁 plaintext 永锁**:`event_type` 为 `secrets_validator_*` / `key_fingerprint_changed` / `key_rotation_overdue` 时,`payload` 仅含 fingerprint 字典(`{key_name: SHA256[:8]}`);严禁 plaintext key value;严禁末四位(防关联推断);飞书消息 raw_text 完整记录(无 prompt injection 风险因继承 P0-3 §2.5 LLM 严禁拼接飞书消息文本)。

19. **audit 查询入口仅 CLI + GET API 永锁**:`scripts/query_audit.py` CLI + `backend/api/audit.py` GET `/api/audit/events`;**严禁 POST/PUT/PATCH/DELETE 在 backend/api/audit.py**(继承 P1-5 §2 红线 1+2 backend/api/* 仅 GET 除两唯二例外);**前端不占 P1-5 MVP 7 + Phase B 4 共 11 页名额**;后期需加查询界面必须先走 P1-5-amendment + P1-6-amendment 双批准。

20. **Vite host 配置永锁 `'127.0.0.1'`**:`frontend/vite.config.ts` `server.host = '127.0.0.1'`;**修复 P1-5 §2 红线 11 历史违规**(当前 `'0.0.0.0'`);实施期 F-001 必修 + lint rule grep 必空:`grep -rnE "host\s*:\s*['\"]0\.0\.0\.0" frontend/vite.config.*`。

## 3. 实施期任务清单

> P1-6 决策完成不等于实施落地。本节列出从决策锁定到代码合并的具体动作清单。**任何遗漏会让本决策只是文字游戏**。Phase A 与 P1-5 实施期 Phase A(写入接口收口)合并执行;Phase B 与 P1-5 Phase B(MVP 7 页落地)合并执行,以充分复用部署窗口。

### Phase A — 立即修复 + 防御层落地(与 P1-5 Phase A 合并执行)

- **F-001** **【红线 20 历史违规修复】** `frontend/vite.config.ts` `server.host` 由 `'0.0.0.0'` 改为 `'127.0.0.1'`;lint rule grep 必空验证(§1.5.1 + §2 红线 20)
- **F-002** Backend `deploy/quantmind-backend.service` ExecStart 显式 `--host 127.0.0.1`(§1.5.1 + §2 红线 8);CLAUDE.md §5 操作速查命令同步更新
- **F-003** `deploy/nginx-quantmind.conf` `listen 80` / `listen 443` 显式补充 `127.0.0.1:80` / `127.0.0.1:443`(§1.5.1 + §2 红线 8)
- **F-004** 新建 `.pre-commit-config.yaml` 引入 gitleaks v8.18+ + `.gitleaks.toml` custom rules(§1.1.2 + §2 红线 5)
- **F-005** 新建 `backend/services/secrets_validator.py`(§1.1.3 完整代码 + §2 红线 6)
- **F-006** `backend/main.py` `lifespan` 启动期调用 `validate_secrets()` + 失败 `exit(1)` + 写 audit_events(§1.1.3)
- **F-007** 新建 `docs/runbook/secrets-incident-response.md` 三步应急 playbook(§1.4.1 完整内容 + §2 红线 7)

### Phase B — 审计基础设施落地(与 P1-5 Phase B 合并执行)

- **F-008** 新建 `backend/audit/__init__.py` + `backend/audit/models.py`:`AuditEvent` frozen Pydantic + `AuditEventType` enum 22 类 + `AuditActor` enum 5 类 + `AuditOutcome` enum 4 类(§1.7.1 + §2 红线 11+12)
- **F-009** 新建 `backend/audit/store.py`:`AuditStore` 双写抽象(Mongo 主 + JSONL 备份)(§1.7.2 + §2 红线 14+15)
- **F-010** MongoDB 创建 `audit_events` collection + 5 索引 + TTL 180 天(§1.7.3 + §2 红线 13);通过 `scripts/init_audit_collection.py` 一次性初始化
- **F-011** `backend/main.py` lifespan 初始化 `AuditStore` 单例;DI 注入到所有挂载点(§1.8.3)
- **F-012** **【类 1 — 两写入端点】** `backend/api/execution_reports.py` POST handler 集成 `AuditStore.write(event_type=EXECUTION_REPORT_SUBMITTED, ...)`;`backend/api/reconciliation_tickets.py` POST `/decide` handler 集成(§1.8.3 + §2 红线 17)
- **F-013** **【类 2 — 模式切换 + 生命周期】** `backend/services/mode_switcher.py`(若存在;否则在调用方)集成 `feishu_interactive_toggled` + `mockbroker_reset`;`backend/broker/scheduler.py` EOD chain 集成 `eod_pipeline_*` + `recovery_snapshot_created`;`backend/broker/applicators/reconciliation.py` 集成 `mockbroker_reset`;`backend/broker/scheduler.py` `advance_day` 集成 `advance_day_executed`(§1.8.3)
- **F-014** **【类 2 — 5 冻结源】** 新建 `backend/services/freeze_state.py` 集中管理 5 冻结源状态变迁 + hook 写 `freeze_source_*_changed`(switch / ticket_open / circuit_breaker / data_quality / eod_pipeline 各一)(§1.8.3 + 继承 P1-5 §2 红线 9)
- **F-015** **【类 3 — 凭证生命周期】** `backend/services/secrets_validator.py` 集成 `secrets_validator_passed` / `secrets_validator_blocked` + `key_fingerprint_changed`(对比上次 audit_events fingerprint)+ `key_rotation_overdue`(12 月超期检测)(§1.8.3 + §2 红线 18)
- **F-016** **【类 3 — 飞书收发】** `backend/feishu/client.py` 发送 hook 集成 `feishu_main_message_sent` + `feishu_webhook_alert_sent`;`backend/feishu/scheduler.py` lark-oapi 长连接接收 hook 集成 `feishu_message_received`(§1.8.3)
- **F-017** **【类 4 — 异常 + 拦截】** `backend/risk/engine.py` 14-check 拦截 hook 集成 `risk_engine_check_rejected`(reason 携带 check_id);`backend/llm/instruction_builder.py` 5 道早返集成 `builder_early_return`;`backend/broker/mock_broker.py` at-fill 拦截集成 `mockbroker_price_limit_violation_at_fill`;`backend/data/data_quality.py` breach 触发集成 `data_quality_breach`;`backend/services/cost_guard.py` ¥20 触发集成 `daily_cost_ceiling_20cny_breached`;`backend/llm/router.py` 30s 超时集成 `llm_call_timeout_30s`;状态机非法迁移集成 `state_machine_illegal_transition`(§1.8.3 + §2 红线 17)
- **F-018** **【类 4 — ticket OPEN/EXPIRED】** `backend/services/reconciliation_engine.py` ticket 状态变迁集成 `reconciliation_ticket_open_or_expired`(§1.8.3)
- **F-019** 新建 `scripts/query_audit.py` CLI 工具(§1.9.1 完整代码 + §2 红线 19)
- **F-020** 新建 `backend/api/audit.py` GET `/api/audit/events` 端点(§1.9.2 完整代码 + §2 红线 19);路由挂载到 `backend/main.py`

### Lint rule + 静态检查(实施期持续生效)

- **F-021** Lint rule grep `grep -rnE "DEEPSEEK_API_KEY|DASHSCOPE_API_KEY|MOONSHOT_API_KEY|FEISHU_(APP_ID|APP_SECRET|VERIFY_TOKEN|ENCRYPT_KEY|CUSTOM_BOT_WEBHOOK_URL|CUSTOM_BOT_SIGN_SECRET)" .env .env.example` 必空(§2 红线 1)
- **F-022** Lint rule grep `grep -rnE "(host|listen)\s*[=:]\s*['\"]?0\.0\.0\.0" backend/ frontend/ deploy/ docker-compose*.yml | grep -v "local_address"` 必空(§2 红线 8)
- **F-023** Lint rule grep `grep -rnE "Bearer|Authorization|JWT|@app\.middleware\(.*auth" backend/api/ frontend/src/ | grep -v "lark-oapi\|lark_oapi"` 必空(§2 红线 10)
- **F-024** Lint rule grep `grep -rnE "host\s*:\s*['\"]0\.0\.0\.0" frontend/vite.config.*` 必空(§2 红线 20)
- **F-025** Lint rule grep `grep -rn "from backend.audit\|audit_store\.write" backend/llm/ backend/agents/` 必空(§2 红线 16)
- **F-026** Lint rule grep `grep -rn "audit_events.*\.update\|audit_events.*\.delete\|audit_events.*\.replace" backend/audit/` 必空(§2 红线 11)
- **F-027** CLAUDE.md §5 操作速查节增补本决策 4 条 grep 静态检查命令(P1-6 红线检查)

### 测试覆盖要求(继承全局 §2.10)

- **F-028** 单元测试:`AuditEvent` schema 完整性(strict + extra='forbid' + frozen 不可变)+ `AuditEventType` enum 全 22 类覆盖 + `AuditStore.write` Mongo 失败 fail-open + JSONL 兜底写入断言
- **F-029** 单元测试:`secrets_validator.validate_secrets()` 全场景:.env 含 forbidden key → blocked / process env LLM key 缺失 → blocked / process env LLM key 格式错 → blocked / 全部通过 → passed + fingerprints
- **F-030** 集成测试:`audit_events` collection 创建 + 5 索引 + TTL 180 天断言(`db.audit_events.getIndexes()` + `expireAfterSeconds == 15552000`)
- **F-031** 集成测试:类 1+2+3+4 各事件类型端到端写 audit_events 断言;包括 actor / event_type / outcome / payload / correlation_id 完整性
- **F-032** 集成测试:Mongo connection error 模拟 → JSONL 兜底写入 + logs/quantmind.jsonl WARNING 断言;主路径不阻
- **F-033** E2E 测试:① `secrets_validator_blocked` 启动期 exit(1) ② `execution_report_submitted` 用户回报录入端到端 ③ `feishu_interactive_toggled` 模式切换写 audit ④ `risk_engine_check_rejected` 14-check 拦截写 audit reason 携带 check_id ⑤ `mockbroker_price_limit_violation_at_fill` at-fill 拦截写 audit reason_namespace ⑥ `query_audit.py --time-range 24h --event-type EXECUTION_REPORT_SUBMITTED` CLI 查询 ⑦ `GET /api/audit/events?event_type=...&time_range=...` API 查询
- **F-034** 安全测试:`gitleaks protect` 模拟 staged diff 含 `sk-test123...` → hook 失败阻止 commit;`gitleaks protect` staged diff 不含 secret → 通过

### Codex review hard gate(major 5 轮 R1-R5)

P1-6 涉及 secrets 管理 + IP 边界 + 审计基础设施 + 红线收口,major 级别,实施期 5 轮 codex review:

- **R1 — Architecture review**(secrets shell env 单源 + audit Mongo+JSONL 双写架构 + IP 全层 127.0.0.1 边界 + SSH tunnel 远程访问 + 不加本机认证决策合理性)
- **R2 — Security review**(gitleaks rules 完备性 + secrets_validator fail-fast 边界 + audit_events fingerprint 不可逆性 + LLM 严禁写 audit 隔离完备 + Mongo audit failure fail-open vs fail-closed for data corruption 区分)
- **R3 — Implementation review**(F-001~F-034 任务清单与代码 diff 一致性;特别核 audit hook 22 类 event_type 全覆盖;红线 grep lint rule 必空验证)
- **R4 — SDK & dependency review**(motor MongoDB driver async 调用模式 + pre-commit framework + gitleaks v8.18+ 兼容性 + Pydantic v2 frozen+strict+extra=forbid 三层守门)
- **R5 — Final review**(red lines 20 条全覆盖 + 决策依据完整性 + 与 P0-1~P0-10 + P1-2.A/B/C + P1-5 累积红线兼容性)

输出存 `docs/reviews/p1-6-r{N}-{topic}.md`;触发前 `git pull` 同步 `LanEinstein/CCodexSkill`(继承 §2.10)。

## 4. 决策依据

### 4.1 用户对齐(2026-05-10 P1-6 决策对齐 3 轮 9 议题)

第一轮 4 议题(全部对齐推荐):
- Q1 Secrets 存储升级路径 → 继续 shell env 单源 + git 钩子防护 + 启动期 fail-fast 三件套 ✅
- Q2 凭证轮换周期与触发 → 事件驱动 + 12 月最长保质期 5 类强制触发 ✅
- Q3 飞书 6 凭证落地时机 → P1-6 仅锁约束实际配置延迟到 feishu_interactive 启用前 ✅
- Q4 凭证泄露应急 + gitleaks → 三步应急 + gitleaks pre-commit + 启动期 fail-fast 三件套 ✅

第二轮 4 议题(全部对齐推荐):
- Q5 IP 白名单边界 → 全层严锁 127.0.0.1 only 无例外 ✅
- Q6 本机访问认证 → 不加认证(127.0.0.1 边界 + SSH tunnel 已足够)✅
- Q7 审计存储与保留 → Mongo audit_events 180 天 TTL + JSONL 30 天双写 ✅
- Q8 审计事件清单 → 4 类事件全选(两写入端点 + 模式/冻结/生命周期 + 凭证+飞书 + 异常+拦截)✅

第三轮 1 议题(对齐推荐):
- Q9 Audit 查询入口 → 后端 CLI + GET API,不加前端页面 ✅

### 4.2 关键判断

- **shell env 单源符合 QuantMind 单实例轻量定位**:不引入 sops/age/Vault/keyring 等额外依赖;轮换流程清晰(编辑+source+重启);LLM key 已在 ~/.bashrc 当前状态符合
- **12 月事件驱动平衡安全与运维**:不主动定期轮换避免 provider rate limit + 运维负担;5 类强制触发覆盖核心风险;12 月 warning 不强制 exit 避免突发宕机
- **飞书凭证延迟落地符合 P0-1 模式切换 = 账户生命周期事件节奏**:实际启用日期由 acceptance 决定可能 P2 末;现在配可能过期再轮换浪费;启用前一周配齐 + 集成测试是合理窗口
- **三步应急 + 自动检测 双件套防御层完备**:gitleaks 防 commit 阶段误提交;启动期 fail-fast 防 .env 误塞 secret 启动;手工 playbook 覆盖事后清理与影响评估;符合 fail-closed for data corruption 原则
- **127.0.0.1 全层严锁 + SSH tunnel 远程访问符合零公网暴露**:与 P0-2 §2.5 + CLAUDE.md §2.10 完全一致;SSH 是身份认证层不需应用层重复;Vite 0.0.0.0 历史违规必修
- **不加本机认证符合 127.0.0.1 边界 + P1-5 §2 红线 11 节奏**:OS 用户隔离已是身份认证;前端不存凭证已锁;加 token / cookie 鸡蛋问题或破红线;OAuth/mTLS 单人项目过度
- **Mongo + JSONL 双写符合 fail-open for infra glitches**:Mongo 故障期间用户回报 / 模式切换 / 飞书发送等关键事件由 JSONL 兜底;data corruption 仍 fail-closed;两原则区分清晰
- **180 天 TTL 覆盖 P0-6 45 交易日 acceptance 窗口 × 4 倍安全余量**:可复盘 acceptance 期间全部决策;180 天 = 6 个月足够任何复盘场景
- **4 类事件全写 + 调试性事件不入符合信噪比**:写入端点 / 模式生命周期 / 凭证飞书 / 异常拦截覆盖核心审计需求;LLM raw / RiskEngine trace / 行情快照走 quantmind.jsonl 30 天足够
- **CLI + GET API 不加前端页面符合 P1-5 11 页名额永锁**:个人项目 CLI 是自然路径;GET API 预留未来接入;不破坏 P1-5 锁状态;不超出范围

### 4.3 排除选项

- **sops + age 加密文件入 git**:单实例项目过度工程 + age 私钥泄露 = 全凭证泄露(把鸡蛋放一篮)+ 增加学习成本
- **1Password CLI / HashiCorp Vault**:单点故障 + 不符合 QuantMind 单机轻量定位
- **OS keyring**:需 GUI 会话 + headless server 受限
- **季度 / 月度强制轮换**:对单实例项目过度运维 + provider rate limit 风险
- **永不到期任意一种**:长期不变累积风险
- **P1-6 lock 时同步配置全 6 飞书凭证**:启用日期未定可能配过期再轮换浪费 + 与 P0-1 节奏不一致
- **差异化 4+2 飞书凭证配置**:增加复杂度记忆成本高
- **仅 gitleaks pre-commit + 应急 playbook**(无启动期 fail-fast):.env 误塞 secret + 启动 → 静默泄露
- **仅启动期 fail-fast + 应急 playbook**(无 gitleaks):git commit 阶段无防护
- **仅手工 playbook 无任何自动检测**:防御层薄弱违反 fail-closed 原则
- **Backend/DB 127.0.0.1;Frontend Vite LAN 段开放**:LAN 任意设备可访问未认证 UI = 不可接受风险
- **全 0.0.0.0 + iptables 控制源 IP**:单机过度复杂 + iptables 漏配即全凭证泄露 + 与 P0-2 红线冲突
- **单 token 文件认证**:鸡蛋问题
- **OAuth/OIDC 集成**:单人项目过度工程
- **mTLS 客户端证书**:证书轮换复杂
- **仅 Mongo 不双写 JSONL**:Mongo 故障即 audit 丢失
- **仅 JSONL 365 天不走 Mongo**:难查询 + 不能关联堆栈分析
- **仅现有 logs/quantmind.jsonl 30 天**:< 45 交易日 acceptance 窗口决策复盘丢历史
- **后端 CLI + GET API + 前端独立"审计查询"页**:超出 P1-5 锁状态 + 个人项目过度
- **仅 logs/quantmind.jsonl + jq 查询**:Mongo audit_events 存在但无入口架构不一致

### 4.4 与 P0/P1-2.A/B/C/P1-5 红线协同

- 继承 P0-1 §1.3 模式切换 = 账户生命周期事件 → 类 2 audit `feishu_interactive_toggled` + `mockbroker_reset`(§1.8.1)
- 继承 P0-2 §1.2 永禁 HTTPS 入站 + §2.5 飞书 6 凭证仅 shell env → §1.5 远程访问仅 SSH tunnel + §1.1 飞书 6 凭证 ~/.bashrc 单源
- 继承 P0-3 §2 红线 12 frozen Pydantic strict + extra="forbid" → §1.7.1 `AuditEvent` frozen + strict + extra='forbid'
- 继承 P0-4 §3.1 ExecutionReportApplier 单一入口 → 类 1 audit `execution_report_submitted`(§1.8.1)
- 继承 P0-5 §1.5 reconciliation_ticket 三选一 → 类 1 audit `reconciliation_ticket_decided`(§1.8.1)
- 继承 P0-6 §1.1 45 交易日滚动窗口 → §1.7.3 audit_events TTL 180 天 = 4 倍安全余量
- 继承 P0-7 §2 红线 14 RiskConfig runtime 不可改 + agent_models.yaml hot-reload 禁用 → §1.2 凭证轮换流程 = 编辑 + source + 重启严禁 hot-reload
- 继承 P0-8 §2 红线 7 DataQualityState 早返冻结 → 类 4 audit `data_quality_breach`(§1.8.1)
- 继承 P0-10 §2 红线 1 LLM 字段权限矩阵 → §2 红线 16 LLM 严禁写 audit_events;§1.4 ¥20 hard ceiling 触发 → 类 4 audit `daily_cost_ceiling_20cny_breached`
- 继承 P1-2.A §1.4 broker_events append-only insert-only 8 项红线 → §2 红线 11 audit_events 同款约束;§1.6.2 EOD chain freeze → 类 2 audit `eod_pipeline_*` + `freeze_source_eod_pipeline_changed`
- 继承 P1-2.B §1.7 DataQualityProvider per-stock evaluate → 类 4 audit `data_quality_breach` reason 携带 stock_code
- 继承 P1-2.C §2 红线 11 三层 reason 命名空间区分 → §1.7.1 audit `reason_namespace` 字段携带命名空间
- 继承 P1-5 §2 红线 11 P1-6 处置 + 前端不允许存储任何凭证 + Vite host 127.0.0.1 → §1.6 不加本机认证 + §2 红线 10 前端 axios 不插 Authorization + §2 红线 20 Vite host 永锁;§2 红线 14 末四位脱敏 → §2 红线 3 凭证 fingerprint SHA256[:8] 比末四位更严
- 继承 P1-5 §2 红线 1 MVP 7 + Phase B 4 共 11 页永锁 → §1.9.3 audit 查询不加前端页面;§2 红线 5 仅 2 写入端点 → §2 红线 19 audit GET only 严禁 POST/PUT/PATCH/DELETE

## 5. 后续动作

### 5.1 SSoT 文档同步

- 更新 `docs/quantmind_owner_decision_points_2026-05-07.md` §P1-6:标 ✅ + 链接本决策文档
- 新建 memory 文件 `/home/ps/.claude/projects/-home-ps-papers-QuantMind/memory/project_p1_6_secrets_ip_audit.md`
- 更新 `MEMORY.md` 索引:加 P1-6 锁定 entry
- 更新 `CLAUDE.md` §2 加 §2.12 P1-6 红线节(简化版,详细规约在本决策 §2);§5 操作速查增补 P1-6 grep 4 条

### 5.2 派生 amendment(若有)

无破坏式;新增 backend/audit/* 模块 + scripts/query_audit.py + 新增 GET /api/audit/events 端点 + 修复 Vite host '0.0.0.0' → '127.0.0.1' 历史违规;P1-5 §2 红线 11 在本决策完成"P1-6 处置"承诺即"不加本机认证"。

### 5.3 下一站

- **P1-7**:预算扩(从单 ¥20/日 hard 到分类 LLM/数据/运维 + 月预算 + 告警阈值;本决策铺垫:audit_events 类 4 `daily_cost_ceiling_20cny_breached` + Phase B 收尾成本拆解面板)
- **P1-1 / P1-3 / P1-4 / P1-8**:已由 P0-3 + P0-4 + P0-5 + P0-6 累积锁定无独立决策;本决策与之协同(audit_events 类 1 用户回报 + 类 4 异常拦截 reason 命名空间)

### 5.4 实施期启动条件

- P1 全锁(P1-5 + P1-6 + P1-7 完成)→ 启动实施期 Phase A(代码迁移)+ Phase B(数据 schema 落地)
- Phase A 与 P1-5 Phase A(写入接口收口)+ P0-1 旧 AUTHORIZATION_MODE 矩阵删除合并执行(F-001~F-007 与 P1-5 E-001~E-013 同窗口)
- Phase B 在 P1-2.A/B/C 数据 schema 全量落地 + Phase A 代码清理后启动(F-008~F-020 与 P1-5 E-014~E-028 同窗口)

### 5.5 本决策不做的事

- 不锁定多人 RBAC 权限分级(P2-3)
- 不锁定加密文件 secret 管理(sops/age/Vault P2-3)
- 不锁定远程跨网访问(P2-3)
- 不锁定审计事件实时告警通道(P1-7 合并)
- 不锁定审计 ML 异常检测(P3)
- 不锁定多 Mongo replica geo-distribution(P2-3)
- 不锁定审计区块链不可篡改存证(过度工程)
- 不锁定飞书凭证 6 项立即配置(§1.3 延迟)
- 不锁定预算扩展(P1-7)

---

**P1-6 决策对齐完成 ✅**

P1 决策对齐路径 A 第五份决策锁定;P1-6 = secrets shell env 单源 + 12 月事件驱动 5 类强制轮换 + 飞书 6 凭证延迟落地 + 三步应急 + gitleaks + 启动期 fail-fast + 全层 127.0.0.1 only + SSH tunnel 远程访问 + 不加本机认证 + Mongo audit_events 180 天 TTL + JSONL 30 天双写 + 4 类事件全写 + CLI + GET API 查询不加前端页面 + 20 红线 + 34 实施期任务(F-001~F-034 Phase A 7 + Phase B 13 + Lint 7 + 测试 7)。

下一站:P1-7(预算扩 — 从单 ¥20/日 hard 到分类 LLM/数据/运维 + 月预算 + 告警阈值)。
