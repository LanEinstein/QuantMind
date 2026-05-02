未发现新的 CRITICAL。以下是仍需处理的 HIGH/MEDIUM。

**Findings**
1. [HIGH] File: [backend/api/watchlist.py:211](/home/ps/papers/QuantMind/backend/api/watchlist.py:211)  
Confidence: High  
Issue: `/api/watchlist/{code}/category` 仍是未鉴权的配置写接口，可直接持久化 policy 并更新 scheduler。  
Fix: 给 watchlist 写接口加 operator/admin 鉴权依赖，并补 401/403 测试；不要用 `AUTHORIZATION_MODE` 代替请求鉴权。

2. [HIGH] File: [backend/main.py:192](/home/ps/papers/QuantMind/backend/main.py:192), [backend/data/analysis_scheduler.py:181](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:181)  
Confidence: High  
Issue: malformed cron fallback 只把 `scheduler._policy` 清成 `None`，但 `app.state.watchlist_policy` 仍保留无效 policy，启动日志也会报 `fast_slow_mode=True`。之后 API 仍可更新 category，并把 scheduler 又切回一个没有 fast/slow cron job 的 policy 状态。  
Fix: `start()` 后用 `analysis_scheduler.policy` 回写 `app.state.watchlist_policy` 和日志；cron fallback 后 `/policy`/`/category` 应明确进入 legacy/503 状态，或重新注册 fast/slow cron。

3. [HIGH] File: [backend/data/analysis_scheduler.py:436](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:436), [backend/services/cost_guard.py:183](/home/ps/papers/QuantMind/backend/services/cost_guard.py:183)  
Confidence: High  
Issue: 日预算仍只检查“当前已花费是否已达 hard cap”，没有 projected/reservation；Redis probe 失败也 fail-open。因此 `spent=1.19` 时仍会启动一次完整 pipeline，不能保证 ≤ ¥1.20。  
Fix: pipeline 前做预算预留/预计单次成本检查；cron 路径 Redis 不可用时 fail-closed 或强制低成本降级。

4. [MEDIUM] File: [backend/api/watchlist.py:60](/home/ps/papers/QuantMind/backend/api/watchlist.py:60)  
Confidence: High  
Issue: docstring 说必须发送 `{"category": null}` 才清除 override，但字段 `default=None` 使 `{}` 也会通过并清除 override。  
Fix: 改成 required nullable：`category: str | None = Field(...)`，或检查 `"category" in body.model_fields_set`。

5. [MEDIUM] File: [backend/api/watchlist.py:253](/home/ps/papers/QuantMind/backend/api/watchlist.py:253), [backend/services/watchlist_policy.py:350](/home/ps/papers/QuantMind/backend/services/watchlist_policy.py:350)  
Confidence: High  
Issue: `asyncio.to_thread(save_policy, ...)` 修复了 event-loop 阻塞，但没有锁。两个并发 POST 可基于同一个旧 policy 产生 lost update；固定 `.tmp` 文件名还可能互相 rename 导致 500。  
Fix: 抽 `PolicyStore`，用 `asyncio.Lock` + `filelock` 包住 read-modify-write；临时文件用唯一名称，锁内重新读取最新 policy 后合并写入。

6. [MEDIUM] File: [backend/data/analysis_scheduler.py:318](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:318), [backend/data/analysis_scheduler.py:528](/home/ps/papers/QuantMind/backend/data/analysis_scheduler.py:528)  
Confidence: Medium  
Issue: `run_category_analysis()` 只 snapshot 了分桶匹配；实际 per-stock config/timeout 仍从当前 `self._policy` 读取。若运行中 policy 被替换，同一个 tick 的分类和 pipeline config 可来自不同 policy。  
Fix: 将 policy snapshot 传入 `_run_codes/_run_and_persist/_resolve_services_and_timeout`；同时把 `overrides` 改成不可变 mapping。

**Verified**
- R1 malformed YAML/cron fallback: partially resolved。YAML error 已包装，scheduler cron fallback 可用；但 `app.state` 状态不同步。
- R1 policy immutability/snapshot: partially resolved。frozenset 和分桶 snapshot 已加；`overrides` 仍可变，config snapshot 未传到底。
- R1 单进程假设: partially resolved。doc 写了，但未强制，API 写入仍无锁。
- R1 `pipeline` 字段未生效: partially resolved。代码 doc 标 reserved；YAML/API 仍容易误导。
- R2 invalid category envelope: resolved for invalid category；Pydantic extra/missing 类错误仍不是统一 envelope。
- R2 unknown code 404: resolved。
- R2 category logs: resolved。
- R2 save_policy 丢 YAML 注释: partially resolved，仅文档说明，未保留注释。
- R2 null discoverability: resolved，但 `{}` 清除 override 是新问题。
- R3 cron flake tests: resolved for new fast/slow cron tests；旧 `TestStartStop` 仍直接 `start()`。
- R3 API mutation real scheduler test: not addressed，仍只断言 `MagicMock.update_policy()`。
- R3 budget breach / catch-up / mixed daily / empty watchlist / save OSError tests: resolved。
- R3 loader matrix: partially resolved，新增 YAML parse/timeout；仍非完整矩阵。
- R4 global lock, end-to-end SLA, sequential fast latency: not addressed，仅文档化 caveat。
- R4 async save blocking and assign_category perf: resolved。
- R5 unauthenticated config mutation: not addressed。
- R5 mutation consistency: not addressed。
- R5 OSError path in client response: resolved；路径仍在服务端日志中。

**验证限制**
`git diff --check` 通过。pytest 未能运行：当前只读沙箱没有可用临时目录，pytest 在初始化 capture 时失败。