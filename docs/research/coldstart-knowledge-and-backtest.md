# QuantMind 冷启动知识库 + 回测基础设施研究

> Research brief for QuantMind re-scope: full-market (5000+ A-share + ETF) screening, an
> auto-evolving **knowledge-graph** of strategies / finance-knowledge / trader-heuristics, and
> rigorous backtesting. Self-hostable, Python, 127.0.0.1-only, signals to Feishu for MANUAL
> execution. Compiled 2026-05-24. All repo metadata verified via GitHub API on that date.
>
> **Compliance note (red-lines that constrain adoption):** anything pulled in here is for the
> *research/knowledge-base + backtest* layer. None of it may write decision fields, the
> RiskEngine, MockBroker, or Feishu text (P0-10 §2.2/§2.3). Self-evolution paths remain the
> locked conservative 3 (DSPy GEPA / provenance-gated RAG / FinMem exemplars) per P2-2; this
> brief surfaces SOTA but does **not** authorize new evolution code outside that envelope.

---

## 0. TL;DR decision table

| Layer | Primary pick | Why (one line) |
|---|---|---|
| Strategy/factor cold-start corpus | **qlib Alpha158 + Alpha360** + **gtja-191** + **WorldQuant-101** | Battle-tested A-share factor sets, runnable day one |
| Auto-evolving R&D loop (closest to QuantMind vision) | **microsoft/RD-Agent (RD-Agent-Q)** | Built-on-qlib closed hypothesis→code→backtest→feedback loop, SOTA-set "knowledge forest" |
| Backtest engine (research/screening) | **qlib** (primary) + **vectorbt** (fast factor IC sweeps) | qlib is A-share-native + ML-first; vectorbt for 5000-stock vectorized sweeps |
| Backtest engine (execution realism, optional) | **rqalpha** | China-native event-driven, A-share trading-calendar/limit-up rules baked in |
| Full-market daily data | **Tushare Pro 5000-pt tier** (`daily`/`daily_basic`/`fina_indicator_vip`) primary; **akshare**/**baostock**/**adata** backup | Single-call full-market pull; akshare for breadth/free fallback |
| Knowledge-graph store | **Kùzu** (embedded) or **Neo4j Community** (local) | Embedded = zero-daemon 127.0.0.1; Neo4j = Cypher + native vector index |
| Multi-agent reference (A-share) | **TauricResearch/TradingAgents** (arch) + **hsliuping/TradingAgents-CN** (A-share adapter) | Mirrors QuantMind's multi-agent debate; CN fork already wires Tushare + DeepSeek |

---

## 1. COLD-START STRATEGY & KNOWLEDGE SOURCES

### 1.1 A-share-capable strategy / factor libraries

| Repo | Stars | License | Last push | A-share fit | Rationale |
|---|---|---|---|---|---|
| **microsoft/qlib** | 43.4k | MIT | 2026-04-22 | ★★★★★ | A-share-native; ships **Alpha158** (158 hand-engineered factors) + **Alpha360** (raw 360-dim price/vol), CSI300/CSI500 benchmarks, 20+ ML models. The single best foundation. |
| **microsoft/RD-Agent** | 13.2k | MIT | 2026-05-13 | ★★★★★ | RD-Agent-Q: automated factor+model co-optimization **on qlib**; 2× ARR with 70% fewer factors on CSI300. Directly maps to "auto-evolving KB". |
| **AI4Finance-Foundation/FinRL** | 15.2k | MIT | 2026-05-18 | ★★★★ | RL trading lib; has A-share env adapters. Useful as a *strategy class*, less as cold-start factor source. |
| **ricequant/rqalpha** | 6.4k | "Apache-ish"¹ | 2026-05-20 | ★★★★★ | China-native event-driven backtest+strategy framework; Mod plugin system. Strategy examples are A-share-ready. |
| **UFund-Me/Qbot** | 17.4k | MIT | 2026-03-11 | ★★★★ | AI quant "all-in-one" wrapping qlib + strategies; good reference for integration patterns. |
| **zvtvz/zvt** | 4.1k | MIT | 2026-04-13 | ★★★★ | A-share-first data+factor+trading framework; pandas-centric factor DSL. |

