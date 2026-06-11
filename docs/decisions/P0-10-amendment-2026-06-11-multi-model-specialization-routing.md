# P0-10 修订 — 2026-06-11 恢复 deepseek / qwen / kimi 三模型按特长分工路由(supersede 2026-05-25 全 qwen 临时路由)+ 计价表全面校准

> **修订基准**: [P0-10 LLM 角色边界 + 四必经 Agent](./P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md) §2.3 + [P0-10-amendment-2026-05-25 全 qwen 临时路由](./P0-10-amendment-2026-05-25-mandatory-agents-qwen.md)(本 amendment **supersede** 其 §2.1 路由表;其安全条款 §2.3 全保留)
> **关联**: [P1-7-amendment-2026-05-26](./P1-7-amendment-2026-05-26-daily-hard-100-and-multi-candidate-debate.md)(日 ¥100 hard + 真·预留)+ [P0-10-amendment-line2-2026-06-01](./P0-10-amendment-line2-2026-06-01-position-thesis-advisory.md)(thesis advisory)+ [P0-8-amendment-2026-06-01](./P0-8-amendment-2026-06-01-llm-theme-research-peer-sourcing.md)(theme_research peer-sourcing)
> **修订日期**: 2026-06-11
> **触发**: MVP 阶段为省事+控成本把双线 4 必经 agent 全临时指向 qwen(2026-05-25 amendment)。owner 2026-06-11 指示恢复三模型按「特长 + 当前资费」分工,并经 `AskUserQuestion` 四问拍板(见 §2)。调查同时发现 `fallback.py` 计价表全员过期且**方向危险**(低估 3-30 倍 = 熔断静默超支),必须一并校准。

## 1. 修订前现状(2026-06-11 实查)

- 4 必经:fundamental/technical/risk = qwen3.6-plus、fund_manager = qwen3.7-max(全 DashScope,deepseek 兜底)。
- 活跃 LLM 调用点实查(纠正任务假设两处):
  1. Line-1 `agents_team`(09:35 cron,≤8 辩论/日 × 4 调用)。
  2. **旧 9-agent 分析管线仍活跃**(`main.py:2908 → _init_analysis_scheduler`,fast cron 09/11/13/15 + slow 09:00;2026-06-04 运行日志实证):news_crawler/sentiment(deepseek-v4-pro)、fundamental/technical/risk(qwen3.6-plus)、intelligence_officer/bull/bear(kimi-k2.6 thinking)、fund_manager(qwen3.7-max)。kimi 三件套为 **primary** 调用,¥4/日 cap 只拦 escalation,不拦 primary。
  3. Phase W thesis 盘后复盘(17:30,≤10 次/日)复用 agent 名 `intelligence_officer` → 与旧管线/MiroFish 三方共用一个 yaml 条目。
  4. MiroFish(event cap=1/日)7 处调用走 `intelligence_officer`。
  5. theme_research investigator(Phase Y,运行期未接线)= 注入式 `LlmClient` Protocol,无模型归属。
  6. `data_cleaner` 在 yaml 但全仓零调用点(死条目,保留并降档);`fund_manager_shadow_baseline` 仅 shadow(不动)。
- `fallback.py` 计价表(全过期):deepseek 0.2/0.2、qwen 1/1、kimi 2.1/8.4、qwen3.7-max 2.5/10。

## 2. 官方资费复核(2026-06-11)与 owner 四问拍板

资费(官方,¥/M tokens,输入为缓存未命中价):

| 模型 | 输入 | 输出 | 缓存命中 | 上下文 | 来源 |
|------|------|------|----------|--------|------|
| deepseek-v4-pro | 3 | 6 | 0.025 | 1M | api-docs.deepseek.com 中文价目(旧名 deepseek-chat/reasoner **2026-07-24 弃用**) |
| deepseek-v4-flash | 1 | 2 | 0.02 | 1M | 同上 |
| qwen3.6-plus | 2 | 12 | 0.2 | 256k 档 | 阿里云百炼(新户 90 天免费额度大概率已耗尽,「~¥0」假设过期) |
| qwen3.7-max | **12 / 36 列价**(限时 5 折 6/18) | — | 5 折同享 | 1M(出 64k) | 阿里云百炼;GPQA 92.4 / SWE-Pro 60.6 全场第一 |
| kimi-k2.6 | $0.95 ≈ **7.5**(假设) | $4.00 ≈ **30**(假设) | $0.16 ≈ 1.2 | 256k | platform.kimi.com 仅美元价;RMB 按 FX **7.5 上垫**(高于现行 7.1-7.3 区间,确保表只多算不少算)**待 owner 控制台核实**;K2.5→K2.6 涨价 58%/33% |

owner `AskUserQuestion` 2026-06-11 四问拍板:

