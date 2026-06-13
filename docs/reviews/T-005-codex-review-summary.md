# T-005 Codex 跨模型代码审查报告

**任务**: T-005 — qlib 评估 + 全栈联调 + 程序级验收
**审查时间**: 2026-06-13
**审查轮次**: 1 cycle + 1 read-only 最终复核
**最终判定**: ✅ 通过(经最终复核)

## 审查范围
- `tests/monitoring/test_mvp_e2e.py`(参数化 `_run_double_line` + 全栈 e2e `test_full_stack_two_line_e2e` + MVP gate 不变 `test_full_stack_preserves_mvp_gate`)
- `docs/research/t-005-qlib-eval-and-fullstack-acceptance-2026-06-13.md`(qlib deferred + 验收沿用 P0-6 决策记录,docs-only)

## 发现的问题(cycle 1)— 1 × P2,已修

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P2 | test_mvp_e2e.py | 全栈 e2e 只断言 `sell_msgs` 产生,但 `_crash()` fixture 本就触发**核心 N-001 检测器** → 即便 T-003 全栈未接线/fail-closed 测试仍绿 = gate 形同虚设 | **FIXED**:`DoubleLineResult` 暴露 `anomaly_feature_version` + `anomaly_kinds`;全栈测试断言 `feature_version=='monitoring.anomaly/v2'` **AND** `'isolation_forest' in kinds`(证明 T-003 真触发);MVP gate 测试断言 v1 + 无全栈 kind |

## 最终复核(read-only,codex exec)
- **RESOLVED**;新增 P1 回归:**NONE**。

## 门禁
- `ruff` ✅ / monitoring 全量 240 passed + 1 skipped ✅。
- **全量回归**:`pytest --cov=backend --cov-fail-under=70` → **5774 passed / 14 skipped / coverage 90.47%**(基线 #83 5694 → +80)。
- `redline-check.sh` 全绿。

## 守住安全地基
全栈合流 e2e:交易员人格(T-002)+ 全异动栈(T-003)同启 → 6 LLM 调用 + BUY 仍确定性 sizing 的 VALIDATED(单一构造点不破)+ Line-2 SELL 经监控单一构造点 + 14-check;默认路径 bit-identical(4 调用/v1/无全栈 kind)= 新旋钮严格 additive。qlib 评估后定**非核心 deferred**(若引入须类 rqalpha 可选 test-time + PIT 校验 + 单独 amendment);程序级验收**完全沿用 P0-6**(`can_switch_to_feishu_on` 无 env 绕过,既有测试覆盖);45 日验收 RUN 推迟到整体测试期。
