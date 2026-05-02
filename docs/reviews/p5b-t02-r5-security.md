## 总结表

| 结论 | 严重度 | 位置 | Confidence | 摘要 |
|---|---:|---|---:|---|
| Finding 1 | HIGH | [watchlist.py:193](/home/ps/papers/QuantMind/backend/api/watchlist.py:193) | High | `/category` 是未鉴权的配置写接口 |
| Finding 2 | MEDIUM | [watchlist.py:218](/home/ps/papers/QuantMind/backend/api/watchlist.py:218) | Medium | policy 写盘和内存更新缺少一致性边界 |
| Finding 3 | LOW | [watchlist.py:226](/home/ps/papers/QuantMind/backend/api/watchlist.py:226) | High | 错误响应和日志泄露 policy 文件路径 |
| 已确认 | PASS | [watchlist_policy.py:180](/home/ps/papers/QuantMind/backend/services/watchlist_policy.py:180) | High | 新代码 YAML load/save 安全 |
| 已确认 | PASS | `backend/risk/` | High | 未发现风险引擎隔离红线违规 |

## Findings

### [HIGH] File: [backend/api/watchlist.py:193](/home/ps/papers/QuantMind/backend/api/watchlist.py:193)  
Confidence: High  
Issue: `POST /api/watchlist/{code}/category` 没有任何请求级鉴权依赖，直接进入 `save_policy()` 并更新 scheduler。`backend/services/authorization.py` 只管 Phase/Auth mode 生命周期：启动断言和 `/api/risk/auth-mode` 跨阶段切换校验，并不是 API 调用者鉴权。  
这不等于突破 `AUTHORIZATION_MODE=suggest` 的交易红线，因为分类变更不下单；但它是 privileged config mutation，会改变后续分析频率、pipeline 成本和调度行为。若服务暴露到非可信网络，任意请求可把股票 pin 到 `fast`，放大 LLM 成本和运行负载。

Fix: 给所有 watchlist 写接口加 operator/admin 级鉴权依赖，例如 `Depends(require_operator_write)`，并加 401/403 测试。不要要求 `confirm`/`auto` 才允许分类变更；Phase 5B 应允许在 `suggest` 下由已授权操作员修改分类。

### [MEDIUM] File: [backend/api/watchlist.py:218](/home/ps/papers/QuantMind/backend/api/watchlist.py:218), [backend/api/watchlist.py:228](/home/ps/papers/QuantMind/backend/api/watchlist.py:228), [backend/services/watchlist_policy.py:325](/home/ps/papers/QuantMind/backend/services/watchlist_policy.py:325)  
Confidence: Medium  
Issue: policy mutation 没有统一锁或事务边界。单进程单 event loop 下没有明显 `await` TOCTOU 间隙，但多 worker/多进程时会出现 lost update：每个进程有自己的 `app.state.watchlist_policy` 和 scheduler，POST 只更新命中的那个进程。`save_policy()` 还使用固定 `.tmp` 文件名，并发跨进程写同一路径可能互相覆盖或导致 `FileNotFoundError`。另外，写盘成功后若 `scheduler.update_policy()` 抛错，磁盘和 `app.state` 已变，scheduler 可能仍旧。

Fix: Phase 5 若坚持单实例，显式强制/文档化 `uvicorn --workers 1`。更稳妥的修复是抽 `PolicyStore`：用 `asyncio.Lock` + 文件锁包住 read-modify-write，使用唯一临时文件后 atomic replace，并通过 Redis/pubsub 或 reload hook 通知所有进程更新 scheduler。

### [LOW] File: [backend/api/watchlist.py:226](/home/ps/papers/QuantMind/backend/api/watchlist.py:226)  
Confidence: High  
Issue: 失败响应把 `OSError` 原文返回给客户端，通常会包含绝对路径。成功/失败日志也记录 `path=policy_path`：见 [watchlist.py:223](/home/ps/papers/QuantMind/backend/api/watchlist.py:223)、[watchlist.py:237](/home/ps/papers/QuantMind/backend/api/watchlist.py:237)。这会泄露 `QUANTMIND_WATCHLIST_POLICY_PATH` 的实际值或部署目录结构。

Fix: 客户端只返回通用错误，例如 `Failed to persist watchlist policy`；日志中默认记录逻辑配置名或相对路径，绝对路径降到 debug 或仅本地安全日志。

## 已核验项

- Authorization mode: 当前实现没有把 category mutation 接到 `authorization.py`，但该模块本身也不是请求鉴权网关。分类变更应允许在 `AUTHORIZATION_MODE=suggest` 下执行，但必须是已授权 operator 操作。
- Risk isolation: `rg` 未发现 `backend/risk/` import `backend.llm` / `backend.agents` / `backend.mirofish`。新增 `backend/services/watchlist_policy.py` 也没有引入这些模块。
- Path traversal: API 用户可控数据只有 `{code}` 和 `category`。`code` 经 6 位数字正则校验，`category` 经 Pydantic `Literal["fast","slow"] | None` 校验；它们只写入 YAML `overrides`，不参与路径拼接。路径来自环境变量，不是请求输入。
- YAML safety: 新代码使用 `yaml.safe_load` 和 `yaml.safe_dump`，`save_policy()` payload 也是 dict/list/str/int 基本类型，不会发出 `!!python/object`。全仓没有 PyYAML `yaml.load(`；但历史代码仍有 `backend/api/risk.py:357` 的 `yaml.dump`，不属于本轮新增问题。
- Input validation: `/category` 边界输入基本足够。可选增强是 `SetCategoryRequest` 设置 `extra="forbid"`，避免客户端误以为额外字段生效。