¹ rqalpha & TradingAgents-CN report `NOASSERTION` via API (custom/Apache-derived LICENSE files) — **must read LICENSE before vendoring**.

### 1.2 Formulaic alpha sets (encode directly into the KB as seed strategies)

| Set | Repo | Stars | License | Last push | Notes |
|---|---|---|---|---|---|
| **GTJA-191** (国泰君安 191 alphas) | `wpwpwpwpwpwpwpwpwp/Alpha-101-GTJA-191` | 134 | none⚠ | 2019-10-20 | 191 alphas designed for A-share; includes the 101 PDF + GTJA-191 paper. **Stale** — port the *formulas*, not the code. |
| **WorldQuant 101** | `yli188/WorldQuant_alpha101_code` | 779 | none⚠ | 2019-03-07 | Canonical Quantigic 101 formulaic alphas in Python. |
| **WorldQuant 101 (strategized)** | `Harvey-Sun/World_Quant_Alphas` | 348 | none⚠ | 2017-03-07 | Adds backtest/strategy wrapper. Very stale. |

⚠ **License risk:** these repos have **no LICENSE file** → default "all rights reserved." The *alpha formulas themselves* come from public papers (Kakushadze 2016 "101 Formulaic Alphas"; GTJA 2017 research report) and are mathematical expressions, not copyrightable. **Re-implement the formulas from the source papers** rather than copying these repos' code. qlib's `Expression` engine + `Alpha158` already implement many; extend with GTJA-191/WQ-101 expressions in qlib's factor DSL.

**Cold-start action:** seed the KB with three tiers — (a) qlib Alpha158/Alpha360 (runnable, MIT), (b) GTJA-191 re-implemented in qlib expression syntax, (c) WorldQuant-101 re-implemented. This gives ~600+ seed factors with provenance metadata day one.

### 1.3 Codified "经验/skill" of top managers & traders (encodable rule sets)

These are *knowledge corpora* to ingest into the graph, not code:

- **Factor-anomaly literature (authoritative, public):**
  - Kakushadze (2016), *101 Formulaic Alphas* — arXiv:1601.00991 (the canonical WQ-101 source).
  - GTJA (国泰君安金工) *191 Alphas* research report — the GTJA-191 source.
  - Fama–French 3/5-factor, Carhart momentum, Q-factor (Hou-Xue-Zhang) — standard factor definitions.
  - **AlphaAgent paper set** (arXiv:2502.16789) and **Chain-of-Alpha** (arXiv:2508.06312) — recent decay-resistant alpha design heuristics.
