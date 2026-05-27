# U-E5 (A 部分) 代码审查 summary — 2026-05-27

> **任务**: U-E5 缺口2 端到端双线测的**无发送前置**(owner allowlist + 只读 list_feishu_chats + cond5/6/7/11 翻牌)。
> **审查方式**: codex CLI 仍在使用额度窗口(~2026-05-31 恢复)→ 按 owner 既定回退 **claude `/code-review high`**
> (记忆 `feedback_codex_rate_limit_fallback`)。7 维度并行 finder(3 correctness + 3 cleanup + 1 altitude)+ verify。
> **审查范围**(`git diff HEAD` + 新增未跟踪文件):
> - `backend/integrations/feishu/inbound_gate.py`(新,纯函数)
> - `backend/main.py`(`_feishu_dispatch` 接 InboundGate + 启动期 fail-fast)
> - `scripts/list_feishu_chats.py`(新,只读)
> - `config/pilot_readiness.yaml`(cond5/6/7/11 false→true)
> - `tests/test_feishu_inbound_gate.py` / `tests/test_list_feishu_chats.py` / `tests/test_pilot_cond_evidence.py`(新)
> - `tests/test_pilot_readiness.py`(committed-manifest 测试更新)
> - `docs/decisions/P0-2-amendment-2026-05-27-owner-open-id-allowlist.md`(新 amendment)

## 结论

- **Correctness:两个 correctness 角度(line-by-line + removed-behavior;cross-file tracer + gate 影响)均 `[]`,无 bug。**
- 关键安全核查(cross-file tracer 重点)**通过**:翻 cond5/6/7/11 **不会**提前打开 PILOT go-live gate ——
  `PilotReadinessProbe.evaluate()` 聚合全部 11 条;cond3(`dry_run_double_line_pass`)+ cond4
  (`feishu_send_recv_smoke_pass`)仍 False → `_manifest_unmet()` 恒报 2 条 unmet → `can_switch_to_feishu_on(PILOT)`
  恒 `allowed=False`,与 5 条 live-probe(含 cond2 owner auth `QUANTMIND_PROD_RUN`/`OWNER_PROD_AUTHORIZATION`)无关。
- 全仓除 `test_pilot_readiness.py` + `test_pilot_cond_evidence.py`(均已同步更新)外,**无**其他测试加载真 committed manifest
  断言全 False / 特定 unmet-count;`can_switch_to_feishu_on` 的其他测试全用 stub probe / tmp_path manifest,绝缘。
- 新 `InboundGate.from_env` 的 `SystemExit` 在任何现有测试中**不可达**:lifespan interactive 块先经
  `feishu_client is not None` + acceptance gate `allowed`(现被 cond3/4 拒)守门;唯一 runtime `TestClient` 用例
  (`test_llm_routing_escalation.py`)build 全新 `FastAPI()` 不跑真 lifespan;lifespan 回归测试全是 AST 静态文本检查。
- audit 写法 schema 合法:`FEISHU_MESSAGE_RECEIVED` + `FEISHU_USER` + `BLOCKED` 均存在;非 evolution 类(SYSTEM-only
  actor 校验不触发);payload 仅 `compute_fingerprint` 值(plaintext-secret 校验不触发);`message.sender_id` 空已被
  `events.py::_extract_message` 在上游 `_SkipEventError` 丢弃,dispatch 永不见空值。

## Cleanup / altitude findings(4 条,均非 correctness)

| # | 文件 | 摘要 | 处置 |
|---|------|------|------|
| 1 | `backend/main.py` | `FEISHU_DECISION_CHAT_ID` 在 lifespan 块内被读+校验两次(既有 `decision_chat_env` 块 + 新 `InboundGate.from_env`) | **不改**。既有 `decision_chat_env` 块先于 gate 跑(它还做 alert≠decision 校验 + 日志前缀),非死代码;gate 作为可复用纯模块独立持有自己的 fail-closed 不变量是合理的防御层叠。非本次引入。 |
| 2 | `backend/main.py` | `DROP_WRONG_CHAT` 日志仍用 `decision_chat_env[:6]+'***'` 手搓指纹,与 11 行下新用的 `compute_fingerprint` 不一致 | **不改**。该 `[:6]` 切片是**既有原代码**(旧 `_feishu_dispatch` 即如此),非本次引入;改它=改既有日志格式,范围蔓延。 |
| 3 | `tests/test_pilot_readiness.py` + `tests/test_pilot_cond_evidence.py` | 两测试都断言完整 manifest 6-flag 状态;`test_pilot_cond_evidence` docstring 却称前者"只守 schema" → 自相矛盾(本次引入) | **已修**。`test_pilot_readiness` 改为只守 schema(key 集)+ owner-gated 两条(cond3/4)仍 False(防提前签收);完整 4-true/2-false ledger 锁单一归 `test_pilot_cond_evidence`。docstring 与代码现一致。 |
| 4 | `scripts/list_feishu_chats.py` | `_list_chats_real` 重建 lark client 链(与 `FeishuClient._build_acreate` 同款 log_level guard) | **不改**(agent 自评"可接受")。`FeishuClient` 故意只做 `send_message`,只读 ListChat 路径它不覆盖;脚本 docstring 已声明。 |

## 门禁(commit 前)

- `pytest -q --cov=backend --cov-fail-under=70` → **4009 passed / 13 skipped,覆盖率 90.58%**。
- `ruff check` + `ruff format --check` → ALL PASS(3 文件已 format)。
- `bash scripts/redline-check.sh` → **All redline checks passed**(含 InstructionPlan 单一构造点 M-004 / Line-2 隔离 /
  cost_guard 隔离 / 127.0.0.1 等;新模块 `inbound_gate.py` 纯函数零 `backend.{llm,agents,mirofish}` import)。
- 只读 `scripts/list_feishu_chats.py` 实跑核对:机器人在 2 群,`FEISHU_DECISION_CHAT_ID`→"QuantMind决策执行群" present、
  与告警群不同(`decision_is_alert=false`),verdict OK。**零发送**。

## 仍待 owner / 开盘(U-E5 未 done 部分)

- **cond3**(dry_run_double_line_pass):需开盘时段(09:35 后)重跑 `scripts/dry_run_double_line.py` 渲出真 BUY + 判据,
  owner 审阅后翻。本 session 22:05 已收盘,做不了。eastmoney 代理隐患:现复测直连+代理均 200(瞬时态),建议把
  eastmoney 域名加进 `no_proxy` 以免开盘走代理路由瞬断 → 全降级。
- **cond4 + (B) 真发**:owner 设 `FEISHU_INTERACTIVE_ENABLED=true` + `FEISHU_OWNER_OPEN_ID`(新增,owner open_id)
  + go-live gate 全过后,真发 1 条 BUY 到决策群 → owner 按 v2 模板回填 → WS(鉴权 + allowlist)→ parser → applier →
  镜像 → 16:00 对账。真发前必停下来向 owner 明示内容 + 目标群拿确认。
