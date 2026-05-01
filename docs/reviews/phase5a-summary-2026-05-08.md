# Phase 5A Summary — 2026-05-01 (实际完成)

> **计划日期**: 2026-05-08(week 1 末)
> **实际完成**: 2026-05-01(同日 4 task 一气呵成)
> **闭合提交范围**: `24a0db6` (SSoT) → `54f7def` (P5A-T03)

---

## 1. 工作清单完成情况

| Task ID | Title | Status | Commit | Test Report |
|---------|-------|--------|--------|-------------|
| P5A-T00 | 提交决策与基线复核 | ✅ 已完成 | 24a0db6 (SSoT + push origin/main + tag phase5-eval-start) | git log/status verify |
| P5A-T01 | 修复 news_crawler 'result' KeyError | ✅ 已完成 | 975af0b | `docs/reviews/p5a-t01-r1-architecture.md` + `r3-testing.md` |
| P5A-T02 | Daily LLM Cost Hard-Cap (cost_guard) | ✅ 已完成 | 61cfc7d | `docs/reviews/p5a-t02-codex-review.md` (5 轮综合) + R1-R5 占位 |
| P5A-T03 | AUTHORIZATION_MODE Startup Assertion | ✅ 已完成 | 54f7def | `docs/reviews/p5a-t03-r1-architecture.md` + `r3-testing.md` |

---

## 2. 测试基线

| 套件 | Phase 5A 起始 (2026-05-01) | Phase 5A 结束 (2026-05-01) | Δ |
|------|------------------------|-------------------------|---|
| pytest 总数 | 672 passed / 11 skipped | 762 passed / 11 skipped | **+90** |
| pytest 失败 | 0 | 0 | 0 |
| pytest 时长 | 2.50s | 5.05s (含 cov) / 2.5s (no cov) | 不显著回归 |
| **关键模块覆盖率** | | | |
| `backend/risk/engine.py` | 100% (既有) | 100% | 不变,达 ≥95% 目标 |
| `backend/risk/circuit_breaker.py` | 96% (既有) | 96% | 不变 |
| `backend/risk/stop_loss.py` | 97% (既有) | 97% | 不变 |
| `backend/services/cost_guard.py` | (新建) | **100%** | 全覆盖,达 ≥95% major-feature 目标 |
| `backend/services/authorization.py` | (新建) | **100%** | 全覆盖 |
| `backend/data/news_crawler.py` | 既有 | 90% | 达 ≥90% minor-fix 目标 |
| **Backend 全量** | (基线未单跑) | 82% (TOTAL) | 远超 ≥70% non-risk 目标 |
| vitest | 既有(未跑) | 既有 (P5A 无前端改动) | — |
| playwright | 既有(未跑) | 既有 (P5A 无前端改动) | — |

---

## 3. 关键决策记录

### 决策 1:NaN/Inf 双层防御 (P5A-T02)
- **场景**: `QUANTMIND_DAILY_BUDGET=nan` 或 Redis 中 `cost_rmb="-inf"` 都会让 `_classify` 返回 "ok",静默禁用 hard cap
- **选项**: A. 仅在 env 层校验 / B. env 层 + 数据层双层校验 / C. 数据层校验
- **采用**: B,defense-in-depth
- **理由**: 任一层漏过另一层兜底;cycle 3 codex review 主动发现 spent_today 路径残留漏洞,验证了双层必要性

### 决策 2:Fail-Closed (数据腐败) vs Fail-Open (基础设施故障)
- **场景**: cost_guard 检查 → Redis ConnectionError(瞬时) vs cost_rmb=NaN(数据腐败)
- **采用**: 分类处理 — Connection error → fail-open (proceed,scheduler 兜底);Data corruption → fail-closed (hard_breach status)
- **理由**: 瞬时故障多为临时性,继续跑减少业务损失;数据腐败是确定性 bug,必须人工介入

### 决策 3:asyncio.Lock 而非 Redis 分布式锁 (P5A-T02)
- **场景**: cron + manual API 并发可能 double-spend 预算
- **采用**: 进程级 `asyncio.Lock`,留 Redis 分布式锁到 Phase 6+ 多实例
- **理由**: eval-period 单实例;manual API 罕用 + cron 10s sleep 互让;cross-process race 在当前部署模式不存在

