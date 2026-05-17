# Phase J Codex 跨模型代码审查报告

**项目**: QuantMind
**审查时间**: 2026-05-17 session #20
**审查轮次**: 5 / 5 + 最终复核
**最终判定**: ✅ 通过 (经最终复核 PASS,18/18 RESOLVED,0 critical regression)

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数 | 31 (19 untracked + 12 modified) |
| 代码改动 | 约 3500 LOC (含 J-006 1053 行 runbook + playbook 文档) |
| 新增测试 | 11 个文件(覆盖 J-001..J-007 + cycle 1-5 全部 18 个回归)|
| 发现问题总数 | 18 (4 P1 + 12 P2 + 2 P3,0 dismissed false positive) |
| 已修复 | 18 |
| 误报排除 | 0 |
| 未解决 | 0 |

---

## 各轮次详情

### 第 1 轮(初次审查)

**Codex 判定**: NEEDS_FIXES

`codex review --uncommitted` 找出 2 个 P1 + 3 个 P2:

| # | 严重度 | 文件 | 问题描述 | 处理结果 |
|---|--------|------|----------|----------|
| 1 | P1 | scripts/simulate_n_trading_days.py:309-311 | `_prepare_env` 用 `setdefault` 导致父 shell 预设的 `QUANTMIND_LLM_STUB=0` 静默生效;`SimulationOutcome.ok` 不检查 stub 模式,可在伪 PASS 下烧真实 LLM | RESOLVED — 改为 force-set + `ok` 默认要求 `llm_router_stubbed=True` + `real_llm_calls_observed==0`(除非 `allow_real_llm=True`)|
| 2 | P2 | backend/services/reset_trigger_detector.py:264 | `dedup_key` 含分钟粒度,同一长 outage 在 10:30 / 10:31 产生不同 key,FeishuAlerter 15 分钟去重窗口无效 | RESOLVED — `dedup_key = trigger.value`(同触发器同 key,不同触发器不同 key)|
| 3 | P1 | deploy/quantmind.service:24-27 | unit 跑 `User=quantmind` 但工作树 `/home/ps/papers/QuantMind` 归 ps 所有,缺 ACL 授权;systemd 启动后 uvicorn 无法读取 backend 树 | RESOLVED — `scripts/install_quantmind_service.sh` 加 `setfacl` 步骤(`/home/ps` 与 `/home/ps/papers` 加 traversal `x`,REPO_ROOT 递归 + default ACL 加 `rX`,`logs`/`data` 加 `rwX`)+ 末尾 `sudo -u quantmind test -r` 验收 |
| 4 | P2 | deploy/quantmind.service:58-59 | `StartLimitIntervalSec`/`StartLimitBurst` 放在 `[Service]` 段被 systemd 静默忽略,20-restart cap 实际未生效 | RESOLVED — 移到 `[Unit]` 段 |
| 5 | P2 | backend/main.py:922-924 | `assert_owner_authorization_or_exit()` 不传 `audit_jsonl_path`,设置了 LOG_AUDIT_PATH 的部署里 owner-auth 事件落到默认 `logs/audit.jsonl`,AuditStore 读另一个路径,审计轨迹分裂 | RESOLVED — `_resolved_audit_jsonl_path` 提前到 lifespan 顶部,传给 owner auth 与 AuditStore |

### 第 2 轮(增量复核)

**Codex 判定**: MAJOR_CONCERNS(5/5 Cycle 1 全 RESOLVED + 5 个 NEW)

