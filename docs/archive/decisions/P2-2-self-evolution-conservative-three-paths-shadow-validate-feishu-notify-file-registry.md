# P2-2 — 自进化机制边界(保守 3 路径 + 全人工 gate + 飞书主动通知 + 45 日 shadow validate + 文件式 prompt registry)

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P2-2 |
| 决策日期   | 2026-05-11 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §P2-2(自进化机制边界 ⏳ → ✅)|
| 前序决策   | `docs/decisions/P2-decisions-finalization-2026-05-10.md` §2(P2-2 deferred to dedicated session;用户 critical feedback 自进化必须有 + 模拟盘验证 + 状态回滚)|
| 用户 critical feedback | `~/.claude/projects/-home-ps-papers-QuantMind/memory/feedback_self_evolution_must_have.md`(2026-05-10 P2 收官时锁;原话:"自进化功能是必须要有的,可以引入'自我进化后必须经过模拟盘验证'以及状态回滚,但绝对不能完全禁止。大模型如果没有持续学习/持续适应新变化/持续追踪最前沿量化以及金融交易思路的能力,就一定无法长久立于不败之地。具体采用怎样的策略,我们可以单开一个 session 仔细调研和讨论。")|
| 依赖决策   | `docs/decisions/P0-1-simulation-base-feishu-overlay.md`(§2 红线 8 LLM 不决定股数/价格/风控边界)+ `docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md`(§2 红线 12 frozen Pydantic strict + extra='forbid')+ `docs/decisions/P0-6-acceptance-45-day-rolling-stability-and-strategy-gates.md`(§1.1 滚动窗口语义 + §1.2/§1.3 5+3 硬门槛)+ `docs/decisions/P0-7-risk-redlines-position-circuit-universe-llm-immutability.md`(§1.4 RiskConfig 全锁 + risk_parameter_proposals 提议通道 + §2 红线 14 hot-reload 禁用)+ `docs/decisions/P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md`(§2 红线 14 evidence_id 5 前缀约定不变;MiroFish 输出仅入 evidence_collection)+ `docs/decisions/P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md`(§1.1 LLM positive list 4 类 + §1.2 LLM negative list 8 类累积 + §2 红线 1 字段权限矩阵)+ `docs/decisions/P1-2.A-persistence-hybrid-snapshot-and-broker-scheduler.md`(§1.4 BrokerScheduler 3 cron + §1.6 EOD chain 失败处理)+ `docs/decisions/P1-5-frontend-workflow-mvp-7-pages-readonly-first-write-strict-bounded.md`(§1.2 写入接口 Phase A 一次性破坏式删除 + §2 红线 1 仅 2 写入端点)+ `docs/decisions/P1-6-secrets-shell-env-12month-event-driven-rotation-loopback-only-no-local-auth-audit-mongo-jsonl-dual-write.md`(§1.1 凭证池仅 LLM 3 + 飞书 6 锁状态 + §1.5 全层 127.0.0.1 only + §1.7 audit schema + §1.8 AuditEventType 22 类 + §2 红线 12 任何新增/删除/重命名必走 amendment + §2 红线 16 LLM 严禁写 audit_events + §2 红线 17 4 类事件强制写 audit)+ `docs/decisions/P1-7-cost-budget-llm-only-monthly-440-daily-20-kimi-cap-4-soft-degrade-feishu-alert.md`(§1.1 LLM 总日 ¥20 hard + 月 ¥440 soft + Kimi ¥4 daily cap + §1.7 告警通道仅飞书 + §2 红线 8 严禁 SMTP/Slack/Discord 第二通道)|
| 派生 amendment | (1) `docs/decisions/P0-7-amendment-2026-05-11-risk-proposals-shadow-validation.md`(risk_parameter_proposals 扩字段 + 走 P2-2 shadow validate 流程;非破坏式扩展)(2) `docs/decisions/P1-2.A-amendment-2026-05-11-evolution-shadow-cron-5th.md`(BrokerScheduler 4 → 5 cron;新增 evolution_shadow_run 22:00 mon-fri;非破坏式追加)(3) `docs/decisions/P1-6-amendment-2026-05-11-audit-eventtype-34.md`(AuditEventType 27 → 34 类;新增 7 类自进化生命周期;非破坏式追加)|
| 不锁定的 | 实施期推进时间表 — 待用户在 dedicated 计划文档 session 锁定后才允许 Phase X 实施代码启动;本决策仅锁红线 + 任务清单粒度 |

## 决策摘要

QuantMind 自进化机制采用 **保守 3 路径 + 全人工 gate + 飞书主动通知 + 45 交易日 shadow validate + 文件式 prompt registry + BrokerScheduler 第五 cron + RAG 白名单永锁 + frontier 每日 22:00 抓取 + risk_proposals 合并 P2-2 + audit 27→34 类 + 实施期 deferred 等用户计划文档 + Codex 5 轮在计划文档后启动** 架构:

1. **启用自进化 3 路径**(保守集;Round 1 Q1):DSPy GEPA 离线 prompt 演化 + RAG provenance-gated 知识库自扩展 + FinMem 风格 in-context exemplars(从 decision_ledger 历史成功 case 衰减检索)。tiered routing 沿用 P0-10 已锁。**严禁** fine-tune / online learning / RLHF / DPO / continual SFT / 自动 mutate config / 引入新 LLM provider / LLM 自动决策权。

2. **全人工 gate + 飞书主动通知**(Round 1 Q2):任何自进化产物(prompt 新版本 / RAG 新文档 / proposal / amendment 草案)必须经过人工最终批准 + amendment + restart 才生效。LLM 严禁直接 mutate 任何 runtime 状态。**用户关键约束(Round 1 Q2 自由输入)**:有自进化提议待处理时,系统主动发飞书告诉用户"有待进化,该处理进化了";走 P1-7 §1.7 锁定的 `FEISHU_CUSTOM_BOT_WEBHOOK_URL` 通道(继承 P0-2 §2.5 备用 webhook 仅可发系统告警);严禁 SMTP/Slack/Discord 第二通道(继承 P1-6 §1.1 凭证池封闭性)。

3. **Shadow validate 沿用 P0-6 45 交易日**(Round 1 Q3):完全复用 P0-6 acceptance 框架(`compute_acceptance_window` + `acceptance_reports` collection + 5 稳定性硬门槛 + 3 策略硬门槛 + 沪深 300 基准);shadow chain 二支线复用同套基础设施;验证通过 + 5+3 硬门槛达标 + production 基准被占优(challenger 胜)才允许 promote。严禁缩短到 30 日或拉长到 60 日(任何调整必走 P2-2 amendment + P0-6 amendment 双批准)。

4. **状态回滚:文件式 prompt registry + git + restart**(Round 1 Q4):新增 `config/prompts/{agent}/{version}.yaml` + `config/prompts.lock.json`(MLflow-shaped 文件版本化语义);RAG 数据 `data/rag/{source}/{date}/{doc_id}.md` + `data/rag/provenance.jsonl`(URL/commit/scanned_at/llm_summary_model/llm_tokens);版本切换 = `git commit` + amendment + restart。严禁引入 MLflow / LangSmith / 任何外部 hosted prompt registry(违反 P1-6 §1.5 全层 127.0.0.1 单实例原则);严禁 runtime mutate(继承 P0-7 + P0-10 hot-reload 禁用)。

5. **Shadow chain 实现位置:BrokerScheduler 第五 cron**(Round 2 Q1):派生 P1-2.A amendment(4 → 5 cron;新增 `evolution_shadow_run` 22:00 mon-fri Asia/Shanghai);在 EOD chain 16:00:30 + MiroFish 17:00 之后;读近 45 日 acceptance_reports;用待验证 prompt 版本重跑决策链;输出 shadow_acceptance_report;与 production acceptance 对比 5+3 硬门槛。复用 P1-7 cost_guard(计入 daily ¥20 hard)。

6. **飞书通知触发条件:shadow 45 日验证通过即发**(Round 2 Q2):shadow run 通过 + 5 稳定性硬门槛达标 + 3 策略硬门槛达标 + production 基准被占优(challenger 胜)→ 立即飞书通知 + 写 audit `evolution_feishu_notified`。Alerter dedup_15min 防轰炸(继承 P1-7 §1.7)。严禁周汇总或日汇总(用户明确要"实时通知有待进化")。

7. **RAG 数据源白名单**(Round 2 Q3):仅允许 arxiv.org(q-fin/cs.LG/cs.AI) + semanticscholar.org + openreview.net + github.com/{qlib,vn.py,freqtrade,NautilusTrader,TradingAgents,DSPy,jesse,FinGPT,FinRobot} releases + akshare/adata/baostock changelogs。**严禁** Twitter/X/Reddit/贴吧/任意 blog/未白名单 PDF(防 prompt injection)。`rag_ingester` 启动期 fail-fast 校验白名单源;非白名单源拒绝入库即写 audit `rag_document_rejected_non_whitelist`。

8. **Frontier 调度频率:每日 22:00 mon-sun**(Round 2 Q4):arxiv q-fin/cs.LG 近 24h 新增 + semanticscholar 近 24h cited papers + github qlib/vn.py/freqtrade/NautilusTrader/TradingAgents/DSPy/jesse/FinGPT/FinRobot 近 24h releases + akshare changelog。DeepSeek 总结(~¥0.05-0.10/日,远低于 ¥20 daily cap)。入库提议 → 人工 review。严禁周汇总或月汇总(及时性优先)。

9. **Risk_parameter_proposals 合并 P2-2 体系**(Round 3 Q1):已有的 P0-7 §1.4 `risk_parameter_proposals` 走 P2-2 同款 `evolution_shadow_run` cron + 45 日 shadow validate + 飞书通知 + 自动起草 P0-7-amendment。人工仅 review + sign-off,不再走 P0-7 §1.4 周报 review 流程。`risk_parameter_proposals` collection 扩字段(`target_artifact_type` discriminator + `shadow_validation_status` + `pending_amendment_id`);派生 P0-7-amendment-2026-05-11。