- **Encodable trader heuristics / rule sets:**
  - Classic rule systems that map cleanly to the graph: Dual-momentum (Antonacci), Turtle trading rules, CAN SLIM (O'Neil), Minervini SEPA trend templates, Wyckoff phases. These are *named rule sets* with crisp entry/exit conditions — ideal KB nodes with `IF/THEN` heuristic edges.
  - 缠论 (Chan theory) / 龙虎榜 (dragon-tiger list) / 板块轮动 (sector rotation) — A-share-specific trader heuristics widely codified in zh quant communities; encode as heuristic nodes, validate via backtest before promotion.
- **LLM-agent memory corpora (for the FinMem exemplar path, already in P2-2):**
  - **pipiku915/FinMem-LLM-StockTrading** — reference impl of layered-memory trading agent; the "character design" + memory-decay schema is a template for QuantMind's FinMem exemplars (≤3/prompt per P2-2).

### 1.4 Authoritative finance/quant knowledge corpora to ingest

- **Books → structured notes** (encode as knowledge nodes, cite source): de Prado *Advances in Financial Machine Learning* (purged CV, meta-labeling, the canonical anti-overfit playbook); Grinold-Kahn *Active Portfolio Management* (IC/IR, fundamental law); Narang *Inside the Black Box*.
- **arXiv q-fin / cs.LG** continuous feed — already QuantMind's whitelisted RAG source (P2-2). Prioritize alpha-mining + alpha-decay papers (§4).
- **akshare changelog** + **Tushare docs** — whitelisted; for data-field/interface evolution.
- **CSRC / 交易所 rule docs** — limit-up/down, ST rules, board eligibility; encode as hard constraints (these already live in RiskEngine but the *rationale* belongs in the KB).

---

## 2. BACKTEST INFRASTRUCTURE COMPARISON

| Engine | Stars | License | Last push | Paradigm | A-share fit | Speed | Slippage/cost | Look-ahead guards | LLM-strategy integration |
|---|---|---|---|---|---|---|---|---|---|
| **qlib** | 43.4k | MIT | 2026-04-22 | Event+expression, ML-first | ★★★★★ native | Fast (cached expr engine) | Configurable (`exchange` cost model, limit rules) | Point-in-time dataset + train/valid/test segments; rolling | ★★★★★ — RD-Agent already injects LLM-generated factors |
| **vectorbt** (OSS) | 7.7k | "Apache-ish"¹ | 2026-04-25 | Vectorized (Numba) | ★★★★ (BYO data) | ★★★★★ fastest (1M sims/20s) | Fees/slippage params, fixed/% | **Purged + combinatorial CV, walk-forward** (de Prado) — esp. in PRO | ★★★★ — feed agent factor → portfolio sim in ms |
| **rqalpha** | 6.4k | NOASSERTION¹ | 2026-05-20 | Event-driven | ★★★★★ native (A-share calendar, limit-up, T+1) | Moderate | Built-in A-share commission/stamp/transfer + slippage Mods | Event clock prevents look-ahead by construction | ★★★ — strategy = Python file; wrap agent output |
| **zipline-reloaded** | 1.8k | Apache-2.0 | 2026-01-06 | Event-driven | ★★★ (US-centric; A-share needs custom bundle/calendar) | Slow (per-bar Python) | Commission/slippage models | **Pipeline API** = strong PIT factor semantics, no look-ahead | ★★★ |
| **nautilus_trader** | 23.0k | LGPL-3.0 | 2026-05-24 | Event-driven (Rust core) | ★★ (no A-share adapter OOTB) | ★★★★ (Rust) | High-fidelity order/latency/L2 | Tick-level replay, no look-ahead | ★★ — execution-realism focus, overkill for daily screening |
| **backtrader** | 21.7k | GPL-3.0⚠ | **2024-08-19 (stale)** | Event-driven | ★★★ (BYO data) | Slow | `slip_perc`/`slip_fixed`, commission schemes | Manual; user must lag signals | ★★★ — easy API but **GPL-3.0 is viral** |
| **bt** | ~2k | MIT | active | Tree/weight-based | ★★ | Fast | Basic | Weight-rebalance model, limited intrabar realism | ★★ |

¹ vectorbt OSS license is custom (Apache-2.0-with-commons-clause-style restriction in some versions) — **read LICENSE; vectorbtpro is paid/closed** and a hosted-adjacent dependency, so prefer OSS vectorbt to stay self-host-pure. rqalpha = NOASSERTION (verify).

⚠ **backtrader is GPL-3.0** (viral copyleft) and **unmaintained since 2024-08** → avoid as a core dependency for a product. Use only for throwaway experiments.

### 2.1 Recommendation

**Primary: qlib** as the backtest + factor research substrate, because:
1. **A-share-native** — built and benchmarked on CSI300/CSI500/A-share; ships trading-calendar, limit-up/down and cost models.
2. **Point-in-time correctness** — its dataset/segment design (train/valid/test, rolling retrain) is the strongest built-in defense against look-ahead bias for ML-discovered factors.
3. **LLM-agent integration is already solved** by **RD-Agent-Q** — agent generates a factor → qlib backtests it → IC/ARR feedback → iterate. This *is* the auto-evolving-KB loop QuantMind wants, and it's MIT-licensed.
4. MIT license, 43k stars, actively maintained (push 2026-04).

**Secondary: vectorbt (OSS)** for high-throughput factor *screening* across 5000+ stocks — vectorized Numba sims run an entire universe IC/factor sweep in seconds, with **purged + walk-forward CV** for honest out-of-sample estimates. Use it as the fast "first filter" before a candidate is promoted to a full qlib backtest.

**Tertiary (execution realism, optional): rqalpha** when a promoted strategy needs A-share-accurate fill/cost simulation (T+1, stamp duty, transfer fee, limit-up no-fill) close to QuantMind's own MockBroker semantics — useful as an *independent cross-check* of MockBroker results, never as the order path.

**Avoid as core deps:** backtrader (GPL-3.0 + stale), nautilus (no A-share adapter, execution-grade overkill), zipline-reloaded (US-centric; A-share calendar/bundle is heavy custom work).

> **Anti-bias checklist for every backtested strategy** (de Prado): point-in-time data only; signal lagged ≥1 bar; purged+embargoed CV (no train/test leakage across the prediction horizon); walk-forward out-of-sample; deflated Sharpe / multiple-testing correction before promotion. qlib + vectorbt both support these; enforce them in the promotion gate (§4).

---

## 3. A-SHARE DATA REALITIES (full-market daily screening)

### 3.1 Feasibility of scanning 5000+ stocks daily

**Feasible and cheap** — the whole A-share universe (~5,400 listed: 沪/深主板 + 创业板 + 科创板 + 北交 + ETFs) is a few MB/day of daily OHLCV+fundamentals. The right pattern is **full-market single-call by trade-date**, not 5000 per-symbol calls.

### 3.2 Fields a screening pipeline needs

- **Price/volume (daily):** `open/high/low/close/pre_close/vol/amount/pct_chg` + adjusted (复权) factor.
- **Liquidity/valuation (`daily_basic`):** `turnover_rate`, `volume_ratio`, `pe/pe_ttm`, `pb`, `ps_ttm`, `dv_ratio`, `total_mv`, `circ_mv`, `total_share/float_share/free_share`.
- **Fundamentals (`fina_indicator`):** ROE, ROA, gross/net margin, debt ratio, EPS, revenue/profit YoY growth, cashflow.
- **Reference/eligibility:** list date (新股/次新 age), ST/*ST flag, board (主板/创业板/科创板/北交), suspension flag, limit-up/down price, index membership.
- **Derived for screening:** market cap buckets, momentum windows, factor z-scores per universe, the QuantMind watchlist-exclusion four-set (ST / 科创 / 北交 / 可转债).

### 3.3 Provider comparison at full-market scale

| Provider | License/cost | Full-market single-call | Rate limit | A-share fit | Verdict |
|---|---|---|---|---|---|
| **Tushare Pro** (waditu/tushare, BSD-3) | Free core; **points-gated** | ✅ `daily`/`daily_basic`/`fina_indicator_vip` return **all stocks for one `trade_date`** in one call (max 7000 rows/call > 5400 universe) | 120pt→50/min, 200pt→100/min, 500–1500pt→500/min, **10000pt+→higher**; `fina_indicator_vip` needs **5000 pts**, `daily` needs **120 pts** | ★★★★★ | **Primary.** Cleanest full-market pulls + financials. 5000-pt tier (donate/contribute to earn) unlocks the `*_vip` single-call endpoints. |
| **akshare** (akfamily/akshare, MIT, push 2026-05-20) | Free, no key | ✅ `stock_zh_a_spot_em()` = full-market real-time snapshot (Eastmoney) | Soft/IP-based (Eastmoney/Sina scraping); throttle to be safe | ★★★★★ breadth | **Primary backup / breadth.** Free, no points, huge surface (futures/options/funds/macro/news). Source-fragility risk → wrap with retries. |
| **baostock** (pip, MIT-ish, free) | Free, no key | per-symbol loops (no full-market single-call); reliable historical | Generous but slow at 5000 loops | ★★★★ | **Historical/backup.** Very stable adj-price history for backfills; weak for daily fundamentals breadth. |
| **adata** (1nchaos/adata, Apache-2.0, push 2025-12-26) | Free, no key | Multi-source aggregator (Eastmoney/百度/腾讯) | Source-dependent | ★★★★ | **Third fallback** — already a QuantMind-approved free source; good redundancy layer. |

**Recommended data architecture:** Tushare 5000-pt as the **point-in-time master** (write to local store with `trade_date` partition), akshare as the **breadth + real-time-snapshot fallback**, baostock for **historical adj-price backfill**, adata as **third redundancy**. This matches QuantMind's existing free-source posture (akshare/adata/baostock, no data cost ceiling per P1-7) and adds Tushare for clean financials. Single daily ETL: ~5 full-market calls (daily, daily_basic, fina_indicator_vip latest quarter, basic/ST flags, suspend) — well within even the 50/min tier.

---

## 4. KNOWLEDGE-BASE UPDATE LOOP (discover → validate → promote → prune)

### 4.1 Closest existing blueprint: RD-Agent-Q (microsoft/RD-Agent)

RD-Agent-Q is the strongest open reference and is **license-compatible (MIT)** and qlib-native. Its loop:

1. **Specification Unit** — encodes data interfaces/output format so every candidate is backtest-compatible.
2. **Hypothesis (Idea)** — propose factor/model ideas (from market hypotheses, literature, prior results).
3. **Implementation Unit** — translate hypothesis → factor code.
4. **Validation Unit** — **real-market backtest on qlib** (IC, ARR, drawdown).
5. **Synthesis Unit** — maintains a **"knowledge forest"** of all experiments and a **SOTA set** of best factors/models; adaptively tunes hypothesis complexity from recent performance.
6. **Analysis Unit** — multi-dimensional post-round assessment → feedback to next hypothesis.

Result on A-share: IC 0.0532, ARR 14.21%, **2× ARR with 70% fewer factors** than classical libraries on CSI300. This is exactly QuantMind's "agents discover → validate → promote" loop, with the "knowledge forest / SOTA set" being a natural fit for the **knowledge-graph** store.

### 4.2 Alpha-mining agent references (2024–2026 SOTA)

| Framework | Repo / paper | Stars | License | Key idea for QuantMind |
|---|---|---|---|---|
| **AlphaAgent** | `RndmVariableQ/AlphaAgent` (arXiv:2502.16789) | 301 | MIT | 3 agents (Idea/Factor/Eval); **regularization to counteract alpha decay** + dedup vs existing factors → directly informs pruning + non-redundancy gate. |
| **QuantaAlpha** | `QuantaAlpha/QuantaAlpha` | 903 | (none yet⚠) | Evolutionary self-evolving factor trajectories; describe a direction, factors are mined+evolved+validated. Maps to evolution path. |
| **Chain-of-Alpha** | arXiv:2508.06312 | — | paper | Dual-chain: Factor Generation + Factor Optimization chains iterate without human; clean separation of propose vs refine. |
| **CogAlpha** | (paper) | — | paper | 7-level agent hierarchy + multi-agent code validation/repair — mirrors QuantMind's 4-Agent gate + validation. |

### 4.3 Promotion / pruning pattern (backtest-gated, shadow-validated)

This aligns with QuantMind's locked P0-6 / P2-2 mechanics — reuse them, don't invent new ones:

**Lifecycle states (knowledge-graph node status):**
`candidate → shadow → promoted(active) → decaying → retired`

1. **Discover (candidate):** agent or RD-Agent-style loop proposes a factor/strategy node + provenance edge (arXiv/akshare/internal). Stored, not yet trading.
2. **Validate (shadow):** mandatory backtest gate BEFORE any promotion —
   - vectorbt fast screen across full universe (IC, IC-IR, turnover) — cheap first filter.
   - qlib full backtest with **purged+walk-forward CV** + **deflated Sharpe / multiple-testing correction** (de Prado) to kill false discoveries.
   - **45-trading-day shadow window** reusing P0-6 `compute_acceptance_window` + the 5 stability + 3 strategy hard gates (this is already QuantMind's locked challenger-win rule for evolution; bind strategy promotion to the same gate).
3. **Promote (active):** only if challenger strictly-better / not-worse-by-0.5pct on P0-6 gates AND passes non-redundancy (correlation with existing active factors below threshold — AlphaAgent-style dedup). Promotion = file-based registry + git + restart (P2-2), **human Feishu gate** — never auto.
4. **Monitor (decaying):** track rolling IC / live-vs-backtest decay. Alpha decay is real and fast (≈5–10%/yr in liquid markets; "mechanical" factors like momentum crowd fastest, "judgment" factors like value slower). Flag a node `decaying` when rolling IC falls below a floor for N windows.
5. **Retire (pruned):** auto-demote `decaying` nodes that fail the floor over the shadow window; keep them in the graph as `retired` with reason+date (never hard-delete — provenance + avoids re-proposing dead factors). Crowding/decay metrics from the alpha-decay literature (arXiv:2512.11913 "Not All Factors Crowd Equally"; arXiv:2502.04284) inform the floor per factor type.

**Knowledge-graph schema sketch (nodes/edges):**
- Nodes: `Strategy`, `Factor`, `Heuristic`, `Concept`, `Paper/Source`, `BacktestRun`, `Instrument`/`Sector`.
- Edges: `DERIVED_FROM` (provenance), `USES_FACTOR`, `CORRELATED_WITH` (redundancy), `VALIDATED_BY` (→ BacktestRun w/ IC/Sharpe/window), `SUPERSEDES` (challenger>incumbent), `DECAYS_INTO`/`RETIRED_AS`.
- Each `BacktestRun` carries the immutable metrics + window + CV scheme so promotion decisions are auditable (fits QuantMind's frozen-Pydantic + audit posture).

### 4.4 Knowledge-graph storage (self-hostable, 127.0.0.1)

| Option | License | Mode | Vector search | Fit |
|---|---|---|---|---|
| **Kùzu** | MIT | **Embedded** (no daemon) | Yes (built-in) | ★★★★★ Best for QuantMind: embedded "DuckDB of graphs," zero network surface, columnar/fast analytics, attaches DuckDB. Pure local. |
| **Neo4j Community** | GPLv3 (server) | Local daemon (bind 127.0.0.1) | Native vector index + Cypher | ★★★★ Richest ecosystem + `neo4j-graphrag-python` (LLM KG builder, Ollama-compatible). Heavier; GPL server. |
| **LightRAG** (HKUDS) | MIT | Lib over PG/Neo4j | Yes | ★★★★ Lightweight GraphRAG; fewer LLM calls/chunk → cheaper KB ingestion; can use Postgres-only all-in-one. |

**Recommendation:** **Kùzu** as the embedded graph store (no daemon = trivially 127.0.0.1-pure, fits the "no hosted SaaS" rule), optionally with **LightRAG** as the ingestion/GraphRAG layer for turning papers/changelogs into nodes cheaply. Neo4j Community only if Cypher tooling/visualization is worth running a local daemon.

---

## 5. License & maintenance risk summary

- **Safe to vendor (MIT/Apache, active):** qlib, RD-Agent, FinRL, FinGPT/FinRobot, akshare, adata, Tushare (BSD-3), AlphaAgent, Kùzu, LightRAG, zipline-reloaded (Apache).
- **Read LICENSE first (NOASSERTION/custom):** rqalpha, vectorbt OSS, TradingAgents-CN, QuantaAlpha (no license yet → treat as all-rights-reserved until clarified).
- **Avoid as core dep:** backtrader (**GPL-3.0** viral + unmaintained since 2024-08); vectorbt**pro** (paid/closed, hosted-adjacent — breaks self-host purity); nautilus (LGPL ok but no A-share adapter).
- **Formula repos with no license (GTJA-191 / WQ-101):** re-implement formulas from the public *papers* (Kakushadze 2016, GTJA report) in qlib's expression DSL — do not copy the unlicensed repo code.

---

## 6. Top recommendations

1. **Adopt qlib as the research/backtest substrate** (MIT, A-share-native) and **RD-Agent-Q as the auto-evolving-KB engine reference** — it already implements discover→implement→backtest→feedback with a SOTA-set "knowledge forest" that maps onto the knowledge graph.
2. **Cold-start the KB with ~600+ seed factors:** qlib Alpha158 + Alpha360 (vendor directly, MIT) + GTJA-191 + WorldQuant-101 **re-implemented from the source papers** into qlib's expression DSL (avoid the unlicensed repos' code).
3. **Two-tier backtesting:** vectorbt (OSS) for fast full-universe factor screens with purged+walk-forward CV, then qlib full backtest for promotion candidates; keep rqalpha as an independent A-share-realistic cross-check of MockBroker. Reject backtrader (GPL + stale) and vectorbtpro (closed/hosted).
4. **Data layer:** Tushare Pro 5000-pt tier as point-in-time master (full-market single-call `daily`/`daily_basic`/`fina_indicator_vip`), with akshare (breadth/real-time), baostock (history backfill), adata (3rd fallback). Daily full-market ETL is ~5 calls — trivially feasible.
5. **Knowledge graph on Kùzu** (embedded, MIT, no daemon → 127.0.0.1-pure), optional LightRAG for cheap paper/changelog ingestion; never Neo4j Aura or any hosted graph.
6. **Promotion/pruning loop = reuse P0-6 + P2-2 verbatim:** candidate→shadow→active→decaying→retired, gated by a 45-trading-day shadow window + the 5 stability/3 strategy hard gates + de Prado anti-overfit (purged CV, deflated Sharpe), AlphaAgent-style non-redundancy dedup, and human Feishu approval. Retired nodes stay in the graph (provenance, no re-proposal). Tune decay floors per factor type using the 2025–2026 alpha-decay literature.
7. **Multi-agent reference:** study TauricResearch/TradingAgents (Apache-2.0) for the debate architecture and hsliuping/TradingAgents-CN for the A-share+Tushare+DeepSeek wiring — adapt patterns, do not adopt their decision path (QuantMind's 4-Agent gate + RiskEngine stays authoritative).
8. **Hard compliance fence:** every item above is research/KB/backtest only. None may write decision fields, RiskEngine, MockBroker, or Feishu text; self-evolution stays inside P2-2's 3 conservative paths with human gates.

---

### Sources

- qlib — https://github.com/microsoft/qlib ; benchmarks (Alpha158/Alpha360) https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md
- RD-Agent / RD-Agent-Q — https://github.com/microsoft/RD-Agent ; paper https://arxiv.org/html/2505.15155v2 ; docs https://rdagent.readthedocs.io/en/latest/scens/quant_agent_fin.html
- TradingAgents — https://github.com/TauricResearch/TradingAgents ; CN fork https://github.com/hsliuping/TradingAgents-CN
- FinRL — https://github.com/AI4Finance-Foundation/FinRL ; FinGPT https://github.com/AI4Finance-Foundation/FinGPT ; FinRobot https://github.com/AI4Finance-Foundation/FinRobot
- rqalpha — https://github.com/ricequant/rqalpha ; zvt — https://github.com/zvtvz/zvt ; Qbot — https://github.com/UFund-Me/Qbot
- GTJA-191/WQ-101 — https://github.com/wpwpwpwpwpwpwpwpwp/Alpha-101-GTJA-191 ; https://github.com/yli188/WorldQuant_alpha101_code ; https://github.com/Harvey-Sun/World_Quant_Alphas ; Kakushadze "101 Formulaic Alphas" arXiv:1601.00991
- AlphaAgent — https://github.com/RndmVariableQ/AlphaAgent ; arXiv:2502.16789 ; QuantaAlpha https://github.com/QuantaAlpha/QuantaAlpha ; Chain-of-Alpha arXiv:2508.06312
- FinMem — https://github.com/pipiku915/FinMem-LLM-StockTrading ; arXiv:2311.13743
- Backtest engine comparisons — https://autotradelab.com/blog/backtrader-vs-nautilusttrader-vs-vectorbt-vs-zipline-reloaded ; https://python.financial/
- vectorbt CV / walk-forward — https://vectorbt.pro/features/optimization/ ; purged CV https://en.wikipedia.org/wiki/Purged_cross-validation
- backtrader — https://github.com/mementum/backtrader ; zipline-reloaded https://github.com/stefan-jansen/zipline-reloaded ; nautilus https://github.com/nautechsystems/nautilus_trader ; vectorbt https://github.com/polakowo/vectorbt
- Tushare — https://github.com/waditu/tushare ; points/freq table https://tushare.pro/document/1?doc_id=290 ; fina_indicator https://tushare.pro/document/2?doc_id=79
- akshare — https://github.com/akfamily/akshare ; adata — https://github.com/1nchaos/adata
- Knowledge graphs — FinReflectKG arXiv:2508.17906 ; FinDKG arXiv:2407.10909 ; FinKario arXiv:2508.00961 ; Kùzu GraphRAG https://datalabtechtv.com/posts/graphrag-with-kuzudb/ ; LightRAG https://github.com/HKUDS/LightRAG ; neo4j-graphrag-python https://github.com/neo4j/neo4j-graphrag-python
- Alpha decay — arXiv:2512.11913 "Not All Factors Crowd Equally" ; arXiv:2502.04284 ; https://www.exegy.com/alpha-decay/
- awesome-quant — https://github.com/wilsonfreitas/awesome-quant
