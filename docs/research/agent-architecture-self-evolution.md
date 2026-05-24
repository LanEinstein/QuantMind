# Multi-Agent Architecture & Self-Evolution — Research Findings

> Research date: 2026-05-24 · Author: research agent (Claude Opus 4.7)
> Scope: agent orchestration frameworks, trading-specific multi-agent systems, distinct-persona + self-evolution techniques, and safety/governance for self-evolving agents.
> Target platform: QuantMind — local-only (127.0.0.1, no hosted SaaS), Python, runtime LLMs = DeepSeek + Qwen (DashScope) + MiniMax/Kimi, signals delivered to a human via Feishu for manual execution.
> **Hard invariant respected throughout:** deterministic risk checks (position limits, circuit breakers) stay pure-Python; an LLM must NEVER directly write the final decision/risk fields. This mirrors the already-locked P0-7/P0-10 redlines and the P2-2 conservative-3-path self-evolution decision.

---

## 0. TL;DR

- **Orchestration framework: use LangGraph.** It is the only candidate that natively gives all four required properties at once — graph-structured workflows, persistent per-agent memory via pluggable checkpointers (local SQLite/Postgres), debate/critique loops (cycles in the graph), and deterministic tool-gating (tools are plain Python edges you fully control; the LLM never decides the final node). MIT-licensed, model-agnostic, v1.0 (late 2025), and is the substrate the leading open trading system (TradingAgents) already builds on.
- **Adopt the TradingAgents (Tauric Research) role decomposition as the reference blueprint.** Apache-2.0, ~79k stars, v0.2.5 (May 2026), built on LangGraph, and — critically — natively supports **DeepSeek + Qwen/DashScope (China endpoints) + MiniMax + Ollama-local**, i.e. exactly QuantMind's runtime trio with full loopback compatibility. Port its role graph (analysts → bull/bear debate → trader → risk team → fund manager); do NOT adopt it whole, because its risk team is LLM-driven (violates QuantMind's pure-Python risk invariant).
- **Self-evolution = three complementary, fully-offline loops** that QuantMind has already half-locked in P2-2: (1) **GEPA/DSPy** reflective prompt evolution (offline, ≤¥5/run), (2) **FinMem-style layered/decaying-exemplar memory** seeded from the decision ledger, (3) **RAG provenance-gated knowledge base** from a source whitelist. Add **Reflexion-style verbal self-critique** as the per-trade reflection generator that feeds (2). None of these require fine-tuning or online gradient updates.
- **Governance = shadow-validate → human gate → restart, with file+git rollback.** Every evolved artifact (prompt version, exemplar set, RAG doc, risk-param proposal) is validated against the P0-6 45-trading-day rolling acceptance gate in shadow, surfaced to the human via Feishu, applied only by an explicit human edit + amendment + restart, and rolled back by `git revert` + restart. The LLM writes only to `proposal_text`/`reasoning`-class fields and to staging files — never to runtime decision/risk state.

---

## 1. Agent Orchestration Frameworks

### 1.1 Requirement scoring

QuantMind needs four properties simultaneously: **(A) persistent per-agent memory**, **(B) debate/critique loops**, **(C) deterministic tool-gating** (LLM cannot reach final decision/risk fields), **(D) graph-structured workflows**. Plus two hard constraints: **local/self-hostable** and **model-agnostic** (DeepSeek/Qwen/MiniMax).