10. **Audit 27 → 34 类**(Round 3 Q2):新增 7 类自进化生命周期事件 — `prompt_version_pinned` / `prompt_version_rolled_back` / `rag_document_ingested` / `rag_document_rejected_non_whitelist` / `shadow_evolution_run_completed` / `evolution_amendment_drafted` / `evolution_feishu_notified`。归类 5 自进化生命周期(本 amendment 新建类别 5;归类 1-4 继承 P1-6)。actor=SYSTEM 或 SCHEDULER(严禁 LLM/frontend_user/feishu_user 直接写 evolution 类 audit)。派生 P1-6-amendment-2026-05-11(P1-6 第 3 次 amendment)。

11. **Frontier 输出仅写文件系统**(Round 3 Q3):`data/rag/{source}/{date}/{doc_id}.md` + `data/rag/provenance.jsonl`。**不入 Mongo**(零 P0-8 amendment);**严禁写 evidence_collection**(防破 P0-8 §2 红线 14 evidence_id 5 前缀约定 NEWS-/MIROFISH-/MARKET-/RISK-/DEBATE-);严禁创建 frontier_papers collection。零 schema 负担;LangChain DocumentLoader 加载 RAG 文件提供给 shadow validate。

12. **实施期 deferred 等用户计划文档**(Round 3 Q4 自由输入):本 session 仅锁红线 + 任务清单粒度。**严禁本 session 写任何自进化代码**(包括 prompt registry / RAG ingester / shadow chain / frontier crawler 任一 Python 文件);严禁修改任何 backend/ frontend/ config/ 文件除文档外;Phase A/B/B-finale 进行期间也严禁写自进化代码。**待用户在 dedicated 计划文档 session 锁定推进时间表后才允许 Phase X 实施期启动**。

13. **实施期任务清单 H-001~H-028 完整列出标 deferred**(Round 4 Q1):沿用 E/F/G 系列命名规范;28 个任务分 5 块(Phase X-A 基础设施 7 + Phase X-B 自进化执行 8 + Phase X-C audit+守门 5 + Phase X-D 前端 3 + Phase X-E Codex review 5);§3 顶部明标"本清单 deferred 等用户计划文档实际排程跟进,与现 E/F/G 系列 Phase A/B 排程不同期"。

14. **派生 3 amendment 永锁**(Round 4 Q2):(1) P0-7-amendment-2026-05-11-risk-proposals-shadow-validation(risk_parameter_proposals 扩字段 + 走 P2-2 shadow validate 流程);(2) P1-2.A-amendment-2026-05-11-evolution-shadow-cron-5th(BrokerScheduler 4→5 cron;evolution_shadow_run 22:00 mon-fri);(3) P1-6-amendment-2026-05-11-audit-eventtype-34(AuditEventType 27→34 类;新增 7 类自进化生命周期)。**零 P0-8/P0-10/P1-7 amendment**(用户选项已锁零冲突)。

15. **清理顺序:文档先锁,清理在另 session**(Round 4 Q3):本 session 严禁修改 backend/ frontend/ 任何代码;P0-1 旧 AUTHORIZATION_MODE/QUANTMIND_PHASE 矩阵 + Phase 5B 遗留 + ApprovalQueue / Settings 写入接口 — 全部在 Phase A 实施期一次性破坏式删除(履行 P1-5 §1.2 锁定);本 session 不顺手清理(避免决策期与实施期混杂、保持联合备份点裂解)。

16. **Codex review hard gate 节奏:计划文档 session 后启动**(Round 4 Q4):本 session 不跑 Codex(决策文档无代码,R3 SDK signatures + R5 coverage 抽象);Codex 5 轮 R1-R5(R1 consistency + R2 red-team + R3 SDK + R4 security + R5 coverage)在 Phase X 实施期跑;计划文档 session 也不跑(计划文档纯文本无代码无 SDK);每轮 review 必存 `docs/reviews/H-XXX-rN-{topic}.md`。

17. **DSPy GEPA 离线 prompt 演化运行约束**:运行模式 = batch offline(类似 fine-tune offline batch);严禁实时运行(避免 LLM cost 失控);单次 GEPA compile 任意 ≤ ¥5(continuous monitor;超 raise `GEPABudgetExceededError`);GEPA 输出仅写 `config/prompts/{agent}/{candidate}.yaml` + git diff;严禁直接覆盖 production version;promote 必经 shadow validate + 飞书通知 + 人工 amendment + restart。

18. **In-context exemplars FinMem 风格约束**:exemplar 来源仅 `decision_ledger` 当前 simulation_auto 历史成功 case(限近 90 日);严禁手工注入 exemplars;严禁从未通过 RiskEngine 14-check 的 case 抽取;衰减检索算法 = recency weighting + relevance scoring + diversity sampling;严禁超过 3 exemplars per prompt(防 cost 飙升);每日 22:00 evolution_shadow_run 时刷新 exemplars 集合。

19. **cost_guard 计入 P2-2 LLM 调用**:DSPy GEPA + frontier_crawler DeepSeek 总结 + shadow_chain LLM 重跑 + amendment_drafter LLM 起草 — 全部计入 P1-7 daily ¥20 hard ceiling + monthly ¥440 soft + Kimi ¥4 daily cap;不绕过 cost_guard;严禁 P2-2 单独预算池(继承 P1-7 §1.4 LLM only 预算锁定;不引入新预算维度)。

20. **自进化模块依赖隔离**:`backend/services/{evolution_dispatcher,frontier_crawler,rag_ingester,amendment_drafter,dspy_gepa_runner,shadow_chain,prompt_registry,rag_provenance,exemplar_selector,evolution_feishu_notifier,evolution_audit_writer}.py` 严禁 import `backend.{api,broker,risk,llm,agents,mirofish,data}`(防破 P0-10 §2 红线 1 LLM 字段权限矩阵;防 LLM/agents 反向调用 evolution_dispatcher.activate_* 绕过守门)。

## 1. 决策具体内容

### 1.1 自进化路径启用范围(保守 3 路径)

#### 1.1.1 启用 3 路径

| 路径 | 实现技术 | 输出物 | 持久化 | 计入 cost_guard | 实施期任务 |
|------|---------|-------|--------|----------------|----------|
| **Prompt 演化** | DSPy GEPA(Reflective Prompt Evolution;`gepa-ai/gepa` v0.x+ 兼容)| 新候选 `config/prompts/{agent}/{candidate}.yaml`(staging alias) | 文件 + git 版本化 | ✅(每次 GEPA compile ≤ ¥5) | H-001 / H-003 / H-009 |
| **RAG provenance-gated 知识库自扩展** | LangChain DocumentLoader + custom whitelist validator | `data/rag/{source}/{date}/{doc_id}.md` + provenance JSON | 文件 + git 版本化 + audit hook | ✅(frontier_crawler DeepSeek 总结 ~¥0.05-0.10/日) | H-002 / H-004 / H-010 / H-011 |
| **In-context exemplars** | FinMem 风格衰减检索(`pipiku915/FinMem-LLM-StockTrading` 模式) | 内存中 exemplar 列表(每日 22:00 刷新) | 内存 + decision_ledger 反向索引 | ✅(prompt 注入 exemplar 时 token 增加计入) | H-006 |

#### 1.1.2 严禁路径(共 7 类)

```
1. Fine-tuning(LoRA / QLoRA / full SFT)         # 需 GPU + 训练 infra;违反"单实例无 GPU"
2. Online learning(real-time gradient update)   # 同上
3. RLHF / DPO / 连续 SFT                          # 同上
4. 自动 mutate config(任何 *.yaml 文件)           # 违反 P0-7 hot-reload 禁用 + P0-10 LLM 字段权限矩阵
5. 自动 mutate runtime state(BudgetState / RiskConfig 等 frozen Pydantic) # 同上
6. 引入新 LLM provider(claude/gpt-4o 等)         # 违反 P1-6 §1.1 凭证池仅 LLM 3 + 飞书 6 锁定
7. LLM 自动决策权(LLM 直接写 InstructionPlan)    # 违反 P0-10 §2 红线 1 LLM 字段权限矩阵
```

#### 1.1.3 与 P0-10 LLM positive list 兼容性

本决策**不扩展** P0-10 §1.1 LLM positive list 4 类(保持 `InstructionPlan.reasoning` / `evidence_collection.content` / `agent_debate_records.{reasoning_text, conclusion}` / `risk_parameter_proposals.proposal_text`)。理由:

- DSPy GEPA 是 batch offline 运行(类似 fine-tune offline batch),不是 runtime LLM 写 Mongo collection;输出写 `config/prompts/` 文件而非 LLM 直接 mutate
- RAG 文档写 `data/rag/` 文件而非 LLM 写 Mongo collection;`evolution_dispatcher` 由 SYSTEM/SCHEDULER actor 写而非 LLM 直接写
- amendment 草案写 `pending_amendment.md` 文件而非 LLM 写 Mongo collection
- `risk_parameter_proposals` 已有 `proposal_text` 字段在 LLM positive list;扩字段 `target_artifact_type` 等由代码自动绑定(LLM 不写)

零 P0-10 amendment 是用户 Round 4 Q2 已锁路径(零冲突);保持 LLM 字段权限矩阵的极简性。

### 1.2 Human-in-the-loop 严格度(全人工 gate + 飞书主动通知)

#### 1.2.1 全人工 gate 流程

任何自进化产物 promote 到 production 必经 4 步:

```
1. LLM 产出候选(prompt evolution / RAG ingest / risk proposal / exemplar 选择)
   ↓ (写 config/prompts/{agent}/{candidate}.yaml 或 data/rag/... 或 risk_parameter_proposals collection)

2. evolution_shadow_run cron 22:00 mon-fri 触发 shadow validate
   ↓ (45 交易日窗口;复用 acceptance_reports + compute_acceptance_window;输出 shadow_acceptance_report)

3. 5 稳定性 + 3 策略硬门槛比对
   ↓ (challenger 胜 production 才允许 promote)

4. 飞书主动通知用户 + audit `evolution_feishu_notified`
   ↓ (Alerter.fire 走 FEISHU_CUSTOM_BOT_WEBHOOK_URL;dedup_15min)

5. 自动起草 pending_amendment.md(amendment_drafter 写 docs/decisions/pending/{artifact_id}.md)
   ↓

6. 用户 review pending_amendment.md
   ↓ (人工最终批准)

7. 用户编辑 YAML/移动 prompt registry version alias + 起草正式 amendment + 重启
   ↓

8. 重启加载新版本 production alias
```

