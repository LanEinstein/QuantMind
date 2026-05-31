# P-002 代码审查总结 — backend/portfolio_allocation/ 纯模块

**任务**: P-002(组合层逆波动率配比 + 稳健分批 + 整手分配纯模块 + 配置)
**审查日期**: 2026-05-31
**审查工具**: codex 撞使用额度(retry 10:12 AM)→ 按 owner 锁定回退 `claude /code-review` high(7-angle finder + verify;见 [[feedback_codex_rate_limit_fallback]])
**最终判定**: ✅ 通过(confirmed findings 全修;3 dismiss 有据)

## 发现与处置

| # | 维度 | 文件:行 | 问题 | 处置 |
|---|------|--------|------|------|
| 1 | 正确性 | allocator.py `compute_target_cash` | weight>1(负 sibling 诱发)时 raw 步 `w×deployable` 仅受 cap 约束,`Σalloc` 可超 `deployable`;docstring 宣称"任意 weights"均 ≤ deployable | **FIXED** 入口 sanitize(非有限/负权→0)+ Σw>1 时归一化,保证任意输入 Σtarget ≤ deployable |
| 2 | 正确性 | allocator.py 同上 | inf 权重 → `min(inf, cap)=cap`,吃满单只 cap(¥50k)无视 deployable | **FIXED** 同上(inf→0) |
| 3 | 正确性 | allocator.py 同上 | 浮点 ULP:归一权重和=1+ULP → Σalloc 微超 deployable ~1e-13 | **FIXED** Σw>1 分支连 ULP 一并夹回;docstring 注明"up to floating-point rounding";测试保留 1e-6 容差 |
| 4 | 高度 | policy.py `AllocationPolicy` | `vol_lookback` 在本模块不被消费(σ 上游算),易成 dead-config 陷阱 | **FIXED(doc)** 类 docstring 说明该 knob 文档化期望 σ 窗口,P-003 取 σ 处消费做对齐断言 |
| 5 | 简化 | allocator.py 残差步 | 单遍残差重分配是 best-effort(re-cap 名余额泄漏),未来维护者可能误改多遍破确定性 | **FIXED(comment)** 注明单遍是 redline 6(无 mid-walk 再分配)有意为之,保守欠配 |
| 6 | 复用 | policy.py `_require_*` | 与 budget_policy/policy.py 校验助手近重复,且已分叉(本模块拒 bool,budget 不拒) | **DISMISS** 抽共享 validators 会重构已 done/frozen 的 Phase L 模块=超 P-002 范围;本模块更严非 buggy |
| 7 | 高度 | volatility.py:54 | `isinstance(sigma,int\|float)` 在 `dict[str,float\|None]` 下属冗余防御 | **DISMISS** 有意 fail-closed 防御,与模块"不信上游数值"一致 |

## 加固测试
- `TestAdversarialCaps.test_raw_out_of_contract_weights_still_bounded`:weights>1 / 负 / inf / NaN / sum≠1 五场景 × 4 账户,断言每只 ≤ cap 且 Σ ≤ deployable。
- 原 `inverse_vol_weights` 输出对抗测试保留。

## 门禁
- `pytest tests/portfolio_allocation`:118 passed,覆盖率 **100%**。
- 全量 `pytest --cov=backend --cov-fail-under=70`:4273 passed(`FEISHU_INTERACTIVE_ENABLED=false`;true 时 3 个 orchestration 测试因 env 泄漏 SystemExit,与本任务无关)。
- `ruff check`:All checks passed。
- redline-check.sh:全绿;`[P-002]` import 隔离 + 单一构造点 grep 均空。
