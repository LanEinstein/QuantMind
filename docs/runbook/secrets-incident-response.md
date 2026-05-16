# 凭证应急 / 轮换 Runbook (P1-6 §1.4 / H-001)

> 唯一权威:本文档承载 5 类强制轮换 + 12 月 warning + 三步应急 playbook。
> 凡涉及凭证(LLM 3 + 飞书 5,见 [`secrets_validator.py`][validator])
> 都按本文操作。决策红线 见 [`docs/decisions/project_p1_6_secrets_ip_audit.md`][p1-6]
> 与 [`docs/decisions/P0-2-amendment-2026-05-16-custom-bot-disabled.md`][amendment]。

[validator]: ../../backend/services/secrets_validator.py
[p1-6]: ../decisions/project_p1_6_secrets_ip_audit.md
[amendment]: ../decisions/P0-2-amendment-2026-05-16-custom-bot-disabled.md

## 0. 凭证池清单

`P1-6 §1.1 + P0-2-amendment-2026-05-16` 锁定 8 个凭证(注:**FEISHU_CUSTOM_BOT_***
不在池内,owner 飞书租户全局禁用 custom-bot,残留 env 仅 audit warning 不阻启动)。

| 类别 | 名称 | 形状 | 用途 |
|------|------|------|------|
| LLM | `DEEPSEEK_API_KEY` | `sk-` + ≥16 字符 | DeepSeek 主信号 |
| LLM | `DASHSCOPE_API_KEY` | `sk-` + ≥16 字符 | Qwen(Alibaba DashScope) |
| LLM | `MOONSHOT_API_KEY` | `sk-` + ≥16 字符 | Kimi |
| 飞书 | `FEISHU_APP_ID` | `cli_` + 16 alnum(=20 字符) | 自建应用 ID |
| 飞书 | `FEISHU_APP_SECRET` | 32 alnum | 自建应用密钥 |
| 飞书 | `FEISHU_VERIFY_TOKEN` | 32 alnum | 事件订阅校验 |
| 飞书 | `FEISHU_ENCRYPT_KEY` | 32 alnum | 事件订阅加密 |
| 飞书 | `FEISHU_ALERT_CHAT_ID` | `oc_` + 32 alnum(=35 字符) | 告警群 `open_chat_id` |

所有凭证 **必须** 存放在 `~/.bashrc` —— `.env` 仅放非密配置(MONGODB_URI / REDIS_URL / BROKER_MODE 等)。
启动期 `secrets_validator` 二层守门:`.env` 静态扫描 + process env 形状校验。

## 1. 五类强制轮换触发条件 (P1-6 §1.4)

| 触发 | 谁判定 | 响应窗口 | 动作 |
|------|--------|----------|------|
| 凭证泄露(git push / 公网 / 日志) | owner / 自动 gitleaks 报警 | **立即** | §3 完整应急 |
| 团队成员变动(任职 / 离职) | owner | 24h 内 | §2 重新生成全部 8 个 |
| Provider 安全告警(DeepSeek / Alibaba / Moonshot / 飞书发邮件) | provider | 工作日内 | §2 重新生成受影响的子集 |
| 12 月到期 warning(自然月计算) | 自动启动期 warning | 30 日内 | §2 重新生成全部 8 个 |
| P2 升级前(Phase X 自进化启用前 / Phase B 部署前 dryrun) | owner | 启用前一周 | §2 重新生成全部 8 个 |

> **12 月 warning 不强制 exit**:仅启动期 structlog warning + Mongo audit
> `credential_rotated` event_type 占位(`outcome=DEGRADED`,
> `reason_namespace="rotation_due_within_30_days"`)。强制 fail-fast 仅在 §3 路径。

## 2. 凭证轮换流程 (Routine Rotation)

> 适用条件:非泄露场景(到期 / 团队变动 / provider 告警 / P2 升级前)。