**关键约束**:

- 步骤 1-5 全自动(SYSTEM/SCHEDULER actor)
- 步骤 6-8 全人工(项目所有者)
- 严禁系统跳过任一步骤
- 严禁人工不 review 直接走步骤 7(决策文档 commit + restart 即视为人工 sign-off,但 review 责任在人)

#### 1.2.2 飞书主动通知约束

**用户 Round 1 Q2 自由输入原话**:

> 必须通过 feishu 通知我,有待进化,让我知道什么时候该处理进化了。

**实现方式**:

- `backend/services/evolution_feishu_notifier.py::EvolutionFeishuNotifier::fire_pending`
- 触发条件:shadow validate 通过 + 5+3 硬门槛达标 + production 基准被占优(challenger 胜)
- webhook URL:`FEISHU_CUSTOM_BOT_WEBHOOK_URL`(沿用 P1-7 §1.7 锁定不引入新通道)
- 签名:`FEISHU_CUSTOM_BOT_SIGN_SECRET`(沿用)
- dedup:Alerter dedup_15min(继承 P1-7 §1.7;防同一 artifact 重复发)
- 严禁 SMTP / Slack / Discord 第二通道(继承 P1-6 §1.1 凭证池封闭性)

**飞书文案模板**(P0-4 §1 五模板基础上新增第六模板;由 `backend/feishu/renderer.py::render_evolution_pending` 函数模板硬编码生成;LLM 严禁拼接 — 继承 P0-3 §2 红线 8):

```
【自进化待处理】[{shadow_run_id}]

类型:{prompt_version|rag_document|risk_proposal|exemplar}
artifact:{artifact_id}

Shadow validate(45 交易日):
  ✅ 指令完整率:{prod}% → {shadow}%
  ✅ 回报解析准确率:{prod}% → {shadow}%
  ✅ 数据缺失率:{prod}% → {shadow}%
  ✅ LLM 超时率:{prod}% → {shadow}%
  ✅ 信号生成成功率:{prod}% → {shadow}%
  ✅ 最大回撤:{prod}% → {shadow}%
  ✅ 累计 PnL:¥{prod} → ¥{shadow}
  ✅ 沪深 300 累计超额:{prod}% → {shadow}%

challenger 胜 production:✅
建议 amendment 草案:docs/decisions/pending/{artifact_id}.md

请 review + 走 amendment + 重启
```

### 1.3 Shadow validation 沿用 P0-6 45 交易日

#### 1.3.1 复用 P0-6 基础设施

```python
# backend/services/shadow_chain.py(实施期 H-007)

from backend.services.acceptance_window import compute_acceptance_window
from backend.services.acceptance_report import AcceptanceReport
from backend.repositories.acceptance_repo import AcceptanceRepository

async def run_shadow_validate(
    *,
    artifact_type: Literal["prompt_version", "rag_document", "risk_proposal", "exemplar"],
    artifact_id: str,
    today: date,
    trading_calendar: TradingCalendar,
    p0_interrupts: list[P0InterruptRecord],
    reconciliation_freezes: list[FreezeWindow],
) -> ShadowAcceptanceReport:
    """45 交易日 shadow validate;完全复用 P0-6 框架。

    Args:
        artifact_type: 待验证产物类型(4 选 1)
        artifact_id: 产物 ID
        today: 当前日期(同 P0-6 acceptance_window 入参)
        其他参数: 继承 P0-6 不变

    Returns:
        ShadowAcceptanceReport: 与 production AcceptanceReport 同结构 + 5+3 硬门槛 + challenger vs production 对比

    依赖:
        - 读 acceptance_reports collection(近 45 日)
        - 替换决策链中的 prompt/RAG/exemplar 为待验证 artifact
        - 重跑 simulation_auto 决策链(纯 replay,不写 MockBroker;走 ShadowMockBroker)
        - 输出 shadow_acceptance_reports collection(独立于 production acceptance_reports)
    """
    window = await compute_acceptance_window(today, ...)  # 复用 P0-6

    if not window.is_complete:
        raise ShadowValidateInsufficientHistoryError(
            f"Need 45 trading days; got {len(window.eligible_days)}"
        )

    # 用 artifact_id 替换决策链对应组件并重跑
    shadow_chain = build_shadow_chain(artifact_type, artifact_id)
    shadow_metrics = await replay_decisions(shadow_chain, window)

    # 与 production 对比 5+3 硬门槛
    return compare_against_production(shadow_metrics, window)
```

**约束**:

- ShadowAcceptanceReport 是 frozen Pydantic v2 strict + extra='forbid' + 5+3 硬门槛字段同 P0-6 不变
- shadow_chain 不写真实 MockBroker(避免污染 production 状态;走 ShadowMockBroker 临时实例)
- shadow run 失败(数据不足 / LLM 超时 / 超 cost cap)→ ShadowAcceptanceReport 状态 = FAILED(不 promote;但仍写 audit `shadow_evolution_run_completed` outcome=FAILURE)

#### 1.3.2 challenger 胜判定逻辑

```python
def is_challenger_winning(
    *,
    shadow: ShadowAcceptanceReport,
    production: AcceptanceReport,
) -> bool:
    """challenger 胜 production 当且仅当:

    1. 5 稳定性硬门槛全部达标(继承 P0-6 §1.2;≥95/99/1/5/95%)
    2. 3 策略硬门槛全部达标(继承 P0-6 §1.3;≤8% drawdown + PnL ≥0 + 沪深 300 超额 ≥0)
    3. challenger ≥ production 在以下 4 项:
       - 累计 PnL
       - 沪深 300 累计超额
       - 信号生成成功率
       - 指令完整率
    4. challenger 不显著差于 production 在以下 4 项(差 ≤ 0.5 个百分点):
       - 最大回撤
       - LLM 超时率
       - 数据缺失率
       - 回报解析准确率

    任一不达标 → False(不发飞书通知)
    """
```

#### 1.3.3 与 P0-6 acceptance 边界

- shadow validate 仅判定"自进化 artifact 是否可 promote";**不影响** P0-6 acceptance(`can_switch_to_feishu_on()` 仍由 production acceptance 判定)
- shadow_acceptance_reports 是独立 collection(不影响 P0-6 acceptance_reports;两者分离不混)
- 任何 shadow run 失败 → 不影响 production simulation_auto 继续跑

### 1.4 状态回滚机制:文件式 prompt registry + git + restart

#### 1.4.1 目录结构(新增)

```
config/
├── agent_models.yaml         # P0-10 已锁;runtime 不可改;不动
├── broker.yaml               # P1-2.C 已锁;不动
├── data_sources.yaml         # 不动
├── mirofish.yaml             # 不动
├── risk.yaml                 # P0-7 已锁;不动
├── watchlist_policy.yaml     # P0-9 已锁;不动
├── prompts/                  # P2-2 新增 ★
│   ├── fund_manager/
│   │   ├── v1.yaml           # production alias = v1
│   │   ├── v2.yaml           # staging alias = v2(shadow validate 中)
│   │   └── v3-candidate.yaml # GEPA 输出候选
│   ├── risk_officer/
│   │   ├── v1.yaml
│   │   └── ...
│   ├── fundamental_analyst/
│   │   ├── v1.yaml
│   │   └── ...
│   └── technical_analyst/
│       ├── v1.yaml
│       └── ...
└── prompts.lock.json         # P2-2 新增 ★(锁定每 agent 的 production alias / staging alias)

data/
└── rag/                      # P2-2 新增 ★
    ├── arxiv/
    │   └── 2026-05/
    │       └── {paper_id}.md
    ├── semanticscholar/
    │   └── 2026-05/...
    ├── openreview/
    │   └── 2026-05/...
    ├── github/
    │   ├── qlib/
    │   │   └── {release_id}.md
    │   ├── vn.py/...
    │   ├── freqtrade/...
    │   ├── NautilusTrader/...
    │   ├── TradingAgents/...
    │   ├── DSPy/...
    │   ├── jesse/...
    │   ├── FinGPT/...
    │   └── FinRobot/...
    ├── changelogs/
    │   ├── akshare/...
    │   ├── adata/...
    │   └── baostock/...
    └── provenance.jsonl      # provenance 记录(append-only)

docs/decisions/pending/        # P2-2 新增 ★(amendment 草案落地点)
└── {artifact_id}.md           # amendment_drafter 输出;人工 review 后移动到 docs/decisions/
```

#### 1.4.2 prompts.lock.json schema

```json
{
  "schema_version": 1,
  "locked_at": "2026-05-11T22:00:00Z",
  "agents": {
    "fund_manager": {
      "production": "v1",
      "staging": "v2",
      "candidates": ["v3-candidate"]
    },
    "risk_officer": {
      "production": "v1",
      "staging": null,
      "candidates": []
    },
    ...
  }
}
```

- runtime 启动期读 `prompts.lock.json` 决定每个 Agent 加载哪个 prompt 版本
- 严禁 runtime mutate(继承 P0-7 §2 红线 14)
- 版本切换 = `git diff prompts.lock.json` + amendment + restart

#### 1.4.3 RAG provenance.jsonl 字段

```jsonl
{"doc_id": "arxiv-2024-XXXXX", "source_type": "arxiv", "url": "https://arxiv.org/abs/...", "commit_hash": null, "scanned_at": "2026-05-11T22:00:15Z", "llm_summary_model": "deepseek-v4-pro", "llm_tokens": 1234, "title": "...", "authors": [...], "published_at": "2026-05-10"}
{"doc_id": "github-qlib-v0.X.Y", "source_type": "github", "url": "https://github.com/microsoft/qlib/releases/tag/v0.X.Y", "commit_hash": "abc123...", "scanned_at": "2026-05-11T22:00:30Z", "llm_summary_model": "deepseek-v4-pro", "llm_tokens": 567, "title": "qlib v0.X.Y release notes", "release_at": "2026-05-09"}
```

