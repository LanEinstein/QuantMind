# P0-10 (Line-2) 修订 — 2026-06-13 全异动栈(IsolationForest + HMM regime + ruptures 变点)

> **修订基准**: [P0-10-amendment-2026-05-25-line2-monitoring-deterministic-construction](./P0-10-amendment-2026-05-25-line2-monitoring-deterministic-construction.md)（Line-2 确定性零 LLM 构造）
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §1 Line-2 + §3 PIT 可复现 + §8 自进化 7 泄漏路径（ANOMALY_MODEL pin）
> **修订日期**: 2026-06-13（Phase T，T-003）
> **触发**: plan.html T-003 — 把 Line-2 异动从 MVP 4 检测器（z-score/量能 z/EWMA/布林）升级到「全异动栈」。设计依据：异动调研 dossier + Codex round-2 §7「按需加，不一次上全栈；autoencoder deferred」。

## 1. 修订前（N-001 MVP 锁定）

- Line-2 `AnomalyDetector` 仅 4 个**单变量**确定性检测器：价格 z-score / 量能 z-score / EWMA 控制图 / 布林突破；各自 self-gate 历史不足返 None；保守 ≥3σ（精度 > 召回，防告警疲劳）。
- 纯函数 over snapshot 字节 + `FEATURE_CODE_VERSION` pin → 离线 replay bit-exact（R0 §3）。
- 零 LLM 决策；SELL/ADD 经 `assemble_monitoring_plan` 单一构造点 + RiskEngine 14-check + 飞书人工。
- `backend/monitoring/CLAUDE.md` 红线 2：「全栈（IsolationForest/HMM/ruptures/OFI）Phase T，**按需加**；autoencoder deferred」。

## 2. 修订后（本 amendment 锁定）

### 2.1 新增检测器（确定性、零 LLM、精度优先、各自 self-gate、env 门控 OFF 默认）

按 Codex round-2 §7「按需加，不一次上全栈」**本期只上 2 个**新异动 KIND（均接入既有 SELL-intent 精度路径，不新增构造点）：

1. **IsolationForest 多变量异常**（`sklearn.ensemble.IsolationForest`，经 `anomaly-stack` extra 声明；当前 dev 环境已装 1.6.0）：
   - 对持仓行 PIT 尾窗的多元特征（日收益、量能比、当日波幅代理）做 **fit-predict**，`random_state` **固定种子** + 固定超参 → **确定性可 replay**（同字节 → 同 verdict）。
   - **关键边界**：这是「按 PIT 尾窗即时拟合的纯检测器」（同 z-score 在窗内即时计算），**不是离线训练后加载的持久化模型** → 不触发 R0 §8 `ANOMALY_MODEL` pin（无被发现/被重训的模型 artifact）；其「数学 + 超参 + 特征版本」由 enable 时的 v2 特征版本 pin（陈旧 replay manifest fail-closed）。**若未来引入离线训练并持久化的异动模型，则必须经 `LiveArtifactRegistry.ANOMALY_MODEL` 批准哈希 + 重启**（本 amendment 不开此路径）。
2. **ruptures 变点检测**（`ruptures`，**未装** → 可选 lazy-import）：尾窗收益序列的结构性变点 → `CHANGEPOINT` 异动；缺依赖即 fail-closed 返 None。

### 2.2 可选重依赖 = lazy-import + fail-closed（镜像 rqalpha R-002，**不装进交易运行时**）

- 全栈三依赖（`scikit-learn`/`ruptures`/`hmmlearn`）均经 `[project.optional-dependencies].anomaly-stack` extra 声明,**不进默认依赖**。沿用 R-002 rqalpha 先例：**可选依赖、lazy-import、缺失即该检测器 fail-closed 返 None**（disabled 默认时系统零额外依赖完整可跑；enabled 但缺依赖时优雅降级）。`ruptures`/`hmmlearn` 当前**未安装**（变点缺依赖即 None）；`scikit-learn` 当前 dev 环境已装故 IsolationForest 测试可跑,clean install 须 `pip install -e '.[anomaly-stack]'`。**永不 vendor、永不抄代码**；本 session **不**改 conda 环境（owner 在整体测试期决定安装）。
- 缺依赖 / 拟合异常 / 样本不足 → 该检测器返 None（**绝不**抛、绝不阻塞既有 4 检测器、绝不误报）。