| Framework | Model | A. Persistent memory | B. Debate/critique loops | C. Deterministic tool-gating | D. Graph workflow | Local / self-host | License | Maturity (May 2026) |
|---|---|---|---|---|---|---|---|---|
| **LangGraph** | Graph / state machine | ✅ Built-in checkpointers (SQLite/Postgres local), time-travel, thread-scoped + cross-thread store | ✅ Cycles in graph = native multi-round debate | ✅ Tools are explicit edges; you decide which node runs; LLM output can be forced through a pure-Python validator node | ✅ First-class (it IS a graph) | ✅ Fully local, model-agnostic | MIT | v1.0 (late 2025), LangChain default runtime; **highest maturity for stateful workflows** |
| **AutoGen / AG2** | Conversational (GroupChat) | ⚠️ In-memory message history by default; persistence via external integration | ✅✅ Best-in-class for free-form debate-to-consensus (GroupChat) | ⚠️ Harder — control flow emerges from conversation; gating final fields needs discipline | ⚠️ Conversation graph, less explicit than LangGraph | ✅ Model-agnostic, local | AutoGen MIT / AG2 fork Apache-2.0 | Microsoft AutoGen effectively in **maintenance**; AG2 (community fork) active, event-driven rearch |
| **CrewAI** | Role-based crew | ✅ Built-in structured memory types | ⚠️ Sequential/hierarchical task hand-off; debate is bolt-on, not native | ⚠️ Role prompts, weaker hard gating | ⚠️ Process flows, not arbitrary graphs | ✅ Model-agnostic, local | MIT | Mature, fastest setup; best for role teams, weakest for hard control flow |
| **OpenAI Agents SDK** | Agent loop + handoffs | ❌ No built-in persistence (in-memory per run) | ⚠️ Handoffs, not structured debate | ⚠️ Guardrails exist but loop-centric | ❌ Linear loop, not graph | ❌ **OpenAI models only** — disqualifying | MIT (SDK) | Mature but vendor-locked → **excluded** |
| **Claude Agent SDK** | Tool-use chain + subagents | ⚠️ Session/context mgmt | ⚠️ Subagent orchestration, not debate-first | ✅ Strong guardrails/safety posture | ⚠️ Orchestration, not explicit graph | ❌ **Claude models only** — disqualifying for runtime | MIT | Mature but vendor-locked → **excluded** |
| **MetaGPT** | SOP-driven software company | ⚠️ Role memory, software-dev oriented | ⚠️ SOP hand-off, not adversarial debate | ⚠️ SOP-encoded, not field-level gating | ⚠️ SOP pipeline | ✅ Model-agnostic, local | MIT | Mature but **domain = software engineering**, not trading; poor fit |
| **Microsoft TaskWeaver** | Code-first plan→code | ⚠️ Session memory | ❌ Not a debate framework | ⚠️ Code-execution centric | ⚠️ Plan/execute, not multi-agent graph | ✅ Local | MIT | Niche / lower activity; **code-interpreter agent, not multi-agent debate** → poor fit |

### 1.2 Recommendation — **LangGraph**

LangGraph is the single framework that satisfies all four required properties without compromise:

- **(D) Graph workflow** is the core abstraction — QuantMind's analysts→debate→trader→risk→fund-manager pipeline maps 1:1 to nodes/edges, and the existing P0-10 "4 mandatory agents + ≥1 debate round" rule becomes literal graph topology that is statically inspectable.
- **(A) Persistent memory**: pluggable checkpointers (`langgraph-checkpoint-sqlite`, `-postgres`, both MIT) snapshot full graph state locally; supports time-travel and resume-after-crash. This aligns with QuantMind's append-only/persistence redlines and runs entirely on 127.0.0.1. Cross-thread `Store` gives per-agent long-term memory.
- **(B) Debate loops**: cycles are first-class — a bull↔bear debate of N rounds is an edge that loops until a `debate_round_count ≥ 1` condition (already a P0-10 redline) is met.
- **(C) Deterministic tool-gating** — the decisive point for QuantMind's safety invariant: in LangGraph, tools and transitions are plain Python edges that **you** author. The LLM produces text into an LLM-writable field; a **pure-Python node** (the RiskEngine 14-check, the InstructionPlanBuilder 5-gate) then runs deterministically and is the only thing that writes decision/risk fields. The LLM literally has no edge into those nodes' writes. This is exactly how the P0-7/P0-10 "LLM never writes decision/RiskCheckSummary" redline is enforced at the graph level rather than by convention.
- **Local + model-agnostic + MIT + v1.0 maturity**, and it is the substrate TradingAgents already runs on, so the reference blueprint ports with minimal impedance.

AG2 (GroupChat) is genuinely better for *free-form* debate, but QuantMind needs *bounded, gateable, auditable* debate, where LangGraph's explicit control flow wins. CrewAI is fastest to stand up but its weak hard-gating and non-graph control flow make the pure-Python risk invariant harder to guarantee. OpenAI/Claude SDKs are excluded on vendor lock-in (cannot run DeepSeek/Qwen/MiniMax). MetaGPT/TaskWeaver are wrong-domain.

---

## 2. Trading-Specific Multi-Agent Systems

### 2.1 Survey table

