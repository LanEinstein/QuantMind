# T-002 Codex 跨模型代码审查报告

**任务**: T-002 — 交易员接辩论(建议 → builder 确定性派生 size/price)
**审查时间**: 2026-06-13
**审查轮次**: 1 cycle + 1 read-only 最终复核
**最终判定**: ✅ 通过(经最终复核)

## 审查范围
- `backend/agents_team/agents.py`(`traders_node` + 人格 system prompt + fund_manager 上下文纳入交易员建议)
- `backend/agents_team/state.py`(`trader_advice` 字段 + `TeamContext.trader_personas`)
- `backend/agents_team/graph.py`(debate→traders→fund_manager 拓扑 + 成本预留 ¥1.0→¥1.5)
- `backend/orchestration/line1_runner.py`(`trader_personas` 注入,镜像 O-004 off_market)
- `backend/main.py`(启动期加载 TraderPersonaRegistry + 注入)
- `config/agent_models.yaml`(2 交易员路由:momentum=deepseek-v4-pro / mean_reversion=qwen3.6-plus)
- tests:`test_traders.py`(新)+ 模块拓扑契约 + **单一构造点对抗**(`test_instruction_plan_builder_assemble.py`)+ 路由表

## 发现的问题(cycle 1)— 1 × P2,已修

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P2 | main.py:1303 | 交易员人格 registry 加载用 fail-**OPEN**(broad except → trader_personas=()),任何治理失败(缺/drift/未批准/<2)被静默吞,禁用交易员路径并隐藏治理错误 | **FIXED**:改 fail-**CLOSED**(直接 from_lockfile,失败即 raise 中止 boot),与 PromptRegistry/LiveArtifactRegistry/secrets_validator 一致;禁用交易员需改 lock(amendment),非静默 runtime 降级 |

> mypy 噪声(agents.py `-> dict` / graph.py BaseCheckpointSaver / line1_runner:863/1248)为**既存基线**(非本次改动行),项目后端门禁为 pytest+ruff+redline,mypy-strict 仅对新纯模块强制(persona_registry 已 strict-clean)。

## 最终复核(read-only,codex exec)
- P2 修复 **RESOLVED**:broad except 已去除,失败 fail-closed 传播;实测 shipped lock 两卡哈希匹配且均 PROMPT_VERSION 已批准,boot 成功加载 2 人格。
- 新增 P1 回归:**NONE**。

## 门禁
- `ruff` ✅ / 主 import smoke ✅ / boot-path 测试(smoke_cold_start + e2e_production_path)43 passed ✅。
- agents_team + builder assemble + router thinking + orchestration:**439 passed** + Line1Runner 全 91 passed;`redline-check.sh` 全绿。

## 守住安全地基
交易员 = **advisory 文本 only**(`traders_node` 仅写 `trader_advice`,对抗测试钉死键集);**fund_manager 仍唯一 BUY/SELL/HOLD 倡议者**;**InstructionPlan 单一构造点不破**(对抗测试:proposal_text 含「买入5000股 限价99.99」→ 派生 volume==200≠5000、limit==4.5≠99.99);拓扑 traders 永不喂 tool 节点;人格加载 fail-closed。
