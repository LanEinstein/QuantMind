**摘要表**

| # | Severity | File:line | Confidence | 问题 |
|---|---|---:|---|---|
| 1 | HIGH | [analysis_scheduler.py:354](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:354) | High | 全局 `_run_lock` 让 fast 可被 slow 单股阻塞到 900s |
| 2 | HIGH | [analysis_scheduler.py:386](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:386) | High | `480/900s` 只包 `run_analysis()`，不等于端到端 SLA |
| 3 | HIGH | [analysis_scheduler.py:360](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:360) | Medium | 日成本 ≤ ¥1.20 不能靠 timeout 保证，当前 guard 仍可超支 |
| 4 | MEDIUM | [analysis_scheduler.py:285](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:285) | High | fast bucket 顺序执行 + 10s 间隔会放大 tick 完成时间 |
| 5 | LOW | [watchlist.py:218](/home/ps/papers/QuantMind/backend/api/watchlist.py:218) | High | async API handler 内同步写 YAML，会阻塞 event loop |
| 6 | LOW | [watchlist_policy.py:242](/home/ps/papers/QuantMind/backend/services/watchlist_policy.py:242) | High | `assign_category()` 有双 lookup，且 default_codes 是 tuple membership |

**Findings**

[HIGH] File: [backend/data/analysis_scheduler.py:354](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:354)  
Confidence: High  
Issue: `_run_lock` 不是锁整个 category loop，但它锁住每只股票的完整 pipeline，因此 fast/slow 共用一个全局执行 lane。配置里 fast 和 slow 都在 09:00 触发；若 slow 先拿锁，fast 第一只股票最多先等 900s，再跑自身 480s，首个 fast 结果可到 1,380s，即 23 分钟。若 slow backlog 仍在，第二只 fast 可能接近 46 分钟。  
Fix: 不要让 slow 与 fast 同分钟启动；更好是引入优先队列或 fast 优先 worker。成本保护应改为短临界区的预算预留/扣减，而不是用一个全局 lock 包住整段 LLM runtime。

[HIGH] File: [backend/data/analysis_scheduler.py:386](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:386)  
Confidence: High  
Issue: `480/900s` 只限制 `run_analysis()`，不覆盖锁等待、`list_stocks()`、filter、10s sleep、Mongo/Redis 持久化，也不覆盖 `wait_for` 取消尾延迟。它能限制“单股 pipeline coroutine”，但不能强制“从 cron tick 到结果”的 fast p95 ≤ 8min / slow ≤ 15min。`category is None` 或 `policy is None` 时还会走无 `wait_for` 分支。  
Fix: 明确 SLA 口径。若是端到端 SLA，把内部 timeout 下调，例如 fast 450s、slow 840s，并把排队/持久化纳入指标；legacy/no-policy 路径也应有兜底 timeout，或者启动时 policy 失败直接标记 SLA disabled。

[HIGH] File: [backend/data/analysis_scheduler.py:360](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:360)  
Confidence: Medium  
Issue: total daily cost ≤ ¥1.20 不是由 timeout 强制的。当前只在 pipeline 前读一次 budget；若 spent=¥1.19，下一次 pipeline 仍会启动并可能把总额打到 ¥1.20 以上。Redis probe 失败时还会 log 后继续执行。  
Fix: 在启动 pipeline 前做 projected/reserved cost 检查，按 bucket 配置单次预算上限；Redis 不可用时 cron 路径应 fail-closed 或降级到极低成本模式。部署侧也必须把 `QUANTMIND_DAILY_BUDGET` 设为 `1.20`。

[MEDIUM] File: [backend/data/analysis_scheduler.py:285](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:285)  
Confidence: High  
Issue: fast tick 内是顺序 per-stock，且每个间隔固定 sleep 10s。上界是 `F*480 + (F-1)*10`；2 只 fast 已是 970s，3 只是 1,460s。`list_stocks()+filter` 本身不是问题，真正的问题是 tick-level latency 随 fast 数线性增长。  
Fix: fast bucket 加 bounded concurrency，或把 fast watchlist 设硬上限并监控 tick duration。10s sleep 可只在实际 provider throttle 需要时启用，或改成 token bucket limiter。

[LOW] File: [backend/api/watchlist.py:218](/home/ps/papers/QuantMind/backend/api/watchlist.py:218)  
Confidence: High  
Issue: `POST /api/watchlist/{code}/category` 在 async route 中同步 `save_policy()`，包括 YAML dump 和 atomic replace。文件很小，低频操作下风险低，但慢磁盘/NFS 会阻塞整个 event loop。  
Fix: 用 `await asyncio.to_thread(save_policy, new_policy, policy_path)` 或 FastAPI/Starlette threadpool 包装。

[LOW] File: [backend/services/watchlist_policy.py:242](/home/ps/papers/QuantMind/backend/services/watchlist_policy.py:242)  
Confidence: High  
Issue: `overrides` 先 `in` 后索引，多一次 dict lookup；更重要的是 `default_codes` 存成 tuple，`code in policy.fast.default_codes` 不是 O(1)。当前 watchlist 小，这不是热瓶颈。  
Fix: 用 sentinel `policy.overrides.get(code, sentinel)`；若 default_codes 会增长，加载时派生 `frozenset` 做 membership。

**非阻塞观察**

`_resolve_services_and_timeout()` 每股 `model_copy()` 不是实际热点：`AnalysisServices` 字段少，浅拷贝成本相对 LLM pipeline 可忽略。可以预计算 fast/slow services，但不是当前性能优先项。