### 决策 4:Vocabulary 双向矩阵 (P5A-T03)
- **场景**: master plan 用 canonical short (suggest/confirm/auto),frontend 用 legacy long (suggestion/semi_auto/full_auto)
- **采用**: env / 策略层 / audit log 用 canonical short;API response / frontend 接口用 legacy long;`_LONG_TO_SHORT` + `_SHORT_TO_LONG` 双向映射
- **理由**: 避免 frontend 破坏性改动;同时 audit trail 不再因别名漂移而暧昧
- **附带修复**: 旧 `_get_auth_mode` 的 `replace("suggest","suggestion")` 在 env="suggestion" 时产生 "suggestionion" 串接错误,被本次彻底替换

### 决策 5:Codex Review 5 轮 hard gate 跑出真实价值
- **观察**: P5A-T02 5 轮迭代发现 6 个 issue(4×P2 + 2×P3),P5A-T03 3 轮发现 3 个 P2;独立审查暴露了 Claude 自审难发现的 vocabulary 漂移、并发竞争、单条数据腐败等问题
- **结论**: codex-review hard gate 投资回报率高,记入未来阶段执行的稳定信心来源

---

## 4. 阻塞与风险

### 当前阻塞
**无**。Phase 5A 全部出口指标达成。

### 已识别风险(待 Phase 5B-5C 关注)

| 风险 | 等级 | 来自 | 缓解 |
|------|------|------|------|
| 跨进程 cost_guard 竞争 | 中 | P5A-T02 内部 TODO | Phase 7 多实例时升级 Redis Lua 原子锁 |
| akshare 上游可能继续退化(其他 endpoint) | 中 | P5A-T01 | 部署后 24h+30d 监控 `eastmoney_news_failed` warning 频率 |
| frontend 仍用 legacy long form,canonical 迁移延后 | 低 | P5A-T03 | 留作独立 frontend task,Phase 6 前完成 |
| `_VALID_AUTH_MODES` 既含 short 又含 long,后续可能引发歧义 | 低 | P5A-T03 cycle 3 closure | 文档已说明,未来 frontend 迁移完成后可去除 long form |

### 部署后跟踪条目(Phase 5B 启动前必须验证)

- [ ] 24h 线上 `journalctl -u quantmind-backend | grep "eastmoney_news_failed: 'result'"` == 0
- [ ] 24h 线上 `journalctl ... | grep "daily_budget_breached"` 仅有真实超 budget 触发,无误熔断
- [ ] 24h 线上 `cost_guard_invalid_spent` 错误日志 == 0
- [ ] backend 启动后 `curl https://quantmind.local/api/monitoring/budget` 返回 BudgetState JSON,`status=ok` 与实际 LLM spend 一致
- [ ] backend 启动 phase=phase5_eval + AUTHORIZATION_MODE=auto 应 fail-fast,journalctl 含 "Refusing to start"

---

## 5. 下一阶段入口条件

- [ ] **用户授权进入 Phase 5B**(必须书面授权,代码不可自动跨阶段)
- [ ] 上述 5 项部署后跟踪条目验证通过(可在 Phase 5B 启动前的 24h 部署窗口完成)
- [ ] Phase 5B 起步前 §5 自验证 7 步全部通过

---

## 6. Phase 5A 之外的副产物

- 修复 `backend/llm/cost_tracker.py` 的 pre-existing F401 unused import
- 加固 `tests/test_risk_api.py` 测试矩阵(从 1 mode-switch 测试扩到 8 个,含 cross-phase / canonical / consistency)
- master plan §7.4 修订记录前两项 commit hash 实填(24a0db6 + 975af0b + 61cfc7d)

---

## 7. 阶段 STOP 标准 (§7.3 模板)

```
Phase 5A STOP — 入口下一阶段需要您的明确授权。

入口条件:
- [✅] 当前阶段 summary 报告已生成: docs/reviews/phase5a-summary-2026-05-08.md
- [✅] 测试基线全绿 (pytest 762 passed / 11 skipped, vitest/playwright N/A 因 P5A 无前端改动)
- [✅] coverage 不下降 (backend/risk/engine.py=100%, cost_guard=100%, authorization=100%)
- [✅] 退出指标全部达成
- [✅] 无 P0/P1 阻塞
- [⏳] 部署后跟踪条目: 待 24h 部署观察窗口 (在 Phase 5B 启动前完成验证)

如同意进入下一阶段,请回复:"授权进入 Phase 5B"。
否则请指明需要补做的事项。
```