```bash
# 1. 在 provider 控制台生成新凭证(LLM 走各 vendor 后台;飞书走开放平台 →
#    应用 → 凭证与基础信息)。

# 2. 临时打开两套 env(双活窗口 ≤ 24h):
export DEEPSEEK_API_KEY_NEXT=sk-<new-value>

# 3. 在测试环境验证:
FEISHU_INTERACTIVE_ENABLED=false \
  DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY_NEXT \
  /home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app --port 8001
# secrets_validator 必须显示 "secrets_validator_ok" + 新 fingerprint。
# 调用一次 /api/health 看 LLM 健康度。

# 4. 在 provider 控制台 revoke 旧凭证。

# 5. 用新值替换 ~/.bashrc 那一行,重开 shell:
sed -i.bak 's|^export DEEPSEEK_API_KEY=.*|export DEEPSEEK_API_KEY=<new-value>|' ~/.bashrc
source ~/.bashrc

# 6. 重启 backend:
systemctl --user restart quantmind-backend   # 或 docker compose restart backend

# 7. 写一条 CREDENTIAL_ROTATED audit 行(scripts/query_audit.py 或人工):
python3 -m backend.audit.cli emit \
  --event-type credential_rotated \
  --actor system \
  --resource-type credential \
  --resource-id DEEPSEEK_API_KEY \
  --reason-namespace routine_rotation \
  --payload '{"fingerprint":"<sha256-prefix>","previous_fingerprint":"<old>"}'
```

## 3. 三步应急 Playbook (Credential Leak Incident)

> 适用条件:任何形式的凭证暴露 —— git push 公开仓库、被 grep 出明文、
> 被 gitleaks / pre-commit 拦截但已临时绕过、出现在公网日志 / pastebin。
> **已 push 到公网视为永久泄露,必须 revoke**。

### 步骤 1 —— 立即 Revoke + 切凭证(≤ 15 分钟)

```bash
# 1. 在 provider 控制台立即 revoke 泄露的凭证。
#    LLM:DeepSeek / Alibaba / Moonshot 各自后台一键 revoke。
#    飞书:开放平台 → 应用 → 凭证与基础信息 → "重新生成 APP_SECRET"。
#    飞书 chat_id 不能 revoke,但 verify_token / encrypt_key 可重新生成。

# 2. 生成新凭证写 ~/.bashrc。

# 3. 重启 backend(系统切换到 simulation_auto 不影响也可走):
systemctl --user restart quantmind-backend

# 4. 写 audit:
python3 -m backend.audit.cli emit \
  --event-type credential_leak_incident \
  --actor cli \
  --actor-detail "<owner-name>" \
  --resource-type credential \
  --resource-id DEEPSEEK_API_KEY \
  --reason-namespace incident_leak \
  --outcome blocked \
  --payload '{"exposure_channel":"<git_public|log|pastebin>","fingerprint":"<old-prefix>"}'
```

### 步骤 2 —— Git 历史排查(≤ 1 小时)

```bash
# 1. 全仓搜索泄露的凭证或其 fingerprint:
git log -p -S "<leaked-fragment>" --all
git log -p -S "<sha256-prefix-8-hex>" --all

# 2. 用 gitleaks 扫描全历史(确认无遗漏):
gitleaks detect --source . --no-banner --config=.gitleaks.toml --log-opts="--all"

# 3. 若发现历史 commit 含明文,用 git filter-repo 重写:
#    https://github.com/newren/git-filter-repo
#    pip install git-filter-repo
git filter-repo --replace-text <(echo "<leaked-fragment>==>REDACTED")
# 注意:重写会改 SHA,需要协调 owner 通知所有 contributor force-pull。

# 4. 若仓库已 push 到公网:
#    - 永远视为泄露,即使 force-push 改写也不能撤销。
#    - 必须 revoke 才算完成,过滤历史只是清理审计痕迹。
```

### 步骤 3 —— 影响评估 + 通知(≤ 4 小时)

