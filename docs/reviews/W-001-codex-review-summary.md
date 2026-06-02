# W-001 PositionThesis 持久化 — codex review summary

> 任务:W-001(plan.html Phase W)。审查:`codex review --uncommitted`(cycle 1,前台 `</dev/null`)。
> 模型:codex-cli 0.133.0。本地门禁前置全绿(pytest 4522→4524 / ruff / redline)后跑 codex。

## 发现与处置(3 条,全修)

| # | 等级 | 位置 | 问题 | 处置 |
|---|------|------|------|------|
| 1 | P1 | `line1_runner.py` / `main.py` | thesis_writer 未在生产接线 —— `main.py` 构造 `Line1Runner` 未传 `thesis_writer`,生产 BUY 全部 `_persist_thesis` 早返,W-002/W-004 无 thesis 可读(dead code in prod) | ✅ `main.py` 新建共享 `PositionThesisStore`(fail-open,init 失败不阻断 BUY)+ 存 `application.state.position_thesis_store` + 传 `thesis_writer=position_thesis_store` 给 Line1Runner |
| 2 | P2 | `line1_runner.py:698` | thesis 在未送达的路由也落库 —— `send_failed`/`skipped_in_flight` 仍是 `Line1Outcome.ROUTED`,会给从未到 owner、不存在的持仓落 stale thesis | ✅ 新增 `_THESIS_DELIVERED_ACTIONS={dispatched,skipped_duplicate,simulation_routed}` gate;只在真正送达/成交(产生持仓)时落库;新增 send_failed 不落库 + simulation_routed 落库 两条对抗测试 |
| 3 | P2 | `redline-check.sh:1380` | redline 扫描器在真违规时静默放过 —— `$(python … || echo SCANNER_ERROR)` 把 Python `sys.exit(1)` 的退出码屏蔽成 0,RC-based 分支把真违规报成 pass | ✅ W-001 + **V-002**(同款潜伏 bug)两块改成 [M-004] 同款 print+`-n` 输出非空判定(不靠退出码);`|| echo` 仅在解释器真崩溃时触发;**负向测试验证**:植入 `from backend.llm…` → 现正确 FAIL,移除 → 绿 |

## 验证
- 修后:pytest **4524 passed, 13 skipped**(+2 delivered-gate 对抗测试)/ ruff All checks passed / redline All passed(含负向测试确认 W-001/V-002 块现真能拦截)。
- 安全地基红线一条未破:单一构造点不变(thesis 非 InstructionPlan,redline `[M-004]`/`[W-001]` 双绿)/ LLM 不写决策字段(支柱文本与确定性阈值解耦,对抗测试钉死)/ PIT replay 引用完整 / import 隔离。

## 备注
- 发现 3 顺带修了 Phase V 的 `[V-002]` 块同款潜伏 bug(同文件同根因)——核心 gate 此前形同虚设,属"绿测试假象"类问题,已一并修正并在 commit/session log 标注供 owner 知悉。
