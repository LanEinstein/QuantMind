# L-002 Codex 跨模型代码审查报告

**任务**: L-002 — `backend/screening/` 全市场纯量化筛(读 K 快照 → 排除四件套 fail-closed → Alpha158 子集因子 → top-N)
**审查时间**: 2026-05-24
**审查模型**: Claude Opus 4.7(实现/修复)+ Codex CLI gpt-5.5(独立审查)
**审查轮次**: 1 cycle review + 1 read-only final verification
**最终判定**: ✅ 通过(经最终复核 PASS,4 问题全 RESOLVED,0 新 P1 回归)

---

## 审查范围

`codex review --uncommitted`,8 文件 / +1106 −10。核心:`backend/screening/factors.py`(Alpha158 子集纯函数)、`backend/screening/screener.py`(解析 + fail-closed 排除 + 确定性截面排名 + top-N + SignalInputManifest 血缘)、`backend/screening/__init__.py`、`tests/screening/`、`scripts/redline-check.sh` 新增 `[L-002]` 隔离子检。

## 发现的问题(4 全修)

| # | 严重度 | 文件:行 | 问题 | 处理 |
|---|--------|---------|------|------|
| 1 | P1 | screener.py `_rank` | survivor 的某加权因子为 `None`(如 `closes[-21]<=0` → `momentum_20d=None`,或 long MA≤0 → `ma_ratio=None`)时 `float(None)` 抛 TypeError,**整个全市场筛崩溃** 而非 fail-close 该行。 | **FIXED** |
| 2 | P1 | screener.py `_parse_floats` | `nan`/`inf` token 被 `float()` 接受 → 崩 `statistics.pstdev` 或绕过流动性/价格上限(NaN 比较恒 False)。 | **FIXED** |
| 3 | P2 | screener.py manifest | 非默认 `weights`/`board_whitelist`/`ExclusionRules` 改变 shortlist,但 manifest 只记 `top_n_cap`/`min_history_bars`、`config_hashes` 空 → replay 可能"血缘有效却重算出不同候选集"。 | **FIXED** |
| 4 | P2 | screener.py `_parse` | 重复 `ts_code` 只把后一份计 malformed,首份仍可进排名(任意版本),且首份 hash 与 replay parser(dup key → 末行)不一致致重建失败。 | **FIXED** |

### 修复详解

1. **P1 unscorable factor**:`_exclude` 在 price 检查后遍历所有加权因子(`self._weights`),任一 `None` 或非有限 → 返回新 `ExclusionReason.UNSCORABLE_FACTOR`。保证进 `_rank` 的 survivor 加权因子全有限。回归测试 `test_undefined_factor_fails_closed_not_crash`(`closes[-21]=0` → momentum None → unscorable,不崩)。
2. **P1 非有限解析**:`_parse_floats` 对每个 token 加 `math.isfinite` 守门,非有限 → `None` → 行计 `malformed_row`(fail-closed)。回归 `test_nan_token_*` / `test_inf_token_*`。
3. **P2 manifest config**:新增 `Screener._config_hash()` = sha256(canonical JSON of {feature_code_version, top_n_cap, min_history_bars, sorted board_whitelist, weights, exclusion_rules}),写入 `manifest.config_hashes["screening_config"]`。回归 `test_config_hash_reflects_effective_config`(随 top_n_cap 变、同配置稳定)。
4. **P2 重复码**:`_parse` 改两遍——先统计码频,凡频>1 的码**全部副本**丢弃并计 malformed。回归 `test_duplicate_code_all_copies_dropped`(两份 600519 全丢,600002 留,malformed==2)。

## 最终验证(read-only 复核)

`codex exec -s read-only`:**PASS**,4 问题全 **RESOLVED**,NEW P1 regressions **NONE**。

## 门禁

- pytest 全量 **3281 passed / 11 skipped**(screening 47 例:factors 18 + screener 29)。
- ruff:`backend/screening/` + `tests/screening/` 全绿(`backend.data` 经 `# noqa: TID251` 合法引入,llm/agents/mirofish 全局 TID251 仍生效)。
- `scripts/redline-check.sh`:全绿,新 `[L-002]` 子检(screening/budget_policy/candidate_selector 无 `import backend.{llm,agents,mirofish}`)。

## 红线确认

- **0 LLM / import 隔离**:screening 仅 `backend.{data,marketdata_snapshot,services.universe_policy}`;无 llm/agents/mirofish(ruff TID251 + redline `[L-002]` + 单测三重守门)。
- **排除四件套 fail-closed**:stock_meta 缺失/历史<21/缺价/非有限/不可评分 → 一律硬排除,不乐观保留。
- **科创/北交/ST/可转债 永禁**:复用 `classify_board`(`ForbiddenCodeError`)+ `is_st_name` + board 白名单。
- **PIT 可复现**:纯 stdlib 因子 + 读 K 原始字节 + 写 SignalInputManifest(消费行血缘 + 配置哈希);同快照同配置 bit-exact 同 shortlist。
- **top-N 固定上限**:超数由确定性 tie-break(score desc, code asc)收窄,**永不调 LLM 补名**。

---

> 本报告由 Claude Code(Opus 4.7)+ Codex CLI(gpt-5.5)协同生成。