| # | 严重度 | 文件 | 问题描述 | 处理结果 |
|---|--------|------|----------|----------|
| 6 | P1 | backend/services/acceptance_report.py:243 | `AcceptanceService._reset_state` 仅在进程内存中;J-004 reset 触发后崩溃/重启,下次 `compute()` 不再 clamp,可让残留 PASS 通过 | RESOLVED — `AcceptanceService.set_reset_state()` 公开 setter + lifespan 在 `_init_orchestration_layer` 内读 audit JSONL 找最新 `SYSTEM_INTERRUPTED` w/ `reason_namespace=acceptance_reset_trigger`,构造 WindowResetState 注入 |
| 7 | P2 | backend/main.py:907 | `assert_secrets_or_exit()` 在 `_resolved_audit_jsonl_path` 计算前运行,soft-warning JSONL 仍落到默认路径 | RESOLVED — `_resolved_audit_jsonl_path` 移到 lifespan 最顶,secrets validator + owner auth + AuditStore 全部复用 |
| 8 | P2 | scripts/smoke_test_cold_start.py:114 | `_prepare_env` 也用 `setdefault` 同 Cycle 1 #1 | RESOLVED — 改为 force-set `QUANTMIND_LLM_STUB` 与 `QUANTMIND_BROKER_SKIP_RS_GATE`,除非 `--allow-real-llm` / `--strict` |
| 9 | P2 | scripts/acceptance_dashboard.py:284 | 投射日期用 `now.astimezone(dt.UTC).date()`;A 股交易日按 Asia/Shanghai 算,00:00-08:00 跑会差一天 | RESOLVED — 改为 `now.astimezone(SHANGHAI).date()`,与 BrokerScheduler cron + AcceptanceReport.trade_date 时区一致 |
| 10 | P2 | scripts/install_quantmind_service.sh:130 | 关键的 `/home/ps` 与 `/home/ps/papers` traversal 授权用 `setfacl ... || true`,失败被静默吞掉,installer 报成功但 service 实际无法 traversal | RESOLVED — 改为显式 `if ! setfacl ...; then echo error >&2; exit 1; fi`;新增 post-install `sudo -u quantmind test -r backend/main.py` 验收 |

### 第 3 轮(增量复核)

