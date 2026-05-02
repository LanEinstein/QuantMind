**测试审查结论**

未运行测试；本轮是静态审查。重点问题如下。

| # | 严重度 | 文件:行 | 结论 |
|---|---|---|---|
| 1 | HIGH | [tests/test_analysis_scheduler_fast_slow.py:382](/home/ps/papers/QuantMind/tests/test_analysis_scheduler_fast_slow.py:382) | `start()` 测试时间相关且可能触发未受控 catch-up |
| 2 | HIGH | [tests/test_api_watchlist_category.py:61](/home/ps/papers/QuantMind/tests/test_api_watchlist_category.py:61) | API mutation 未真实验证“下一次 cron tick 读取新 policy” |
| 3 | HIGH | [backend/data/analysis_scheduler.py:396](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:396) | 缺少 fast/slow 与 hard-cap budget 的交互测试 |
| 4 | HIGH | [backend/data/analysis_scheduler.py:619](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:619) | 缺少 policy 下 missed slow run 的 catch-up 测试 |
| 5 | MEDIUM | [tests/test_analysis_scheduler_fast_slow.py:333](/home/ps/papers/QuantMind/tests/test_analysis_scheduler_fast_slow.py:333) | timeout 测试只测异常分支，不足以证明 SLA enforcement |
| 6 | MEDIUM | [tests/test_api_watchlist_category.py:63](/home/ps/papers/QuantMind/tests/test_api_watchlist_category.py:63) | 全局 `app.state` fixture 无 teardown，存在跨文件污染 |
| 7 | MEDIUM | [backend/data/analysis_scheduler.py:218](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:218) | `run_daily_analysis()` + policy 的 mixed fast/slow 手动扫表未覆盖 |
| 8 | MEDIUM | [backend/data/analysis_scheduler.py:135](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:135) | 无效 cron 回退 legacy 的新分支未覆盖 |
| 9 | MEDIUM | [backend/api/watchlist.py:217](/home/ps/papers/QuantMind/backend/api/watchlist.py:217) | `save_policy()` 失败路径未测试 |
| 10 | LOW | [backend/services/watchlist_policy.py:94](/home/ps/papers/QuantMind/backend/services/watchlist_policy.py:94) | policy loader 校验矩阵仍有不少未覆盖分支 |
| 11 | LOW | [backend/data/analysis_scheduler.py:261](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:261) | empty watchlist with policy 未直接覆盖 |

**详细 Findings**

[HIGH] File: [tests/test_analysis_scheduler_fast_slow.py:382](/home/ps/papers/QuantMind/tests/test_analysis_scheduler_fast_slow.py:382)  
Confidence: High  
Issue: `test_start_with_policy_registers_two_jobs` / `test_start_without_policy_keeps_legacy_job` 调用真实 `start()`，但没有 freeze 时间，也没有 stub `_compute_catch_up_targets()`。`start()` 会在 [analysis_scheduler.py:164](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:164) 探测 catch-up；在工作日 09:45 后可能创建 `_run_catch_up()` 背景任务。当前 `mongodb` mock 也没有真实 `query_signals_for_trade_date` 返回 list，测试有日期相关 flake 风险。  
Fix: 在 cron registration 测试里 patch `_compute_catch_up_targets` 返回 `[]`，或显式配置 catch-up mocks 并 patch/await `asyncio.create_task`。

[HIGH] File: [tests/test_api_watchlist_category.py:61](/home/ps/papers/QuantMind/tests/test_api_watchlist_category.py:61)  
Confidence: High  
Issue: API 测试把 scheduler 做成 `MagicMock`，只断言 `update_policy()` 被调用，未验证下一次 `run_category_analysis()` 真的基于新 policy 分桶。核心生产路径在 [watchlist.py:228](/home/ps/papers/QuantMind/backend/api/watchlist.py:228) 和 [analysis_scheduler.py:254](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:254)。  
Fix: 用真实 `AnalysisScheduler` 作为 `app.state.analysis_scheduler`，POST 修改 override 后调用 `run_category_analysis("fast")`，patch `run_analysis` 并断言刚修改的 code 进入 fast bucket。

[HIGH] File: [backend/data/analysis_scheduler.py:396](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:396)  
Confidence: High  
Issue: 现有 budget 测试是 legacy/no-policy；fast/slow 路径下 hard-cap 是否先于 `asyncio.wait_for` 短路、是否不构造 category timeout pipeline，没有覆盖。  
Fix: 增加 policy scheduler + redis mock，patch `assert_budget_allows` 抛 `DailyBudgetExceededError`，调用 `_run_and_persist("600519", "fast")` 或 `run_category_analysis("fast")`，断言 `run_analysis` / `wait_for` 未调用，只写 `cost_ceiling_breached` record。

