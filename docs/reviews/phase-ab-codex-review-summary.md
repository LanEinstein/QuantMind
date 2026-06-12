# Phase AB(客观自进化)codex review 摘要 — 2026-06-12

> 范围:AB-001..AB-008 全套未提交 diff(8 新模块 + GEPA lint 接线 + cost_guard 进化 sub-budget + main.py boot 消费/22:00 cron 接线 + deploy 脚本 + 9 测试文件)。
> 调用:`codex review --uncommitted`(1 cycle)。
> 结论:**1 P1 + 3 P2,全部修复 + 回归测试;0 P0。**

## Findings 与修复

| # | 级别 | 位置 | 问题 | 修复 |
|---|------|------|------|------|
| 1 | **P1** | `activation.py` `apply_pending_activation` | sim 模式 staged 的 `next_boot.lock` 在重启前 owner 把 `FEISHU_INTERACTIVE_ENABLED` 翻 true 时,boot 仍会应用 → **sim 域晋升在人工 gate 域改写 live lockfile** | apply 时**复查模式**:feishu 模式 → staged 文件隔离为 `.frozen`(owner triage)+ 新状态 `FROZEN_MODE_SWITCH`,live lockfile 不碰。回归:`test_mode_flip_at_boot_freezes_staged_lock` |
| 2 | P2 | `activation.py` | param-only manifest 校验通过并报 APPLIED,但 lockfile 只写 approved pins → **白名单参数晋升静默 no-op** | runtime 参数落地路径(AB harness)存在前**双层拒绝**:staging 时 raise(param-bearing manifest 不可 stage)+ apply 时手工构造的 staged 文件隔离 `.bad`。回归 2 条 |
| 3 | P2 | `backend/main.py` boot 映射 | 非 APPLIED 一律映射 `ROLLED_BACK`,但 intent 仍是 PENDING(allowlist 只许 ACTIVATED→ROLLED_BACK)→ record_status raise 被吞,失败激活**卡死 PENDING** | 新纯函数 `intent_status_for_activation`:APPLIED→ACTIVATED / ROLLED_BACK+CORRUPT→CANCELLED / FROZEN_MODE_SWITCH→FROZEN / NOOP→None;回归断言每个映射都在 PENDING 出边 allowlist 内 |
| 4 | P2 | `backend/main.py` + `scheduler.py` | dispatcher 未挂时回调写 DEGRADED 后正常 return → scheduler 又补一条 **SUCCESS**(审计显示成功但实际跳过) | 回调改返回 skip 信号字符串(`skipped_dispatcher_unwired`/`skipped_budget`/`skipped_no_redis`);`run_evolution_shadow` 收到 str → 审计 DEGRADED(reason `evolution_shadow_run_skipped`),None 保持原 SUCCESS 语义(向后兼容)。回归 2 条 |

## 门禁状态(修复后)
- tests/strategy_evolution 177 passed + cost_guard_evolution 6 + 全量 pytest(见 SESSION_LOG)
- ruff / redline(含新 `[AB-008]`,`[R-002]` grep 放宽到包级、import 级仍由 AST 钉死)/ mypy(strategy_evolution 13 文件)全绿
- 红线自查:7 禁其余 6 条不动;sim/live 域边界三重守门(intent 创建 + staging + **boot apply** 全部模式门);冻结集对抗全绿;零 git/subprocess;LLM 零参与判定。
