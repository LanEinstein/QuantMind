# LLM 多模型路由恢复(P0-10-amendment-2026-06-11)— 审查摘要

- **日期**: 2026-06-11(session #75)
- **门禁路径**: `codex review --uncommitted` 撞 usage limit(0 输出)→ 按既定回退跑 **/code-review high**(7 角度 finder:line-by-line / removed-behavior / cross-file / reuse / simplification / efficiency / altitude,1-vote recall-biased verify)。
- **范围**: amendment + `config/agent_models.yaml` + `backend/llm/fallback.py` + `backend/llm/cost_tracker.py` + `backend/services/{thesis_advisory,theme_llm_client,dspy_gepa_runner}.py` + 相关测试。

## 确认 findings(全部已修)

| # | 严重度 | 发现 | 修复 |
|---|--------|------|------|
| 1 | P1 | **kimi thinking 增量击穿预留**:router 对 thinking-enabled kimi 把请求 `max_tokens` 增大 `thinking.max_tokens`(theme +10k / thesis +8k)→ theme 实际可花 ~50k tokens > 40k/run 预留(先花钱后 abort 循环);thesis worst-case ¥0.356 > ¥0.30 预留 | `RouterLlmClient` 先减 thinking 预算再转发(`thinking_budget_tokens` 参数,默认 10k 与 yaml 镜像 + 漂移测试);无空间先于花费报错。thesis 预留 0.30→0.40,注释按真实 worst-case(4096+8000)×¥30/M 重推导 |
| 2 | P1 | **`cost_tracker.MODEL_PRICING` 镜像表没同步**:仍是旧价(3-30 倍低)且缺 v4-flash,违反自身"keep in sync"契约 | 改为从 `MODEL_COST_RATES` **派生**(by-construction 不再漂移) |
| 3 | P2 | **kimi ¥28 非真上取整**:FX 7.1-7.3 下 $4.00 应 >¥28.4,表反向低估 | kimi-k2.6 上调 **7.5/30**(FX 7.5 上垫,只多算不少算);family 兜底同步派生 |
| 4 | P2 | **`RouterUsageReserver._held` 覆盖泄漏**:二次 reserve 覆盖句柄,首个预留滞留共享日计数器至 TTL | `_held` 改列表,`settle()` 全量释放;新增多次 reserve 测试 |
| 5 | P2 | **yaml 注释谎称"在 kimi ¥4 cap 内"**:该 cap 只拦 escalation 分支,primary kimi 调用不经过;且共享桶含 legacy 三件套必然 >¥4 | 注释改为如实表述(call-count caps + ¥100 真·预留约束;¥4 cap 语义错位记 amendment §6 follow-up) |
| 6 | P2 | **测试缺口**:unmapped-model→family 分支无测试(回归可致 ¥0 计价);yaml 路由模型 ↔ 计价表无交叉校验;3 处 kimi 价手抄常数无防漂移钩子 | 补 `test_unknown_model_string_falls_back_to_family_rate` + `TestPricingDriftGuards`(4 条:费率覆盖/thinking 镜像/thesis worst-case/全路由模型有档) |
| 7 | P3 | GEPA 迁移 v4-pro 后 reasoning 变 per-request 开关,docstring 未警示;`P2-2-implementation-plan` 文档仍写旧名 | docstring 加 MIGRATION CAVEAT;计划文档两处加迁移注记 |
| 8 | P3 | 杂项简化:family 表手抄三份 / `_run_in_thread(lambda:)` 冗余 / 校验块复制 / yaml rationale 注释重复 | family 派生 + `_require_*` 助手 + 测试直接 `asyncio.run(harness())` + risk_officer 注释指回上方主注释 |

## REFUTED / 不实施(记录)

- "kimi cap 应接入 primary reserve 路径":机制改动超出本次 owner 授权范围(owner 已接受该日支出),记 amendment §6 独立决策点。
- "theme 预留 gate 应进 cost_guard(`reserve_theme_research_slot` + dedup + 日 run cap)":cron 尚未接线(Phase Z),记 §6 接线时实施。
- 第 4 个 `_FakeRouter` 测试夹具重复:可接受,不动。

## 终态门禁

5034 passed / 13 skipped;ruff 全绿;`scripts/redline-check.sh` 全绿;`mypy --strict backend/services/theme_llm_client.py` 干净;新模块覆盖 100%。