- append-only 严禁 update/delete(继承 P1-2.A broker_events 8 项红线精神)
- 文件锁 fcntl flock(防并发写损坏)

### 1.5 BrokerScheduler 第五 cron(evolution_shadow_run 22:00 mon-fri)

派生 P1-2.A amendment:BrokerScheduler 4 → 5 cron。

#### 1.5.1 5 cron 编排

```
BrokerScheduler(单实例 APScheduler;继承 P1-2.A § 1.4):

  cron 1: eod_pipeline                  16:00:00 mon-fri Asia/Shanghai
            └─ execution_cutoff → chase_poller_terminal → reconciliation_request → acceptance_report → recovery_snapshot

  cron 2: mirofish_postclose            17:00:00 mon-fri Asia/Shanghai
            └─ MiroFish 盘后复盘 + evidence_collection 写入

  cron 3: advance_day                   次交易日 09:00:00 mon-fri
            └─ 交易日历切换 + intraday MTM reset

  cron 4: intraday_mtm                  每 30 秒 (09:30-11:30 + 13:00-15:00 Asia/Shanghai)
            └─ Mark-to-market + equity_points 写入

  cron 5: evolution_shadow_run          22:00:00 mon-fri Asia/Shanghai ★ 新增
            └─ pending artifacts → run_shadow_validate × N → 通过即 flag pending_promote → 飞书通知 → amendment_drafter
```

**约束**:

- 严禁占用其他 cron 时间窗口(冲突 EOD 16:00 / MiroFish 17:00 / advance_day 09:00 / intraday 30s 任一)
- 严禁日内运行(避免影响 production simulation_auto)
- 严禁多实例并行(BrokerScheduler 已锁单实例;evolution_shadow_run 同款约束)
- 失败处理:1 次重试 → 仍失败 → audit `shadow_evolution_run_completed` outcome=FAILURE + log + 不发飞书(避免误通知);**不**阻断 production simulation_auto

#### 1.5.2 与 frontier_crawler 时间冲突协调

- `frontier_crawler` 每日 22:00 mon-sun 跑(派生 H-010 任务)
- `evolution_shadow_run` 每日 22:00 mon-fri 跑(本决策)
- 冲突 → frontier_crawler 改 21:30 早跑(给 evolution_shadow_run 留 30 min);或 evolution_shadow_run 22:30 晚跑(给 frontier_crawler 留 30 min)
- **本决策不锁定具体时间偏移**(待计划文档 session 定);两者 22:00 同时启动是基线但允许 ±30 min 偏移

### 1.6 飞书通知触发条件(shadow 通过即发)

#### 1.6.1 evolution_feishu_notifier 时序

```
evolution_shadow_run cron(22:00):

  对每个 pending artifact:
    1. run_shadow_validate(artifact_id, today) → ShadowAcceptanceReport
    2. 写 shadow_acceptance_reports collection
    3. 写 audit `shadow_evolution_run_completed`
    4. if is_challenger_winning(shadow, production):
         a. amendment_drafter.draft → docs/decisions/pending/{artifact_id}.md
         b. 写 audit `evolution_amendment_drafted`
         c. EvolutionFeishuNotifier.fire_pending(artifact_id, shadow_metrics, amendment_path)
         d. 写 audit `evolution_feishu_notified`
       else:
         a. log + 不发飞书 + 不起草 amendment
         b. (artifact 仍在 pending 状态;下次 shadow run 再试)
```

#### 1.6.2 dedup 防轰炸

- Alerter dedup_15min(继承 P1-7 §1.7)— 同 artifact_id 15 分钟内重复触发仅发 1 次飞书
- 用户人工 promote 后(prompts.lock.json 改 production alias)— artifact 从 pending 移除 → 不再触发飞书
- 用户人工拒绝(staging alias 设回 null)— artifact 从 pending 移除 → 不再触发飞书

#### 1.6.3 与 P0-2 备用 webhook 兼容性

- 飞书通知走 `FEISHU_CUSTOM_BOT_WEBHOOK_URL`(P0-2 §2.5 备用 webhook 仅可发系统告警)
- 自进化通知本质是系统告警(告诉用户"有待处理"),不是买卖指令/对账请求/澄清消息 → 与 P0-2 兼容
- 与 P1-7 §1.7 告警通道一致(飞书 + audit + Phase B 成本拆解面板)

## 2. 红线(P2-2)

> 以下条款一律以本 P2-2 决策为准。**违反即视为红线违规**。

1. **保守 3 路径永锁**:仅启用 DSPy GEPA 离线 prompt 演化 + RAG provenance-gated 知识库自扩展 + FinMem 风格 in-context exemplars(共 3 路径)。**严禁** fine-tune / online learning / RLHF / DPO / continual SFT 任一路径(违反"单实例无 GPU"环境约束)。**严禁** 自动 mutate config(包括 RiskConfig / PositionLimitsConfig / CircuitBreakerConfig / WatchlistPolicy / BrokerConfig / agent_models.yaml / cost_guard 4 常量;违反 P0-7 + P0-10 hot-reload 禁用 + LLM 字段权限矩阵)。**严禁** 引入新 LLM provider(违反 P1-6 §1.1 凭证池仅 LLM 3 + 飞书 6 锁状态)。**严禁** LLM 自动决策权(继承 P0-10 §2 红线 1)。

2. **全人工 gate + 飞书主动通知永锁**:任何自进化产物(prompt 新版本 / RAG 新文档 / proposal / amendment 草案)必须经过人工最终批准 + amendment + restart 才生效;LLM 严禁直接 mutate 任何 runtime 状态。**用户关键约束**:shadow validate 通过即主动发飞书告知用户"有待进化",走 `FEISHU_CUSTOM_BOT_WEBHOOK_URL`(沿用 P1-7 §1.7);**严禁** SMTP/Slack/Discord 第二告警通道(违反 P1-6 §1.1 凭证池封闭性)。

3. **Shadow validation 沿用 P0-6 45 交易日永锁**:完全复用 P0-6 `compute_acceptance_window` + `acceptance_reports` collection + 5 稳定性硬门槛(95/99/1/5/95%)+ 3 策略硬门槛(≤8% drawdown + PnL ≥0 + 沪深 300 累计超额 ≥0);shadow chain 与 production chain 复用同套基础设施;**严禁** 缩短到 30 日或拉长到 60 日(任何调整必走 P2-2 amendment + P0-6 amendment 双批准)。

4. **challenger 胜判定严格永锁**:challenger 必须满足 5+3 硬门槛全部达标 + 4 项严格优于 production(累计 PnL / 沪深 300 累计超额 / 信号生成成功率 / 指令完整率)+ 4 项不显著差于 production(差 ≤ 0.5 个百分点;最大回撤 / LLM 超时率 / 数据缺失率 / 回报解析准确率)。**严禁** 单项胜即 promote(任一不达标 = False = 不发飞书)。

5. **文件式 prompt registry + git + restart 永锁**:新增 `config/prompts/{agent}/{version}.yaml` + `config/prompts.lock.json` + `data/rag/{source}/{date}/{doc_id}.md` + `data/rag/provenance.jsonl`(append-only)+ `docs/decisions/pending/{artifact_id}.md`(amendment 草案)。**严禁** 引入 MLflow / LangSmith / 任何外部 hosted prompt registry(违反 P1-6 §1.5 全层 127.0.0.1 单实例原则)。版本切换 = `git commit` + amendment + restart;**严禁** runtime mutate(继承 P0-7 + P0-10 hot-reload 禁用)。

6. **BrokerScheduler 第五 cron `evolution_shadow_run` 22:00 mon-fri Asia/Shanghai 永锁**(派生 P1-2.A amendment):BrokerScheduler 4 → 5 cron。**严禁** 占用其他 cron 时间窗口(EOD 16:00 / MiroFish 17:00 / advance_day 09:00 / intraday_mtm 30s 任一)。**严禁** 日内运行(避免影响 production simulation_auto)。**严禁** 多实例并行(继承 P1-2.A 单实例约束)。失败处理:1 次重试 → 仍失败 → audit + log + 不发飞书 + 不阻断 production simulation_auto。

7. **飞书通知触发条件:shadow 45 日通过即发永锁**:shadow run 通过 + 5+3 硬门槛达标 + challenger 胜 production → 立即飞书通知 + 写 audit `evolution_feishu_notified`。Alerter dedup_15min 防轰炸。**严禁** 周汇总或日汇总(用户明确要"实时通知有待进化")。

8. **RAG 数据源白名单永锁**:仅允许 arxiv.org(q-fin/cs.LG/cs.AI)+ semanticscholar.org + openreview.net + github.com/{qlib,vn.py,freqtrade,NautilusTrader,TradingAgents,DSPy,jesse,FinGPT,FinRobot} releases + akshare/adata/baostock changelogs。**严禁** Twitter/X/Reddit/贴吧/任意 blog/未白名单 PDF(防 prompt injection;违反 P0-3 §2 红线 5 LLM 严禁拼接飞书消息文本伸展意旨)。`rag_ingester` 启动期 fail-fast 校验白名单;非白名单源拒绝入库即写 audit `rag_document_rejected_non_whitelist`。

9. **Frontier 调度频率每日 22:00 mon-sun 永锁**:近 24h 新增 → DeepSeek 总结(预算 ~¥0.05-0.10/日;计入 P1-7 daily ¥20 hard)。**严禁** 周汇总或月汇总(及时性优先;NeurIPS/ICML/ICLR 论文不能延迟一周)。**严禁** 实时运行(违反 cost 控制)。

10. **Risk_parameter_proposals 合并 P2-2 体系永锁**(派生 P0-7 amendment):`risk_parameter_proposals` collection 扩字段(`target_artifact_type` discriminator + `shadow_validation_status` + `pending_amendment_id`);走 P2-2 同款 `evolution_shadow_run` cron + 45 日 shadow validate + 飞书通知 + 自动起草 P0-7-amendment 流程。**严禁** 走 P0-7 §1.4 原周报 review 流程(避免双轨混乱);人工仅 review + sign-off。

