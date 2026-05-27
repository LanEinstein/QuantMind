# U-E4 代码审查总结 — 飞书 BUY 信号判据段(display-only)

> **任务**: U-E4 / 缺口3 — 飞书 BUY 信号含判据(① 量化 score+因子 ② 推理 fund_manager+3 分析师),display-only。
> **审查方式**: **claude /code-review high**(codex CLI 撞使用额度,约 2026-05-31 恢复 → owner 既定回退,见
> `feedback_codex_rate_limit_fallback`)。high 档 = 7 finder 角度(3 correctness + 3 cleanup + 1 altitude)× verify。
> **日期**: 2026-05-27。**结论**: 5 项 correctness/cleanup finding 已全修;其余 verify 后 REFUTED / 设计有意,记录在案。
> **本地门禁**: 3989 passed / cov 90.63%(risk 98%+)/ ruff 全绿 / redline-check ALL PASS。

## 范围
- 改:`backend/integrations/feishu/renderer.py`(`render_buy_signal` 加 `rationale` 参数 + 显眼块;`_single_line`/`_truncate` 改 import text_safety;删死代码 `_strip_controls`)、`backend/orchestration/line1_runner.py`(装配 + 传判据)。
- 新:`backend/integrations/feishu/text_safety.py`、`backend/integrations/feishu/signal_rationale.py`、3 测试文件。
- 治理:`docs/decisions/P0-3-amendment-2026-05-27-buy-signal-rationale-display-only.md`。

## 已修 finding(CONFIRMED / 采纳)

| # | 角度 | 问题 | 修复 |
|---|------|------|------|
| 1 | Removed-behavior / 多角度 | `render_buy_signal` docstring 声称 `rationale=None` 时输出"byte-identical to pre-amendment",但显眼块现**无条件**加入 → 声明失实,误导维护者 | 改写 docstring:每条 BUY 信号都带显眼 交易要点 块;`None` 时仅省略判据块(非整体字节一致) |
| 2 | Cross-file tracer | feishu 包红线(no `backend.{llm,agents,mirofish}`)的隔离测试 `test_module_isolation` **只扫 renderer.py**,新模块 text_safety/signal_rationale 无守护 | 泛化为扫**整个 `backend/integrations/feishu/` 包**(glob `*.py`),覆盖新模块;未来任何 feishu 模块引 LLM/agents 即红 |
| 3 | Line-by-line | `_format_factor_value` / 综合评分 无 finite 守门 → 若非有限值(NaN/Inf)逃逸到 rationale,字面 `nan`/`inf` 会推给 operator | 加 `math.isfinite` 守门:None **或**非有限 → `—(数据不足)`(fail-closed,CLAUDE.md §3);加测试 `test_rationale_lines_non_finite_values_fail_closed` |
| 4 | Reuse / Simplification | `_build_buy_rationale` 硬编码 5 因子名元组,与 `FactorVector` 字段 + `_FACTOR_PRESENTATION` 三处重复 → 新增 Alpha158 因子时漏改即静默不上墙 | 改 `tuple((f.name, getattr(fv, f.name)) for f in dataclasses.fields(fv))` 从 FactorVector 单一真相源派生;未来新因子自动呈现(presentation 表有未知名 fallback) |
| 5 | Simplification | 抽取 `_single_line`/`_truncate` 到 text_safety 后,同簇的 `_strip_controls` 死代码(零调用方)遗留 renderer.py,`render_alert` 注释仍引用它 | 删除死 `_strip_controls` + 修 `render_alert` 过时注释 |

## verify 后 REFUTED / 设计有意(不改,记录理由)

- **`truncate(limit<=0)` 返回 1 字符 `…` 超预算**:与抽取前 `_truncate` **字节一致**的既有行为;实际调用方均用 ≥80(REASONING_LIMIT=160 / CONCLUSION_LIMIT=120 / clarification=80),`limit<=0` 不可达。保持原行为(抽取不改语义)。
- **显眼块与 7 段体重复 股数/限价**:有意(顶部显眼重述 + 详情);两处同源 `plan` 字段 + 同 `_format_money`,结构上不可能不一致。
- **`stock_name` 在显眼块原样插值(未 `_single_line`)**:沿用既有 `_dispatch_body_lines` 同款做法;`InstructionPlan._check_stock_name` 构造期已拒控制符 → 非新注入面。
- **判据仅在 `line1_runner` 路径**:`test_mvp_e2e` 等直调 `render_buy_signal` 不传判据是测试夹具;生产 BUY 走 line1_runner。`rationale` keyword-only 默认 `None`,无回归。
- **`"stub thesis" not in plan.model_dump_json()` 因 InstructionPlan 无 reasoning 字段而平凡成立**:这正是 display-only 的**结构性保证**(plan strict + extra=forbid 无处可放),测试作回归守护保留。
- **`_format_factor_value` 的 亿元/% 与 `_format_money` 并存**:单位语义不同(¥ 2dp vs 比率/百分比/亿元),无法委托(且 renderer→signal_rationale 已是依赖方向,反向 import 会循环)。

## display-only 不变量证据(本任务红线)
- 判据经 `line1_runner._route_candidate` 作**渲染参数**传 `render_buy_signal`,**不进** `InstructionPlan`(frozen/strict/extra=forbid 无 reasoning 字段)。
- AST 测试 `test_rationale_type_unreachable_from_parser_and_idempotency`:`parser.py` / `appliers.py`(含 `compute_idempotency_key`)**不 import** `signal_rationale` → 判据结构上不可达 parser / 幂等键。
- 文本断言:marker 出现在 wire,但**不在** `plan.model_dump_json()` / 任何 `RiskCheckSummary` 行;`volume`/`limit_price`/`side` 不变(单一构造点 M-004)。
- LLM 自由文本(fund_manager + 3 分析师)逐条经 `text_safety.single_line`(防伪头)+ `truncate`(≤160/≤120);防注入测试覆盖嵌入换行/控制符 + 超长截断。
