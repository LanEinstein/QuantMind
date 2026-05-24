# P2-2 修订 — 2026-05-24 保守 3 路径 deferred → 主动策略发现 + 知识图谱进化(人工 gate 不变)

> **修订基准**: [P2-2 自进化保守 3 路径 + shadow validate + 飞书通知 + 文件式 registry](./P2-2-self-evolution-conservative-three-paths-shadow-validate-feishu-notify-file-registry.md) + [P2-2 实施计划 2026-05-18](./P2-2-implementation-plan-2026-05-18.md)
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §2 第 5 项 + §8 LiveArtifactRegistry
> **修订日期**: 2026-05-24
> **触发**: Owner 要本地知识库(知识图谱)+ agent 在模拟中不断发现 / 总结 / 验证 / 淘汰策略。`AskUserQuestion` 确认推翻 P2-2「deferred + 严禁主动 mutate」,但**进化自主度锁定「人工 gate + 飞书审批 + 重启生效」**。原 P2-2「deferred 等 owner dedicated session」的解除条件 = 本次 session 即达成。

## 1. 修订前(P2-2 原锁定)

- 启用 3 路径(DSPy GEPA 离线 prompt / RAG provenance-gated 白名单 / FinMem exemplars ≤3);严禁 7 路径(fine-tune / online learning / RLHF / DPO / continual SFT / 自动 mutate config / 新 LLM provider / LLM 自动决策权)。
- 全人工 gate + 飞书主动通知 + 45 日 shadow validate(沿用 P0-6)+ 文件式 prompt registry + git + restart。
- BrokerScheduler 第五 cron `evolution_shadow_run` 22:00;**实施期 deferred 等 owner dedicated 计划文档 session**。

## 2. 修订后(本 amendment 锁定)

### 2.1 新增「主动策略发现 + 知识图谱」(在原 3 路径之上)

- **本地知识图谱**(`backend/knowledge_graph/`,Phase Q):SQLite + NetworkX 起步(LadybugDB 推迟,Kùzu 被 Apple 收购归档),存 策略 + 因子 + 概念 + 板块/标的关系 + 基金经理/操盘手启发式 + **回测 provenance**;Graphiti 式双时态(`t_valid`/`t_ingest`)+ `SUPERSEDES` 边建模策略晋升/退役;**append-only**,退役节点留图保 provenance。LightRAG 检索**离线只读**先行(碰实时辩论才上,且快照 retrieval + index 版本 + doc 哈希入 audit)。
- **冷启动**:qlib Alpha158/360(~600 因子)+ GTJA-191 / WorldQuant-101(按论文重写)+ 编码的操盘启发式 → KG 节点。
- **策略生命周期**(`backend/strategy_evolution/`,Phase R):`candidate → shadow → active → decaying → retired`;agent 发现/验证/淘汰。

### 2.2 人工 gate **不变**(安全核心)

- 发现物(策略 / 知识库更新)经 **45 日 shadow validate**(完全沿用 P0-6:5 稳定性 + 3 策略硬门槛 + challenger 胜判定)→ **飞书主动通知** → **人工起草 amendment + 逐条批准** → **git commit + 重启**才生效。
- **agent 永不自动改决策路径 / 风控 / config**(进化自主度 = 人工 gate,Owner 2026-05-24 锁定)。

### 2.3 `LiveArtifactRegistry`(新增防泄漏单点,R0 §8)

- startup 从**不可变 config** 载入**批准哈希集** `{strategy_code_hash, feature_def_hash, prompt_version_hash, anomaly_model_hash, rag_index_version}`。
- 实时路径**拒绝**任何不在集内的哈希;**无 runtime 路径**加哈希(镜像 hot-reload 禁用 + restart-only)。
- **对抗测试先写**:种入一个**未批准的高 Sharpe** 策略到 KB,断言实时 `CandidateSelector` **不可读 / 不可执行**它;且**有效但未 pin** 的哈希(非仅畸形)也被拒。这是 CLAUDE.md「1139 测试绿但 RiskEngine 没接单」教训的应用——断言泄漏**不可能**,而非 happy path 工作。
- 7 个泄漏路径各有控制(策略 ID 被 live config 引用 / KB 特征被 live selector 消费 / LightRAG 注入未批准规则 / 回测排名自动晋升 / 异动模型未批准重训 / DB 改 runtime 参数 / 缓存 prompt 越过批准边界)——详见 R0 §8 + 实施期任务。

### 2.4 7 禁不变

fine-tune / online learning / RLHF / DPO / continual SFT / 新 LLM provider / **LLM 自动决策权** 仍**全禁**。「主动发现」指 agent 提出候选 + 量化验证,**不是** agent 自动上线。

## 3. 实施期任务调整

- `backend/knowledge_graph/`(Phase Q)+ `backend/strategy_evolution/`(Phase R):各自 dir + CLAUDE.md。严禁 `import backend.{api,broker,risk,llm,agents,mirofish,data}`(防反向调用绕过守门,继承原 P2-2)。
- `LiveArtifactRegistry` + 对抗测试(Phase R 先写测试)。
- BrokerScheduler `evolution_shadow_run` 22:00(第五 cron,原 P2-2 已规划)启用;**MVP 阶段 shadow OUT**(R0 §8),Phase R 启用时给独立 sub-budget,日余额低时降级/跳过(P1-7 amendment)。
- audit:沿用 P2-2 类 5 自进化生命周期 7 类(`prompt_version_pinned` 等;actor=SYSTEM/SCHEDULER,严禁 LLM 写)。

## 4. 红线清单(本 amendment 之后)

1. **人工 gate 不变**:发现物经 45 日 shadow + 飞书 + 人工 amendment + git commit + 重启才生效;agent 永不自动改决策路径 / 风控 / config。
2. `LiveArtifactRegistry` startup 只认批准哈希集;无 runtime 路径加哈希;对抗测试先写(未批准 / 有效未 pin 均拒)。
3. KB **append-only + 双时态 + SUPERSEDES**;退役节点留图;运行期读 pin 的快照。
4. LightRAG **离线只读**先行;碰实时辩论须快照 retrieval + index 版本 + doc 哈希入 audit。
5. 7 禁不变(含 LLM 自动决策权);自进化模块严禁 `import backend.{api,broker,risk,llm,agents,mirofish,data}`。
6. shadow MVP OUT;Phase R 启用给独立 sub-budget,计入同一 `llm:usage:{utc_date}` 计数器(P1-7 amendment),日余额低降级/跳过。
7. exemplars ≤3/prompt + 文件式 prompt registry + git + restart(原 P2-2 不变)。

## 5. 修订记录追加

`docs/plan.html` Phase Q/R 任务 + 修订记录 + SESSION_LOG 同步追加。CLAUDE.md §2.12 自进化「实施期 deferred / 严禁写自进化代码」改写为「Phase R 实施,人工 gate + LiveArtifactRegistry;MVP 阶段不写」。兑现 [[feedback_self_evolution_must_have]]。
