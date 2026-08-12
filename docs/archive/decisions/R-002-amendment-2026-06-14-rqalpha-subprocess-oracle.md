# R-002 修订 — 2026-06-14 rqalpha 子进程隔离 oracle 接线 + R-002 redline allowlist 放宽(P1a)

> **修订基准**: [P2-2-amendment-2026-05-24 主动发现 + 知识图谱](./P2-2-amendment-2026-05-24-active-discovery-knowledge-graph.md)(R-002 rqalpha 作 test-time 差分 oracle)+ `backend/strategy_evolution/backtest_oracle.py`(R-002 已建:`compare_equity_curves`/`run_differential_check`/`RqalphaBacktestRunner` lazy import 恒 `ORACLE_UNAVAILABLE`)
> **关联**: 自进化 dossier §3.1 + §8.4.6 + §9(codex 对抗审查:oracle 双 lane、防版本差污染决策)+ R0 §3(PIT 可复现)
> **修订日期**: 2026-06-14
> **触发**: 闭合量化参数进化环需 rqalpha 实际可跑(当前恒 UNAVAILABLE)。rqalpha 6.1.5 隔离 venv `/home/ps/rqalpha-smoke-venv` 已验证可装可 import(自取 numpy 2.4.6/pandas 2.3.3,高于主 env → 必须隔离 + 子进程)。

## 1. 修订前(R-002 已决边界)

- rqalpha = **唯一权威 A 股 test-time 差分 oracle**(交叉校验 MockBroker shadow;**永不**第二执行真相、**永不**实时路径);MockBroker 仍单一镜像。
- LICENSE 已读(2026-06-12):Apache 2.0(非商用)+ 商用需米筐授权 → **永不 vendor、不抄代码、仅非商用 pip 依赖**。
- `RqalphaBacktestRunner.run` lazy import,缺失/未配 → `OracleUnavailableError` → `ORACLE_UNAVAILABLE`(fail-closed 非 pass)。run harness「留给 Phase AB」未实现。
- redline `[R-002]`:字符串 `rqalpha` **仅限** `backtest_oracle.py` 一个文件;AST 契约测试:无 strategy_evolution 外模块 import 该 oracle 模块。

## 2. 修订后(子进程隔离 + redline allowlist 放宽)

### 2.1 子进程调用隔离 venv(主 env 零污染)

- `RqalphaBacktestRunner.run(spec)`(主 env,async)→ 写 spec + PIT 数据导出(见 §2.2)到 temp → `asyncio.create_subprocess_exec(QUANTMIND_RQALPHA_VENV_PYTHON, "-m", <runner_entry>, ...)` → 读 result.json → 解析成 `BacktestRunResult`(strategy_hash 自校验)。
- **venv python 路径走 env**(`QUANTMIND_RQALPHA_VENV_PYTHON`,缺省 `/home/ps/rqalpha-smoke-venv/bin/python`);缺失/不可执行/超时/非零退出/解析失败/哈希不符 → `OracleUnavailableError`(沿用 fail-closed,绝不假 pass)。
- **子进程入口脚本 `backend/backtest/rqalpha_entry/`(暂定)零 `backend.*` import**(在 venv 跑,venv 无 backend 依赖);只读自包含 spec.json + PIT 导出文件,import rqalpha,跑回测,写 result.json;**仅 JSON/文件单向交换,无共享内存**。

### 2.2 PIT 数据喂入(同源,Option B)

- rqalpha 读 P1-DATA 摄取的同一份 PIT 快照(`P0-8-amendment-2026-06-14`)派生的**content-addressed 导出**(parquet + manifest sha256 + 复权因子 pin),经子进程自定义数据源喂入;**绝不**另起一路抓数(否则差分 = 数据假象)。导出 sha256 入差分报告 → 可 replay。

### 2.3 摩擦 Mod 对齐 + 校准门(≤25bps)

- rqalpha run config 的 commission/tax/slippage/transfer-fee/T+1/涨跌停 Mod 映射 `config/broker.yaml`(§2.7)。
- **P1a 一次性校准门**:固定已知策略两引擎同 PIT 窗口跑 → 断言 `compare_equity_curves` = CONSISTENT(≤25bps over ≥95% 天);不达标调 Mod。

### 2.4 子进程稳健工程(codex 清单)+ 防版本差

- 超时 + 杀进程组(`start_new_session=True` + killpg);退出码归因(0+结果文件存在且 checksum 有效 = 唯一成功;负码 = 信号杀);结果原子落盘(temp + os.replace)+ checksum 校验后才信;结构化错误信封;POSIX rlimit;`OMP_NUM_THREADS=1`;**两 env(主+oracle venv)numpy/pandas/BLAS 版本指纹入差分 manifest**(归因维度,防版本差伪装成逻辑发散)。
- **oracle 角色澄清(codex J2,本 amendment 锁定)**:rqalpha = **执行/记账层差分对照**(订单流/撮合/费用一致性),**非**跨版本重做策略决策;策略决策层的独立校验由 `backend/backtest/` 的 golden-vector 测试承担(见 `P2-2-amendment-2026-06-14-deterministic-backtest-harness`)。

### 2.5 R-002 redline allowlist 放宽

- redline `[R-002]`:字符串 `rqalpha` 从「单文件 `backtest_oracle.py`」放宽为**显式 allowlist** = `{backtest_oracle.py, backend/backtest/rqalpha_entry/*}`(子进程入口脚本)。**其余约束不变**:rqalpha 永不入实时路径(仅 22:00 cron + 手动 replay 触发的 test-time)、永不 vendor、AST 契约「无主 env 模块 import rqalpha 入口脚本」(入口脚本只被 subprocess 执行,从不被 import)。

## 3. 实施与门禁

- 本 amendment = 边界文档(无代码)→ docs 例外不触 codex。**实施代码任务(P1a)** commit 前过 codex-review + 全量 pytest(主 env,**rqalpha 不入主 env 依赖,5774 基线不动**)+ ruff + redline(`[R-002]` allowlist 更新)+ AST 契约。TDD 对抗测试先写:venv 缺失→UNAVAILABLE / 子进程超时→UNAVAILABLE 不假 pass / stdout 污染不破 JSON(走文件)/ 校准门 CONSISTENT。
- rqalpha venv 安装 = owner 已验证(`/home/ps/rqalpha-smoke-venv`,**勿重装**);env 设定 owner 亲为。

## 4. 红线清单(本 amendment 之后)

1. rqalpha 子进程跑隔离 venv,**主 env 零污染、5774 基线不动**;缺失/失败 → ORACLE_UNAVAILABLE(fail-closed)。
2. rqalpha **永不入实时路径**、永不 vendor、不抄代码(非商用 Apache 2.0);仅 test-time oracle/replay。
3. redline `[R-002]` allowlist = {backtest_oracle.py, rqalpha_entry/*};入口脚本零 backend import、只被 subprocess 执行。
4. PIT 同源(Option B 导出 + checksum);两 env 版本指纹入 manifest;OMP_NUM_THREADS=1。
5. rqalpha = 执行/记账差分层,**非**跨版本重做决策;决策层独立校验归 golden-vector(harness amendment)。

## 5. 修订记录追加

`docs/plan.html` 修订记录 + SESSION_LOG;plan.html P1a 任务。
</content>