| System | Year / venue | Core idea | Role decomposition | Memory / reflection | Published results | Limitations | License / repo |
|---|---|---|---|---|---|---|---|
| **TradingAgents** (Tauric / UCLA+MIT) | arXiv 2412.20138, Dec 2024 → rev Jun 2025; repo v0.2.5 May 2026 | Simulate a trading firm with specialized LLM agents + adversarial debate | **7 agents / 4 teams**: Analysts (Fundamentals, Sentiment, News, Technical, parallel) → Researchers (Bull vs Bear, multi-round debate) → Trader (size/timing) → Risk team (Aggressive/Neutral/Conservative debate) → Fund Manager (final approval) | Decision log + one-paragraph reflection w/ realized returns; recent same-ticker + cross-ticker lessons injected into prompts; LangGraph checkpoint resume | AAPL 26.6% / GOOGL 24.4% / AMZN 23.2% cumulative return; Sharpe 5.6–8.2; max drawdown 0.9–2.1% (Jan–Mar 2024) | **Authors flag**: 3-month window only; "anomalously high Sharpe due to few pullbacks"; never deployed live; ~$1–5 & 11 LLM + 20 tool calls per decision; mega-cap tech only; 2024 data-contamination risk | Apache-2.0, `TauricResearch/TradingAgents`, ~79k★, LangGraph, **supports DeepSeek/Qwen/MiniMax/Ollama-local** |
| **FinAgent** (A Multimodal Foundation Agent) | arXiv 2402.18485, KDD 2024 | First **multimodal** trading agent (numeric+text+visual) | Market-intelligence module + **dual-level reflection** + diversified memory retrieval + tool augmentation | Dual-level reflection (fast/slow adaptation); diversified memory retrieval; integrates expert strategies | +36% avg profit improvement over 9 SOTA baselines across 6 datasets (stocks + crypto); 92.27% return on one set | Single-asset focus per run; multimodal (chart images) adds cost/complexity; results on US equities + crypto | Code partial / research repo |
| **FinMem** (Stevens) | arXiv 2311.13743, AAAI-SS 2024 | Layered human-like memory + character/persona | **3 modules**: Profiling (persona/risk-character) + **layered Memory** (shallow/intermediate/deep with decay) + Decision | **Layered memory w/ adjustable cognitive span**; recency/relevance/importance-style retrieval; persona conditioning | Leading trading performance vs algorithmic agents on real-world stock data; persona + span tuning materially improves returns | Single-agent (not multi-agent debate); requires per-stock tuning; small evaluation universe | **MIT**, `pipiku915/FinMem-LLM-StockTrading` |
| **FinRobot** (AI4Finance) | arXiv 2411.08804 / 2405.14767, 2024 | Open-source AI-agent **platform** for equity research/valuation | Analyst-style agents for company analysis, valuation metrics, risk assessment; multi-LLM layer | Chain-of-thought; document-grounded analysis | Produces analyst-grade company reports w/ numeric grounding | Research/report-generation oriented, not a live trading decision loop | open-source, `AI4Finance-Foundation/FinRobot` |
| **FinGPT** (AI4Finance) | 2023–2024 | First open-source finance LLM family (incl. RLHF) | N/A (model family + data pipelines, not a multi-agent system) | N/A | Open finance LLMs/datasets; sentiment + forecasting fine-tunes | **Uses fine-tuning/RLHF** — out of scope for QuantMind's no-fine-tune invariant | open-source, `AI4Finance-Foundation/FinGPT` |
| **Toward Expert Investment Teams** (fine-grained tasks) | arXiv 2602.23330, 2026 | **Fine-grained task decomposition** (encode analyst SOPs into prompts) beats coarse role prompts | 3 levels: Analysts (Technical/Quant/Qualitative/News) → Aggregators (Sector/Macro) → Portfolio Manager | Prompt-encoded SOPs | Fine-grained settings → significantly higher Sharpe (p<0.0001 most configs); Technical agent is primary driver | Japan market, GPT-4o only; unclear if gains are from decomposition vs linguistic priors; 2yr window capped by knowledge cutoff | research repo |
| **Multi-Agent System for Chinese Public REITs** | arXiv 2602.00082, 2026 | Multi-agent + **small fine-tuned LLM** specialized for Chinese REITs | Price-Momentum Agent + Decision Agent + analytical components | Historical-pattern memory | Empirical outperformance on Chinese REITs | Fine-tunes small LLM (out of scope); REIT-specific; weaker real-time adaptation | research repo |

### 2.2 What architectures/decompositions work