11. **Frontier 输出仅写文件系统 `data/rag/` 永锁**:**严禁** 写入 `evidence_collection`(防破 P0-8 §2 红线 14 evidence_id 5 前缀约定 NEWS-/MIROFISH-/MARKET-/RISK-/DEBATE-);**严禁** 创建 `frontier_papers` collection(零 Mongo 依赖);**严禁** 创建任何新 Mongo collection(零 schema 负担)。`data/rag/provenance.jsonl` 必须记录 URL/commit_hash/scanned_at/llm_summary_model/llm_tokens(provenance 5 字段必填)。

12. **AuditEventType 27 → 34 类永锁**(派生 P1-6 amendment 第 3 次):新增 7 类自进化生命周期事件 — `prompt_version_pinned` / `prompt_version_rolled_back` / `rag_document_ingested` / `rag_document_rejected_non_whitelist` / `shadow_evolution_run_completed` / `evolution_amendment_drafted` / `evolution_feishu_notified`。归类 5 自进化生命周期(本 amendment 新建类别 5;归类 1-4 继承 P1-6 / P1-7 / P2-4 amendment 累积)。`actor` = `SYSTEM` 或 `SCHEDULER`;**严禁** `LLM` / `FRONTEND_USER` / `FEISHU_USER` 直接写 evolution 类 audit(防伪造);`resource_type` = `'self_evolution_artifact'`;`reason_namespace` = `'self_evolution_lifecycle'`。

13. **实施期 deferred 等用户计划文档永锁**:本决策仅锁红线 + 任务清单粒度。**严禁本 session 写任何自进化代码**(包括 prompt registry / RAG ingester / shadow chain / frontier crawler / amendment_drafter / evolution_feishu_notifier / 任一 Python 文件)。**严禁** 修改任何 `backend/` `frontend/` `config/` 文件(除文档外)。Phase A/B/B-finale 进行期间也**严禁** 写自进化代码。待用户在 dedicated 计划文档 session 锁定推进时间表后才允许 Phase X 实施期启动。

14. **DSPy GEPA 离线 prompt 演化运行约束永锁**:运行模式 = batch offline(类似 fine-tune offline batch);**严禁** 实时运行(避免 LLM cost 失控)。单次 GEPA compile 任意 ≤ ¥5(continuous monitor;超 raise `GEPABudgetExceededError`)。GEPA 输出仅写 `config/prompts/{agent}/{candidate}.yaml` + git diff;**严禁** 直接覆盖 production version;promote 必经 shadow validate + 飞书通知 + 人工 amendment + restart 4 步。

15. **In-context exemplars FinMem 风格约束永锁**:exemplar 来源仅 `decision_ledger` 当前 simulation_auto 历史成功 case(限近 90 日);**严禁** 手工注入 exemplars(防 prompt injection);**严禁** 从未通过 RiskEngine 14-check 的 case 抽取(防污染);衰减检索算法 = recency weighting + relevance scoring + diversity sampling;**严禁** 超过 3 exemplars per prompt(防 cost 飙升 + token 占用过多);每日 22:00 evolution_shadow_run 时刷新 exemplars 集合。

16. **cost_guard 计入 P2-2 LLM 调用永锁**:DSPy GEPA + frontier_crawler DeepSeek 总结 + shadow_chain LLM 重跑 + amendment_drafter LLM 起草 + in-context exemplars 注入 token 增量 — 全部计入 P1-7 daily ¥20 hard ceiling + monthly ¥440 soft + Kimi ¥4 daily cap;**严禁** 绕过 cost_guard;**严禁** P2-2 单独预算池(继承 P1-7 §1.4 LLM only 预算锁定;不引入新预算维度)。

17. **自进化模块依赖隔离永锁**:`backend/services/{evolution_dispatcher, frontier_crawler, rag_ingester, amendment_drafter, dspy_gepa_runner, shadow_chain, prompt_registry, rag_provenance, exemplar_selector, evolution_feishu_notifier, evolution_audit_writer}.py` **严禁** import `backend.{api, broker, risk, llm, agents, mirofish, data}`(防破 P0-10 §2 红线 1 LLM 字段权限矩阵;防 LLM/agents 反向调用 evolution_dispatcher.activate_* 绕过守门)。

18. **Pydantic v2 frozen + strict + extra='forbid' 永锁**:`PromptRegistry` / `PromptLockFile` / `RAGDocument` / `ProvenanceRecord` / `EvolutionProposal` / `ShadowAcceptanceReport` / `ExemplarRecord` / `FrontierPaperSummary` / `EvolutionArtifact` 全部 frozen + strict + extra='forbid'(继承 P0-3 §2 红线 12 + P1-2.A/B/C + P1-7 各自扩展);**严禁** mutation;**严禁** hot-reload。

19. **过时代码清理 deferred to Phase A 永锁**:本 session **严禁** 修改 backend/ frontend/ 任何代码;P0-1 旧 AUTHORIZATION_MODE/QUANTMIND_PHASE 矩阵 + Phase 5B 遗留 + ApprovalQueue / Settings 写入接口 — 全部在 Phase A 实施期一次性破坏式删除(履行 P1-5 §1.2 锁定)。本 session 不顺手清理(避免决策期与实施期混杂、保持联合备份点裂解)。

20. **Codex review hard gate 节奏永锁**:本 session **不**跑 Codex(决策文档无代码,R3 SDK signatures + R5 coverage 抽象);Codex 5 轮 R1-R5(R1 consistency + R2 red-team + R3 SDK + R4 security + R5 coverage)在 Phase X 实施期跑;计划文档 session 也**不**跑(计划文档纯文本无代码无 SDK);每轮 review 必存 `docs/reviews/H-XXX-rN-{topic}.md`。

## 3. 实施期任务清单

> ⚠️ **本清单 deferred 等用户在 dedicated 计划文档 session 锁定推进时间表后才允许启动 Phase X 实施。本 session 严禁写任何自进化代码。**
>
> 与现 E (P1-5) / F (P1-6) / G (P1-7) 系列 Phase A/B 排程**不同期**;Phase X 在 Phase A + Phase B + Phase B-finale 完成后启动。
>
> 任务粒度沿用 E/F/G 命名规范(H-001 ~ H-0NN);H = Phase X 自进化阶段任务前缀。

### 3.1 Phase X-A: 基础设施(7 任务)

| 任务 ID | 内容 | 依赖 | 估时 |
|---------|------|------|------|
| H-001 | 创建 `config/prompts/{agent}/{version}.yaml` + `config/prompts.lock.json` 目录结构 + frozen Pydantic `PromptLockFile` schema | 无 | 0.5d |
| H-002 | 创建 `data/rag/{source}/{date}/{doc_id}.md` + `data/rag/provenance.jsonl`(append-only fcntl flock 锁)目录结构 | 无 | 0.5d |
| H-003 | 编写 `backend/services/prompt_registry.py`(PromptRegistry frozen Pydantic v2 strict + load_pinned_version + version_lock_validate + alias_resolver) | H-001 | 1d |
| H-004 | 编写 `backend/services/rag_provenance.py`(`RAGDocument` frozen + provenance_validator + whitelist_check 启动期 fail-fast) | H-002 | 1d |
| H-005 | P1-2.A amendment 落地 — BrokerScheduler 新增 `evolution_shadow_run` 5th cron(22:00 mon-fri Asia/Shanghai;占用 BrokerScheduler 单实例) | P1-2.A 主决策 | 0.5d |
| H-006 | 编写 `backend/services/exemplar_selector.py`(FinMem 风格 decision_ledger 历史 case 选择 + 衰减检索 + diversity sampling + 严禁 >3 exemplars per prompt) | 无 | 1d |
| H-007 | 编写 `backend/services/shadow_chain.py`(读 45 日 acceptance_reports + replay 决策链 with 候选 artifact + 输出 `shadow_acceptance_reports` collection + challenger_winning 判定) | H-003 / H-004 / H-006 / P0-6 主决策 | 2d |

### 3.2 Phase X-B: 自进化执行(8 任务)

| 任务 ID | 内容 | 依赖 | 估时 |
|---------|------|------|------|
| H-008 | 编写 `backend/services/evolution_dispatcher.py`(协调 prompt evolution + RAG ingest + risk proposal + exemplar 4 类自进化提议;严禁 LLM 反向调用 .activate_*) | H-003 ~ H-007 | 1.5d |
| H-009 | 编写 `backend/services/dspy_gepa_runner.py`(DSPy GEPA 离线 prompt 演化;单次 ≤ ¥5 budget;输出 `config/prompts/{agent}/{candidate}.yaml`;严禁覆盖 production) | H-003 + cost_guard 集成 | 2d |
| H-010 | 编写 `backend/services/frontier_crawler.py`(每日 22:00 mon-sun cron 扫 arxiv/semanticscholar/openreview/github releases/changelog + DeepSeek 总结 + 写 `data/rag/`) | H-004 + cost_guard | 2d |
| H-011 | 编写 `backend/services/rag_ingester.py`(provenance JSON 校验 + 白名单源校验 + 写 `data/rag/` + audit hook;严禁非白名单源入库) | H-004 / H-010 | 1d |
| H-012 | P0-7 amendment 落地 — `risk_parameter_proposals` collection 扩字段(`target_artifact_type` discriminator + `shadow_validation_status` + `pending_amendment_id`;非破坏式扩展) | P0-7 主决策 | 0.5d |
| H-013 | 编写 `backend/services/amendment_drafter.py`(shadow 通过后自动起草 `docs/decisions/pending/{artifact_id}.md` 草案;LLM 生成 amendment 文本走 P1-7 cost_guard) | H-007 + cost_guard | 1.5d |
| H-014 | 编写 `backend/services/evolution_feishu_notifier.py`(shadow validate 通过 → 飞书通知;复用 `FEISHU_CUSTOM_BOT_WEBHOOK_URL`;Alerter dedup_15min;走 `backend/feishu/renderer.py::render_evolution_pending` 模板硬编码) | P1-7 §1.7 主决策 | 1d |
| H-015 | 编写 `backend/services/evolution_audit_writer.py`(写 audit 7 类新事件 to `audit_events` collection + JSONL 双写;actor=SYSTEM 或 SCHEDULER) | H-016 + P1-6 §1.7 主决策 | 0.5d |

