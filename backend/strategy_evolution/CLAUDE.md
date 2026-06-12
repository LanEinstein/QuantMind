# backend/strategy_evolution/ — 子任务上下文(Phase R)

> 状态:**R-001 done(#73)+ R-002 done(2026-06-12,session #79:`lifecycle.py` + `backtest_oracle.py` + `anti_overfit.py`)**;R-003/R-004 范围已被 Phase AB 部分吸收(见 plan.html notes)。治理:[P2-2-amendment-2026-05-24](../../docs/decisions/P2-2-amendment-2026-05-24-active-discovery-knowledge-graph.md) + R0 §8。任务:plan.html R-001..R-005。

## 职责
**自进化**:agent 发现/验证/淘汰策略;生命周期 `candidate→shadow→active→decaying→retired`;经人工 gate 进化知识库。

## 本模块红线(进化自主度 = 人工 gate,owner 2026-05-24 锁定)
1. **`LiveArtifactRegistry`**:startup 从**不可变 config** 载入批准哈希集 `{strategy_code_hash, feature_def_hash, prompt_version_hash, anomaly_model_hash, rag_index_version}`;实时路径**拒任何不在集内**的哈希;**无 runtime 路径**加哈希。**对抗测试先写(RED)**:种入未批准高 Sharpe 策略 → 实时 selector 不可读/执行;有效但未 pin 也拒。
2. **人工 gate 不变**:发现物经 **45 日 shadow validate**(完全沿用 P0-6 5+3 硬门槛)→ 飞书通知 → 人工起草 amendment + 逐条批准 → git commit + 重启才生效。**agent 永不自动改决策路径/风控/config**。
3. **7 禁不变**:fine-tune / online learning / RLHF / DPO / continual SFT / 新 LLM provider / **LLM 自动决策权**。"主动发现" = 提候选 + 量化验证,**非自动上线**。
4. rqalpha 作 **test-time 差分 oracle**(交叉校验 MockBroker);MockBroker 仍**单一镜像**,rqalpha **永不入实时路径**。de Prado 防过拟(purged CV / deflated Sharpe)。
5. `evolution_shadow_run` 22:00(BrokerScheduler 第五 cron)+ **独立 sub-budget**(日余额低降级/跳过;MVP 阶段 OUT);audit 类 5 七类 actor=SYSTEM/SCHEDULER,**LLM 严禁写 audit**。

## import 隔离
严禁 `import backend.{api,broker,risk,llm,agents,mirofish,data}`(继承 P2-2;防绕过守门)。可用:`backend.knowledge_graph` + `backend.marketdata_snapshot`(回测取数)+ rqalpha。

## 接口契约
- **`LiveArtifactRegistry`(R-001 已实现,`live_artifact_registry.py`)**:`from_lockfile("config/live_artifacts.lock.json")` boot 载入(fail-closed:缺文件/坏 JSON/坏 schema/非 sha256/未知 kind 全 raise)+ `from_lock(lock)`(in-memory/测试)。`is_approved(kind: ArtifactKind, identifier) -> bool` **kind-typed**(草案的 `is_approved(hash)` 精化为 5 类分型,防策略哈希批准 prompt;在已决 5 类边界内,非新 amendment)+ `approved(kind) -> frozenset[str]`。5 类 `ArtifactKind` = strategy_code/feature_def/prompt_version/anomaly_model/rag_index,全 sha256 hex(content-addressed)。**完全不可变**(`__setattr__` raise,无 approve/add/reload/promote;无 runtime 加哈希路径);空 bootstrap = deny-all。晋升经 amendment+pin+git+restart。
- **生命周期(R-002 已实现,`lifecycle.py`)**:`StrategyLifecycleState` 5 态 + `ALLOWED_LIFECYCLE_TRANSITIONS` allowlist(RETIRED 终态,无出边)+ `transition_lifecycle`(唯一转移入口;**ACTIVE 必须 registry pin** —— `is_approved(STRATEGY_CODE, hash)` 不过即 `UnapprovedStrategyError`,无 registry 同拒)+ `MongoLifecycleLedger`(append-only `strategy_lifecycle_events`;current state = fold;**retired 哈希永不可 re-propose**)。
- **差分 oracle(R-002 已实现,`backtest_oracle.py`)**:`BacktestRunner` 注入式 Protocol;`compare_equity_curves` 纯函数(共享日 |diff| ≤25bps、散点日 ≤5% 才 CONSISTENT)+ `run_differential_check`(oracle 失败 → `ORACLE_UNAVAILABLE`,**非 pass、不抛**)。`RqalphaBacktestRunner` lazy import 可选依赖;**rqalpha LICENSE 已读(2026-06-12):Apache 2.0 + 商用需米筐书面授权 —— 永不 vendor、不抄代码、仅非商用 pip 依赖;真实 run harness 归 Phase AB(数据 bundle + Mod 配置),此前恒 UNAVAILABLE fail-closed**。redline `[R-002]` + AST 契约测试钉死 rqalpha 不入实时路径。
- **防过拟(R-002 已实现,`anti_overfit.py`)**:`purged_kfold_splits`(purge+embargo)+ `deflated_sharpe_ratio`(PSR vs E[max SR | N trials],Bailey-de Prado 公式自推导)+ `meets_anti_overfit_bar`(DSR ≥0.95);纯数学零 IO,AB 晋升门消费。
- `shadow_validate`(沿用 P0-6)。**todo**(R-003,sim 范围由 AB 客观晋升替代)。
