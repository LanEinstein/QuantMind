# backend/theme_research/ — 子任务上下文(Phase Y,主题研究 peer-sourcing 层)

> 状态:**Y-002 + Y-006 done(2026-06-11,session #74:`sop_schema` + `provenance` + `prompts_loader` + `investigator` + `config/prompts/theme_research/v1.yaml` + lock)**;**Y-003/Y-004/Y-005 进行中**。治理:[P0-8-amendment-2026-06-01](../../docs/decisions/P0-8-amendment-2026-06-01-llm-theme-research-peer-sourcing.md) + R0 §3/§4 + P2-2(LiveArtifactRegistry pin)。任务:plan.html Y-002..Y-006。

## 职责
**LLM+web 联网调查 = 定时·留痕·人工 pin 的 peer-SOURCING 层**(方向①)。只【追加】主题候选,**永不作全局 universe 过滤器**;量化仍是资格权威,纯量化路径始终完整可跑。**严禁**进 Line-1 实时信号 / runtime / replay 路径取数。

## 本模块红线(P0-8-amendment-2026-06-01 §3)
1. **只在定时 research job 内**调 LLM+web(注入式 `WebFetcher`/`LlmClient` Protocol);signal/runtime/replay 路径不联网、不调 LLM。
2. **全留痕**:web 输入 + LLM 输出在调查时全部捕获进 append-only content-addressed 快照(`ThemeResearchSnapshot` 存原始字节 + sha256,仿 `MarketDataSnapshot`)。**隐藏字节的 run = non-promotable**(`ThemeResearchRun.is_promotable()` fail-closed:缺 PROMPT/LLM_RESPONSE/output digest 或 cited 快照未捕获即拒)。replay 永不联网。
3. **evidence ↔ 机读候选分离**:LLM 原始产出只写 `evidence_collection.content`(`THEME-` 前缀,Y-003);**严禁 live code 正则解析 evidence 文本成候选**。机读候选 = 严格 schema 校验的结构化输出(`ThemeResearchOutput`,**非散文正则**)→ 人工 pin 的 content-addressed artifact(Y-003/Y-004)。
4. **结构化 SOP prompt = 一等设计物**:5 步倒推(direction→sectors→chain→chokepoint→tickers)**骨架 frozen**(`ThemePromptRegistry` boot 校验 `validate_sop_skeleton`);措辞/exemplars/source_allowlist 可进化(经 P2-2 离线 + 人工 gate + 45 日 shadow + 重启)。prompt 走文件式版本化 registry(`config/prompts/theme_research/{version}.yaml` + `prompts.lock.json`,SHA256 pin),version hash 入 `LiveArtifactRegistry.PROMPT_VERSION`;实时只认批准版本(`require_pinned=True`)。
5. **prompt-injection 容器化**:source allowlist(非白名单域 fetch 前即 fail-closed 拒)+ 调查 agent **无** secrets/交易状态/RiskConfig 访问(注入式 client,本模块不读 env)+ 严格输出 schema(敌意 web 文本无法夹带决策字段;schema 本就无 side/volume/price 字段)+ 确定性校验。
6. **成本有界**(P1-7 沿用):per-run max web fetches / LLM calls / tokens / timeout;LLM 预留经注入式 `UsageReserver`(写同一 `llm:usage:{utc_date}`,¥100/日 hard,拒超即 abort)。
7. **量化仍资格权威**:pin 的主题候选作 peer-sourced 进确定性管线,仍须过 排除四件套 + 可负担性 + 14-check + builder 单一构造点 + 飞书人工 gate(Y-004)。主题配额 ≤ `final_shortlist_size − min_quant_slots`(≤2);≥3 纯量化名额保留;纯量化永不被否决;无 pin → 配额空、纯量化照跑。

## import 隔离
**本模块 = LLM-bearing,但零 backend 交易栈 import**:LLM/web 经注入式 Protocol(非硬 import),不 `import backend.{risk,broker,api,data,llm,agents,mirofish,screening,marketdata_snapshot}`。可用:`backend.strategy_evolution`(LiveArtifactRegistry pin)+ `backend.knowledge_graph`(读 pin 产业链 KG,Y-004)+ `backend.models.evidence`(THEME- 前缀,Y-003)+ 标准库/pydantic/structlog/filelock/yaml。**screening/marketdata_snapshot/risk/cost-guard 仍 0-LLM**(它们永不 import theme_research)。AST 隔离测试见 Y-005。

## 模块结构(已实现 Y-002+Y-006)
| 文件 | 内容 | 任务 |
|------|------|------|
| `sop_schema.py` | 严格输出 schema(`ThemeResearchOutput`=趋势→板块→链环节→`ChokePointFinding`→`ThemeCandidate`;5 步 `ThemeStep`;sourcing-only 无决策字段;null_result 透明) | Y-002 |
| `provenance.py` | `ThemeResearchSnapshot`(原始字节+checksum 自校验,仿 MarketDataSnapshot)+ `ThemeResearchRun`(promotability fail-closed)+ `ThemeResearchStore`(content-addressed append-only) | Y-002 |
| `prompts_loader.py` | `ThemePromptRegistry`(文件式 SHA256 pin + frozen SOP 骨架校验 + LiveArtifactRegistry.PROMPT_VERSION 认证;不可变/fail-closed/无 hot-reload,镜像 PromptRegistry) | Y-006 |
| `investigator.py` | `ThemeInvestigator`(定时有界 job body:allowlist + 全捕获 + 成本 bound + 严格 parse;注入式 web/LLM/reserver) | Y-002 |
| `config/prompts/theme_research/v1.yaml` | 5 步倒推 SOP(一等设计物;骨架 frozen,措辞可进化) | Y-002/Y-006 |
| `config/prompts/theme_research/prompts.lock.json` | v1 SHA256 pin(active_version=v1) | Y-006 |

## 接口契约
- `ThemePromptRegistry.from_lockfile(lock, *, repo_root, registry=None, require_pinned=False)` → 不可变;`active_prompt()` / `active_sha256` / `active_version`。
- `ThemeInvestigator.investigate(ResearchRequest) -> ThemeResearchResult`(`output: ThemeResearchOutput | None` + `promotable` + `aborted_reason`)。
- 待续(Y-003/Y-004):`candidate_artifact.py`(THEME- evidence + content-addressed `ThemeCandidateArtifact` 人工 pin)+ `peer_sourcing.py`(CandidateSelector 接线,配额/保量化)。