```bash
# 1. 在 provider 后台拉取该凭证的最近 30 天调用日志,确认无未授权调用。
#    DeepSeek / DashScope / Moonshot:用量分页。
#    飞书:开放平台 → 凭证 → 调用日志(若 verify_token/encrypt_key 泄露,
#    必须重新订阅事件)。

# 2. 写后续 audit 行:
python3 -m backend.audit.cli emit \
  --event-type credential_rotated \
  --actor system \
  --resource-type credential \
  --resource-id DEEPSEEK_API_KEY \
  --reason-namespace post_incident_rotation

# 3. 若飞书 verify_token / encrypt_key 泄露:
#    - 飞书开发者后台 → 事件订阅 → 重新配置 verify_token + encrypt_key。
#    - 必须重启长连接(F-003 后,本任务期 placeholder)。
#    - 写一条 FEISHU_LONGCONN_DISCONNECTED + FEISHU_LONGCONN_CONNECTED audit。

# 4. 若有撞库 / 异常调用,启动 simulation_auto 模式 7 日观察(切回
#    FEISHU_INTERACTIVE_ENABLED=false 直到 P0-6 acceptance gate 再放行)。

# 5. 撰写事故记录:docs/incidents/credential-leak-YYYY-MM-DD.md
#    包含:暴露通道 / 时间线 / 受影响凭证 / revoke 时间 / 后续观察期。
```

## 4. 启动期失败诊断

`secrets_validator_blocked` 是 fail-fast 信号 —— uvicorn 退出非零,
systemd / docker-compose 日志显示完整错误清单:

```
Refusing to start: secrets_validator blocked startup (3 error(s)):
  - required LLM credential 'DEEPSEEK_API_KEY' is missing from process env — export it in ~/.bashrc
  - Feishu credential 'FEISHU_APP_ID' fails shape check (expected \Acli_[A-Za-z0-9]{16}\Z)
  - .env line 12: forbidden credential assignment 'DASHSCOPE_API_KEY' — move to ~/.bashrc (P1-6 §1.1)
Fix: export the missing credentials in ~/.bashrc and reopen the shell, then retry.
```

| 错误模式 | 修复 |
|---------|-----|
| `... is missing from process env` | `export <NAME>=<value>` 写 `~/.bashrc`,重开 shell |
| `... fails shape check` | 检查 provider 后台是否复制完整;`echo $<NAME> \| wc -c` 比对长度 |
| `.env line ...: forbidden ... assignment` | 把那行从 `.env` 删除(改 `~/.bashrc`),`source ~/.bashrc` |
| 启动期 `secrets_validator_soft_warning` (不阻启动) | 检查是否还残留 `FEISHU_CUSTOM_BOT_*`,删之即可 |

## 5. CI / pre-commit hook 故障

```bash
# 跳过钩子是红线(CLAUDE.md §3 / [[feedback_codex_findings_real]]),
# 但 hook 文件本身崩溃时可临时:
git commit --no-verify        # 永远 audit 记录 + 立即修 hook,绝不长期使用

# 修 hook:
pre-commit clean
pre-commit install
pre-commit run --all-files     # 看具体哪条 rule 错
```

> **绝不**长期 disable gitleaks。如某文件需绕过,**新增** `[allowlist].paths`
> 或 `[allowlist].regexes` —— 不要在 commit 上加 `--no-verify`。

## 6. 关联资料

- 决策红线:[`project_p1_6_secrets_ip_audit.md`](../decisions/project_p1_6_secrets_ip_audit.md)
- Custom-bot 全网禁用:[`P0-2-amendment-2026-05-16-custom-bot-disabled.md`](../decisions/P0-2-amendment-2026-05-16-custom-bot-disabled.md)
- Audit 34 类:[`backend/audit/models.py`](../../backend/audit/models.py)
- 验证器:[`backend/services/secrets_validator.py`](../../backend/services/secrets_validator.py)
- 测试:[`tests/test_secrets_validator.py`](../../tests/test_secrets_validator.py)
