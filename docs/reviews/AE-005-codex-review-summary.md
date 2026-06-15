# AE-005 代码审查总结 — 量化参数进化 lane(P1c)

> 任务:AE-005 量化参数 lane + Sobol 诚实搜索 + 批量 registry + 三阶段门 + 反自欺 + boot 接线
> 日期:2026-06-15 · session #92
> 审查方式:**codex 撞 usage limit(至 2026-06-18,见 #91)→ 回退 `/code-review high`**(3 finder 角度 × 并行 agent,recall-biased)。印证 [[feedback_codex_rate_limit_fallback]]。

## 审查范围

新增 9 模块 + 6 测试文件 + `backend/main.py` boot/cron 接线 + `pyproject.toml`(scipy mypy override):

- `backend/strategy_evolution/disclosure_stats.py`(MinBTL 准入 + PBO-CSCV + Hansen SPA 披露)
- `backend/strategy_evolution/mechanism_registry.py`(机制假设白名单门)
- `backend/strategy_evolution/quant_param_search.py`(Sobol 诚实搜索 + 约束变换 + 累计 N 不重置)
- `backend/strategy_evolution/candidate_batch.py`(一等不可变批量对象)
- `backend/strategy_evolution/sentinel.py`(零 edge 哨兵对照组)
- `backend/strategy_evolution/forward_shadow_mandate.py`(stage-3 声明 + 诚实仪表盘)
- `backend/strategy_evolution/quant_param_lane.py`(三阶段漏斗 + BacktestResult→PromotionInputs seam)
- `backend/strategy_evolution/quant_lane_runner.py`(每夜 async 编排)
- `backend/services/quant_backtest_runner.py`(生产 runner 工厂,owner-gated 数据接线)

## 发现与修复(全部 P0/P1/P2 已修)

| # | 严重度 | 文件 | 问题 | 修复 |
|---|--------|------|------|------|
| A1 | 🔴 HIGH | quant_param_search.py `_finalise_sum_to_one` | 负 residual 被倒进零值权重 → `-1e-6` → `validate_param_set` 拒 → `produce()` 抛错(~3-4/3M Kraemer draw 的潜在崩溃,违反"每点合法 by construction") | 方向感知 residual target:正残差→最大 headroom 权重;负残差→最大值权重(有 floor 余量)。回归测试钉死。 |
| B1+B2+C3 | 🔴 HIGH | quant_param_lane.py `run_batch` + quant_lane_runner `_register_experiments` | 哨兵完整性破坏时只清 batch 级 `mandates`,**未清** per-candidate `survived`/`mandate` → 下游消费者(registry `success=True`、未来 stage-3 dispatcher 遍历 `evaluations`)可绕过 fail-closed;且 append-only registry 永久记录不可信 run 的 `success=True` | 在源头清洗:破坏时 `replace(e, survived=False, mandate=None)` 重建全部 evaluations。一并修复 registry success 与遍历泄漏。测试钉死 `all(not e.survived)`。 |
| C1 | 🔴 HIGH | main.py boot 块 | `int(os.environ.get("QUANTMIND_QUANT_EVOLUTION_SEED"))` 无 try/except → 畸形 env 值 crash lifespan,违反 `_init_orchestration_layer` fail-open 契约 | 防御性解析:`ValueError` → 回退默认 seed + warn。 |
| C2 | 🟠 MED | quant_lane_runner.py `_run_family` | 固定 nightly seed → 每夜抽**同一**批候选(永不探索);content-addressed `experiment_id` 幂等 skip → 声明 N 膨胀但 registry 冻结 → DSR n_trials 与真实试验数背离 | `effective_seed = self.seed + n_before`(按累计试验数推进):每夜探索新候选 + 注册增长 + N 诚实;同夜重跑仍 bit-identical。测试断言 night-2 → registry 16。 |
| B3 | 🟠 MED | sentinel.py `make_sentinels` | 已知但未映射 family → `KeyError` 逃逸 `run_nightly`(只 catch `BacktestDataUnavailableError`/`ParamSearchError`)→ 整个 nightly run 崩溃而非按 family fail-closed | `dict.get` + 缺失 → `ParamSearchError`(runner 按 family 跳过)。 |
| B5 | 🟢 LOW | quant_param_lane.py `daily_excess` | `zip(strict=False)` 静默截断,掩盖 champion/challenger 同窗不变量(而窗口门按全长 `trading_days` 校验) | 改 `strict=True` fail-loud(同窗 by construction;违反则该 family batch 中止 + cron 记录)。 |
| B4 | 🟢 LOW | quant_param_lane.py 注释 | 注释过度宣称"所有 acceptance 指标对回测都是 ideal"(实际仅 `execution_report_accuracy_rate` 结构性恒 1.0;3 策略指标是真实的) | 修正注释:`acceptance_not_degraded` 因 accuracy 结构性平手而**不可满足**;真实策略指标非劣化交由 stage-3 forward shadow + DSR(需正 excess Sharpe)。不新增门(尊重"门做减法")。 |

### 已验证 NOT bug(false positive / 设计意图)
- 哨兵三重守护(`mechanism_ok=False` / `not is_sentinel` / `mandate` 需 mechanism)+ 源头清洗 → 哨兵永不晋升。
- `_PREFILTER_VETO_GATES` 无门名拼写错误(7 个全匹配 `evaluate_promotion` 发出名)。
- 45 日 shadow 完成判据无 off-by-one(`>= min_calendar_days`,创建时 False)。
- 破坏 run 仍注册候选 = 诚实 N(经 B1 修复后 `success=False`,语义正确)。
- 每夜预算 cap 数学正确;cron `try/finally` 始终 `settle_budget`;`run_nightly` 未捕异常被 cron 内层 `except Exception` 兜住 → 仍 settle。
- water-fill 收敛、SPA recentering 符号、PBO logit smoothing、MinBTL floor 均经数值验证正确。

## 门禁(修复后)

- pytest 全量 **6035 passed** / 14 skipped / **90.13% cov**(基线 #91 = 5944 → +91)
- 新模块覆盖:quant_param_lane 99% / quant_param_search 95% / quant_lane_runner 94% / disclosure_stats·candidate_batch·mechanism_registry·forward_shadow_mandate 高覆盖
- ruff 全绿 · mypy strict(9 新模块)全绿 · redline-check 41 ok / 0 FAIL(含 [AB-008] promotion engine confined / [R-002] / [BACKTEST] 隔离)
- main.py 新增 0 mypy 错误(17 → 17,全在我的编辑范围外)

## 安全地基红线(一条未破)

零 LLM 数值晋升判定 · `objective_promotion` 仍限于 strategy_evolution(seam 在 strategy_evolution 内,services 工厂不含该字符串)· import 隔离(新模块仅 import backend.backtest 类型,无 llm/agents/mirofish/api/broker/data)· 晋升主门=45 日冻结 forward shadow(lane 永不 auto-activate,只产 PENDING mandate)· 人工 pin 不拆 · 127.0.0.1 · 仅 2 写端点不变 · fail-closed。
