# backend/strategy_evolution/ — 子任务上下文(Phase R)

> 状态:**todo**(Phase R,MVP 阶段 K-N **不写**)。治理:[P2-2-amendment-2026-05-24](../../docs/decisions/P2-2-amendment-2026-05-24-active-discovery-knowledge-graph.md) + R0 §8。任务:plan.html R-001..R-005。

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

## 接口契约(草案)
- `LiveArtifactRegistry.is_approved(hash) -> bool`(startup 载入,无 runtime 加)。
- `StrategyLifecycle` 状态机 + `shadow_validate`(沿用 P0-6)。