[HIGH] File: [backend/data/analysis_scheduler.py:619](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:619)  
Confidence: High  
Issue: catch-up 旧测试只覆盖 `_compute_catch_up_targets()`，没有覆盖 policy 下 `_run_catch_up()` 会把 missed slow code 用 slow config/timeout 执行。  
Fix: 构造 policy，调用 `_run_catch_up(["000858"])`，capture `run_analysis` 的 `services.pipeline_config`，断言 `max_debate_rounds == 2` 且 timeout 为 slow bucket。

[MEDIUM] File: [tests/test_analysis_scheduler_fast_slow.py:333](/home/ps/papers/QuantMind/tests/test_analysis_scheduler_fast_slow.py:333)  
Confidence: High  
Issue: timeout 测试把 `asyncio.wait_for` 替换成立即抛 `TimeoutError` 的 fake，只证明 except 分支会落库；没有证明生产代码用正确 timeout 包住 coroutine，也没有验证真实取消行为。  
Fix: 至少让 fake 记录 `timeout == 480`；更好是用 spy 包真实 `asyncio.wait_for`，或用很小测试 policy timeout 做一次真实超时测试。

[MEDIUM] File: [tests/test_api_watchlist_category.py:63](/home/ps/papers/QuantMind/tests/test_api_watchlist_category.py:63)  
Confidence: Medium  
Issue: API 测试直接改全局 `backend.main.app.state`，fixture 没有恢复。函数内会重设本文件需要的字段，所以“别的测试污染这些测试”的风险较低；但这些测试会污染后续文件，尤其 [test_503_when_policy_not_loaded](/home/ps/papers/QuantMind/tests/test_api_watchlist_category.py:97) 会留下 `watchlist_policy = None` 直到下一次 fixture。  
Fix: 增加 autouse fixture snapshot/restore `app.state._state`，或使用 app factory 创建隔离 app。

[MEDIUM] File: [backend/data/analysis_scheduler.py:218](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:218)  
Confidence: High  
Issue: `run_daily_analysis()` 在 policy loaded 时应对每个股票动态 resolve fast/slow；新增测试只覆盖 category cron 和 single analysis，没有覆盖手动全量分析 mixed bucket。  
Fix: `scheduler_with_policy.run_daily_analysis()` capture 三个 code 的 config，断言 fast code 用 1/480，slow/default/override 用 2/900。

[MEDIUM] File: [backend/data/analysis_scheduler.py:135](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:135)  
Confidence: High  
Issue: invalid cron fallback 到 legacy 的新分支未测试。  
Fix: 构造 malformed `policy.fast.cron`，调用 `start()`，断言只存在 `daily_analysis` job、`fast_analysis/slow_analysis` 不存在、`scheduler.policy is None`。

[MEDIUM] File: [backend/api/watchlist.py:217](/home/ps/papers/QuantMind/backend/api/watchlist.py:217)  
Confidence: High  
Issue: `save_policy()` 抛 `OSError` 时应返回 500 且不更新 `app.state.watchlist_policy` / scheduler；当前 API 测试未覆盖。  
Fix: patch `backend.api.watchlist.save_policy` 抛 `OSError`，断言 response 500、旧 policy 保持、`scheduler.update_policy` 未调用。

[LOW] File: [backend/services/watchlist_policy.py:94](/home/ps/papers/QuantMind/backend/services/watchlist_policy.py:94)  
Confidence: High  
Issue: loader 分支仍缺少 root 非 mapping、YAML parse error、bucket 非 mapping、missing required key、`max_debate_rounds` 非 int/负数、`default_codes` 非 list、`overrides` 非 mapping、invalid/default missing category、`policy_version` 非 int、`cron_for`/`bucket_for` 等测试。  
Fix: 用 parameterized invalid YAML 覆盖校验矩阵。

[LOW] File: [backend/data/analysis_scheduler.py:261](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:261)  
Confidence: High  
Issue: empty watchlist with policy 未直接测试；现有 no-match 测试是“非空但不匹配”。  
Fix: 增加 `policy != None` 且 `list_stocks.return_value = []` 的 scheduler/API 测试，断言返回 `[]` / assignments `{}` 且不调用 pipeline。

**Mock 与 Env 结论**

`mongodb.save_analysis_record` / `save_signal` 返回 `str`，当前 mock 返回 `"rec_id"` / `"sig_id"`，类型匹配。`watchlist.list_stocks()` 的返回形状也基本匹配真实服务。

`QUANTMIND_WATCHLIST_POLICY_PATH` 使用 `monkeypatch.setenv`，函数级 fixture 会自动恢复，环境变量本身不泄漏。真正的问题是全局 `app.state` 没有 teardown。