# Codex Review — 开发侧 readiness 收口(2026-06-14)

**范围**:`tests/conftest.py`(代码)+ `docs/runbook/i-002-production-runbook.md`
(docs 漂移修订)+ `docs/runbook/overall-testing-go-no-go-checklist.md`(新 docs)。
**模式**:`codex review --uncommitted`,1 cycle。**codex CLI** v0.137.0,本地直跑
(skill wrapper 脚本不在仓内 → 直调 `codex review`)。

## 判定

✅ **通过(P2 已修)**。codex 明确:**测试隔离代码改动(conftest.py autouse
fixture)safe**。唯一发现是 P2 docs 陈旧项,已修正。

## 发现与处理

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P2 | `overall-testing-go-no-go-checklist.md:51-55` | U-E5 状态陈旧:照搬 plan.html `U-E5` 任务旧 notes(#50,2026-05-27)写 cond3/cond4 仍 false、要 owner 再发一笔 BUY。但 `config/pilot_readiness.yaml` 已签 cond3(2026-05-28)/cond4(2026-05-29),且 CLAUDE.md 记 **MVP 2026-06-01 真启上线(首笔真实 Line-1 BUY 经飞书全闭环)**。按陈旧文档会让 owner 重复一次已签的 owner-gated 真发流程。 | **FIXED**:重写 §2.1 + §4 —— U-E5 闭环已演示、PILOT cond 全签、系统 2026-06-04 起 owner 主动停机;重启后为「在 O/T 新代码上的回归复验」而非重发;加显式警示别按旧 plan.html notes 行事;建议把 `U-E5` 收为 `done` 并刷新 plan.html notes(下次 SSoT 记账)。 |

## 根因备注

P2 暴露了一个真实陷阱:**plan.html 的 `TASKS` 条目(status/notes)会滞后于
`config/*.yaml` 真值 + SESSION_LOG + CLAUDE.md 头部**。`U-E5` 名义仍 `doing`,
但 PILOT manifest 与 MVP 上线事实表明其目标已达。撰写操作型汇编文档时应以
**config 真值 + CLAUDE.md 头部状态**为准,不能只信 TASKS notes。

## 门禁

- 全量回归(owner 生产 env 下):**5774 passed / 14 skipped / 90.47% cov**。
- ruff(conftest.py):PASS。redline-check:全绿。
- codex:conftest.py 代码 safe;P2 docs 陈旧项已修。
