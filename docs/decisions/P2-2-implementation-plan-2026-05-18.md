# P2-2 实施计划文档(Phase X 自进化详细 28 任务清单 + 2026 SOTA 技术决策)

## 元数据

| 字段       | 值 |
|-----------|----|
| 计划编号   | P2-2-implementation-plan |
| 锁定日期   | 2026-05-18 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com(项目所有者)|
| Session    | #21 phase-x-dedicated-planning(2026-05-18)|
| 前序决策   | `docs/decisions/P2-2-self-evolution-conservative-three-paths-shadow-validate-feishu-notify-file-registry.md`(2026-05-11 锁;本计划文档实施期落地)|
| 调研依据   | 6 路并行 web 研究(2026-05-18 session #21 启动期):DSPy GEPA + prompt 优化替代方案 + FinMem + RAG provenance + LLM 量化生态 + shadow validation 工程模式;汇总 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_phase_x_research_2026_05_18.md`|
| 不变更前序 | P2-2 主决策 20 红线 100% 保留;本计划仅落地实施期细节,**不**改变 P2-2 锁定的 3 路径 / 全人工 gate / 45 日 shadow / 文件式 registry / BrokerScheduler 第 5 cron / RAG 白名单 / audit 27→34 类等任一架构层决策 |
| 派生 amendment | 0 新派生(P2-2 已派生 3 amendment 全部 sufficient);本计划文档**不**新增任何 amendment;3 项 R1/R3/R7 风险硬约束作为 P2-2 §2 红线 21~23 直接扩展,**不**单独派生 amendment(沿用 P2-2 主决策 §2 红线扩展形式)|

## 决策摘要

2026-05-11 P2-2 主决策锁定**保守 3 路径 + 全人工 gate + 45 日 shadow validate + 文件式 prompt registry**;2026-05-18 dedicated 计划文档 session(本文档)落地实施期细节 ✅ 16 议题逐题锁定,**P2-2 三路径 2026-05-18 SOTA 验证强化锁定**(DSPy GEPA ICLR 2026 Oral 接收 + 文件式 RAG 7 维度碾压 Mongo/向量库 + FinMem→ERL 路径升级 deferred 到 Phase X-finale)。

**16 议题决策(Q1-Q16):**

| # | 议题 | 决策 |
|---|------|------|
| Q1 | SDK pin 策略 | 严格 pin 当前最新 + 每次升级前 Codex R3 SDK 验证 |
| Q2 | Embedding model 选型 | **Qwen3-Embedding-0.6B 本地**(QwenLM 2025-06;MTEB multilingual #1 ranking;Apache 2.0;CPU ~300-500ms/query;零新凭证)|
| Q3 | GitHub PAT 凭证管理 | 只读异质凭证不加入 P1-6 凭证池(GITHUB_TOKEN 放 ~/.bashrc 单源 + secrets_validator 校验 + gitleaks rule 扩展 ghp_/github_pat_)|
| Q4 | 7 风险信号红线化 | R1+R3+R7 三项硬约束;R2/R4/R5/R6 已被 P0 红线天然 hedge 不重复 |
| Q5 | Exemplar collection 策略 | decision_ledger Mongo aggregation pipeline 派生视图,零新 collection |
| Q6 | ExemplarRecord schema | 14 字段含 is_anti_exemplar(防 majority bias)|
| Q7 | provenance.jsonl JSON Schema | 全采纳 17 字段 + 5 子字段 sanitization_applied(Pydantic v2 frozen+strict+extra='forbid')|
| Q8 | shadow_acceptance_reports 字段 | 沿用 P0-6 AcceptanceReport + 扩 3 字段(bootstrap_pnl_ci_95pct + challenger_artifact_id + champion_baseline_id)|
| Q9 | 第 4 路径 frontier→proposal Agent | DEFER 到 Phase Y dedicated session(Phase X 不破 P2-2 "保守 3 路径"红线)|
| Q10 | FinMem→ERL 升级 | Phase X-A 先落 FinMem 基线,Phase X-finale 评估 ERL heuristic 蒸馏升级 |
| Q11 | 4 Agent 差异化注入 | fundamental→deep+intermediate / technical→shallow / risk→必含 anti_exemplar / fund_manager→each layer 1 |
| Q12 | 前端策略 | 严格沿用 P2-2 lock(SystemStatus.vue 内嵌"自进化待处理"slot + 飞书主动通知;不新增页履行 P1-5 11 页名额)|
| Q13 | H→X 命名重映射 | X-001..X-028 替代 P2-2 doc 的 H-001..H-028;删除 plan.html 现有 X-001/X-002/X-003 3 个占位 |
| Q14 | Phase X 与 I-002 排序 | **并行**(Phase X 18 模块全部 import 守门解耦,与 I-002 主路径零依赖)|
| Q15 | Codex 5 轮触发节点 | P2-2 原计划全做完再 5 轮,但 X-B(DSPy GEPA + frontier_crawler 落地)完成后**提前**跑 R3 SDK |
| Q16 | 本 session 边界 | 严守 P2-2 lock 纯 docs(决策文件 + plan.html 更新 + SESSION_LOG)+ 预飞演练(跑现有 scripts/smoke_test_cold_start.py + simulate_n_trading_days.py;零新代码)|

## 1. 决策具体内容

### 1.1 Q1 SDK pin 策略(严格 pin + Codex R3 升级验证)

**pyproject.toml 必锁版本号**(Phase X 实施期 X-001 必须落地):

```toml
[project]
dependencies = [
    "dspy==3.2.1",           # 2026-05-05 最新稳定;严禁 dspy-ai(已 deprecated)
    "gepa>=0.0.26,<0.1",     # DSPy 3.1.3 起捆绑;锁 0.x 防 1.x breaking
    "litellm>=1.60,<2",      # dashscope provider 完整 starting 1.60
    "openreview-py>=1.40",   # API 2 默认 starting 1.40
    "bleach>=6.0",           # HTML sanitize for Layer 1 prompt-injection 防护
    "simhash>=2.1",          # OpenReview-arxiv 标题 SimHash 64-bit 去重
    "sentence-transformers>=3.0",  # Qwen3-Embedding-0.6B 推理
]
```

**升级守门**:任何 dspy / gepa / litellm minor 或 patch 升级**前**必跑 Codex R3 SDK 验证(继承 `feedback_codex_findings_real`);R3 通过 + 现 SDK 升级理由 documented in commit message 才允许升级。R3 失败 → 升级回滚 + audit 记录。

**关键工程坑(必须在 dspy_gepa_runner.py docstring 体现)**:
1. DeepSeek `<thinking>` 吞失(DSPy issue #7489)— reflection_lm 用 `deepseek-reasoner` 必须 `dspy.Reasoning` 显式启用
2. DSPy 对 GPT-5 误传 `max_tokens` 而非 `max_completion_tokens`(issue #8612)— **本项目无 GPT-5,不涉及**,但 reasoning 模型场景需 audit
3. compile() 不可中断 — 必经 BrokerScheduler 第 5 cron 22:00 调度,失败 1-retry + audit;**严禁工作时段触发**
4. Pareto frontier 持久化 — `log_dir=data/dspy_runs/{YYYY-MM-DD}/` 文件,**不入 Mongo**

### 1.2 Q2 Embedding model(Qwen3-Embedding-0.6B 本地)

**选型理由**:
- **2026 SOTA**:MTEB multilingual #1 leaderboard(8B 模型 70.58 分;0.6B 小版本对比 BGE-M3 同参数 +7.9%)
- **Apache 2.0** 商业友好
- **与 LLM provider Qwen 同源**(QwenLM Alibaba),中文质量上限最高
- **本地 CPU 推理**:600M 参数;CPU 单 query ~300-500ms;90 日窗口 <5k 条 batch ~5-10min(EOD chain 后台跑)
- **零新凭证**:严守 P1-6 §1.1 凭证池封闭性(LLM 3 + 飞书 5)

**部署**:
- 模型权重下载到 `data/models/Qwen3-Embedding-0.6B/`(`huggingface-cli download Qwen/Qwen3-Embedding-0.6B --local-dir data/models/Qwen3-Embedding-0.6B`)
- 启动期 `sentence_transformers.SentenceTransformer(path, device='cpu')` 加载到 app.state.embedding_model
- 严禁运行期下载(防 SHA256 漂移 + IPv4-only egress 兼容性问题);启动期 fail-fast 校验模型文件存在 + checksum

**输出向量**:1024 维 float32;cosine similarity 计算 numpy einsum 暴力快(<5k 条 <10ms);**不引 FAISS/Chroma**(防破 P1-6 §1.5 全层 127.0.0.1 + 0 新服务)。

### 1.3 Q3 GitHub PAT 凭证管理(只读异质凭证)

**理由**:
- GitHub PAT 是**只读 + 可零代价撤销 + 不接触决策路径**,与 LLM key(执行模型)/ 飞书凭证(消息收发)性质完全不同
- 2025-05-08 GitHub 匿名 60/hr 全局 IP 收紧 = 必须 auth;5000/hr PAT 全 cover Phase X 9 repos × 每日 1 次需求
- 不破 P1-6 凭证池封闭性 = 不需要 amendment

**落地**:
1. `GITHUB_TOKEN=ghp_xxxx`(fine-grained PAT,仅 9 repos read scope)放 `~/.bashrc` 单源
2. `backend/services/secrets_validator.py` 启动期校验 `os.environ.get("GITHUB_TOKEN")` 存在性(类似 P1-6 §1.7 secrets_validator,但**不入凭证池统计**;仅 fail-fast on missing)
3. `.gitleaks.toml` 加 rule 扩展 `ghp_/github_pat_/gho_/ghu_/ghs_/ghr_` 6 前缀(GitHub PAT 全格式)
4. **严禁** `.env` / 代码 hardcode / 写 Mongo / 写文件 plaintext;仅 `os.environ` 读取
5. fingerprint = SHA256[:8](沿用 P1-6 §1.4 fingerprint 模式)

### 1.4 Q4 R1+R3+R7 三项 Phase X 硬约束

**R1 GEPA 过拟合防护**(扩 P2-2 §2 红线 21):
- `backend/services/dspy_gepa_runner.py` 必有 module 级常量 `GEPA_MAX_SAMPLES = 100` + `GEPA_MAX_ITERATIONS = 10`
- DSPy GEPA 实例化必传 `max_metric_calls=GEPA_MAX_SAMPLES * 2`(每 sample × 2 含 valset 评估)
- iteration 数通过 `auto="light"`(20-50 call ≈ 10 iter)硬上限
- 超限 raise `GEPASampleLimitExceededError`(继承 `RuntimeError`)+ audit `shadow_evolution_run_completed` outcome=BLOCKED reason='sample_limit'
- **单元测试**断言 GEPA_MAX_SAMPLES + GEPA_MAX_ITERATIONS 常量值 + raise 行为

**R3 RAG retrieval precision fail-closed**(扩 P2-2 §2 红线 22):
- `backend/services/rag_provenance.py` 必有 `RAG_RETRIEVAL_PRECISION_FLOOR = 0.80`
- shadow_chain 调用 RAG 时,若过去 7 日 retrieval precision(human-labeled relevance / total retrieved)<80% → fail-closed 跳过 RAG 增强,沿用现 prompt 走完 shadow run + audit `rag_document_rejected_non_whitelist` outcome=BLOCKED reason='precision_floor_breached'
- precision 度量:每个 retrieved doc 注入 prompt 后,owner 在飞书 amendment review 时打 relevant/irrelevant 标签;近 7 日 rolling
- **集成测试**断言 80% 以下 fail-closed 行为

**R7 amendment 强制 diff + 可读性**(扩 P2-2 §2 红线 23):
- `backend/services/amendment_drafter.py` 起草的 amendment 必含 4 强制 section:
  1. `## diff`(prompt YAML 字段级 diff,unified format)
  2. `## shadow evidence`(链接 `shadow_acceptance_reports` 文档 + 5+3 硬门槛达标证据)
  3. `## readability check`(challenger prompt 总 token 数 + Flesch 阅读难度等价中文指标 + 与 champion 长度比;长度膨胀 >50% raise 警告标记 owner 重点 review)
  4. `## rollback`(`git revert <commit_hash>` 命令模板 + restart 步骤)
- 缺任一 section → drafter raise `AmendmentSchemaError`;**单元测试**断言

### 1.5 Q5-Q6 ExemplarRecord schema(14 字段含 is_anti_exemplar,decision_ledger 派生视图)

```python
# backend/services/exemplar_selector.py
from pydantic import BaseModel, ConfigDict
from typing import Literal

class ExemplarRecord(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    
    # 标识(必备 3)
    exemplar_id: str             # "EXEMPLAR-{instruction_id}"
    instruction_id: str          # FK -> instruction_plans
    decision_date: date          # 决策日
    
    # 决策上下文(必备 4)
    agent_role: Literal["fundamental_analyst", "technical_analyst",
                        "risk_officer", "fund_manager"]
    stock_code: str              # 6 位股票代码或 ETF 代码
    action: Literal["BUY", "SELL", "HOLD"]
    reasoning_excerpt: str       # ≤500 tokens,P0-10 positive list 允许
    
    # 证据 + 置信度(必备 2)
    evidence_ids: tuple[str, ...]   # P0-8 5 前缀;frozen tuple
    confidence_at_decision: float   # 0-1,LLM 自评写
    
    # 结果反馈(必备 2)
    outcome: Literal["profit", "loss", "neutral", "pending"]
    layer: Literal["shallow", "intermediate", "deep"]  # decay layer
    
    # 强制扩展(必备 3,Q6 锁定)
    outcome_pnl_bp: int | None       # 实现损益(basis points);pending 时 None
    embedding: tuple[float, ...] | None  # Qwen3-Embedding-0.6B 1024-d;可重算可不存
    is_anti_exemplar: bool           # 反例标记;risk_officer 强制召回 ≥1
```

**派生视图实现**:`backend/services/exemplar_selector.py` 的 `ExemplarBuilder.derive_from_acceptance(date)` 在 BrokerScheduler EOD chain 第 4 步触发(acceptance_report 落地后);Mongo aggregation pipeline JOIN `decision_ledger + instruction_plans + execution_reports + agent_debate_records` 产出 `ExemplarRecord` 列表;**不**入新 collection;**不**持久化(每次 retrieve 重算或 90 日 window 后端 batch 重算 embedding cache)。

**召回策略**(Q11 4 Agent 差异化):
```python
def retrieve(agent_role: str, query_context: str, k: int = 3) -> tuple[ExemplarRecord, ...]:
    # Stage 1: time-window 90 日 + agent_role 过滤(Mongo $match)
    # Stage 2: outcome 分层(facet)— 强制 k 内 outcome 多样性
    # Stage 3: vector similarity(numpy einsum,Qwen3 1024-d cosine)
    # Stage 4: 4 Agent 差异化 stratify:
    #   - fundamental: deep 2 + intermediate 1
    #   - technical:   shallow 3
    #   - risk_officer: 必含 ≥1 is_anti_exemplar=True
    #   - fund_manager: each layer 1
    # Stage 5: top-k=3 限定(P2-2 §1.1.1 锁;over-prompting 防护)
    ...
```

**严禁** LLM-rerank(破 P1-7 cost guard);严禁 k>3(论文 arxiv 2509.13196 over-prompting + DeepSeek triage 8k context cap 双重风险)。

### 1.6 Q7 provenance.jsonl 17 字段全 Schema

```python
# backend/evolution/provenance/models.py
from pydantic import BaseModel, ConfigDict, HttpUrl
from typing import Literal

SanitizationApplied = TypedDict("SanitizationApplied", {
    "html_stripped": bool,
    "control_chars_removed": int,
    "injection_markers_flagged": int,
    "max_consecutive_whitespace_collapsed": bool,
})

class RagProvenanceEntry(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    
    doc_id: str                     # ^(ARXIV|S2|OPENREVIEW|GH-REL|AKSHARE)-[A-Za-z0-9._-]+$
    source: Literal["arxiv", "semanticscholar", "openreview",
                    "github_releases", "akshare"]
    source_url: HttpUrl
    source_domain: str
    title: str                      # maxLength=500
    authors: tuple[str, ...]        # maxItems=50
    published_at: datetime
    ingested_at: datetime
    content_sha256: str             # ^[a-f0-9]{64}$
    content_length_chars: int       # 0-200000
    whitelist_rule_version: str     # ^v\d+\.\d+$
    license: str
    external_id: str
    category: tuple[str, ...]
    language_detected: Literal["en", "zh", "other"]
    sanitization_applied: SanitizationApplied  # 5 子字段强制
    ingester_version: str
    rejection_reason: str | None    # None on success
```

**append-only 守门**:`backend/evolution/provenance/writer.py` 必用 `fcntl.flock(LOCK_EX)` 锁文件 + `O_APPEND` 写入;**严禁** truncate / seek / 覆写;启动期 fail-fast 校验文件存在 + 非空时最后一行 JSON valid。

**hash-anchored citation**:每条 retrieval 注入 prompt 时必带 `<RAG_DOCUMENT doc_id="..." sha256="...">...</RAG_DOCUMENT>` 包裹;LLM 输出引用必含 doc_id + sha256;hash mismatch fail-closed 丢弃。

### 1.7 Q8 shadow_acceptance_reports 扩 3 字段

```python
# backend/services/shadow_chain.py
class ShadowAcceptanceReport(AcceptanceReport):  # 继承 P0-6 AcceptanceReport
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    
    # 扩 3 字段(P0-6 不变)
    bootstrap_pnl_ci_95pct: tuple[float, float]   # (low, high) 95% CI
    challenger_artifact_id: str                   # "PROMPT-{agent}-v{N+1}"
                                                  # 或 "RISK-PROPOSAL-{proposal_id}"
                                                  # 或 "EXEMPLAR-SCHEMA-v{N+1}"
    champion_baseline_id: str                     # 对比基线 ID
```

**bootstrap CI 计算**:scipy.stats.bootstrap 1000 次 resample;**严禁** 额外 BLAS lib(numpy + scipy 已在依赖中);95% 双尾 percentile method。

**collection**:`shadow_acceptance_reports`(物理隔离 production `acceptance_reports`;P0-6 §2 红线 5 acceptance 路径独立);unique index on `(challenger_artifact_id, trade_date)` + `(champion_baseline_id, trade_date)` + DESC index on `computed_at`。

### 1.8 Q9 第 4 路径 frontier→proposal Agent DEFER 到 Phase Y

**deferred 理由**:
- Phase X 当前 28 任务工作量已 ~28 工作日;加入第 4 路径破坏 P2-2 "保守 3 路径"红线 + 工作量膨胀到 ~40 天
- 第 4 路径需要 owner 充分讨论(RD-Agent-Quant 论文细节 + factor mining 决策权边界 + amendment 起草自动化深度)
- Phase Y 独立 dedicated session 启动条件:Phase X 全部 done + 45 日 shadow validate 至少 1 轮成功 + owner 主动召开

**Phase Y 占位**(plan.html 不在本 session 添加,等 owner 决策启动):
- Y-001: RD-Agent-Quant 风格架构调研 + 决策对齐 session
- Y-002: strategy/factor proposal Agent 实现
- Y-003: proposal 走 risk_proposals 通道 + amendment 起草自动化

### 1.9 Q10 FinMem→ERL 渐进升级

**Phase X-A 基线**(本计划锁):FinMem 风格原始 reasoning_excerpt 注入(reasoning_excerpt 字段已含 ≤500 tokens 摘要),decay layer 三层(shallow 0.9 / intermediate 0.967 / deep 0.988)沿用 FinMem 论文 α 值。

**Phase X-finale 评估升级 ERL**:
- ERL = arxiv 2603.24639 "Experiential Reflective Learning";heuristic 蒸馏 = LLM 阅读决策 trace 后输出 reusable heuristic("当 PE>30 且 RSI>70 时 risk_officer 应优先 HOLD")
- 评估指标:相同 shadow validate 45 日 + 5+3 硬门槛;ERL 优于 FinMem 基线 → 触发 P2-2 amendment 升级 + Phase X-A schema 扩字段 `distilled_heuristic` + Phase X-B exemplar_selector 注入 heuristic 而非原始 reasoning

### 1.10 Q11 4 Agent 差异化注入策略

| Agent | Layer 分布 | 反例强制 | 备注 |
|-------|-----------|---------|------|
| fundamental_analyst | deep 2 + intermediate 1 | No | 长周期年报/季报洞察主导;ETF 标的 fallback intermediate 3 |
| technical_analyst | shallow 3 | No | 短周期决策 trace;同股 exemplar 优先,跨股相似走势次之 |
| risk_officer | shallow 2 + 反例 ≥1 | **Yes** | 熔断/14-check 拦截过的 case 优先;is_anti_exemplar=True 强制召回 1 |
| fund_manager | each layer 1 | No | BUY/SELL/HOLD 三种 outcome 各 1;综合决策终局 |

**实现**:`exemplar_selector.py::retrieve(agent_role, ...)` 内 `_AGENT_STRATEGY` dict;每 agent_role 走不同 stratify 分支;**单元测试** 断言每个 agent_role 召回结果满足 layer 分布 + anti_exemplar 约束。

### 1.11 Q12 前端 SystemStatus 内嵌(不新增页)

**履行 P1-5 §2 红线 1**:11 页名额已锁(MVP 7 + Phase B 4),Phase X 不破。

**SystemStatus.vue 扩展**:在现有 5 freeze source 卡片之后加 1 行"自进化待处理":
- 0 个 pending → 绿色 ✅
- 1-3 个 pending → 黄色 ⏳
- >3 个 pending → 红色 ⚠️
- 数据源:GET `/api/evolution/pending`(X-022 实现);5 min polling

**飞书通知主路径**(沿用 P0-2-amendment-2026-05-16):
- 走 owner 自建应用 OpenAPI 同款发到 `FEISHU_ALERT_CHAT_ID`
- **严禁** `FEISHU_CUSTOM_BOT_*`(P0-2-amendment-2026-05-16 永禁存在)
- 模板:`render_evolution_pending`(纯文本,沿用 P0-3 阶段 1 模板格式)

### 1.12 Q13 H→X 命名重映射(本 session 必修)

**修改**:
- 删除 `docs/plan.html` 现有 X-001 / X-002 / X-003 3 个占位(行 1738-1770)
- 插入 X-001..X-028 详细任务(本计划文档 §5);TASKS 数组所在位置保持 phase ordering 一致
- `docs/decisions/P2-2-self-evolution-conservative-three-paths-shadow-validate-feishu-notify-file-registry.md` §3 H-001..H-028 表格**保留**作历史记录;新计划文档 §5 是 SSoT
- CLAUDE.md 不需改(§2.12 P2-2 描述仍正确)

### 1.13 Q14 Phase X 与 I-002 并行(import 守门解耦)

**并行可行性论证**:
- Phase X 18 模块(`backend/evolution/*` 14 + `backend/services/{prompt_registry, rag_provenance, shadow_chain, exemplar_selector, dspy_gepa_runner, evolution_dispatcher, amendment_drafter, evolution_feishu_notifier, evolution_audit_writer}` 9)**严禁** import `backend.{api, broker, risk, llm, agents, mirofish, data}`(P2-2 §2 红线 17 + 本计划 X-018 守门)
- I-002 主路径 = backend 长跑累积 45 真实交易日 acceptance window;与 evolution 模块零依赖
- 唯一接入点:BrokerScheduler 第 5 cron(I-002 期间 backend restart 注入新 cron 约 5 分钟;P0-6 reset 触发器 5 类不含 "Phase X module 注入",**不触发 acceptance 重置**)

**实施步骤**:
1. I-002 启动后(owner 手动 + J-007 二级门 + systemd unit);
2. Phase X 实施期任意 session 在 I-002 主路径不受影响时 land X-001..X-019(全部 backend/evolution + backend/services/* 新文件,**不**改 main.py / scheduler.py 等 I-002 hot path);
3. X-020 BrokerScheduler 第 5 cron 接入需 **owner 协调 5 min restart 窗口**(audit `brokerscheduler_started/stopped`);
4. X-021..X-028 在第 5 cron 接入后陆续推进

### 1.14 Q15 Codex 5 轮 + X-B 后提前 R3

**P2-2 原计划 5 轮**:R1 consistency + R2 redteam + R3 SDK + R4 security + R5 coverage,Phase X-A/B/C/D 全完成后启动。

**新增**:Phase X-B 完成后(X-008..X-015 即 DSPy GEPA + frontier_crawler 落地)**立即跑 R3 SDK**(不等 X-C/X-D)。理由:
- DSPy GEPA + LiteLLM + openreview-py 跨小版本 adapter behavior 漂移已记录(DSPy issue #7489/#8612);
- R3 SDK 是 5 轮中**最易受 SDK 升级回滚整批代码**的轮;
- 提前 R3 = X-B 后立即验证 SDK 契约 → 即使 R3 失败,回滚范围仅 X-B(8 任务)而非全 28 任务

**修订 P2-2 §3.5 任务清单**:
- X-024 R1 consistency:Phase X-A/B/C/D 全部完成后
- X-025 R2 redteam:R1 通过后
- **X-026 R3 SDK(提前):X-015 完成后立即跑,不等 X-D 完成**
- X-027 R4 security:R3 通过 + X-D 完成后
- X-028 R5 coverage:R4 通过后

### 1.15 Q16 本 session 严守 P2-2 lock(纯 docs + 预飞演练)

**本 session 交付物**(2026-05-18 session #21):
1. `docs/decisions/P2-2-implementation-plan-2026-05-18.md`(本文档)
2. `docs/plan.html` 更新:删 3 占位 + 插 X-001..X-028 28 任务 + 添 SESSION_LOG #21 + 修订记录
3. `~/.claude/projects/.../memory/project_phase_x_research_2026_05_18.md`(已落)+ `MEMORY.md` 索引更新(已落)
4. 预飞演练:跑 `scripts/smoke_test_cold_start.py` + `scripts/simulate_n_trading_days.py --days 45`(QUANTMIND_LLM_STUB=1 默认,零真实 LLM ¥)+ 输出预飞报告 `docs/reviews/phase-x-preflight-2026-05-18.md`(简版)
5. docs-only commit(per CLAUDE.md §1.4 session 结尾一次性)

**严禁**:
- 写任何 `backend/evolution/*.py` 或 `backend/services/{prompt_registry, rag_provenance, ...}.py` 自进化代码
- 改 main.py / scheduler.py / agents/prompts.py 等现有代码
- 创建 `config/prompts/{agent}/` 目录或 `data/rag/{source}/` 目录(留到 X-001/X-002 实施期)

## 2. Phase X 红线扩展(P2-2 §2 红线 21~23)

继承 P2-2 主决策 §2 红线 1~20;本计划文档新增红线 21~23 落地 Q4 三项硬约束:

### §2 红线 21 — GEPA 过拟合 hard cap
- `backend/services/dspy_gepa_runner.py` 必有 `GEPA_MAX_SAMPLES = 100` + `GEPA_MAX_ITERATIONS = 10` module 常量;runtime 不可改 + hot-reload 禁用(继承 P0-7 §2 红线 14)
- DSPy GEPA 实例化必传 `max_metric_calls=200` + `auto="light"`(< iter 10)
- 超限 raise `GEPASampleLimitExceededError` + audit `shadow_evolution_run_completed` outcome=BLOCKED reason='sample_limit'
- 单元测试断言常量值 + raise 行为

### §2 红线 22 — RAG retrieval precision fail-closed
- `backend/services/rag_provenance.py` 必有 `RAG_RETRIEVAL_PRECISION_FLOOR = 0.80` module 常量
- 每 retrieval 注入 prompt 后 owner 在飞书 amendment review 时打 relevant/irrelevant 标签;近 7 日 rolling precision
- precision < 0.80 → fail-closed 跳过 RAG 增强;沿用现 prompt 走完 shadow run;audit `rag_document_rejected_non_whitelist` outcome=BLOCKED reason='precision_floor_breached'
- 集成测试断言 fail-closed 行为

### §2 红线 23 — amendment 强制 4 section + 长度警告
- `backend/services/amendment_drafter.py` 起草必含 `## diff` + `## shadow evidence` + `## readability check` + `## rollback` 4 section
- challenger prompt 长度膨胀 >50% → readability check section 加 `[WARNING] length_inflation_50pct` 标记 owner 重点 review
- 缺任一 section → drafter raise `AmendmentSchemaError`
- 单元测试断言

## 3. Phase X 实施期 28 任务清单(X-001..X-028)

> ⚠️ **本清单 deferred 等用户在 Phase X 实施期 dedicated session 启动后才允许实施;本 session 严禁写自进化代码。**
>
> Phase X 与 Phase J/I 关系:Phase J 已 done ✅(2026-05-17 session #20);Phase X 与 I-002 真实长跑可**并行**(Q14 决策);Phase X-finale 完成 + 45 日 shadow validate 至少 1 轮成功 → 解锁 Phase Y dedicated session(Q9 deferred)。

### 3.1 Phase X-A: 基础设施(7 任务)

| ID | 标题 | 依赖 | 估时 | 描述 |
|----|------|------|------|------|
| X-001 | `config/prompts/{agent}/{version}.yaml` 目录 + `prompts.lock.json` schema | 无 | 0.5d | 创建 config/prompts/ + 4 必经 Agent 子目录(fundamental_analyst/technical_analyst/risk_officer/fund_manager);frozen Pydantic `PromptLockFile` schema;.gitkeep 占位 |
| X-002 | `data/rag/{source}/{date}/{doc_id}.md` + `provenance.jsonl` 目录(fcntl flock 锁) | 无 | 0.5d | 5 source 子目录(arxiv/semanticscholar/openreview/github_releases/akshare);provenance.jsonl append-only;启动期 fail-fast 校验 |
| X-003 | `backend/services/prompt_registry.py`(PromptRegistry frozen + load_pinned_version + version_lock_validate + alias_resolver) | X-001 | 1d | 单一真相源加载 prompts.lock.json + 校验文件存在 + checksum + 启动期 fail-fast;0 runtime mutate;0 hot-reload |
| X-004 | `backend/evolution/provenance/{models, writer, verifier}.py`(RagProvenanceEntry 17 字段 frozen + 启动期白名单校验) | X-002 | 1d | Q7 schema 落地;hash-anchored citation;3 文件 model_config strict+frozen+extra='forbid' |
| X-005 | P1-2.A amendment 落地 — BrokerScheduler `evolution_shadow_run` 5th cron(22:00 mon-fri Asia/Shanghai) | P1-2.A 主决策 | 0.5d | `add_job(CronTrigger.from_crontab("0 22 * * 1-5", timezone="Asia/Shanghai"), id="evolution_shadow_run")`;失败 1-retry + audit;不冻结买卖路由 |
| X-006 | `backend/services/exemplar_selector.py`(FinMem 风格 ExemplarRecord 14 字段 + 4 Agent stratify + Qwen3-Embedding-0.6B + ≤3 cap) | 无 | 1.5d | Q5/Q6/Q11 落地;decision_ledger 派生视图;Mongo aggregation pipeline 4 stage;严禁 LLM-rerank |
| X-007 | `backend/services/shadow_chain.py`(读 45 日 acceptance_reports + replay decision chain + ShadowAcceptanceReport 输出 + challenger 胜判定) | X-003/X-004/X-006 + P0-6 主决策 | 2d | Q8 三字段扩;沿用 P0-6 compute_acceptance_window;5+3 硬门槛 + 4 严格优于 + 4 不差于 0.5pct;bootstrap CI scipy.stats.bootstrap 1000 resample |

### 3.2 Phase X-B: 自进化执行(8 任务)

| ID | 标题 | 依赖 | 估时 | 描述 |
|----|------|------|------|------|
| X-008 | `backend/services/evolution_dispatcher.py`(4 类自进化提议协调 + 严禁 LLM 反向调用 .activate_*) | X-003..X-007 | 1.5d | 单一入口 dispatch prompt / RAG / risk_proposal / exemplar 4 类;import 守门 |
| X-009 | `backend/services/dspy_gepa_runner.py`(DSPy GEPA 离线 prompt 演化 + R1 sample/iter cap + ≤¥5 budget + deepseek-reasoner reflection_lm) | X-003 + cost_guard | 2d | Q1 SDK pin + R1 硬约束 + Reasoning module + log_dir 文件持久化 |
| X-010 | `backend/evolution/frontier_crawler.py`(每日 22:00 5 源 crawl + DeepSeek 总结 + 写 data/rag/) | X-004 + cost_guard | 2d | arxiv OAI-PMH + S2 + OpenReview + GitHub PAT + akshare Atom;asyncio.Semaphore + Spotlighting datamarking |
| X-011 | `backend/evolution/rag_ingester.py`(provenance + 白名单 + sanitize + audit + 严禁非白名单入库) | X-004 + X-010 | 1d | 3 层防 injection;hash-anchored citation;starting 校验 whitelist_rule_version |
| X-012 | P0-7 amendment 落地 — risk_parameter_proposals 扩 4 字段(target_artifact_type + shadow_validation_status + pending_amendment_id + feishu_notified_at) | P0-7 主决策 | 0.5d | 非破坏式扩展 default=None;sub Pydantic schema |
| X-013 | `backend/services/amendment_drafter.py`(shadow 通过后自动起草草案 + R7 强制 4 section + ≤¥5 budget) | X-007 + cost_guard | 1.5d | R7 硬约束;`docs/decisions/pending/{artifact_id}.md` 文件;LLM 生成走 cost_guard |
| X-014 | `backend/services/evolution_feishu_notifier.py`(shadow 通过 → 飞书通知 + Alerter dedup_15min + 走 `renderer.py::render_evolution_pending`) | P1-7 §1.7 + P0-2-amendment-2026-05-16 | 1d | 走自建应用 OpenAPI 发 FEISHU_ALERT_CHAT_ID;**严禁** FEISHU_CUSTOM_BOT_*;纯文本模板;dedup 防轰炸 |
| X-015 | `backend/services/evolution_audit_writer.py`(写 7 类 audit + JSONL 双写 + actor=SYSTEM/SCHEDULER) | X-016 + P1-6 §1.7 | 0.5d | 7 类自进化 audit 写入封装;严禁 LLM/FRONTEND_USER 写;调用 audit_writer.write() |

### 3.3 Phase X-C: Audit + 守门(5 任务)

| ID | 标题 | 依赖 | 估时 | 描述 |
|----|------|------|------|------|
| X-016 | P1-6 第 3 次 amendment 验证 — AuditEventType 7 类自进化 enum 已落地 review | P1-6 主决策 + 前 2 amendment | 0.2d | 已发现 `backend/audit/models.py:81-87` 7 类预定义 ✅;本任务仅 review + 单元测试断言 7 类存在 + ATA actor 守门 |
| X-017 | cost_guard 集成 P2-2 — DSPy GEPA + frontier_crawler + amendment_drafter LLM 调用计入 daily ¥20 / monthly ¥440 / Kimi ¥4 | P1-7 主决策 | 0.5d | `assert_budget_allows()` 调用包裹所有 P2-2 LLM 出站;严禁单独预算池 |
| X-018 | 守门检查 — 18 模块严禁 import backend.{api,broker,risk,llm,agents,mirofish,data} 三层(grep + ruff lint + AST + 单元测试) | X-003..X-015 | 0.5d | ruff isort.no-restricted-imports rule + AST scan in test + grep redline-check.sh 扩展 |
| X-019 | 单元测试 — prompt_registry / rag_provenance / shadow_chain / exemplar_selector / dspy_gepa_runner / frontier_crawler / rag_ingester / evolution_dispatcher 各 200+ 案例 + 覆盖率 >70% | X-003..X-015 | 3d | 覆盖快路径 + 失败路径 + budget 超限 + 白名单拒绝 + R1/R3/R7 边界 |
| X-020 | 集成测试 — 端到端 evolution_shadow_run 22:00 cron → shadow validate → 通过 → 飞书通知(mock webhook)→ amendment_drafter → audit 完整链 | X-019 | 2d | happy path + 失败 path + budget 超限 + 白名单源拒绝 + R1 sample 超限 + R3 precision 触发 |

### 3.4 Phase X-D: 前端(3 任务)

| ID | 标题 | 依赖 | 估时 | 描述 |
|----|------|------|------|------|
| X-021 | `backend/api/evolution.py`(GET `/api/evolution/pending` 只读 + GET `/api/evolution/runs` shadow_run 历史 + GET `/api/evolution/precision`) | X-008 + P1-5 主决策 | 0.5d | 仅 GET 符合 P1-5 §2 红线 1+2;3 端点;Pydantic strict |
| X-022 | 前端不新增独立页(P1-5 §2 红线 1 11 页名额永锁) | P1-5 主决策 | 0d | 纯约束 |
| X-023 | SystemStatus.vue 内嵌"自进化待处理"slot(`evolution_pending_count` 0 绿 / 1-3 黄 / >3 红;轮询 GET `/api/evolution/pending` 5min 间隔) | X-021 + P1-5 §1.1 | 0.5d | Q12 落地;复用现有 source-card 样式 |

### 3.5 Phase X-E: Codex review 5 轮(5 任务)

| ID | 标题 | 触发条件 | 估时 | 描述 |
|----|------|----------|------|------|
| X-024 | Codex R1 — consistency + redline coherence(P2-2 + 3 amendment + 本计划 §2 红线 21~23 vs 累积 130+ 红线一致性检查) | Phase X-A/B/C/D 全部完成 | 1d | 输出 `docs/reviews/X-XXX-r1-consistency.md` |
| X-025 | Codex R2 — red-team / adversarial(尝试绕过 shadow validate / human gate / prompt injection / 自进化死锁 / cost 超限) | R1 通过 | 1.5d | 输出 `docs/reviews/X-XXX-r2-redteam.md` |
| **X-026** | **Codex R3 — SDK signatures(DSPy GEPA / LiteLLM / Qwen3-Embedding / openreview-py / bleach / simhash 兼容性 + 实际运行验证)** | **X-015 完成立即跑(不等 X-D)** | 1d | **提前 R3 防 SDK 漂移导致大范围回滚;输出 `docs/reviews/X-XXX-r3-sdk.md`** |
| X-027 | Codex R4 — security(RAG ingestion provenance / data/rag/ 文件权限 / provenance.jsonl 篡改防护 / GitHub PAT 防泄露 / cost_guard 不绕过) | R3 通过 + X-D 完成 | 1.5d | 输出 `docs/reviews/X-XXX-r4-security.md` |
| X-028 | Codex R5 — coverage(80%+ 单元测试 + 集成测试 + 端到端 + R1/R3/R7 硬约束断言覆盖;严禁覆盖率回退) | R4 通过 | 1d | 输出 `docs/reviews/X-XXX-r5-coverage.md` |

### 3.6 任务总数 / 估时

- **任务总数**: 28(X-A 7 + X-B 8 + X-C 5 + X-D 3 + X-E 5)
- **总估时**: ~26.7d(连续推进;实际可能跨 4-6 周;Phase X 与 I-002 并行,owner 优先级灵活)
- **关键里程碑**:
  - X-A 完成 → 基础设施就绪
  - X-B 完成 → 自进化执行链路通 + **X-026 R3 SDK 立即跑**
  - X-C 完成 → audit + 守门齐备
  - X-D 完成 → SystemStatus.vue 自进化状态点上线
  - X-E 全部通过 → Codex 5 轮 hard gate 通过 + Phase X 闭环

## 4. 决策依据

### 4.1 16 议题决策 round-by-round 用户对齐(2026-05-18 session #21)

#### Round 1: SDK/依赖/pin
- Q1: 严格 pin + Codex R3 升级验证 ✅
- Q2: Qwen3-Embedding-0.6B 本地 ✅(user 主动询问技术背景后选 SOTA)
- Q3: GitHub PAT 只读异质凭证不加入凭证池 ✅
- Q4: R1+R3+R7 三项硬约束 ✅

#### Round 2: Schema/数据结构
- Q5-Q8 全部按推荐选项锁定(user 指令"剩余选择均采用推荐选项,继续推进")

#### Round 3: 路径与边界
- Q9-Q12 全部按推荐选项锁定

#### Round 4: 任务清单+时间线
- Q13-Q16 全部按推荐选项锁定

### 4.2 关键判断

1. **P2-2 三路径 2026-05-18 SOTA 验证强化**:DSPy GEPA ICLR 2026 Oral + 文件式 RAG 7 维度碾压 Mongo + FinMem→ERL 升级路径明确,**无需推翻 P2-2 lock**
2. **第 4 路径 frontier→proposal DEFER**:基于 RD-Agent-Quant ICML 2026 + TradingAgents v0.2.4 social proof,潜力大但破"保守 3 路径"红线 + 工作量膨胀风险高,放 Phase Y 独立 session 决策
3. **Embedding 选型从 BGE-small-zh 升级到 Qwen3-Embedding-0.6B**:user 主动核实后 SOTA 替代基线;零新凭证 + Apache 2.0 + 中文质量上限
4. **R1+R3+R7 三项 Phase X 红线**:基于 Decagon (GEPA 过拟合) + Gartner 2026 (RAG 14.7% 金融 hallucination) + Benjamin Anderson "Contra DSPy and GEPA" 三 critical signal 落地硬约束
5. **Phase X 与 I-002 并行**:import 守门解耦 + BrokerScheduler 第 5 cron 接入仅需 5 min restart 窗口;最经济执行序列
6. **本 session 纯 docs**:严守 P2-2 lock "本 session 严禁写自进化代码";所有 X-001..X-028 实施代码 deferred 到 Phase X 实施期独立 session

### 4.3 排除选项

- **加入 Phase Y 第 4 路径到 Phase X**: 破"保守 3 路径"红线 + 工作量膨胀;DEFER 到独立 session
- **Phase X 实施期与 I-002 串行**: import 守门已实现解耦,串行损失 ~6 周(45 日 acceptance 等待期)
- **Embedding 选 BGE-small-zh**: 2026 已被 Qwen3-Embedding 全面超越;Qwen3 0.6B 同体量 +7.9% 质量
- **Embedding 选 DeepSeek API**: 引入网络出站 + 计入 ¥20 hard,违反"零新凭证"原则
- **GitHub PAT 加入凭证池**: 异质凭证;只读不接触决策;不需 amendment
- **7 风险信号全转硬约束**: R4/R5/R6 已被 P0-10 / 14-check / 全人工 gate 天然 hedge,重复加红线增加维护负担
- **k>3 exemplars**: arxiv 2509.13196 over-prompting + DeepSeek triage 8k context cap 双重风险
- **新建 exemplar_records collection**: 破 P1-2.A 8 红线 collection 边界;decision_ledger 派生视图够用
- **FAISS / Chroma 外部向量库**: 破 P1-6 §1.5 全层 127.0.0.1 + 0 新服务边界
- **Phase X 新增独立前端页**: 破 P1-5 11 页名额;飞书 + SystemStatus 内嵌足够

## 5. 验证清单

本计划文档锁定后,Phase X 实施期 dedicated session 启动条件:
- [x] P2-2 主决策 lock(2026-05-11 已 ✅)
- [x] dedicated 计划文档 session 完成(2026-05-18 本文档 ✅)
- [x] 6 路并行研究 + 2026 SOTA 验证(2026-05-18 ✅;memory project_phase_x_research_2026_05_18.md)
- [x] X-001..X-028 28 任务 SSoT 落地(2026-05-18 docs/plan.html 更新)
- [x] 预飞演练通过(2026-05-18 scripts/smoke_test_cold_start.py + simulate_n_trading_days.py --days 45)
- [ ] owner 主动召开 Phase X 实施期 session(待 owner 决定;可与 I-002 长跑并行)

---
*本文档锁定 2026-05-18 session #21;后续 Phase X 实施期任意改动必经本计划 amendment + git diff + owner sign-off。*