1. **Firm-style role decomposition with adversarial debate** (TradingAgents) is the dominant, reproducible pattern: parallel analysts → bull/bear debate → trader → risk → fund manager. The debate step is repeatedly shown to lift performance because it surfaces counter-evidence before commitment. QuantMind's P0-10 already mandates this exact shape (4 mandatory agents + fund_manager as sole BUY/SELL/HOLD proposer + ≥1 debate round).
2. **Fine-grained, SOP-encoded prompts beat generic role prompts** (2602.23330, p<0.0001). Actionable: each QuantMind agent's prompt should encode an explicit analytical procedure (the steps a human analyst follows), not a one-line persona — and this is precisely what DSPy/GEPA later optimizes.
3. **Layered, decaying memory + persona** (FinMem) is the proven mechanism for both *distinct personality* and *learning from the past*, without any weight updates.
4. **Dual-level reflection** (FinAgent: fast tactical + slow strategic) maps cleanly onto QuantMind's intraday-fast vs daily-slow cadence (P0-9).

### 2.3 Critical caveat (governance-relevant)

Every published trading-agent result above is on a **short, possibly LLM-contaminated backtest window**, with **anomalous Sharpe** the authors themselves disown, and **none deployed live**. This is the strongest possible evidence that QuantMind's design choices — simulation-only, 45-trading-day rolling acceptance, human-gated Feishu execution, no live brokerage — are correct, and that any self-evolution MUST be shadow-validated on a long rolling window before promotion. Do not trust a challenger prompt because it backtests well on 3 months.

---

## 3. Distinct Personality + Self-Evolution (no online fine-tuning)

### 3.1 Technique catalog

| Technique | Source | What it gives | Persona vs Evolution | License |
|---|---|---|---|---|
| **Generative Agents — memory stream + reflection** | Park et al. 2304.03442 (Stanford) | Append-only NL memory; retrieval scored by **recency × relevance × importance**; periodic **reflection** synthesizes high-level beliefs that steer future behavior | Both: persona = profile + accumulated reflections; evolution = reflection rewrites beliefs | research, MIT-style |
| **Reflexion — verbal reinforcement** | Shinn et al., NeurIPS 2023 (2303.11366) | Agent writes an NL self-critique after a failure, stores it in episodic memory, retries conditioned on it (no gradients) | Evolution: turns each sim outcome into a stored lesson | MIT, `noahshinn/reflexion` |
| **FinMem — layered memory + character** | 2311.13743 (AAAI-SS 2024) | Shallow/intermediate/deep memory layers with **decay** + adjustable cognitive span + persona/risk-character profiling | Both: character = stable persona; layered decay = learned recency | MIT, `pipiku915/FinMem-LLM-StockTrading` |
| **Voyager — skill library** | Wang et al. 2305.16291 (NeurIPS 2023) | Ever-growing library of **executable code skills** + self-verification + auto-curriculum; reusable, composable | Evolution: accumulates reusable procedures (for QuantMind: reusable *analysis recipes / signal templates*, NOT trade execution) | MIT, `MineDojo/Voyager` |
| **DSPy + GEPA — reflective prompt evolution** | DSPy (Stanford, MIT); GEPA 2507.19457, **ICLR 2026 Oral** | Optimizes the *prompt/program text* by reflecting on trajectories on a Pareto frontier; **+13% over MIPROv2 with 35× fewer rollouts**, works with ~10 examples | Evolution: offline, batch prompt improvement | DSPy MIT; GEPA MIT-family, `gepa-ai/gepa` |
| **In-context exemplars from a decision ledger** | FinMem-style retrieval | Inject ≤3 past *successful* decisions (decayed, relevance-ranked) as few-shot exemplars | Evolution: behavior adapts as the ledger grows; zero weight change | n/a (pattern) |

### 3.2 Stable distinct personas WITHOUT fine-tuning

Persona = **(profile card) + (persona-scoped memory) + (versioned system prompt)**, never weights:

