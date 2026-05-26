# U-D4 Codex Review Summary — 真冒烟 + ExecutionReportApplier durable 幂等 + qwen3.7-max 计价

> Task: **U-D4**(Phase U 生产编排上线)。1 cycle review + 2 read-only verify。
> 结论:**4 findings(cycle 1)+ 1 finding(verify-1)全部 RESOLVED,verify-2 终判 P0/P1/P2 none — clean**。0 残留。

## 范围(一个 feature commit,三组件 + codex 修复)

1. **qwen3.7-max 计价**(预算精度)— `fund_manager` 旗舰模型 token 原按 qwen 家族默认 ¥1/M 计 → 少算(预算少算 = 烧钱风险)。新增 model-aware 费率:`backend/llm/fallback.py` `MODEL_COST_RATES["qwen3.7-max"] = CostRate(2.5, 10.0)`(权威中国区 DashScope ≤32K 档:¥2.5/M 入、¥10/M 出,May 2026 联网查证;owner 指示联网查;100 万免费额度只让实际花费更低 → 按付费档计是保守 fail-safe)+ `resolve_cost_rate(provider, model)`(model 档 → 家族档 → 零)。`track_usage`/`calculate_cost` 加 `model` 参,router 透传 `model=model`。**Redis key 仍按 provider 家族键** → cost_guard 日聚合不变,只让 `cost_rmb` 变准。
2. **ExecutionReportApplier durable report_id 幂等** — 新增 `backend/broker/applied_report_guard.py`(`AppliedReportGuard` Protocol + `RedisAppliedReportGuard` SET NX EX + `InMemoryAppliedReportGuard` LRU+TTL,镜像 U-B2 outbox claim/release)。applier claim-first → 重复 claim 短路 no-op(`execution_report_duplicate_skipped`,不动 broker/event/audit)。main.py 接 Redis 守门(lifespan redis 先于编排层就绪已验证)。
3. **上线冒烟脚本** `scripts/smoke_live_double_line.py` — 真 qwen 4-agent 辩论成本校验(0 错+usage 计数+花费 ≪¥20,复用 `dry_run_double_line.run_dry_run` + cost-guard spend 前后读)+ 真飞书发收往返(经新增 `renderer.render_smoke_ping` 固定字面量防注入)。真实网络路径 `--real` 门 + 凭证齐备双门,默认 0 网络(沿用 U-D3 先例,owner 跑)。

## Cycle 1 findings(4,全 RESOLVED)

| # | 级别 | 位置 | 问题 | 修复 |
|---|------|------|------|------|
| 1 | **P1** | `appliers.py` apply claim | 解析器每次 parse 生成新随机 `report_id` → 同一回报二次提交(前端双击/飞书重投绕过 envelope dedup)claim 不同 key 仍双改 broker | 改 claim **确定性内容键** `compute_idempotency_key(report)` = sha256(`instruction_id|kind|prefix|stock_code|filled|remain|price|fee|reason`),channel/时间戳无关;不同内容(改价/改量/不同 remain)哈希不同不误抑 |
| 2 | **P1** | `appliers.py` 释放 | broker 已 mutate 后若 BrokerEvent/audit 写失败,blanket `except` 释放 claim → 重试二次改 broker | 释放**仅限 mutate 前**失败:`_apply_fill` 仅 `apply_external_fill` raise(broker 未变)时释放;mutate 后 event/audit 失败 claim **保留**上抛;`_apply_unfilled`(零 mutate)audit 失败释放 |
| 3 | **P2** | `smoke_live_double_line.py` 凭证门 | `--real` 仅校验 `DASHSCOPE`,但 `build_real_context` 需 `TUSHARE_TOKEN` + LLMRouter init 急切解析 deepseek/kimi key → 深处才失败 | `_DEBATE_CREDS` 扩 `TUSHARE_TOKEN`+`DASHSCOPE`+`DEEPSEEK`+`MOONSHOT` |
| 4 | **P3** | `applied_report_guard.py` 过期序 | 重复 claim `move_to_end` 把旧时间戳移末尾 → `_purge_expired`(按序早退)失效,stale claim 超 TTL 仍抑制合法重试 | 重复 claim **不 move_to_end**(固定窗 TTL),保留 purge 顺序不变量 |

## Verify-1 finding(1,RESOLVED)

| # | 级别 | 位置 | 问题 | 修复 |
|---|------|------|------|------|
| 5 | **P2** | `smoke_live_double_line.py` `_FEISHU_CREDS` | 发送腿 `FeishuClient.from_env()` 需全 5 凭证池(`FEISHU_CREDENTIAL_NAMES` 含 `FEISHU_ALERT_CHAT_ID`),但 `_FEISHU_CREDS` 只列 `DECISION_CHAT_ID` → `--real` 过 preflight 后在 from_env 才炸 | `_FEISHU_CREDS = (*FEISHU_CREDENTIAL_NAMES, "FEISHU_DECISION_CHAT_ID")` 从 secrets_validator 引入,preflight 与 from_env 单一真相源同步 |

**Verify-2 终判:P0/P1/P2 none — clean。**

## 门禁

- ruff(触及文件全绿)+ redline-check(M-004 单一构造点 / X-018 编排隔离 / N-005 Line-2 隔离 / L-004 等全不破)。
- 全量 **3824 passed / 13 skipped**(基线 3780 → +44:pricing 9 + guard 12 + appliers idempotency 7 + smoke 16)。
- 新模块覆盖:`applied_report_guard` 92% / `appliers` 100% / `smoke_live_double_line` 70%(未覆盖 = owner-run 真实网络路径,设计如此)。

## owner gate(真冒烟未跑)

`FEISHU_DECISION_CHAT_ID` / `FEISHU_INTERACTIVE_ENABLED` / `QUANTMIND_OWNER_PROD_AUTHORIZATION` 当前为空 → 真 qwen 辩论 + 真飞书发收是 owner 亲跑(`python scripts/smoke_live_double_line.py --real`);**真发飞书是独立 owner gate**。冒烟脚本默认 0 网络。