1. **Line-1 四必经 = 特长分工**:fundamental+risk=qwen3.6-plus(中文 A 股域)、technical=deepseek-v4-pro(数值/指标推理强且输出半价)、fund_manager=qwen3.7-max 不动。
2. **theme 调查 + thesis 复盘 = kimi-k2.6 thinking,各建专用 yaml 条目**(`theme_investigator` / `thesis_reviewer`),与 `intelligence_officer` 三方共用解耦。
3. **旧管线 kimi 三件套不动**(owner 原话「这个成本完全可以接受」;¥2-16/日敞口接受,仍受 ¥100 hard 兜底);爬虫层 news_crawler/sentiment/data_cleaner 按推荐矩阵降 deepseek-v4-flash(高频批量摘要,¥1/¥2 新便宜档)。
4. **计价表 = 保守列价**(宁高估不低估):qwen3.7-max 按列价 12/36(限时折扣随时停)、kimi 按 7/28 汇率上取整假设。

## 3. 修订后路由表(config/agent_models.yaml)

| agent | provider/model | fallback | thinking | 理由(特长 × 资费) |
|-------|----------------|----------|----------|--------------------|
| `fundamental_analyst` | qwen/qwen3.6-plus | deepseek/deepseek-v4-pro | disabled | 财报/估值=中文金融域(不变) |
| `technical_analyst` | **deepseek/deepseek-v4-pro** | qwen/qwen3.6-plus | disabled | K线/指标=数值推理,v4-pro LiveCodeBench/Terminal-Bench 第一,输出 ¥6 = qwen3.6-plus 一半 |
| `risk_officer` | qwen/qwen3.6-plus | deepseek/deepseek-v4-pro | disabled | 合规/风险语境=中文 A 股域(不变) |
| `fund_manager` | qwen/qwen3.7-max | deepseek/deepseek-v4-pro | disabled | 终局决策,GPQA/SWE-Pro 全场第一(不变) |
| **`thesis_reviewer`(新)** | kimi/kimi-k2.6 | qwen/qwen3.7-max | enabled 8000/none | Phase W 盘后复盘:证据对比+深推理,≤10 次/日,worst-case ≈ ¥0.36/次(预留 ¥0.40/次) |
| **`theme_investigator`(新)** | kimi/kimi-k2.6 | **无**(fail-closed) | enabled 10000/none | Phase Y 产业链倒推 SOP:长网页证据综合+thinking,1 run/日 ≤40k token ≈ ¥1.2 预留/日;investigator 失败=aborted run,不设兜底 |
| `news_crawler` / `sentiment_analyst` / `data_cleaner` | deepseek/**deepseek-v4-flash** | qwen/qwen3.6-plus | disabled | 高频批量摘要/清洗,¥1/¥2 便宜档 |
| `intelligence_officer` / `bull_researcher` / `bear_researcher` | kimi/kimi-k2.6(不动) | qwen/qwen3.6-plus | enabled(不动) | 旧管线+MiroFish;owner 拍板成本可接受 |
| `fund_manager_shadow_baseline` | kimi/kimi-k2.6(不动) | deepseek/deepseek-v4-pro | enabled(不动) | 永不入决策路径(P0-10 不变) |

满负荷估算 ≈ ¥4-18/日(旧管线 kimi 占大头),距日 ¥100 hard / ¥70 soft 安全余量充足。

## 4. 配套代码修订

1. **`backend/llm/fallback.py` 计价表重写**(低估=静默超支,方向性必修):
   - `MODEL_COST_RATES`(每个实际用到的 model 一档):deepseek-v4-pro 3/6、deepseek-v4-flash 1/2、qwen3.6-plus 2/12、qwen3.7-max 12/36、kimi-k2.6 **7.5/30**(FX 7.5 上垫假设)。
   - `COST_RATES`(provider family 兜底)由 `_FAMILY_MEMBERS` × `MODEL_COST_RATES` **取家族最贵档派生**(by-construction,不再手抄两份);`backend/llm/cost_tracker.py` 的 `MODEL_PRICING` 镜像表同样改为派生(review 发现它仍是 3-30 倍旧价且缺 v4-flash)。
2. **`backend/services/thesis_advisory.py`**:默认 `agent_name` `intelligence_officer` → `thesis_reviewer`;`_DEFAULT_ESTIMATED_RMB` 0.05 → **0.40**(真实 worst-case = defaults.max_tokens 4096 + router kimi thinking 增量 8000 = 12,096 输出计费 token × ¥30/M + prompt ≈ ¥0.38,review 发现原 0.30 仍低估)。
3. **theme_research 编排层适配器(新)**:`RouterLlmClient` + `RouterUsageReserver`(`backend/services/theme_llm_client.py`),实现 investigator 的注入式 `LlmClient`/`UsageReserver` Protocol,绑定 agent 条目 `theme_investigator`;**investigator 内部零改动**(保持注入式);定时 research cron 接线仍属 Phase Z/owner 重启,不在本 amendment。**kimi thinking 钳制**(review 发现的关键 seam):router 对 thinking-enabled kimi 请求把 `max_tokens` 增大 `thinking.max_tokens`,适配器先把调用方 cap **减去 thinking 预算**再转发,保证 completion+thinking ≤ 调用方 cap(不破 investigator 40k/run 总界、预留只多不少);无空间则**先于任何花费**报错成 aborted run。`RouterUsageReserver` 持有**全部**已批预留(列表)、`settle()` 全量释放(防二次 reserve 覆盖泄漏)。
4. **`backend/services/dspy_gepa_runner.py`**:GEPA reflection_lm `deepseek-reasoner` 旧名 2026-07-24 官方弃用 → 迁 `deepseek-v4-pro`(纯离线 P2-2 路径,≤¥5/次预算上限不变);**迁移 caveat 已写入 docstring**:v4-pro 的 reasoning 是 per-request 开关(旧 reasoner 恒思考),未来生产 adapter 必须显式开启,否则 GEPA 退化成贪心随机搜索。`docs/decisions/P2-2-implementation-plan-2026-05-18.md` 两处旧名同步加迁移注记。
5. **防漂移测试**(review 发现三处 kimi 价手抄常数会随 owner 校准 MODEL_COST_RATES 而静默过期 → 用测试机械连动):`tests/services/test_theme_llm_client.py::TestPricingDriftGuards` 断言 ① 适配器默认费率 ≥ kimi 输出档;② 适配器 thinking 预算 == yaml `theme_investigator.thinking.max_tokens`;③ thesis 预留 ≥ 由 MODEL_COST_RATES+yaml 推导的 worst-case;④ **yaml 全部路由模型(primary+fallback+default_model)必须在 MODEL_COST_RATES 有档**(防新模型悄悄按 family 价计)。

## 5. 安全核心不变(配 P0-10 / R0 §4 / P1-7)

- LLM positive list 4 字段;LLM 永不写决策字段;`fund_manager` 仍唯一 BUY/SELL/HOLD 倡议者;4 必经缺一降级 HOLD;`debate_round_count ≥ 1`;单调用 30s + 0 重试。
- **不新增 provider**(deepseek/qwen/kimi 三把 key 既有;P2-2 §7 不破);`deepseek-v4-flash`/`kimi-k2.6` 复用既有 provider base_url + key。
- 成本 4 常量不动(日 ¥100 hard / soft 0.70 / 月 ¥440 / Kimi ¥4)+ 真·预留 + 同一 `llm:usage:{utc_date}` 计数器;**严禁全 deepseek-only 降级**(qwen 中文域保留于 fundamental/risk/fund_manager)。
- `agent_models.yaml` runtime 不可改 + hot-reload 禁用;本次改 = git diff + 本 amendment + **重启**(system 当前停机,owner 集中测试时统一重启生效)。
- `fund_manager_shadow_baseline` 永不入决策路径;theme_research 红线(P0-8-amendment-2026-06-01 §3:量化资格权威 / 全留痕 / 人工 pin / 严禁进运行时数据路径)全部不变。

## 6. 遗留 follow-up(本 amendment 记录,不实施)

- **kimi RMB 实价校准**:当前 7.5/30 为 FX 上垫假设。owner 在 Moonshot 控制台核实后,改 `backend/llm/fallback.py::MODEL_COST_RATES["kimi-k2.6"]` + 重启(无需新 amendment,本 amendment 已授权按官方实价校准)。**注意不止一行**:若实价高于 30 输出档,§4.5 的防漂移测试会红(`theme_llm_client._DEFAULT_RMB_PER_MILLION_TOKENS` / `thesis_advisory._DEFAULT_ESTIMATED_RMB` 需同步上调)——测试红即改全,这是设计行为,防止预留口径静默过期。
- **kimi ¥4/日 cap 语义错位**(review 发现,本次不改机制):该 cap 在 router 只拦 **escalation** 分支,而当前生产无 tiered routing、kimi 全是 primary 调用(intelligence_officer/bull/bear + 新 thesis/theme)→ cap 实际不约束任何运行时调用,仅 `get_kimi_budget_state` 状态/告警会在共享 kimi 桶 >¥4 时变 hard_breach(成本面板红灯属预期,owner 已接受该日支出)。若未来要让 ¥4 cap 真正约束 primary kimi,或如实上调/退役该常量(CLAUDE.md §2.10),走独立 amendment。
- **theme research cron 接线时**(Phase Z):建议镜像 `reserve_thesis_review_slot`/`reserve_anomaly_llm_slot` 在 cost_guard 增加 `reserve_theme_research_slot`(dedup + 日 run 数 cap),并由 cron wrapper 在 `investigate()` 返回后调用 `RouterUsageReserver.settle()`(否则预留靠 1h TTL 自然过期,可能在 09:35 辩论窗挤占额度)。
- 旧 9-agent 管线与双线并跑的去留是独立决策点(owner 已表态成本可接受,故本次不动);若未来停用走独立 amendment。
- qwen3.7-max 限时 5 折到期/变价同上,按官方实价校准即可。

## 7. 修订记录追加

`docs/plan.html` 修订记录 + SESSION_LOG #75 同步追加。
