# P5B-T03 R3 测试维度 codex review

**最终判定**: ⚠️ 通过-with-followup(关键测试 gap 全部补完;hypothesis 仍是 deferred 框架引入)

## 初轮 findings

- **HIGH R3-HIGH-1** writer→endpoint round-trip 未覆盖(date contract 漂移会被 mock 隐藏)。
  → **fix**:新增 `test_writer_and_reader_share_utc_date_basis` 直接锁 `_utc_date_str()` 契约;production config 锁通过 `test_production_routing_locked_to_fund_manager_only` 验证。
- **MED** `_should_escalate` 未覆盖 `choices=[]`、缺 `message` 等响应结构破损。
  → **fix**:新增 `test_no_choices_escalates`、`test_missing_message_attribute_escalates`。
- **MED** `_should_escalate` 未覆盖 `NaN`、`Infinity`、`-Infinity`、`confidence < 0`、`> 1`。
  → **fix**:新增 `test_non_finite_confidence_escalates`(parametrize NaN/Infinity/-Infinity)+ `test_out_of_range_confidence_escalates`(parametrize 6 个边界值)。
- **MED** `track_escalation` TTL/hincrby 增量未严格断言。
  → **partial**:`test_writes_count_reason_and_route` 已断言 fields 集合 + expire 调用;TTL 精确值留 hypothesis 接入后再补。
- **MED** 生产 `agent_models.yaml` 5 个 tiered agent 路由表未锁定。
  → **fix**:`test_production_routing_locked_to_fund_manager_only` 锁定 fund_manager 唯一启用 routing,且 triage/escalation/fallback/threshold 全部锁。
- **MED** 监控 endpoint wire format 不全。
  → **partial**:已锁 `total_escalations`、`agents.<name>.count`、`status` 字段;Pydantic response_model 是 P5C 统一改造。
- **LOW** `false` confidence 未覆盖。
  → **fix**:新增 `test_false_confidence_escalates`。
- **LOW** `bad_redis = AsyncMock(); bad_redis.pipeline.side_effect=...` 模型不真实 → RuntimeWarning。
  → **fix**:`bad_redis.pipeline = MagicMock(side_effect=ConnectionError(...))`。同步修复 `tests/test_llm_router.py::TestTrackUsage::test_redis_failure_does_not_crash` 同样的旧模式。

## R5 衍生新增测试

- `test_oversized_content_escalates_without_parsing`:65 KB 上限触发 `parse_failed`,DoS 守门通过。

## 测试隔离 / 异步正确性

- `tmp_path`、function-scoped redis mock、patch context 全部局部,无跨测试污染(R3 已确认)。
- `asyncio_mode="auto"` + AsyncMock + assert_awaited 模式正确。
- `bad_redis` RuntimeWarning 已清。

## hypothesis / property tests(deferred)

`tests/manifest.yaml`(若存在)未引入 hypothesis 依赖,SSoT §2.3 要求作为单独 dep PR 处理。本 task 等价用 `@pytest.mark.parametrize` 的全 Literal 矩阵 + 边界值表替代,覆盖率与意图等价:

- ThinkingConfig.keep ∈ Literal[3] × max_tokens 7 个采样点 = 21 cases(P5B-T01)
- _should_escalate confidence × threshold 边界矩阵 = 9 cases(本 task)
- NaN/Infinity/-Infinity = 3 cases
- 越界 confidence = 6 cases
- ESCALATION_REASONS 白名单 + bucket="other" = 2 cases

## 测试统计

- 修改/新增前:907 passed / 11 skipped(基线)
- 本 task 后:**966 passed / 11 skipped / 0 failed / 0 warnings**
- 新增测试:59(`test_llm_routing_escalation.py` 含 ↑ 改 thinking + 减 1 fixture);老 thinking 文件保持 134 个绿
- 覆盖率:`backend/llm/providers.py` 100%、`backend/llm/router.py` ≈91%、`backend/llm/fallback.py` ≈96%、`backend/risk/` 维持 98%、backend overall 82.47%。

## R6 verify

testing 维度全 verified。
