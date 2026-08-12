# P0-10 修订 — 2026-05-25 4 必经 agent 全 qwen(3 分析/风控用 qwen3.6-plus 免费额度;fund_manager 用 qwen3.7-max 深度推理)+ deepseek 兜底

> **修订基准**: [P0-10 LLM 角色边界 + 四必经 Agent](./P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md) §2.3 + agent_models.yaml baseline
> **关联**: [P1-7 成本预算](./P1-7-amendment-2026-05-24-precall-reservation-fanout-cap.md)(日 ¥20 hard + 真·预留)+ [P0-8-amendment tushare-data-source](./P0-8-amendment-2026-05-24-tushare-data-source.md)(异质凭证不入池)
> **修订日期**: 2026-05-25
> **触发**: 双线 MVP go-live。owner `AskUserQuestion` 2026-05-25 两步拍板:① 4 必经 agent 不再混 kimi,全走 DashScope qwen(优先**免费额度**,新户 ~100 万 token/模型,90 天先用先得,日常成本 ~¥0);② **终局决策 `fund_manager` 用更强的深度推理模型 `qwen3.7-max`**(Claude 对比 kimi-k2.6 vs qwen3.7-max:qwen3.7-max GPQA Diamond 92.3% > kimi 90.5%、公认 superior deep reasoning,且同 DashScope 单一 key 无 kimi 依赖)。现 `config/agent_models.yaml` 仅 `fundamental_analyst` / `technical_analyst` = qwen3.6-plus;`risk_officer` / `fund_manager` 仍 = kimi(+ fund_manager tiered routing)。

## 1. 修订前

- `agent_models.yaml`:`fundamental_analyst`→qwen(fb deepseek)、`technical_analyst`→qwen(fb deepseek)、`risk_officer`→**kimi**(fb qwen)、`fund_manager`→**kimi** + `routing{triage=qwen, escalation=kimi, confidence_lt:0.6}`(fb deepseek)。
- 一次双线 4-agent 辩论混跑 qwen + kimi;kimi 名义计价 ¥2.1/8.4 RMB/M(`fallback.py:48`)。

## 2. 修订后

### 2.1 4 必经 agent 全 qwen + deepseek 兜底(fund_manager 升 qwen3.7-max 深度推理)

| agent | provider | model | fallback |
|-------|----------|-------|----------|
| `fundamental_analyst` | qwen | qwen3.6-plus | deepseek/deepseek-v4-pro(**不变**) |
| `technical_analyst` | qwen | qwen3.6-plus | deepseek/deepseek-v4-pro(**不变**) |
| `risk_officer` | qwen | qwen3.6-plus | deepseek/deepseek-v4-pro(kimi → qwen3.6-plus) |
| `fund_manager` | qwen | **qwen3.7-max** | deepseek/deepseek-v4-pro(kimi → qwen3.7-max) |

- **`fund_manager` = `qwen3.7-max`**(终局决策需最强深度分析推理;owner 拍板,Claude 对比 GPQA Diamond 92.3% vs kimi 90.5%)。其余 3 个用 `qwen3.6-plus`(免费额度,成本 ~¥0)。
- **`fund_manager` 删除 `routing` block**(triage/escalation 在全 qwen 下无意义 —— 不再有 qwen-triage→kimi-escalation 分流;`fund_manager_shadow_baseline` 已证无 routing block 合法,router `is_tiered = routing is not None`)。
- `thinking` 配置改 `type: disabled`(qwen 非 kimi,不产 reasoning_content;与现 fundamental/technical 一致)。
- `qwen3.7-max` 复用 `providers.qwen` 的 base_url + `DASHSCOPE_API_KEY`(per-agent `model` 覆盖 provider `default_model`);无需新增 provider。
- **非必经 agent**(news_crawler/sentiment/data_cleaner/intelligence_officer/bull/bear/`fund_manager_shadow_baseline`)**不在本 amendment 范围**,保持原配置;`fund_manager_shadow_baseline` 永不入决策路径(P0-10 不变)。