**Codex 判定**: NEEDS_FIXES(4/5 Cycle 2 RESOLVED + #6 UNRESOLVED 深化 + 3 个 NEW)

| # | 严重度 | 文件 | 问题描述 | 处理结果 |
|---|--------|------|----------|----------|
| 11 | P1 | backend/services/acceptance_report.py:436 | `can_switch_to_feishu_on()` 只看 latest report 的 outcome=PASS;reset 触发后但 next compute 前,重启 hydrate 了 reset state 仍可让 Feishu interactive 从过时 PASS 启动 | RESOLVED — 门加 reset_state 检查:`reset_date = last_reset_at.astimezone(SHANGHAI).date()`,若 `reset_date >= pass_date` 拒绝 PASS |
| 12 | P1 | backend/services/reset_trigger_detector.py:256 | `_fire()` 在 await `AlertDispatcher.fire()` 之前调 `record_reset()`;dispatcher 吞 audit 失败返 `audit_written=False`,导致内存被改但持久审计无法 replay | RESOLVED — `_fire()` 改为先 dispatch + 检查 `audit_written`,False 时 raise RuntimeError 不调 record_reset |
| 13 | P2 | backend/services/acceptance_report.py:307 | clamp 用 `last_reset_at.date()` 取事件自身时区的日期;reset at 2026-05-14T16:30Z(= 2026-05-15 Shanghai)会 clamp 到 2026-05-14,漏一天 | RESOLVED — clamp 改为 `last_reset_at.astimezone(SHANGHAI).date()` |
| 14 | P3 | scripts/install_quantmind_service.sh:153 | 验收步骤硬编码 `sudo -u quantmind ...`;最小化 root 主机可能无 sudo,验收误报失败 | RESOLVED — 加 portable shell-fn dispatcher:`runuser` 首选,`sudo` 回退,都无则 exit 1 |

### 第 4 轮(增量复核)

**Codex 判定**: NEEDS_FIXES(4/4 Cycle 3 全 RESOLVED + 1 个 NEW)

| # | 严重度 | 文件 | 问题描述 | 处理结果 |
|---|--------|------|----------|----------|
| 15 | P2 | backend/services/reset_trigger_detector.py:276 | `_fire()` 仍会先 dispatch audit/Feishu 然后才调 `record_reset()`;若 caller 传 naive datetime,record_reset() 会 raise,但 audit 已落地 — 持久 alert 无内存 clamp,且 hydration 也会拒绝 naive timestamp | RESOLVED — `_fire()` 入口加 `if when.tzinfo is None or when.utcoffset() is None: raise ValueError(...)`,所有副作用前 fail-fast |

### 第 5 轮(增量复核)

**Codex 判定**: NEEDS_FIXES(1/1 Cycle 4 RESOLVED + 3 个 NEW)

| # | 严重度 | 文件 | 问题描述 | 处理结果 |
|---|--------|------|----------|----------|
| 16 | P2 | backend/services/acceptance_report.py:261 | `record_reset()` 无条件覆盖;两个并发 reset 触发器经 `_fire()`(audit+Feishu await),老 reset 后到可能覆盖新 reset;hydration 用 `reversed()` 取 append 顺序而非 max 时间戳 | RESOLVED — `record_reset()` 与 `set_reset_state()` 加 monotonic 守门(老 timestamp 是 no-op);hydration 改用 `max(events, key=lambda e: e.timestamp)` |
| 17 | P3 | backend/main.py:689 | `_acceptance_callback` 闭包引用 `ticket_repo`,但 `ticket_repo = MongoTicketRepository(db)` 在 `broker_scheduler.start()` **之后**赋值;APScheduler misfire 可触发 NameError | RESOLVED — 把 `ticket_repo` / `mongo_daily_store` / `snapshot_lookup` 绑定移到 `_has_open_reconciliation_ticket` 闭包定义**之前**(也在 `broker_scheduler.start()` 之前)|
| 18 | P3 | scripts/smoke_test_cold_start.py:137 | `_run_lifespan_smoke()` 只 catch `Exception`;lifespan fail-fast 用 `SystemExit`(BaseException 子类),smoke script 在 --json 模式下静默退出无结构化结果 | RESOLVED — 改为 `except BaseException`(`KeyboardInterrupt` 重新 raise),保留 SystemExit traceback 到 structured result |

---

## 最终验证 (Phase 6)

**复核状态**: EXECUTED
**复核判定**: PASS
**触发原因**: max_cycles_reached(5/5)+ FIXES_APPLIED_TOTAL=18

### 历史问题复核结果

| # | 原问题 | 当前状态 | 备注 |
|---|--------|----------|------|
| 1 | simulate_n_trading_days stub safety | RESOLVED | force-set + ok 要求 stubbed |
| 2 | reset dedup_key with minute | RESOLVED | dedup_key = trigger.value |
| 3 | systemd User=quantmind ACL | RESOLVED | installer 加 setfacl + 验收 |
| 4 | StartLimit* in wrong section | RESOLVED | 移到 [Unit] |
| 5 | owner-auth audit path mismatch | RESOLVED | _resolved_audit_jsonl_path 共享 |
| 6 | reset state lost on restart | RESOLVED | set_reset_state + audit JSONL hydration |
| 7 | secrets soft warnings bypass | RESOLVED | path resolved at lifespan top |
| 8 | smoke `_prepare_env` setdefault | RESOLVED | force-set + opt-out flags |
| 9 | dashboard projection UTC | RESOLVED | Asia/Shanghai |
| 10 | installer setfacl || true | RESOLVED | 显式 fail-exit + 验收 |
| 11 | switch gate ignored reset_state | RESOLVED | gate 检查 reset Shanghai date >= pass_date |
| 12 | _fire audit-first ordering | RESOLVED | audit 先 + audit_written 检查 + RuntimeError |
| 13 | reset clamp event timezone | RESOLVED | astimezone(SHANGHAI).date() |
| 14 | installer validation sudo only | RESOLVED | runuser 首选,sudo 回退 |
| 15 | _fire naive datetime bypass | RESOLVED | 入口 prevalidate aware datetime |
| 16 | reset state monotonicity | RESOLVED | record_reset/set_reset_state 拒老;hydration max-timestamp |
| 17 | broker_scheduler before ticket_repo | RESOLVED | ticket_repo 绑定提前到 broker_scheduler.start() 之前 |
| 18 | smoke missed SystemExit | RESOLVED | except BaseException + KeyboardInterrupt re-raise |

### 复核中发现的新增严重问题

**无**(NONE)

---

## 审查维度覆盖

| 维度 | 检查项 | 发现问题 |
|------|--------|----------|
| 正确性与逻辑(P0-6/P1-2.A 不变量)| 18 | 8(reset state 持久化、switch gate 与 reset 联动、并发 monotonicity、闭包绑定顺序、naive datetime 早返、env force-set 等)|
| 安全性(P1-6 §1.1 凭证 + ACL)| 6 | 1(systemd User=quantmind ACL 授权)|
| 错误处理 & resilience | 6 | 4(setfacl 失败吞掉、audit fail-open 与 reset 顺序、smoke 漏 SystemExit、record_reset naive datetime)|
| 性能 | - | 0 |
| 代码质量 / 维护性 | 6 | 2(setdefault 模式跨脚本复现、time-zone 复制粘贴)|
| 语言/框架最佳实践 | 6 | 3(systemd 段定位、Python BaseException vs Exception、Bash `|| true` 守门)|

---

## 测试结果

| 阶段 | pytest | 覆盖率 | Phase J 新测试增量 |
|------|--------|--------|---------------------|
| 起点(session #20 开始)| 2410 passed / 11 skipped | 88.18% | 0 |
| Cycle 0(Phase J 完成,pre-codex)| 2591 passed / 11 skipped | — | +181 |
| Cycle 1 fixes | 2593 passed / 11 skipped | — | +2 |
| Cycle 2 fixes | 2594 passed / 11 skipped | — | +1 |
| Cycle 3 fixes | 2599 passed / 11 skipped | — | +5 |
| Cycle 4 fixes | 2601 passed / 11 skipped | — | +2 |
| Cycle 5 fixes | **2605 passed / 11 skipped** | — | +4 |

Phase J 共 +195 个新测试。Ruff 全部 Phase J touched files 全绿。redline-check.sh 全绿(含 H-003 / A-007 / P0-7 / G-009 / J-007 owner auth 不入红线)。

---

## 关键观察

- 5 cycle + 1 verification 总耗 18 个真实 bug,**0 dismissed false positive** — 再次印证 [[feedback_codex_findings_real]] 准则:本地 2591 绿 pytest + ruff touched-files + redline-check pre-codex 仍非 commit-safe。Codex 找出的问题里有 4 个 P1(stub-mode 安全、systemd User 权限、acceptance reset 持久化、switch gate 与 reset 联动)若漏会直接破坏 I-002 长跑或 J-002 烧 LLM 预算保证。
- **审查捕获了 8 个 P0-6 / P1-2.A 不变量违反**(reset state 持久化、switch gate 与 reset 联动、Shanghai 时区一致性、monotonicity)— 这些都不会被本地门禁发现,因为它们是跨组件的语义不变量。
- **环境变量 force-set vs setdefault 反复在两个脚本里出现同样的 bug**(Cycle 1 simulator + Cycle 2 smoke);Codex 第二次发现说明 Cycle 1 修复时没做跨文件搜索 — 操作员经验值。
- 最终验证通过(PASS),所有 18 个修复都已 codex 独立 read-only 复核确认 RESOLVED + 0 critical regression。

---

> 本报告由 Claude Code + Codex CLI 协同生成
> Claude Code (修复) + Codex CLI (审查)+ 5 + 1 个完整 cycle
> Codex 报告原始输出存于 `/tmp/codex_review_phase_j_iAabBJ/cycle_{1..5}.md` + `final_verification.md`
