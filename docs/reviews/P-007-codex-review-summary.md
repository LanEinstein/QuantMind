# P-007 代码审查总结 — 红线扫描 + 双线 dry-run 整合 + Phase P 收尾

**任务**: P-007(redline-check.sh 加 [P-002] 隔离 + [P-004] FeishuMessageKind=6 + 确认 M-004 仍绿;dry_run_double_line.py 接 allocation;Phase P 收尾)
**审查日期**: 2026-05-31
**审查工具**: codex review --uncommitted(完整出结论)
**最终判定**: ✅ 通过(codex 报 1×P2,已修)

## 发现与处置

| # | 级别 | 文件 | 问题 | 处置 |
|---|------|------|------|------|
| 1 | P2 | redline-check.sh:1226 | 新 [P-002] grep 只覆盖 `import backend.X`/`from backend.X` 两式,漏 `from backend import llm`(name-level)+ 相对 `from .. import agents` —— 作 standalone gate 时可被另一 import 语法绕过(N-005 上方已覆盖全式)| **FIXED** 改用 N-005 同款 5-式模式集(dotted/name-level/relative);planted-violation 自检(`from backend import llm`)确认现在 FAIL,真模块仍 ok |

## 实现要点
- **redline-check.sh [P-002]**:portfolio_allocation 不 import backend.{llm,agents,mirofish}(全 5 式)+ 不被 backend/risk import(`grep portfolio_allocation backend/risk` 必空)。
- **redline-check.sh [P-004]**:python3 导入 `FeishuMessageKind` 断言成员集 == 6 锁定值(含 basket_digest)。
- **M-004**:InstructionPlan 单一构造点 AST 扫描每次 redline 运行(本就绿,确认未破)。
- **dry_run_double_line.py**:`_run_line1` 注入 `load_allocation_policy(...)` → Line1ContextProvider(allocation_policy=...),dry-run 现真实走 P-003 逆波动率 clamp(end-to-end 演示「充分考虑持仓配比」);4 个依赖 dry-run 的测试(test_dry_run_double_line / test_pilot_readiness / test_e2e_production_path / test_pilot_cond_evidence)全绿(断言结构契约非具体手数,clamp 不破)。

## scope 说明(诚实记录)
- dry-run digest 演示:dry-run = DRY_RUN 渲染模式(action=`dry_run_rendered`),digest 发送路径只认 delivered(dispatched/skipped_duplicate)→ dry-run 不发 digest;digest 不可解析性已由 renderer 单测(`test_digest_is_not_parseable_as_execution_report`)证明,未在 dry-run 重复演示。
- dry-run 止盈/减仓演示:Line-2 intraday runner 已接 account+cap(P-005),dry-run 已 exercise Line-2 intraday;未额外构造 +1R/超配 fixture(止盈/减仓行为已由 P-005 单测覆盖)。

## 门禁
- `bash scripts/redline-check.sh`:全绿(新 [P-002] 全式 + [P-004]=6 + M-004 单一构造点);planted-violation 自检通过。
- `ruff check scripts/dry_run_double_line.py`:All checks passed。`bash -n redline-check.sh`:OK。
- 全量 `pytest`:4317 passed(`FEISHU_INTERACTIVE_ENABLED=false`)。