### 3.3 Phase X-C: Audit + 守门(5 任务)

| 任务 ID | 内容 | 依赖 | 估时 |
|---------|------|------|------|
| H-016 | P1-6 第 3 次 amendment 落地 — `AuditEventType` enum 27 → 34 类(新增 7 类 + 完整性测试 + 类别 5 自进化生命周期) | P1-6 主决策 + P1-6 第 1/第 2 amendment | 0.5d |
| H-017 | cost_guard 集成 P2-2 — 在 `backend/services/cost_guard.py` 新增 P2-2 LLM 调用计入 daily ¥20 hard / monthly ¥440 / Kimi ¥4 daily cap;严禁单独预算池 | P1-7 主决策 | 0.5d |
| H-018 | 守门检查 — `backend/services/{evolution_dispatcher, frontier_crawler, rag_ingester, amendment_drafter, dspy_gepa_runner, shadow_chain, prompt_registry, rag_provenance, exemplar_selector, evolution_feishu_notifier, evolution_audit_writer}.py` 严禁 import `backend.{api, broker, risk, llm, agents, mirofish, data}`(grep + lint rule + 单元测试断言) | H-003 ~ H-015 | 0.5d |
| H-019 | 单元测试 — `prompt_registry` / `rag_provenance` / `shadow_chain` / `exemplar_selector` / `dspy_gepa_runner` / `frontier_crawler` / `rag_ingester` / `evolution_dispatcher` 各 200+ 案例;覆盖率 >70% | H-003 ~ H-015 | 3d |
| H-020 | 集成测试 — 端到端 evolution_shadow_run 22:00 cron → shadow validate → 通过 → 飞书通知(mock webhook)→ amendment_drafter → audit 完整链;含 happy path + 失败 path + budget 超限 + 白名单源拒绝 | H-019 | 2d |

### 3.4 Phase X-D: 前端(3 任务)

| 任务 ID | 内容 | 依赖 | 估时 |
|---------|------|------|------|
| H-021 | 编写 `backend/api/evolution.py`(GET `/api/evolution/pending` 只读 — 列待处理自进化提议 + GET `/api/evolution/runs` shadow_run 历史;仅 GET 符合 P1-5 §2 红线 1+2) | H-008 + P1-5 主决策 | 0.5d |
| H-022 | 前端**不**新增独立页(履行 P1-5 §2 红线 1 锁 11 页名额);自进化待处理通知通过飞书 + StatusBar | P1-5 主决策 | 0d(纯约束) |
| H-023 | 前端 `frontend/src/views/SystemStatus.vue` 内补一行"自进化待处理"标识(展示 `evolution_pending_count`;0 时绿;>0 时黄;>3 时红;轮询 GET `/api/evolution/pending` 5min 间隔) | H-021 + P1-5 §1.1 主决策(SystemStatus.vue 已存在 P1-5 7 页) | 0.5d |

### 3.5 Phase X-E: Codex review(5 任务)

| 任务 ID | 内容 | 触发条件 | 估时 |
|---------|------|----------|------|
| H-024 | Codex R1 — consistency + redline coherence(本 P2-2 决策 + 3 amendment vs P0+P1+P2 累积 130+ 红线一致性检查;输出 `docs/reviews/H-XXX-r1-consistency.md`) | Phase X-A/B/C/D 全部完成 | 1d |
| H-025 | Codex R2 — red-team / adversarial(尝试找出绕过 shadow validate / 绕过 human gate / prompt injection / 自进化死锁 / cost 超限 等攻击路径;输出 `docs/reviews/H-XXX-r2-redteam.md`) | H-024 通过 | 1.5d |
| H-026 | Codex R3 — SDK signatures(DSPy GEPA SDK / LangChain DocumentLoader / arxiv API SDK / Semantic Scholar API SDK / openreview SDK 兼容性 + 实际运行验证;输出 `docs/reviews/H-XXX-r3-sdk.md`) | H-025 通过 | 1d |
| H-027 | Codex R4 — security(RAG ingestion provenance / `data/rag/` 文件权限 / `data/rag/provenance.jsonl` 篡改防护 / 飞书 webhook 签名 / cost_guard 不绕过;输出 `docs/reviews/H-XXX-r4-security.md`) | H-026 通过 | 1.5d |
| H-028 | Codex R5 — coverage(80%+ 单元测试 + 集成测试 + 端到端;严禁覆盖率回退;输出 `docs/reviews/H-XXX-r5-coverage.md`) | H-027 通过 | 1d |

### 3.6 任务总数 / 总估时

- **任务总数**: 28(Phase X-A 7 + Phase X-B 8 + Phase X-C 5 + Phase X-D 3 + Phase X-E 5)
- **总估时**: ~28d(连续推进;实际可能跨多周;以用户计划文档为准)
- **关键里程碑**:
  - Phase X-A 完成 → 基础设施就绪
  - Phase X-B 完成 → 自进化执行链路通
  - Phase X-C 完成 → audit + 守门齐备
  - Phase X-D 完成 → 前端 SystemStatus 自进化状态点上线
  - Phase X-E 全部通过 → Codex 5 轮 hard gate 通过 + P2-2 实施期闭环

## 4. 派生 amendment 清单

### 4.1 P0-7-amendment-2026-05-11-risk-proposals-shadow-validation

- **主决策**: `docs/decisions/P0-7-risk-redlines-position-circuit-universe-llm-immutability.md`
- **变更内容**: `risk_parameter_proposals` collection 扩字段(`target_artifact_type` discriminator + `shadow_validation_status` + `pending_amendment_id`);走 P2-2 shadow validate 流程取代周报 review
- **性质**: 非破坏式扩展;新字段默认 `None` 兼容历史 record
- **实施期**: H-012

### 4.2 P1-2.A-amendment-2026-05-11-evolution-shadow-cron-5th

- **主决策**: `docs/decisions/P1-2.A-persistence-hybrid-snapshot-and-broker-scheduler.md`
- **变更内容**: BrokerScheduler 4 → 5 cron;新增 `evolution_shadow_run` 22:00 mon-fri Asia/Shanghai
- **性质**: 非破坏式追加;原 4 cron 不变
- **实施期**: H-005

### 4.3 P1-6-amendment-2026-05-11-audit-eventtype-34

- **主决策**: `docs/decisions/P1-6-secrets-shell-env-12month-event-driven-rotation-loopback-only-no-local-auth-audit-mongo-jsonl-dual-write.md`
- **前序 amendment**: `docs/decisions/P1-6-amendment-2026-05-10-audit-eventtype-26.md`(P1-7 派生 22→26) + `docs/decisions/P1-6-amendment-2026-05-10-audit-eventtype-27.md`(P2-4 派生 26→27)
- **变更内容**: `AuditEventType` enum 27 → 34 类;新增 7 类自进化生命周期;归类 5 自进化生命周期(本 amendment 新建类别 5)
- **性质**: 非破坏式追加;原 27 类不变
- **实施期**: H-016

### 4.4 不派生的 amendment(用户 Round 4 Q2 已锁零冲突)

- ❌ P0-8 amendment(evidence_id 5 前缀约定不变;frontier 走 `data/rag/` 不入 evidence)
- ❌ P0-10 amendment(LLM positive list 4 类不变;DSPy GEPA 是 batch offline 非 runtime LLM 写 Mongo)
- ❌ P1-7 amendment(cost_guard 4 常量不变;P2-2 LLM 调用计入 daily ¥20 hard 不引入新预算维度)

## 5. 决策依据

### 5.1 用户对齐(2026-05-11 P2-2 dedicated session 4 轮 16 议题)

#### Round 1: 架构选型 + 风险等级 + 验证窗口 + 状态回滚

- **Q1 自进化路径启用范围** → "保守 3 路径"(prompt evolution + RAG + in-context exemplars)
- **Q2 Human-in-the-loop 严格度** → 用户自由输入"必须通过 feishu 通知我,有待进化,让我知道什么时候该处理进化了"(全人工 gate + 飞书主动通知)
- **Q3 Shadow validation 窗口长度** → "45 交易日"(沿用 P0-6 acceptance 框架)
- **Q4 状态回滚机制粒度** → "文件式 prompt registry + git + restart"

#### Round 2: 验证框架 + RAG 治理

- **Q1 Shadow chain 实现位置** → "BrokerScheduler 独立第五 cron"(evolution_shadow_run 22:00 mon-fri)
- **Q2 飞书主动通知触发条件** → "Shadow 45 日验证通过即发"
- **Q3 RAG 数据源白名单** → "学术 + GitHub release 仅"(arxiv/semanticscholar/openreview/github releases/changelogs)
- **Q4 Frontier tracking 调度频率** → "每日 22:00"

#### Round 3: 风控参数特殊约束 + audit 扩展 + frontier 落地

- **Q1 P0-7 risk_parameter_proposals 通道是否纳入 P2-2 shadow validation** → "合并到 P2-2 体系"
- **Q2 AuditEventType 扩展** → "中等 27→34 类"
- **Q3 Frontier tracking 输出去向** → "写文件系统 data/rag/ 仅"
- **Q4 实施期切入时机** → 用户自由输入"先把一些必要且确定的基础设施建设好,把当前项目路径下一些没用的东西,过时的东西清理干净。然后先不要动,我会在其他 session 内构建计划文档,后面我们会按照计划文档推进工作"(实施期 deferred 等用户计划文档)

#### Round 4: 合规路径 + 实施期任务清单切入

- **Q1 P2-2 决策文档实施期任务清单写法** → "完整写 H-001~H-0NN 标'等计划文档'"
- **Q2 派生 amendment 范围** → "3 个 amendment"(P0-7 + P1-2.A + P1-6 第 3 次)
- **Q3 派生 amendment 与过时代码清理顺序** → "文档先锁 → 清理在另 session"
- **Q4 Codex review 节奏** → "计划文档 session 后启动"

### 5.2 关键判断

