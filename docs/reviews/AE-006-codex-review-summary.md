# AE-006 代码审查 summary — param 运行时落地(lockfile v2 + RuntimeParamStore + 解除两处拒)

> 任务:AE-006(`AB-003-amendment-2026-06-14-param-runtime-landing`)。
> 审查日期:2026-06-15。
> **codex CLI 不可用**(usage limit 至 2026-06-18 13:27)→ 回退 `/code-review high`(3 正确性 finder 并行 + 1 cleanup/altitude finder → 验证)。沿用 #88–#92 owner 接受的 fallback 模式([[feedback_codex_rate_limit_fallback]])。

## 范围

- `backend/strategy_evolution/runtime_param_store.py`(新)、`evolvable_params.py`、`activation.py`、`live_artifact_registry.py`
- `backend/candidate_selector/{selector.py,__init__.py}`、`backend/theme_research/tier_weights.py`(已回退)、`backend/main.py`(boot)
- 测试:`tests/strategy_evolution/test_runtime_param_store.py`(新)、`test_activation.py`、`tests/candidate_selector/test_value_slots.py`

## 第 1 轮 — 3 正确性 finder(line-by-line / removed-behavior / cross-file),全部收敛于同一发现

### 🔴 HIGH(3 finder 一致)— 解除拒绝后,未接线参数仍可 pin = 静默 no-op 晋升

**问题**:旧 `write_next_boot_lock`/`apply_pending_activation` 对**任何** param-bearing manifest 硬拒(理由原文「a silent no-op promotion is worse than a refusal」)。AE-006 为唯一接线参数 `allocation.value_slot_quota` 解除该拒绝时,**同时**为所有**未接线**参数也打开了 staging/apply:`theme.tier*_weight`(`from_store` 无人调用)、`line2.atr_stop_mult`/`line2.time_stop_trade_days`(无 store reader)可经人工 gate pin → 落 v2 lockfile → 载入 store → 日志 `active`,**但运行时毫无变化**。最坏落在**安全相关止损**(atr_stop_mult / time_stop)上:owner 以为收紧了保护性止损,实则未变。

**根因深挖**:进一步查 `line1_runner.select(quant, advisory=...)` **不传 `value_scores`**,故连 `value_slot_quota` 的*效果*也受独立 AC-005 value-scoring 门控。关键区别 = `value_slot_quota` **端到端接线完整**(store→`SelectorConfig`→`_select_with_value`,其效果门是正交的 AC 数据门),而 theme/line2/selector-weights **完全无接线**。

**修复**:`evolvable_params.RUNTIME_CONSUMED_PARAMS`(= 有 wired/boot-reachable consumer 的参数集,当前仅 `allocation.value_slot_quota`)+ `validate_param_set_for_activation` 增**consumed-param gate**:拒绝任何不在集内的参数(明确 violation:「no runtime consumer wired — activating would be a silent no-op」),保留旧 guard 的安全原则。frozen 名仍走 `FrozenParamViolationError`(更强)。**量化 lane 不受影响**(它用非-activation 的 `validate_param_set`,仍可搜索全 3 family 产 PENDING forward-shadow mandate;仅人工 pin 未接线 family 时被拒并提示)。同时**回退**过早的 `ThemeTierWeights.from_store`(theme runtime 未接线 → 该参数本就被 gate 拒,保留半接线 consumer hook 自相矛盾;待 theme runtime 落地时随其 consumer + 加入 consumed 集一并接)。

## 第 2 轮 — cleanup/altitude finder(最终 diff)

### 🟠 REUSE/ALTITUDE — `RuntimeParamStore.from_lockfile` 复制 `LiveArtifactRegistry.from_lockfile` 的定位/读/解析/归一逻辑(含拷贝错误串 + **异常口径分歧**:bare `Exception` vs `(JSONDecodeError, ValidationError)`)

**问题**:两个 loader 解析同一文件同一 model,各自一份;未来改动易漂移。

**修复**:抽出共享 `live_artifact_registry.load_lockfile(path) -> LiveArtifactLockFile`(单一 fail-closed 定位+解析+错误口径),`LiveArtifactRegistry.from_lockfile` 与 `RuntimeParamStore.from_lockfile` 均委托之。消除重复 + 统一异常口径。

### 🟢 SIMPLIFICATION(注释)— `selector_config_with_params` 的 `int(round(quota))` 注释误称 store「clamp」(实为 reject)

**修复**:改注释如实说明 INT-clamp 校验已拒绝非整数值,`round()` 仅防 float JSON repr;保留防御性 `round`(fail-closed 哲学)。

### 🟢 由设计接受(未改)

- **boot 期 lockfile 解析 3 次**(registry×2 + store×1):boot-only,可忽略;共享 loader 已治漂移,穿线单一 parsed model 属更大重构,超范围。
- **`from_lockfile` 校验失败回落空 store(all-or-nothing)**:§2.2 明授「回落默认 + 大声 audit」;**保留组不变量**(selector 权重 sum=1,部分应用更糟);activation 三门已防坏 param 入 lockfile,仅手工篡改可达 → 回落安全默认 + DEGRADED audit 正确。
- **`value_slot_quota` 不入 `feature_def_hash`**:params 是独立治理轴(lockfile params 块 + AA-004 policy hash 已覆盖,lockfile 在 `POLICY_CONFIG_FILES`);registry 分开 gate artifact 哈希。非 bug。
- **`RUNTIME_CONSUMED_PARAMS`/`FROZEN_BASELINE` 手维护**:与 `FROZEN_NON_EVOLVABLE`/`EVOLVABLE_WHITELIST` 同属显式 fail-closed 治理 allowlist;从代码自动推导「有无 consumer」脆弱有风险。保留为治理事实。

## 门禁(修复后)

- `pytest`:**6070 passed / 14 skipped / 90.14% cov**(基线 6035→+35;`runtime_param_store.py` 100% / `activation.py` 94% / `evolvable_params.py` 99%)。
- `ruff`:clean。`mypy --strict`:clean(strategy_evolution 23 文件 0 issue;仅 selector.py 既存 yaml-stub 单文件解析 quirk,非本次引入)。
- `scripts/redline-check.sh`:全绿([AB-008] evolution git-free + promotion engine confined / [R-002] / [BACKTEST] 等不破)。

## 安全地基红线核对(一条未破)

零 LLM 数值晋升 / objective_promotion 限 strategy_evolution / **晋升主门 = 45 日冻结 forward shadow + 人工 pin + 重启**(本任务只补「pin 后重启生效」最后一段,不动主门)/ config runtime 不可改 + hot-reload 禁(沿用)/ import 隔离(runtime_param_store 仅 import 同包 + stdlib)/ 127.0.0.1 / 空 params = byte-identical。**新增安全收紧**:未接线参数**不可** pin(consumed-param gate),根除「静默 no-op 晋升」类(尤其安全相关止损)。
</content>
</invoke>