- **Profile card** (FinMem character design): a frozen YAML per agent encoding role, risk-character (aggressive/neutral/conservative for the risk-debate trio), analytical SOP, and tone. Lives in `config/prompts/{agent}/{version}.yaml` (already the P2-2 file-registry shape).
- **Persona-scoped memory namespace**: each agent reads only its own memory stream + the shared evidence, so personas stay distinct rather than collapsing into one voice.
- **Versioned prompt**: persona drift is impossible at runtime because the prompt is a pinned, git-versioned file (P0-7/P0-10 hot-reload disabled). Persona *change* is a deliberate, human-approved version bump.
- **Two specialized trader agents** (the user's ≥2-trader requirement) are realized as two persona cards with different mandates — e.g. a **momentum/entry-timing trader** and a **mean-reversion/sizing trader** — both proposing into the same `trader` graph node; the fund_manager + pure-Python RiskEngine reconcile them. They differ by profile card + exemplar pool, not by model.

### 3.3 Feedback loops (sim outcome → reflection → KB update → behavior change)

Four concrete, fully-offline loops. Each ends at a *file/exemplar* the human gates — never at a runtime mutation.

**Loop 1 — Per-trade verbal reflection (Reflexion + Generative-Agents reflection)**
```
sim fill + realized PnL (decision_ledger)
  → Reflexion node writes NL critique into agent_debate_records.reasoning_text / a reflection field (LLM-writable)
  → reflection stored in agent's memory stream (data/, file-based)
  → next decision retrieves top reflections (recency×relevance×importance)
  → behavior shifts via better in-context grounding (no weights touched)
```

**Loop 2 — Exemplar refresh (FinMem decaying exemplars)**
```
nightly (22:00 cron) scan decision_ledger last 90d
  → select ≤3 exemplars per prompt: recency weighting + relevance + diversity sampling
  → ONLY from cases that passed the RiskEngine 14-check (never failed cases)
  → refreshed exemplar set injected as few-shot next session
```

**Loop 3 — Prompt evolution (DSPy/GEPA, offline batch)**
```
GEPA compile (≤¥5, batch offline) reflects on trajectories
  → emits candidate config/prompts/{agent}/{candidate}.yaml (staging alias) + git diff
  → NEVER overwrites production version
  → must pass shadow-validate (§4) → Feishu → human amendment → restart
```

**Loop 4 — Knowledge-base growth (RAG provenance-gated)**
```
frontier crawler (22:00) pulls from WHITELIST only
  (arxiv q-fin/cs.LG/cs.AI, semanticscholar, openreview, whitelisted GitHub releases, akshare changelog)
  → DeepSeek summarizes (~¥0.05–0.10/day)
  → writes data/rag/{source}/{date}/{doc_id}.md + provenance.jsonl (URL/commit/scanned_at/model)
  → non-whitelist source rejected + audit event (anti prompt-injection)
  → shadow-validate uses new docs as retrieval context → human gate to promote
```

**Voyager skill library — adopt cautiously and re-scoped.** A Voyager-style library of *executable code* is powerful but dangerous in trading: it must be re-scoped to **reusable read-only analysis recipes / signal-feature templates**, code-reviewed and committed by a human, NEVER LLM-authored execution code. The deterministic RiskEngine and MockBroker stay hand-written. Treat skills as another git-versioned, human-gated artifact, identical to prompts.

### 3.4 Mapping to QuantMind's already-locked P2-2 design

QuantMind's P2-2 decision already locks **GEPA + RAG-whitelist + FinMem exemplars** as the three enabled paths and bans 7 paths (fine-tune/online/RLHF/DPO/continual-SFT/auto-config-mutation/new-provider/LLM-decision-authority). This research **confirms** that choice as SOTA-aligned and adds two refinements: (a) explicitly add **Reflexion-style per-trade verbal reflection** as the generator that *feeds* the exemplar/memory loop (it is the missing "how does a lesson get written" step), and (b) keep any **Voyager-style skill library read-only and human-committed**.

---

## 4. Safety / Governance for Self-Evolving Agents

### 4.1 The promote pipeline (proposal → shadow → human gate → restart)

The 2025–2026 governance consensus ("shadow mode → progressive rollout → one-click rollback → human-in-the-loop backed by guardian agents") matches QuantMind's already-locked flow:

```
1. LLM/offline process emits CANDIDATE (prompt vN+1 / exemplar set / RAG doc / risk-param proposal)
      → writes to STAGING file or *_proposals.proposal_text (LLM-writable fields only)
2. evolution_shadow_run cron (22:00 mon–fri) runs the candidate against the
      P0-6 45-trading-day rolling acceptance window in SHADOW (no live effect)
3. Gate = 5 stability + 3 strategy hard thresholds; challenger must BEAT production baseline
4. On pass: Feishu push to human ("there is a pending evolution") + audit event
5. amendment_drafter writes docs/decisions/pending/{artifact}.md
6. HUMAN reviews, edits the YAML / moves the version alias, writes the formal amendment
7. git commit + RESTART loads the new pinned version
```
Steps 1–5 are fully automated (actor = SYSTEM/SCHEDULER). Steps 6–8 are exclusively human. No step is skippable.

### 4.2 Rollback

- **Prompts / exemplar configs / skills**: every version is a git-committed file under `config/prompts/` (+ `prompts.lock.json`). Rollback = `git revert` the version bump + restart. Time-to-rollback is one commit + one process restart — the "one click away" governance principle.
- **RAG knowledge base**: `data/rag/` + `provenance.jsonl` are append-only and git-tracked; a bad doc is removed by a tracked deletion + re-index. Provenance (URL/commit/scanned_at/model) makes every retrieved fact auditable.
- **Runtime state (RiskConfig, BudgetState, MockBroker)**: never mutated by evolution — these are frozen Pydantic and hot-reload-disabled, so there is nothing to roll back at runtime; a config change is itself a git diff + amendment + restart.
- **Account-level**: the existing P0-1 lifecycle (archive + MockBroker reset + reconciliation) is the coarse rollback if an evolved persona ever reaches production and misbehaves.

### 4.3 Preventing the LLM from bypassing deterministic risk controls

This is the core invariant; enforce it with **defense-in-depth**, not trust:

1. **Field-permission matrix (P0-10).** LLMs may write exactly 4 field classes (`InstructionPlan.reasoning`, `evidence_collection.content`, `agent_debate_records.{reasoning_text,conclusion}`, `risk_parameter_proposals.proposal_text`). Everything else — decision fields, `RiskCheckSummary`, RiskConfig — is unreachable. Enforced 3 ways: Pydantic `frozen + strict + extra="forbid"`, a lint rule, and code review.
2. **Graph-level gating (LangGraph).** The RiskEngine 14-check and InstructionPlanBuilder 5-gate are **pure-Python nodes with no LLM edge into their writes**. The LLM's text output is an *input* to these nodes; the nodes' deterministic output is the only writer of decision/risk fields. The LLM physically cannot author the edge that sets a decision.
3. **Module import isolation.** `backend/risk/` and the evolution modules (`evolution_dispatcher`, `frontier_crawler`, `rag_ingester`, `dspy_gepa_runner`, `shadow_chain`, …) are forbidden from importing `backend.{llm,agents,mirofish,data,api,broker,risk}` — preventing an LLM/agent path from calling an evolution function to mutate state, and preventing evolution code from reaching the risk engine. (Already a P0-10/P1-7/P2-2 redline.)
4. **Risk engine is pure & IO-free.** No network, no LLM, no mutation — same inputs always yield the same RiskCheckSummary, independently re-runnable in audit.
5. **Evolution cannot mutate config.** Auto-mutation of any `*.yaml` is a banned path; the only way a threshold changes is human amendment + restart. A self-evolving agent can *propose* a new RiskConfig value (into `proposal_text`) but the value only takes effect after a human edits the frozen YAML.
6. **Audit everything.** 7 evolution-lifecycle audit event types (prompt pinned/rolled-back, RAG ingested/rejected, shadow run completed, amendment drafted, Feishu notified), actor = SYSTEM/SCHEDULER, LLM forbidden from writing audit.

### 4.4 Residual risks to watch

- **Reward hacking on the shadow gate**: a GEPA-evolved prompt could overfit the 45-day window. Mitigation: require challenger to beat baseline on *all* 5+3 thresholds AND be "strictly better on 4 / not-worse on 4 within 0.5pct" (already in P2-2), and keep the window long and rolling.
- **RAG prompt-injection**: strictly enforce the source whitelist + fail-fast on non-whitelist + provenance logging; never ingest arbitrary blogs/social.
- **Persona collapse / sycophancy in debate**: keep bull/bear/risk personas in separate memory namespaces and score debate quality; a debate that doesn't surface counter-evidence should not advance.
- **Exemplar feedback poisoning**: only draw exemplars from RiskEngine-passed, positive-outcome cases; never from rejected or losing trades (else the loop learns the wrong lesson).

---

## 5. Recommended Architecture

**Runtime substrate: LangGraph (MIT) on 127.0.0.1, model-agnostic over DeepSeek/Qwen/MiniMax via OpenAI-compatible/Ollama endpoints.**

**Agent graph (port TradingAgents' shape, harden the risk layer):**

```
                ┌─────────────── Analyst nodes (parallel) ───────────────┐
                │ Fundamental · Technical · (News/Sentiment) · MiroFish*  │   * MiroFish = bonus,
                └───────────────────────┬─────────────────────────────────┘     evidence_collection only
                                        │  structured reports → evidence_collection
                        ┌───────────────▼───────────────┐
                        │   Bull ⇄ Bear debate (cycle)   │  debate_round_count ≥ 1 (P0-10)
                        └───────────────┬───────────────┘
                        ┌───────────────▼───────────────────────────┐
                        │  Trader node: 2 personas                   │  ≥2 specialized traders
                        │   • momentum / entry-timing trader         │  (distinct profile cards
                        │   • mean-reversion / sizing trader         │   + exemplar pools)
                        └───────────────┬───────────────────────────┘
                        ┌───────────────▼───────────────┐
                        │  fund_manager node             │  SOLE BUY/SELL/HOLD proposer (P0-10)
                        │  → writes reasoning ONLY        │
                        └───────────────┬───────────────┘
        ╔═══════════════════════════════▼═══════════════════════════════╗
        ║  PURE-PYTHON nodes — the ONLY writers of decision/risk fields   ║
        ║  InstructionPlanBuilder 5-gate  →  RiskEngine 14-check          ║
        ║  (no LLM edge in; frozen RiskConfig; deterministic)            ║
        ╚═══════════════════════════════╤═══════════════════════════════╝
                                        │  InstructionPlan (DRAFT→VALIDATED→DISPATCHED)
                                        ▼
                            Feishu (human manual execution)
```

**Memory & persona:**
- LangGraph SQLite/Postgres checkpointer for graph state (resume/audit).
- Per-agent FinMem-style layered memory stream (file-based, `data/`), retrieval = recency × relevance × importance.
- Persona = frozen `config/prompts/{agent}/{version}.yaml` profile card; ≥2 trader personas as distinct cards.

**Self-evolution (4 offline loops, all human-gated):**
1. Reflexion verbal reflection per sim trade → feeds memory/exemplars.
2. FinMem decaying exemplars (≤3/prompt, RiskEngine-passed cases, nightly refresh).
3. DSPy/GEPA offline prompt evolution (≤¥5/run, staging YAML + git diff).
4. RAG provenance-gated KB from source whitelist (frontier crawler 22:00).
(Optionally a Voyager-style **read-only, human-committed** analysis-recipe library.)

**Governance:**
- Every candidate → `evolution_shadow_run` cron → P0-6 45-day rolling acceptance in shadow → 5+3 hard thresholds + challenger-beats-baseline → Feishu notify → human amendment → git commit + restart.
- Rollback = `git revert` + restart.
- LLM bypass prevented by: P0-10 field-permission matrix (Pydantic+lint+review) + LangGraph graph-level gating (pure-Python risk nodes with no LLM write edge) + module import isolation + IO-free pure risk engine + no-config-mutation rule + full audit.

**Net:** adopt LangGraph + the TradingAgents role blueprint (Apache-2.0, already DeepSeek/Qwen/MiniMax/Ollama-ready), layer FinMem memory + Reflexion reflection + GEPA prompt evolution + RAG-whitelist KB for self-evolution, and keep the entire decision/risk write path pure-Python and human-gated. This matches and reinforces QuantMind's already-locked P0-7/P0-10/P2-2 redlines while bringing in the strongest 2024–2026 SOTA components — all MIT/Apache-2.0 and fully local.

---

## 6. Concrete adoption shortlist (name · license · maturity · one-line rationale)

| Component | Repo / paper | License | Maturity | Why for QuantMind |
|---|---|---|---|---|
| Orchestration | `langchain-ai/langgraph` | MIT | v1.0, very high | Graph + checkpointed memory + cyclic debate + pure-Python tool-gating, local, model-agnostic |
| Role blueprint | `TauricResearch/TradingAgents` (arXiv 2412.20138) | Apache-2.0 | v0.2.5, ~79k★ | Firm-style debate roles on LangGraph; **native DeepSeek/Qwen/MiniMax/Ollama-local** |
| Layered memory + persona | `pipiku915/FinMem-LLM-StockTrading` (arXiv 2311.13743) | MIT | Stable | Decaying layered memory + character design = distinct personas + learning, no fine-tune |
| Verbal reflection | `noahshinn/reflexion` (NeurIPS 2023, 2303.11366) | MIT | Stable | Turns each sim outcome into a stored NL lesson (gradient-free) |
| Prompt evolution | `stanfordnlp/dspy` + `gepa-ai/gepa` (GEPA arXiv 2507.19457, ICLR 2026 Oral) | MIT | Active, SOTA | +13% over MIPROv2 / 35× fewer rollouts; offline batch fits ≤¥5/run budget |
| Skill library (cautious) | `MineDojo/Voyager` (NeurIPS 2023, 2305.16291) | MIT | Stable | Pattern for reusable, human-committed **read-only** analysis recipes only |
| Memory-stream + reflection design | Generative Agents (arXiv 2304.03442) | research | Foundational | recency×relevance×importance retrieval + periodic reflection design |
| Fine-grained role design | "Expert Investment Teams" (arXiv 2602.23330) | research | 2026 | SOP-encoded prompts beat generic role prompts (p<0.0001) — informs prompt structure |

## Sources

- LangGraph / framework comparisons: [DataCamp](https://www.datacamp.com/tutorial/crewai-vs-langgraph-vs-autogen), [Latenode](https://latenode.com/blog/platform-comparisons-alternatives/automation-platform-comparisons/langgraph-vs-autogen-vs-crewai-complete-ai-agent-framework-comparison-architecture-analysis-2025), [Galileo](https://galileo.ai/blog/autogen-vs-crewai-vs-langgraph-vs-openai-agents-framework), [QubitTool 2026](https://qubittool.com/blog/ai-agent-framework-comparison-2026), [Particula](https://particula.tech/blog/langgraph-vs-crewai-vs-openai-agents-sdk-2026), [Langfuse](https://langfuse.com/blog/2025-03-19-ai-agent-comparison), [Speakeasy](https://www.speakeasy.com/blog/ai-agent-framework-comparison)
- Checkpointer licenses: [langgraph-checkpoint-sqlite (PyPI)](https://pypi.org/project/langgraph-checkpoint-sqlite/), [langgraph-checkpoint-postgres (PyPI)](https://pypi.org/project/langgraph-checkpoint-postgres/), [LangGraph persistence docs](https://docs.langchain.com/oss/python/langgraph/persistence)
- CrewAI / AG2 licenses: [crewAI LICENSE](https://github.com/crewAIInc/crewAI/blob/main/LICENSE), [ag2ai/ag2](https://github.com/ag2ai/ag2), [microsoft/autogen](https://github.com/microsoft/autogen)
- TradingAgents: [arXiv 2412.20138](https://arxiv.org/abs/2412.20138), [repo](https://github.com/TauricResearch/TradingAgents), [explainer w/ backtest numbers](https://beginnersinai.org/tradingagents-explained/)
- FinAgent: [arXiv 2402.18485](https://arxiv.org/abs/2402.18485)
- FinMem: [arXiv 2311.13743](https://arxiv.org/abs/2311.13743), [repo](https://github.com/pipiku915/FinMem-LLM-StockTrading), [OpenReview](https://openreview.net/forum?id=sstfVOwbiG)
- FinRobot / FinGPT: [FinRobot repo](https://github.com/AI4Finance-Foundation/FinRobot), [arXiv 2411.08804](https://arxiv.org/html/2411.08804v1)
- 2026 trading papers: [Expert Investment Teams (2602.23330)](https://arxiv.org/html/2602.23330v1), [Chinese REITs (2602.00082)](https://arxiv.org/pdf/2602.00082)
- DSPy / GEPA: [GEPA arXiv 2507.19457](https://arxiv.org/abs/2507.19457), [ICLR 2026 Oral](https://iclr.cc/virtual/2026/oral/10009494), [gepa-ai/gepa](https://github.com/gepa-ai/gepa), [dspy.GEPA docs](https://dspy.ai/api/optimizers/GEPA/overview/), [Morph optimizer comparison](https://www.morphllm.com/prompt-optimization)
- Self-improving agents: [Generative Agents (2304.03442)](https://ar5iv.labs.arxiv.org/html/2304.03442), [Reflexion (2303.11366)](https://arxiv.org/abs/2303.11366) / [NeurIPS poster](https://neurips.cc/virtual/2023/poster/70114), [Voyager (2305.16291)](https://arxiv.org/abs/2305.16291)
- Governance: [HCLTech guardrails](https://www.hcltech.com/trends-and-insights/guardrails-autonomous-ai-governance-agentic-world), [DevOps.com shadow/rollback](https://devops.com/before-you-go-agentic-top-guardrails-to-safely-deploy-ai-agents-in-observability/), [SafeEvalAgent (2509.26100)](https://arxiv.org/pdf/2509.26100)