### 2.2 成本与预算(P1-7 不破)

- 一次双线 4-agent 单轮辩论 ~几千 token:3 个 qwen3.6-plus 在免费额度内**实际 ¥0**;`fund_manager` 单次 qwen3.7-max 调用(premium 档,可能不在免费额度)token 量仍仅几千 → 即便按 premium ¥/M 计,**单次 ≪ ¥1 ≪ 日 ¥20 hard**。
- `cost_guard` 全辩论估算在首调用前**真·预留**(`run_shortlist` 已做,`_DEBATE_COST_ESTIMATE_RMB`);4 常量(日 ¥20 / soft 0.7 / 月 ¥440 / Kimi ¥4)**不动**;Line-1 一日一次 + Line-2 触发式 LLM 写同一 `llm:usage:{utc_date}` 计数器。**`fallback.py` 计价表需补 `qwen3.7-max` 档**(若与 qwen3.6-plus 计价不同),否则按 qwen 默认 ¥1/M 计 —— 任一档下单次辩论名义计数仍远低于 ¥20,不会误触熔断(此计价表补充在 U-C1/U-D4 落地,属代码任务走 codex)。
- owner 需确认 `qwen3.6-plus`(3 agent)+ `qwen3.7-max`(fund_manager)两 model id 在其 DashScope 账户可用;qwen3.7-max 即便 premium 不影响熔断结论。

### 2.3 安全核心不变(配 P0-10 / R0 §4)

- LLM positive list 4 字段不变;LLM 永不写决策字段;`fund_manager` 仍**唯一 BUY/SELL/HOLD 倡议者**(仅倡议方向);4 必经 agent 缺一降级 HOLD;`debate_round_count ≥ 1`。
- 不新增 provider(qwen 既有;P2-2 §7「禁新 LLM provider」不破);DASHSCOPE_API_KEY 已在凭证池(LLM 3)。
- `agent_models.yaml` runtime 不可改 + hot-reload 禁用;本次改 = git diff + 本 amendment + **重启**。
- 单调用 30s + 0 重试;LLM 全停 1h 系统中断;¥20/日 hard 全 LLM 暂停 —— 全不变。

## 3. 实施期任务映射(plan.html Phase U)

- **U-A3** 改 `config/agent_models.yaml`(risk_officer + fund_manager → qwen + 删 fund_manager routing + thinking disabled)+ 本 amendment(docs/config commit,免 codex)。
- agents_team 编排(`backend/agents_team/`)经 `ctx.llm_router` 按 agent 名查 agent_models.yaml,无需改代码即生效(重启后)。U-D4 真冒烟验证全 qwen 路径。

## 4. 红线清单(本 amendment 之后)

1. 4 必经 agent 全 provider=qwen + fallback=deepseek:fundamental/technical/risk_officer = `qwen3.6-plus`(免费额度);**fund_manager = `qwen3.7-max`**(深度推理);fund_manager 删 routing(triage/escalation)。
2. cost_guard 4 常量不动;真·预留 + 一次辩论/每日 shortlist(非 per candidate)+ 同一 `llm:usage` 计数器不变;免费额度不改计价表、不误触 ¥20 熔断。
3. LLM positive list 4 字段 + fund_manager 唯一倡议方向 + 4 必经缺一降级 HOLD + debate≥1 全不变。
4. 不新增 provider;agent_models.yaml runtime 不可改 + hot-reload 禁用;改走 amendment + 重启。

## 5. 修订记录追加

`docs/plan.html` 修订记录 + SESSION_LOG 同步追加。`CLAUDE.md §performance/§2.2` 的「DeepSeek 高频 / Qwen 中文金融 / MiniMax 编排」运行时策略补注:双线 MVP go-live 4 必经 agent 统一走 qwen(免费额度)+ deepseek 兜底。
