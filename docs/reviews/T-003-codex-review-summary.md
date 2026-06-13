# T-003 Codex 跨模型代码审查报告

**任务**: T-003 — 全异动栈(IsolationForest + ruptures 变点;HMM/OFI/autoencoder 按需 deferred)
**审查时间**: 2026-06-13
**审查轮次**: 1 cycle + 1 read-only 最终复核
**最终判定**: ✅ 通过(经最终复核)

## 审查范围
- `backend/monitoring/anomaly.py`(新检测器 `isolation_forest_anomaly` + `ruptures_changepoint` + `AnomalyConfig.full_anomaly_stack` + env-aware config_hash/feature 版本)
- `backend/monitoring/sell_signal.py`(`SELL_TRIGGER_KINDS` +2 kinds)
- `backend/main.py`(`_anomaly_detector_from_env` env 门控接线)
- `pyproject.toml`(`anomaly-stack` 可选 extra)+ `tests/monitoring/test_anomaly_full_stack.py` + `backend/monitoring/CLAUDE.md`
- amendment `P0-10-amendment-line2-2026-06-13-full-anomaly-stack.md`

## 发现的问题(cycle 1)— 3 × P2,全修(均功能性)

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P2 | main.py | `QUANTMIND_LINE2_FULL_ANOMALY_STACK_ENABLED` 文档存在但从未读;生产 `AnomalyDetector()` 用默认 config → 启用门后仍不生效 | **FIXED**:新 `_anomaly_detector_from_env()` 读 env 构造 `AnomalyConfig(full_anomaly_stack=...)`,两处构造点(daily_runner + rotation scan)均改用 |
| 2 | P2 | pyproject.toml | clean install 中 `scikit-learn` 未声明(注释错称已 default)→ 启用时 IsolationForest 静默全 None | **FIXED**:`anomaly-stack` extra 声明 scikit-learn + ruptures + hmmlearn(均非默认依赖) |
| 3 | P2 | anomaly.py | `ruptures.Pelt` 默认 `jump=5` → 变点落 5 格网(5/10/15),recency 检查(末 2 bar)永不匹配 → CHANGEPOINT 即便装了也无法触发 | **FIXED**:`jump=1` 搜索每个 bar;+ importorskip 安装路径测试 |

## 最终复核(read-only,codex exec)
- 三项全部 **RESOLVED**(env 门控默认"0"仅"1"启用、两构造点均接线;extra 声明三依赖且非默认;Pelt jump=1)。
- 新增 P1 回归:**NONE**(OFF=byte-identical 不变;env 默认 OFF)。

## 门禁
- `ruff` ✅ / `mypy`(anomaly.py)strict ✅ / **273 passed + 1 skipped**(ruptures importorskip)/ `redline-check.sh` 全绿(N-005 monitoring 隔离不变)。
- **OFF = byte-identical 对抗钉死**:默认 config → feature_version v1 + config_hash 与 pre-T-003 7-key payload 逐字节相同;enable → v2 + 新 key。

## 守住安全地基
零 LLM 决策不变;monitoring import 隔离不变(sklearn/ruptures 外部库不在禁列);IsolationForest 确定性(固定种子 + 仅 latest 为窗内最异常点才 flag,精度优先);可选重依赖 lazy-import + fail-closed(缺即 None,不抛不阻塞核心 4 检测器);env 门控 OFF 默认;新 kind 接入既有 SELL 单一构造点路径,不新增构造点。
