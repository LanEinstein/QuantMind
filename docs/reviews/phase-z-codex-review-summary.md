# Phase Z 双线前端 — Codex Review Summary (2026-06-12)

> 任务:Z-002 / Z-003 / Z-004 / Z-005(Z-001 为纯 docs reconcile,无代码,豁免 codex)。
> 命令:`codex review --uncommitted`(1 cycle,覆盖整个 Phase Z 未提交 diff)。
> 门禁:CLAUDE.md §3 —— 有代码编写的任务 commit 前跑 codex,修完 P0/P1/P2。

## 范围
单次 codex 审查覆盖 Phase Z 全部 4 个只读 viz 任务的代码(4 新 GET 端点 + 5 前端组件/面板 + `RotationRunner.max_total_positions` 只读属性 + main.py 4 路由接线 + 全部测试)。一次审查覆盖整组独立 read-only 面板以节省预算,审后按需修复再提交(与 #20/#74 跨任务耦合一次审查的先例一致)。

## 结论
- **本地预门禁全绿**(codex 复跑确认):28 新后端测试 + 156 vitest + ruff + redline + type-check + build;**仅 2 写端点不变**(全新端点皆 GET-only,AST 守门);新模块零 `backend.{llm,agents,risk,broker,data}` import(AST 隔离测试)。
- **codex findings:1 个 P2(0 P0 / 0 P1)。已修 + 加回归测试。**

## Findings

### [P2] RESOLVED/EXPIRED 轮动事件丢失 sell→challenger 腿 — `backend/api/slot_rotation.py`
- **问题**:`RotationIntentStore` 在 RESOLVED/EXPIRED 终态事件上**不再内嵌** `intent`(只留 `intent_id`),`_serialize_event` 原写法 `event.intent.incumbent_code if event.intent else None` 在轮动正常完成/到期后会把 `incumbent_code`/`challenger_code` 序列化成 `null` → Z-004 事件表对已完成轮动渲染成 `—`,丢失"卖谁→换谁"语境(正常终态场景下面板不准确)。
- **修复**:在 `get_slot_rotation` 内从**全量** `load_events()` 构建 `intent_id → (incumbent, challenger)` 映射(全量而非窗口,因 PROPOSED 可能早于事件窗口),终态事件按 `intent_id` 折回原 PROPOSED 腿。`_serialize_event(event, legs)` 优先用自带 `intent`,否则查映射。
- **回归测试**:`test_resolved_intent_leaves_open_set_but_stays_in_events` 追加断言 —— RESOLVED 事件 `incumbent_code=="600000"` / `challenger_code=="000002"`(折回成功)。
- 修复后 `tests/test_api_slot_rotation.py` 9 passed / ruff + mypy clean。

## 安全地基复核(本 Phase 一条未破)
仅 2 写端点(`POST /api/execution-reports` + `POST /api/reconciliation-tickets/{id}/decide`)不变;新增端点全 GET、全 display-only、127.0.0.1;不扩 WS 类(轮询);LLM 不进任何读路径;量化仍资格权威(主题链路 display-only 永不剪 universe / 否决板块);thesis 失效阈值确定性、LLM 文本永不影响阈值。
