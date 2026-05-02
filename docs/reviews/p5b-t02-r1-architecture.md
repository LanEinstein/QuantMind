**问题**

### [HIGH] File: [analysis_scheduler.py](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:172), [main.py](/home/ps/papers/QuantMind/backend/main.py:181)
Confidence: High  
Issue: malformed policy 仍可能让启动失败。`load_policy()` 不验证 cron；非法 cron 会在 `CronTrigger.from_crontab()` 抛 `ValueError`，且 `main` 只包住了 `load_policy()`，没有包住 `analysis_scheduler.start()`。另外 YAML 语法错误会从 `yaml.safe_load()` 抛 `yaml.YAMLError`，也不在当前 fallback 捕获范围内。  
Fix: 在 loader 中把 YAML 解析错误包装成 `WatchlistPolicyError`；在 scheduler 注册 cron 时捕获 `ValueError` 并让 `main` fallback 到 `policy=None` legacy scheduler，或在 `start()` 内直接降级到 legacy 单 cron。

### [MEDIUM] File: [watchlist_policy.py](/home/ps/papers/QuantMind/backend/services/watchlist_policy.py:73), [analysis_scheduler.py](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:93)
Confidence: Medium  
Issue: `WatchlistPolicy` 标称 immutable，但 `overrides` 是可变 `dict`。`update_policy()` 只是无锁替换引用，当前单 event loop 下引用赋值本身不会 torn write，但 `_run_lock` 并不保护 policy 读写，也不保证一次 cron job 使用同一份 policy snapshot。未来如果 runtime 更新 bucket 配置，job 内不同股票可能用到不同配置。  
Fix: 把 `overrides` 改成不可变 mapping/tuple，并在 `run_category_analysis()` 开头 snapshot `policy = self._policy`，后续分类和 `_resolve_services_and_timeout()` 都显式传这个 snapshot。若以后允许并发 policy 写入，抽一个 `PolicyStore` 加 `asyncio.Lock`。

### [MEDIUM] File: [main.py](/home/ps/papers/QuantMind/backend/main.py:194), [watchlist.py](/home/ps/papers/QuantMind/backend/api/watchlist.py:228)
Confidence: High  
Issue: 单进程假设对 runtime policy 更新不够显式。多 worker/多进程时，每个进程都会创建自己的 APScheduler；POST category 只更新当前进程的 `app.state` 和 scheduler，其他进程继续用旧 policy，且 YAML 写入也没有跨进程锁。  
Fix: 在 Phase 5B eval 部署文档和启动日志中明确 `WEB_CONCURRENCY=1`/单 scheduler leader；若要支持多进程，需要 Redis/file lock、leader election、集中式 policy store，或广播 reload。

### [LOW] File: [watchlist_policy.py](/home/ps/papers/QuantMind/backend/services/watchlist_policy.py:56), [analysis_scheduler.py](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:456)
Confidence: Medium  
Issue: policy 暴露并持久化 `pipeline` 字段，但 scheduler 实际只使用 `max_debate_rounds` 和 `pipeline_timeout_seconds`。这会让配置/API 看起来支持 fast/slow pipeline 选择，实际无效。  
Fix: 要么把 `pipeline` 明确标成 reserved/comment-only，要么把它接入真实 pipeline/routing 选择逻辑。

**已核对**

`policy=None` 的核心 legacy 行为保留：构造函数默认不要求调用方改动，仍注册单个 09:45 `daily_analysis` job，`_resolve_services_and_timeout()` 返回原 `self._services` 且 `timeout=None`，不会包 `asyncio.wait_for`。`AnalysisServices.model_copy(update={"pipeline_config": new_config})` 是 Pydantic v2 frozen model 的正确 clone idiom；这里 update 值已是 `PipelineConfig`，浅拷贝共享底层服务对象也符合预期。

**Summary**

| Severity | File:line | Confidence | Issue | Fix |
|---|---|---:|---|---|
| HIGH | [analysis_scheduler.py:172](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:172), [main.py:181](/home/ps/papers/QuantMind/backend/main.py:181) | High | malformed YAML/cron 仍可 crash startup | 包装解析/cron 错误并 fallback legacy |
| MEDIUM | [watchlist_policy.py:73](/home/ps/papers/QuantMind/backend/services/watchlist_policy.py:73), [analysis_scheduler.py:93](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:93) | Medium | policy 不是真不可变，job 无 snapshot | immutable overrides + per-job policy snapshot |
| MEDIUM | [main.py:194](/home/ps/papers/QuantMind/backend/main.py:194), [watchlist.py:228](/home/ps/papers/QuantMind/backend/api/watchlist.py:228) | High | 多进程下 scheduler/policy 更新不一致 | 明确单进程或加分布式锁/leader |
| LOW | [watchlist_policy.py:56](/home/ps/papers/QuantMind/backend/services/watchlist_policy.py:56), [analysis_scheduler.py:456](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:456) | Medium | `pipeline` 配置字段未生效 | 标 reserved 或接入实际 pipeline 选择 |