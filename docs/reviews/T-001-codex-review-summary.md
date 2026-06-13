# T-001 Codex 跨模型代码审查报告

**任务**: T-001 — ≥2 交易员 agent 人格卡(frozen git)+ P0-10-amendment-2026-05-24 落地
**审查时间**: 2026-06-13
**审查轮次**: 1 cycle + 1 read-only 最终复核
**最终判定**: ✅ 通过(经最终复核)

## 审查范围
- `backend/agents_team/persona_registry.py`(新)
- `config/prompts/{trader_momentum,trader_mean_reversion}/v1.yaml`(新人格卡)
- `config/prompts/traders.lock.json`(新)+ `config/live_artifacts.lock.json`(+2 PROMPT_VERSION pin)
- `tests/agents_team/test_persona_registry.py`(新)+ `tests/test_live_artifact_registry.py`(shipped pin 集断言更新)

## 发现的问题(cycle 1)— 3 × P2,全修

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P2 | persona_registry.py:343 | `_personas` 仍是可变 dict,`reg._personas.clear()` 可绕过 `__setattr__` 改变已服务的人格卡 | **FIXED**:`MappingProxyType(dict(personas))` 只读包裹(镜像 prompt/live-artifact registry);值本就 frozen Pydantic |
| 2 | P2 | persona_registry.py:225 | 人格 id 可复用 4 必经 agent 名(`fund_manager` 等),违反 additive 不可变式 | **FIXED**:`TraderPersonaLockFile._check_persona_ids` fail-closed 拒 `_MANDATORY_AGENT_NAMES` |
| 3 | P2 | persona_registry.py:430 | lock entry `path` 目录 / 卡体 `version` 与 persona_id/active_version 不校验,可服务错卡 | **FIXED**:`from_lockfile` 强制 `entry.path == config/prompts/{persona_id}/{active_version}.yaml` + 卡体 `version` == active_version,否则 fail-closed |

## 最终复核(read-only,codex exec)
- 三项全部 **RESOLVED**(`reg._personas.clear()` → AttributeError;4 必经名全拒;path/version 双校验生效)。
- 新增 P1 回归:**NONE**。

## 门禁
- `ruff` ✅ / `mypy --strict`(模块)✅ / 新增 4 对抗测试 → 模块 **23 passed**。
- `scripts/redline-check.sh` 全绿(agents_team 仍仅作纯节点,无 LLM 决策泄漏)。

## 守住安全地基
人格卡 frozen + git 版本化 + SHA256 pin + LiveArtifactRegistry(PROMPT_VERSION)认证 + restart-gated(无 hot-reload);≥2 交易员 additive,**不削减 4 必经 agent**;人格卡内嵌红线(交易员永不倡议方向 / 永不输出 volume·price)。