- **"自进化必须有"是用户 critical feedback 不可妥协边界**:Claude 在 P2 收官时推荐"全锁不启用"被用户明确否决;本决策严格尊重"必须有 + 必须验证 + 必须可回滚 + 必须不破现有红线"四元约束
- **保守 3 路径是单实例无 GPU 环境下的最大可行集**:fine-tune / online learning 需 GPU 不可行;DSPy GEPA + RAG + exemplars 三者覆盖"持续学习 + 持续适应 + 持续追踪前沿"用户三大需求
- **45 日 shadow validate 完全复用 P0-6 是最小工程负担最大决策一致性路径**:不引入 compute_evolution_window 单独函数;不引入独立 shadow 指标体系;不引入新硬门槛;避免决策分叉
- **文件式 prompt registry 是单实例 + 不破 P1-6 §1.5 + git 已有版本控制三者最优解**:不引入 MLflow server 进程;不引入新端口;不引入新数据库 backend;复用 git + amendment 已锁定流程
- **BrokerScheduler 第五 cron 是最小侵入设计**:复用 P1-2.A 已锁定 scheduler 实例 + 已锁定 cron 编排时间窗口;新增 22:00 evolution_shadow_run 与现 4 cron 时间错峰不冲突
- **飞书主动通知是用户关键约束的具象化**:用户原话明确要"主动通知有待进化",不能依赖被动 polling;走已锁 FEISHU_CUSTOM_BOT_WEBHOOK_URL 通道符合 P1-7 §1.7 锁定
- **risk_proposals 合并 P2-2 体系避免双轨**:已有 P0-7 §1.4 周报 review 机制与 P2-2 shadow validate 机制功能重叠;合并到 P2-2 体系是简化路径
- **frontier 仅写文件系统避免 Mongo schema 升级**:不引入 frontier_papers collection;不扩 evidence_id 5 前缀;最小 amendment 范围(零 P0-8 amendment)
- **27→34 类 audit 是覆盖自进化生命周期最小集**:7 类新事件覆盖"提议 / 验证 / 通知 / 起草 / 入库 / 拒绝 / 回滚"主要节点;少于此则反向审计不完整
- **实施期 deferred 等用户计划文档符合用户表态**:用户明确要"先建必要基础设施 + 清理过时 + 然后停下等其他 session 计划文档";Claude 不擅自启动 Phase X
- **3 amendment 是用户选项零冲突路径**:零 P0-8/P0-10/P1-7 amendment 等价于"不再触及其他主决策";最小决策面影响
- **Codex review 在 Phase X 实施期跑符合 review 设计意图**:决策文档无代码,R3 SDK + R5 coverage 抽象;Codex 5 轮 R1-R5 设计是 review 代码而非 review 文档

### 5.3 排除选项

#### 5.3.1 中等 5 路径或激进 7 路径(Round 1 Q1)

- 中等 5 路径(保守 3 + amendment_drafter + frontier_crawler):**Round 1 Q1 用户选保守 3 路径,Round 3 Q1+Q2+Q3 已隐式吸收 amendment_drafter + frontier_crawler 为 3 路径的执行细节,故保守 3 路径的实质内涵已含 amendment_drafter + frontier_crawler;中等 5 路径其实是错误的离散选项**
- 激进 7 路径(中等 5 + tiered routing 自适应 + broker config 提议):违反 P1-6 §1.1 凭证池封闭性 + P0-10 LLM 字段权限矩阵 + P1-7 cost_guard 不变性

#### 5.3.2 分级 gate 或信任 LLM gate(Round 1 Q2)

- 分级 gate(low-risk 自动 promote + high-risk 人工):用户明确"必须通过飞书通知我"= 全人工 gate 即使是 low-risk
- 信任 LLM gate(shadow 通过即自动 merge):违反 P0-10 LLM 字段权限矩阵 + 用户 critical feedback "不能完全禁止" ≠ "完全开放";不可妥协

#### 5.3.3 30 日或 60 日 shadow(Round 1 Q3)

- 30 日:加速但偏离 P0-6 acceptance 框架(需新 compute_evolution_window 函数 + 双轨指标);复杂度 ↑;validation 健壮性 ↓
- 60 日:更保守但 promote 节奏过慢(超过 P0-6 acceptance 节奏);用户希望与 P0-6 对齐

#### 5.3.4 Git only 或 MLflow hosted(Round 1 Q4)

- Git only(YAML 内嵌或单文件):单文件膨胀 + 多 agent prompt 难管理;违反"高内聚低耦合"原则
- MLflow hosted:违反 P1-6 §1.5 单实例 + 全层 127.0.0.1 only 原则;增加外部服务 + 新端口 + 新数据库 backend

#### 5.3.5 EvolutionScheduler 独立进程或 EOD chain 末尾(Round 2 Q1)

- EvolutionScheduler 独立:增加 systemd unit + 跨进程同步 MongoDB 复杂度 ↑;隔离性提升不抵复杂度成本
- EOD chain 末尾:违反 P0-6 §1.3.2 16:00:30 acceptance_reports 生成时限;EOD chain 脱长

#### 5.3.6 周汇总或日汇总飞书通知(Round 2 Q2)

- 周汇总:用户原话明确"实时通知"要求;延迟 1-5 天违反意旨
- 日汇总:连续 5 工作日发 = 每周锁屏 5 次过多

#### 5.3.7 学术 + 财经权威新闻源混合 RAG 白名单(Round 2 Q3)

- 混合源:P0-8 5 财经权威源(stock_news_em 等)已走 evidence_collection;RAG 与 evidence 双写会引入 TTL 差异 + 数据重复;违反"单一真相源"原则

#### 5.3.8 全开 + LLM 过滤 RAG 白名单(Round 2 Q3)

- 全开:违反 P0-3 §2.5 LLM 严禁拼接飞书消息文本意旨(伸展为"LLM 严禁加工外部不可信文本");prompt injection 风险

#### 5.3.9 周汇总或月度 frontier 频率(Round 2 Q4)

- 周汇总:NeurIPS/ICML/ICLR 论文不能延迟一周(及时性 ↓)
- 月度:过低频;量化领域发展快月度可能错过领头论文

#### 5.3.10 保持 P0-7 §1.4 原状 或 Risk 走 90 日 shadow(Round 3 Q1)

- 保持原状:双轨混乱(P0-7 §1.4 周报 + P2-2 shadow validate 功能重叠)
- 90 日 shadow:复杂度 ↑;需双轨 compute_evolution_window;实质收益有限(45 日已包含足够风险参数验证窗口)

#### 5.3.11 最小 27→30 类 或 完整 27→38 类 audit(Round 3 Q2)

- 最小 27→30 类:反向审计不完整(遗漏 rolled_back / amendment_drafted / rag_ingested / rag_rejected)
- 完整 27→38 类:exemplars/frontier 调试事件不应走 audit(违反 P1-6 §1.8 "调试性事件不入 audit");应走 quantmind.jsonl

#### 5.3.12 frontier 写 evidence_collection 或 frontier_papers collection(Round 3 Q3)

- 写 evidence_collection:需 P0-8 amendment(扩 evidence_id 第 6 前缀 FRONTIER-)+ P0-3 evidence_id pattern 扩展;amendment 负担 ↑
- frontier_papers collection:增加 Mongo collection + schema 维护 + 索引 + TTL;过度工程(frontier 是辅助而非核心)

#### 5.3.13 Phase B 同期或 Phase A 末尾切入(Round 3 Q4 / Round 4 Q1)

- Phase B 同期:违反"Phase A/B 严禁写自进化代码"用户表态
- Phase A 末尾:同上违反用户表态;过早起步

#### 5.3.14 4 amendment 或 1 amendment(Round 4 Q2)

- 4 amendment(加 P0-10):P0-10 LLM positive list 4 类不变(DSPy GEPA 是 batch offline 非 runtime LLM 写 Mongo);防御性扩展无实质需求
- 1 amendment(仅 P1-6 audit):P0-7 risk_proposals 字段扩 + P1-2.A BrokerScheduler 5 cron 不在 P2-2 主文档内据有使能;主文档引用混乱

#### 5.3.15 本 session 顺手扫一轮 或 顺手清理(Round 4 Q3)

- 顺手扫一轮(交付清单):污染决策期 session 焦点;扫出的清单可能在 Phase A 时已不准确
- 顺手清理:违反 P1-5 §1.2 "Phase A 一次性破坏式删除"锁定;联合备份点裂解

#### 5.3.16 本 session 跑 Codex R1 或 R1-R5 全部(Round 4 Q4)

- R1 提前跑:决策文档与 P0+P1+P2 130+ 红线一致性可在文档起草时由 Claude 自查 + 用户最终批准达成;Codex R1 提前 30min 价值有限
- R1-R5 全部本 session:R3 SDK + R5 coverage 抽象(决策文档无代码无 SDK 调用无测试覆盖);跑了无意义

### 5.4 与 P0/P1/P2 红线协同

#### 5.4.1 P0-1 ~ P0-5 协同

- 继承 P0-1 §1.1 MockBroker 唯一镜像 → 自进化路径**不**修改 MockBroker(继承 P0-1 §2 红线 3)
- 继承 P0-2 §2.5 备用 webhook 仅可发系统告警 → 飞书通知是系统告警范畴不违反
- 继承 P0-3 §2 红线 5 LLM 严禁拼接飞书消息文本 → render_evolution_pending 由 backend/feishu/renderer.py 函数模板硬编码生成
- 继承 P0-3 §2 红线 12 frozen Pydantic strict + extra='forbid' → 所有 P2-2 frozen 模型同款约束(§2 红线 18)
- 继承 P0-4 §1 严格正则 only + LLM 完全不参与回报路径 → 自进化路径**不**修改 ExecutionReportParser
- 继承 P0-5 §1.2 16:00 系统主动发起对账 → BrokerScheduler 第五 cron 22:00 在 EOD chain + MiroFish 之后不冲突

#### 5.4.2 P0-6 协同