### 2.3 安全包络（一条未松）

- **零 LLM 决策不变**；import 隔离不变（`backend/monitoring` 严禁 `backend.{llm,agents,agents_team,mirofish}`；sklearn/hmmlearn/ruptures 是外部库，不在禁列）。
- **env 门控 OFF 默认**：新检测器经 `QUANTMIND_LINE2_FULL_ANOMALY_STACK_ENABLED` 门控；OFF = 与 N-001 MVP **bit-for-bit 一致**（生产行为不变直到 owner 启用 + 45 日 shadow）。`config_hash` 纳入新检测器开关 + 特征版本（陈旧 manifest fail-closed）。
- **精度优先**：新检测器接入 SAME SELL-intent 路径（`evaluate_sell_intents` 既有语义），**不新增构造点**；保守阈值，防告警疲劳。
- **regime 安全**：HMM regime 仅 **advisory**；既有**确定性指数派生 regime**（`classify_regime`）对「熊市禁补」仍是**权威**；HMM **只增谨慎、永不放松**既有止损/禁补（同既有红线「止损只紧不松」）。
- **SELL/ADD 仍**经 `assemble_monitoring_plan` 单一构造点 + 14-check + 飞书人工；`available_volume`（T+1）不变。

### 2.4 deferred（WON'T-DO-NOW，记录在案 — 「按需加」）

- **HMM regime（hmmlearn）**：既有**确定性指数派生 regime**（`classify_regime`）对「熊市禁补」已是**权威**；再叠一个 advisory HMM 进决策路径，须严格「只增谨慎、永不放松」才安全，边际价值有限而决策路径风险增加（且 hmmlearn 未装）→ deferred；待 owner 确认价值后单独按本 amendment §2.3 安全语义接入。
- **OFI / VPIN**：需 intraday 逐笔/盘口微结构数据，**当前 PIT 日线快照（closes/amounts）不含** → 需新数据馈线，超出本 amendment 范围，deferred（待 owner 决定是否引入微结构馈线）。
- **autoencoder**：黑盒 + 小样本过拟，沿用 N-001 deferred。

## 3. 实施任务

- `backend/monitoring/anomaly.py`（扩展）：新增 `isolation_forest_anomaly`（纯/sklearn）+ `ruptures_changepoint`（lazy）检测器 + `AnomalyKind.{ISOLATION_FOREST,CHANGEPOINT}` + `AnomalyConfig` 新字段（`full_anomaly_stack: bool=False` + 超参）+ enable 时 v2 特征版本（OFF 时 `_config_hash`/manifest 字节与 v1 一致 = bit-identical）。
- **对抗/隔离测试**：各检测器 self-gate + 缺依赖 fail-closed + 误报率门槛 + env OFF bit-identical（含 config_hash/manifest）+ 确定性 replay（同字节同 verdict）+ AST 模块隔离不变。
- `pyproject.toml`：`ruptures`/`hmmlearn` 作**可选** extra（不进默认依赖，不强装）。
- `backend/monitoring/CLAUDE.md` 红线 2 更新；`redline-check.sh` `[N-005]` 隔离不变。

## 4. 红线清单（本 amendment 之后）

1. Line-2 异动**纯量化、零 LLM 决策**不变；import 隔离不变。
2. 新检测器**确定性可 replay**（IsolationForest 固定种子 + 特征版本 pin）；可选重依赖 **lazy-import + fail-closed**，**不装进运行时、永不 vendor**。
3. **env 门控 OFF 默认** = 与 MVP bit-for-bit；启用经 owner + 45 日 shadow + 重启。
4. HMM regime **advisory**；确定性 regime 对熊市禁补**权威**；新信号**只增卖压/谨慎、永不放松**既有止损/禁补。
5. SELL/ADD 仍经**单一构造点** + 14-check + 飞书人工；`available_volume`(T+1) 不变。
6. **离线训练并持久化的异动模型**若未来引入 → 必须经 `LiveArtifactRegistry.ANOMALY_MODEL` pin + 重启（本 amendment 不开此路径）。
7. OFI/VPIN（缺微结构馈线）+ autoencoder（过拟）deferred。

## 5. 修订记录追加

`docs/plan.html` T-003 + SESSION_LOG 同步；`backend/monitoring/CLAUDE.md` 红线 2 更新。
