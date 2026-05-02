**发现**

1. [MEDIUM] File: [backend/api/watchlist.py:50](/home/ps/papers/QuantMind/backend/api/watchlist.py:50)  
Confidence: High  
Issue: `category` 依赖 Pydantic `Literal` 校验，非法值会返回 FastAPI 默认 422 `{"detail": [...]}`，不走现有 `_err()` 的 `status/data/error` 包络，也不会明确提示 `null` 可用于清除 override。  
Fix: 将 `category` 接成 `str | None` 后在 handler 内手动校验，错误返回 `_err("category must be one of 'fast', 'slow', or null to clear the override", 422)`；或添加全局 `RequestValidationError` 包络处理器。

2. [MEDIUM] File: [backend/api/watchlist.py:207](/home/ps/papers/QuantMind/backend/api/watchlist.py:207)  
Confidence: High  
Issue: `POST /api/watchlist/{code}/category` 不校验 `code` 是否在 active watchlist 中；未知但格式合法的 6 位代码会被写入 policy overrides，并返回成功。对 operator 来说，这不是 actionable error，而是静默配置了一个不会运行的代码。  
Fix: 在更新 policy 前读取 `watchlist.list_stocks()`，若不存在 active `stock_code`，返回 404，例如：`Stock 'XXXXXX' is not in the active watchlist; add it via POST /api/watchlist first`。

3. [MEDIUM] File: [backend/data/analysis_scheduler.py:241](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:241)  
Confidence: High  
Issue: category cron tick 的 start/complete 日志有 `category` 和 count，但缺少 `matched_codes`、`total_watchlist`、`policy_version`、bucket SLA/config 等字段。排查“为什么某只股票没跑”时，operator 还需要反查 policy 和 watchlist。  
Fix: 在 `category_analysis_started/skipped/complete` 中加入 `matched_codes=matched`、`total_watchlist=len(all_codes)`、`policy_version`、`cron`、`timeout_seconds`、`max_debate_rounds`；complete 可加 `failed=len(matched)-len(signals)`。

4. [MEDIUM] File: [backend/services/watchlist_policy.py:318](/home/ps/papers/QuantMind/backend/services/watchlist_policy.py:318)  
Confidence: High  
Issue: `save_policy()` 用 `yaml.safe_dump()` 重写整个 YAML，会丢掉 `config/watchlist_policy.yaml` 里的 operator 注释，包括 cron 说明和 480s/900s SLA 说明。首次通过 API 改分类后，配置 discoverability 明显下降。  
Fix: 使用可保留注释的 round-trip YAML 写法，或把 runtime overrides 拆到单独 `watchlist_overrides.yaml`，静态策略模板保持只读。

5. [LOW] File: [backend/api/watchlist.py:41](/home/ps/papers/QuantMind/backend/api/watchlist.py:41)  
Confidence: Medium  
Issue: `category=null` 语义只写在 request model docstring，endpoint docstring 只说 “clear the override”。Swagger/OpenAPI 中字段级 discoverability 不够强，前端开发者容易漏掉“必须发送 JSON null，而不是省略字段”。  
Fix: 用 `Field(description=..., examples=[...])` 给 `category` 加字段说明，并在 endpoint docstring 中写明 `{"category": null}` clears the override。

6. [LOW] File: [config/watchlist_policy.yaml:19](/home/ps/papers/QuantMind/config/watchlist_policy.yaml:19)  
Confidence: Medium  
Issue: `pipeline: fast_pipeline/slow_pipeline` 被配置和 API 暴露，但 scheduler 实际只使用 `max_debate_rounds` 和 `pipeline_timeout_seconds`，没有使用 `pipeline` 字段。fresh operator 可能以为改 pipeline 名称会改变执行路径。  
Fix: 要么实现该字段的实际 routing，要么在 YAML 注释中明确 `pipeline` 当前只是 label/metadata，避免误操作。

**Summary Table**

| Severity | File:line | Confidence | Issue | Fix |
|---|---|---:|---|---|
| [MEDIUM] | `backend/api/watchlist.py:50` | High | invalid category 不走统一 envelope | 手动校验或全局 validation handler |
| [MEDIUM] | `backend/api/watchlist.py:207` | High | unknown code 被成功写入 override | active watchlist 校验，404 actionable message |
| [MEDIUM] | `backend/data/analysis_scheduler.py:241` | High | cron tick 日志缺 `matched_codes` 等排障字段 | start/skip/complete 增加结构化字段 |
| [MEDIUM] | `backend/services/watchlist_policy.py:318` | High | API 保存会丢 YAML 注释 | round-trip YAML 或 overrides sidecar |
| [LOW] | `backend/api/watchlist.py:41` | Medium | `null` 清除 override 不够 discoverable | 字段级 OpenAPI description/example |
| [LOW] | `config/watchlist_policy.yaml:19` | Medium | `pipeline` 字段看似可配但未生效 | 实现或注释为 metadata |

**结论**

API 路径形状整体与现有 `/api/watchlist/*` 一致；成功响应也沿用 `_ok(status/data/error)`。主要 UX/DX 风险集中在错误包络、未知代码校验、cron 日志可排障性，以及 API 写回后破坏 YAML 注释。

**Vue 3 前端影响清单**

- watchlist 列表增加 Fast/Slow 分类列或 badge。
- 页面加载时调用 `GET /api/watchlist/policy`，合并 `assignments` 到现有 watchlist rows。
- 每行增加分类控件：`fast`、`slow`、清除 override，对应 `POST /api/watchlist/{code}/category`，body 为 `{"category":"fast"}`、`{"category":"slow"}`、`{"category":null}`。
- 区分 effective category 与 override source；当前可由 `policy.overrides` 推导，但更理想是 API 直接返回 source。
- 设置页展示 read-only policy：cron、timeout 480/900s、debate rounds、policy_version、last_updated。
- 错误处理需兼容当前两类形状：手动 `_err` 的 `detail.error` 与 Pydantic 默认 `detail[]`；修复后统一读 envelope。