- 继承 P0-6 §1.1 45 交易日滚动窗口 → shadow validate 完全复用(§1.3)
- 继承 P0-6 §1.2 5 稳定性硬门槛 + §1.3 3 策略硬门槛 → challenger 胜判定完全沿用(§1.3.2)
- 继承 P0-6 §1.7 P0 系统级中断重置 → shadow chain 复用 acceptance_reports collection 含中断重置标识
- shadow_acceptance_reports collection 与 production acceptance_reports collection **独立不混**(§1.3.3)

#### 5.4.3 P0-7 协同

- 继承 P0-7 §1.4 RiskConfig 全锁 + LLM 永不持有写引用 → 自进化路径**不**触及 RiskConfig runtime mutation(§1.1.2)
- 继承 P0-7 §2 红线 14 hot-reload 禁用 → 自进化产物 promote = restart 严禁 hot-reload(§1.4.2)
- **派生** P0-7 amendment(risk_parameter_proposals 扩字段);非破坏式

#### 5.4.4 P0-8 协同

- 继承 P0-8 §2 红线 14 evidence_id 5 前缀约定 → frontier **不**写 evidence_collection(§2 红线 11)
- 继承 P0-8 §1 MiroFish 双路径输出仅入 evidence_collection → 自进化 frontier 走独立 data/rag/ 文件系统不冲突
- 零 P0-8 amendment(用户 Round 4 Q2 已锁)

#### 5.4.5 P0-9 协同

- 继承 P0-9 §1.3 watchlist runtime 不可改 → 自进化路径**不**触及 WatchlistPolicy
- 继承 P0-9 §3.5 MiroFish 加分非核心 + 严禁占用 traditional cap → 自进化不调整 traditional 4 + event_reserved 1 = 5 cap

#### 5.4.6 P0-10 协同

- 继承 P0-10 §1.1 LLM positive list 4 类 → 本决策**不**扩(§1.1.3);DSPy GEPA 输出写文件非 LLM runtime mutate Mongo
- 继承 P0-10 §1.2 LLM negative list 8 类累积 → 本决策严格遵守
- 继承 P0-10 §2 红线 1 LLM 字段权限矩阵 → 自进化模块严禁 import LLM/agents(§2 红线 17)
- 继承 P0-10 §1.4 hot-reload 禁用 + agent_models.yaml runtime 不可改 → 自进化 prompt 演化走 prompts.lock.json + restart 路径不冲突
- 零 P0-10 amendment(用户 Round 4 Q2 已锁)

#### 5.4.7 P1-2.A 协同

- 继承 P1-2.A §1.4 BrokerScheduler 3 cron + P1-2.B 第 4 cron(intraday_mtm) → **派生** P1-2.A amendment(4 → 5 cron;非破坏式追加)
- 继承 P1-2.A §1.6 EOD chain 失败处理 + 第五种买卖类路由冻结来源 → evolution_shadow_run 失败**不**冻结买卖类路由(自进化非生产路径)
- 继承 P1-2.A broker_events append-only insert-only 8 项红线 → audit_events 同款约束(P1-6 已锁)+ provenance.jsonl 同款约束(本决策 §1.4.3)

#### 5.4.8 P1-2.B + P1-2.C 协同

- 继承 P1-2.B intraday_mtm 30s + 第 4 cron → 不冲突(evolution_shadow_run 22:00 在交易时段后)
- 继承 P1-2.C MockBroker 撮合 + cost_calculator 抽出 → 自进化路径**不**修改 MockBroker / cost_calculator

#### 5.4.9 P1-5 协同

- 继承 P1-5 §2 红线 1 锁 7+4 = 11 前端页名额 → **不**新增独立自进化页(§3.4 H-022);仅在 SystemStatus.vue 补行(H-023)
- 继承 P1-5 §2 红线 5 仅 2 写入端点 → 自进化前端**不**引入新写入端点(GET /api/evolution/pending + GET /api/evolution/runs 仅 GET;§3.4 H-021)
- 继承 P1-5 §1.2 Phase A 一次性破坏式删除 → 过时代码清理 deferred 到 Phase A(§2 红线 19)

#### 5.4.10 P1-6 协同

- 继承 P1-6 §1.1 凭证池仅 LLM 3 + 飞书 6 锁状态 → 自进化路径**不**引入新 provider(§1.1.2)
- 继承 P1-6 §1.5 全层 127.0.0.1 only + SSH tunnel → 不引入 MLflow hosted 等外部服务(§1.4.1)
- 继承 P1-6 §1.7 audit schema frozen 10 字段 → 7 类新事件复用同 schema(§4.3 amendment)
- 继承 P1-6 §2 红线 16 LLM 严禁写 audit_events → evolution audit 由 SYSTEM/SCHEDULER actor 写(§2 红线 12)
- 继承 P1-6 §2 红线 17 4 类事件强制写 audit + 调试性事件不入 → 7 类自进化生命周期事件归类 5 强制写;exemplar 调试性事件走 quantmind.jsonl
- **派生** P1-6 第 3 次 amendment(27 → 34 类 AuditEventType);非破坏式追加

#### 5.4.11 P1-7 协同

- 继承 P1-7 §1.1 LLM 总日 ¥20 hard + 月 ¥440 soft + Kimi ¥4 daily cap → 自进化 LLM 调用计入(§2 红线 16)
- 继承 P1-7 §1.7 告警通道仅飞书 + audit + Phase B 成本拆解面板 → evolution_feishu_notifier 走 FEISHU_CUSTOM_BOT_WEBHOOK_URL 复用(§1.2.2)
- 继承 P1-7 §2 红线 8 严禁 SMTP/Slack/Discord 第二通道 → 自进化通知**不**引入新通道
- 零 P1-7 amendment(用户 Round 4 Q2 已锁)

#### 5.4.12 P2 收官协同

- 继承 P2-1 superseded by P0-8 → 自进化**不**重新评估 MiroFish 范围
- 继承 P2-3 superseded by P1-6 §1.5 → 自进化**不**开放 LAN/公网入站
- 继承 P2-4 派生 P1-6 amendment 第 2 次(27 类)→ 本决策派生 P1-6 amendment 第 3 次(27→34 类);累积 P1-6 主决策 + 4 amendment 三层

## 6. 后续动作

### 6.1 SSoT 文档同步

- 更新 `MEMORY.md` 索引:加 P2-2 锁定 entry + 自进化必须有 feedback 升级 entry
- 升级 `~/.claude/projects/-home-ps-papers-QuantMind/memory/feedback_self_evolution_must_have.md`(deferred → 已锁;补充实际锁定的策略要点)
- 新建 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_p2_2_self_evolution.md`(P2-2 决策具体内容索引)
- 更新 `CLAUDE.md`:顶部 blurb 改"决策对齐期 P0+P1+P2(含 P2-2)全完成 ✅";§2 加 §2.15 P2-2 红线节;§5 操作速查新增 P2-2 grep
- 更新 `docs/quantmind_owner_decision_points_2026-05-07.md` §P2-2:状态从 ⏳ → ✅;链接到本决策文档

### 6.2 派生 amendment 起草

- ✅ `docs/decisions/P0-7-amendment-2026-05-11-risk-proposals-shadow-validation.md`
- ✅ `docs/decisions/P1-2.A-amendment-2026-05-11-evolution-shadow-cron-5th.md`
- ✅ `docs/decisions/P1-6-amendment-2026-05-11-audit-eventtype-34.md`

### 6.3 下一站

- **决策对齐期 P0 + P1 + P2(含 P2-2)全完成 ✅** — 包括 P2-2 自进化机制边界 dedicated session
- **dedicated 计划文档 session 启动条件就绪**(待用户主动召开;本 session 不主动启动 — 继承用户表态)
- 计划文档锁定后 → 启动 Phase X 实施期(任务 H-001 ~ H-028)
- 实施期 Phase A(代码迁移合并 P1-5 + P1-6 + P1-7 + P0-1 旧矩阵删除)与本决策**不冲突**;Phase A 推进与 P2-2 实施期 Phase X 是并行可能(用户决定)

### 6.4 本 P2-2 决策不做的事

- ❌ 不写任何自进化代码(继承 §2 红线 13;包括 prompt registry / RAG ingester / shadow chain / frontier crawler / 任一 Python 文件)
- ❌ 不修改任何 backend/ frontend/ config/ 文件(除文档外)
- ❌ 不顺手清理过时代码(继承 §2 红线 19)
- ❌ 不跑 Codex review(继承 §2 红线 20)
- ❌ 不主动召开 dedicated 计划文档 session(待用户主动召开)
- ❌ 不引入新 LLM provider / 不引入 MLflow / 不引入 SMTP/Slack/Discord 通道
- ❌ 不扩展 evidence_id 5 前缀 / 不扩展 LLM positive list 4 类 / 不扩展 cost_guard 4 常量
- ❌ 不修改 P0-6 acceptance 框架 / 不修改 P0-7 RiskConfig 全锁 / 不修改 P0-10 LLM 字段权限矩阵

---

**P2-2 自进化机制边界决策 2026-05-11 锁定 ✅**

**决策对齐期 P0 + P1 + P2(含 P2-2 dedicated session)全完成 ✅**

保守 3 路径(prompt evolution + RAG provenance-gated + in-context exemplars)+ 全人工 gate + 飞书主动通知 + 45 交易日 shadow validate(沿用 P0-6)+ 文件式 prompt registry + git + restart + BrokerScheduler 第五 cron evolution_shadow_run 22:00 mon-fri + RAG 数据源白名单(学术 + GitHub release 仅)+ frontier 每日 22:00 抓取 → data/rag/ 文件系统 + risk_proposals 合并 P2-2 体系 + audit 27 → 34 类 + 实施期 deferred 等用户计划文档 + Codex 5 轮 R1-R5 在 Phase X 实施期跑;**20 红线 + 28 实施期任务(H-001 ~ H-028)+ 3 派生 amendment** — 用户 critical feedback "自进化必须有 + 模拟盘验证 + 状态回滚"完全实现;P0+P1+P2 累积 130+ 红线零冲突;不破现有锁定。

**下一站**:dedicated 计划文档 session 启动(待用户主动召开)→ Phase X 实施期(代码 + Codex review 5 轮 R1-R5)。
