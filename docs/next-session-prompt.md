# Next Session Prompt — Phase 5 Kickoff

Copy the content below into a new Claude Code session:

---

## Task: Phase 5 Kickoff — P5-T01 Suggest Mode Infrastructure

### Context

- **Project**: QuantMind quantitative trading system
- **Current branch**: `main`
- **Remote**: https://github.com/LanEinstein/QuantMind
- **Blueprint**: `docs/QuantMind_Project_Blueprint_V3.md` (Phase 5: lines 1084-1109)
- **What just happened**: Phase 4 (Frontend) is 100% complete — all 6 tasks verified. Code is committed and pushed. Two housekeeping items need cleanup first (see Step 0).

### Phase 4 Completion Summary

| Task | Description | Status |
|------|-------------|--------|
| P4-T01 | Dashboard real-time market monitoring | Done |
| P4-T02 | Agent debate visualization with SSE | Done |
| P4-T03 | MiroFish simulation visualization | Done |
| P4-T04 | Portfolio management + circuit breaker + position drawer | Done |
| P4-T05 | Performance analytics + risk center | Done |
| P4-T06 | System settings (LLM, data, MiroFish, cost) | Done |

**Test results**: 35+ backend pytest + 81 Vitest = 116+ all passing. TypeScript type-check clean.

---

## Step 0: Housekeeping Cleanup (Do First)

Two issues from the last session need fixing before Phase 5 begins:

### 0a. Remove accidentally committed coverage artifacts

`frontend/coverage/` (23 files — HTML reports, CSS, JSON) was committed in `6c50741`. These are build artifacts that should never be in git.

```bash
# Add to .gitignore
echo "frontend/coverage/" >> .gitignore

# Remove from git tracking (keeps local files)
git rm -r --cached frontend/coverage/

# Commit
git commit -m "chore: remove coverage artifacts from git, update .gitignore"
git push
```

### 0b. Verify .gitignore completeness

Check that these patterns are all in `.gitignore`:

```
frontend/coverage/
frontend/components.d.ts
*.log
logs/
```

---

## Step 1: Phase 5 Architecture Overview

Phase 5 is fundamentally different from Phase 4. It transitions from **building features** to **running the system live** against real data in suggest mode. The goal is a 4-week evaluation measuring signal accuracy and hypothetical P&L vs. CSI300 benchmark.

### What Already Exists (Reuse)

| Component | File | Status |
|-----------|------|--------|
| 9-agent LangGraph pipeline | `backend/agents/graph.py` | Full |
| LLM Router (DeepSeek/Qwen/Kimi) | `backend/llm/router.py` | Full |
| Risk engine (7-check, stop-loss, circuit breaker) | `backend/risk/` | Full |
| Mock broker (slippage, commission, T+1) | `backend/broker/mock_broker.py` | Full |
| Market data service (adata/AKShare) | `backend/data/market_data.py` | Full |
| History data service (adata/BaoStock) | `backend/data/history_data.py` | Full |
| News crawler | `backend/data/news_crawler.py` | Full |
| Data scheduler (market + news) | `backend/data/scheduler.py` | Full |
| MongoDB service (collections, indexes) | `backend/data/database.py` | Full |
| Redis pub/sub publisher | `backend/data/publisher.py` | Full |
| LLM cost tracker (Redis) | `backend/llm/cost_tracker.py` | Full |
| Frontend: all 6 views + settings | `frontend/src/views/` | Full |
| Docker Compose (MongoDB + Redis + backend) | `docker-compose.yml` | Full |

### What Must Be Built (7 Gaps)

| # | Gap | Priority | Effort | Dependencies |
|---|-----|----------|--------|--------------|
| G1 | Daily analysis orchestrator + watchlist | HIGH | 2h | G2 |
| G2 | Signal persistence (MongoDB collection) | HIGH | 1h | None |
| G3 | CSI300 benchmark data + comparison | MEDIUM | 1.5h | G2 |
| G4 | Signal accuracy evaluation service | MEDIUM | 2h | G2, G3 |
| G5 | Detailed health monitoring endpoint | MEDIUM | 1h | None |
| G6 | LLM cost persistence to MongoDB | LOW | 1h | None |
| G7 | File-based structured logging | LOW | 1h | None |

**Total new code**: ~9-12 hours across 7 sub-tasks.

---

## Step 2: Implement Gaps (P5-T01 Sub-Tasks)

Execute these in dependency order. Each sub-task follows TDD (write test first, then implement).

### Sub-Task 2.1: Signal Persistence (G2) — Do First

**Goal**: Persist every `TradingSignal` to MongoDB so we have a decision log for the entire 4-week eval.

**Files to modify/create**:

1. **`backend/data/database.py`** — Add to `MongoDBService`:
   ```python
   async def save_signal(self, signal: dict) -> str:
       """Save TradingSignal to 'trading_signals' collection. Returns inserted _id."""

   async def query_signals(
       self, stock_code: str | None = None, days: int = 30
   ) -> list[dict]:
       """Query recent signals, optionally filtered by stock code."""

   async def get_signal_by_id(self, signal_id: str) -> dict | None:
       """Retrieve a single signal by its MongoDB _id."""
   ```

2. **`backend/data/database.py`** — Add index in `initialize()`:
   ```python
   self._db["trading_signals"].create_index(
       [("stock_code", ASCENDING), ("trade_date", DESCENDING)],
       unique=True,  # One signal per stock per day
   )
   ```

3. **`backend/api/analysis.py`** — After `run_analysis()` returns, persist:
   ```python
   signal_dict = signal.model_dump()
   signal_dict["created_at"] = datetime.now(UTC)
   await request.app.state.mongodb.save_signal(signal_dict)
   ```

4. **`backend/api/analysis.py`** — Add query endpoint:
   ```python
   @router.get("/api/analysis/signals")
   async def list_signals(stock_code: str | None = None, days: int = 30):
       """List historical signals for review."""
   ```

5. **`tests/test_signal_persistence.py`** — 5+ tests:
   - save_signal creates document
   - query_signals returns recent signals
   - query_signals filters by stock_code
   - unique constraint prevents duplicate per stock+date
   - signal round-trip preserves all fields

**Acceptance criteria**: After running `POST /api/analysis/stock`, the signal is persisted to MongoDB and queryable via `GET /api/analysis/signals`.

---

### Sub-Task 2.2: Daily Analysis Orchestrator + Watchlist (G1)

**Goal**: Automatically run the 9-agent pipeline for each stock in a watchlist at market open, persist all signals.

**Files to create/modify**:

1. **`backend/data/watchlist.py`** (NEW) — Watchlist service:
   ```python
   class WatchlistService:
       """MongoDB-backed stock watchlist management."""

       async def add_stock(self, code: str, name: str) -> None:
       async def remove_stock(self, code: str) -> None:
       async def list_stocks(self) -> list[dict]:
       async def clear(self) -> None:
   ```
   - Collection: `watchlist` in MongoDB
   - Fields: `stock_code`, `stock_name`, `added_at`, `active: bool`

2. **`backend/data/analysis_scheduler.py`** (NEW) — Daily orchestrator:
   ```python
   class AnalysisScheduler:
       """Schedule daily stock analysis for watchlist items.

       Runs at 09:45 CST (15 min after market open, to allow data to settle).
       Iterates through watchlist, runs pipeline, persists signals.
       Rate-limits to avoid LLM API throttling.
       """

       async def run_daily_analysis(self) -> list[TradingSignal]:
           stocks = await self._watchlist.list_stocks()
           signals = []
           for stock in stocks:
               try:
                   signal = await run_analysis(stock["stock_code"], self._services)
                   await self._mongodb.save_signal(signal.model_dump())
                   signals.append(signal)
                   await asyncio.sleep(10)  # Rate limit between stocks
               except Exception as exc:
                   log.error("daily_analysis_failed", stock=stock["stock_code"], error=str(exc))
           return signals
   ```
   - Uses APScheduler `cron` trigger: `hour=9, minute=45, timezone='Asia/Shanghai'`
   - Rate-limits: 10s between stocks to avoid LLM API throttling
   - Logs all failures, continues to next stock (no abort on single failure)

3. **`backend/api/watchlist.py`** (NEW) — REST endpoints:
   ```python
   @router.get("/api/watchlist")        # List watchlist
   @router.post("/api/watchlist")       # Add stock
   @router.delete("/api/watchlist/{code}")  # Remove stock
   @router.post("/api/watchlist/analyze-now")  # Trigger immediate analysis (manual)
   ```

4. **`backend/main.py`** — Wire into app lifespan:
   - Initialize `WatchlistService` and `AnalysisScheduler`
   - Start scheduler after data layer is ready
   - Shut down on app stop

5. **Tests**: `tests/test_watchlist.py` (5+), `tests/test_analysis_scheduler.py` (5+)

**Acceptance criteria**: Adding stocks to watchlist via API, daily analysis runs automatically at 09:45 CST, all signals persisted.

---

### Sub-Task 2.3: CSI300 Benchmark Data (G3)

**Goal**: Fetch and store CSI300 index daily prices for benchmark comparison.

**Files to modify/create**:

1. **`backend/data/market_data.py`** — Add method:
   ```python
   async def get_index_history(self, index_code: str = "000300", days: int = 252) -> list[dict]:
       """Fetch historical index prices (default: CSI300, 1 year)."""
   ```
   - Use adata or AKShare to fetch daily close prices
   - Index code `000300` = CSI300 (沪深300)

2. **`backend/data/database.py`** — Add:
   ```python
   async def save_index_prices(self, index_code: str, prices: list[dict]) -> None:
   async def get_index_prices(self, index_code: str, days: int = 30) -> list[dict]:
   ```
   - Collection: `index_prices`
   - Fields: `index_code`, `date`, `close`, `open`, `high`, `low`, `volume`

3. **`backend/data/scheduler.py`** — Add daily index price job:
   - Run once per day at 15:30 CST (after market close)
   - Fetch today's CSI300 close price, save to MongoDB

4. **`backend/api/performance.py`** — Replace flat benchmark:
   ```python
   # BEFORE: benchmark = 100.0 (flat line)
   # AFTER:  benchmark_prices = await mongodb.get_index_prices("000300", days)
   #         benchmark_returns = compute_normalized_returns(benchmark_prices)
   ```

5. **Tests**: `tests/test_benchmark_data.py` (4+)

**Acceptance criteria**: Performance page shows actual CSI300 equity curve alongside portfolio curve.

---

### Sub-Task 2.4: Signal Accuracy Evaluation (G4)

**Goal**: Measure whether past signals were correct by comparing recommendations to subsequent price movements.

**Files to create/modify**:

1. **`backend/services/signal_evaluator.py`** (NEW):
   ```python
   class SignalEvaluator:
       """Evaluate signal accuracy by checking if price moved in predicted direction.

       For each signal:
       - "买入" (Buy): correct if price rose within `horizon_days`
       - "卖出" (Sell): correct if price fell within `horizon_days`
       - "持有" (Hold): always "correct" (neutral, excluded from accuracy calc)
       """

       async def evaluate(
           self,
           lookback_days: int = 30,  # Evaluate signals from last N days
           horizon_days: int = 5,    # Check price movement over next N days
       ) -> SignalAccuracyReport:
           """Return accuracy stats: hit_rate, total_signals, breakdown by action."""
   ```

2. **`backend/api/analysis.py`** — Add endpoint:
   ```python
   @router.get("/api/analysis/signal-accuracy")
   async def signal_accuracy(lookback_days: int = 30, horizon_days: int = 5):
   ```

3. **Frontend**: Update performance dashboard to show signal accuracy metric (optional in this sub-task, can be a later enhancement).

4. **Tests**: `tests/test_signal_evaluator.py` (6+)

**Acceptance criteria**: `GET /api/analysis/signal-accuracy` returns hit rate, total signals evaluated, and per-action breakdown.

---

### Sub-Task 2.5: Detailed Health Monitoring (G5)

**Goal**: Comprehensive health endpoint for operational monitoring during 4-week eval.

**File to modify**:

1. **`backend/main.py`** — Replace or extend `/api/health`:
   ```python
   @app.get("/api/health/detailed")
   async def detailed_health(request: Request) -> dict:
       """Return detailed system health status."""
       return {
           "status": "ok" | "degraded" | "critical",
           "components": {
               "mongodb": await check_mongo(request),
               "redis": await check_redis(request),
               "llm_router": check_llm_status(request),
               "scheduler": check_scheduler_status(request),
               "market_data_age_seconds": get_data_staleness(request),
               "last_analysis_timestamp": get_last_signal_time(request),
           },
           "uptime_seconds": time.time() - app_start_time,
       }
   ```

2. **Tests**: `tests/test_health_detailed.py` (4+)

**Acceptance criteria**: `/api/health/detailed` returns per-component status with meaningful degradation signals.

---

### Sub-Task 2.6: LLM Cost Persistence (G6)

**Goal**: Persist LLM cost data from Redis to MongoDB for durable analytics.

**Files to modify**:

1. **`backend/llm/cost_tracker.py`** — Add MongoDB persistence:
   ```python
   async def flush_to_mongodb(self, mongodb: MongoDBService) -> None:
       """Persist today's cost entries from Redis to MongoDB for durable storage."""
   ```

2. **`backend/data/database.py`** — Add:
   ```python
   async def save_cost_entry(self, entry: dict) -> None:
       """Upsert daily cost entry to 'cost_tracking' collection."""

   async def get_cost_history(self, days: int = 30) -> list[dict]:
       """Query cost history from MongoDB."""
   ```

3. **`backend/data/scheduler.py`** — Add daily cost flush job:
   - Run once per day at 23:00 CST
   - Flush Redis cost data to MongoDB

4. **Tests**: `tests/test_cost_persistence.py` (3+)

---

### Sub-Task 2.7: File-Based Structured Logging (G7)

**Goal**: Persist structured logs to JSONL files for post-hoc debugging during eval period.

**Files to create/modify**:

1. **`backend/logging_config.py`** (NEW):
   ```python
   def configure_logging(log_dir: str = "logs") -> None:
       """Configure structlog to output JSON to rotating log files."""
       # - Daily rotation: logs/quantmind-YYYY-MM-DD.jsonl
       # - Retention: 30 days
       # - Also output to stdout for Docker compatibility
   ```

2. **`backend/main.py`** — Call `configure_logging()` at startup.

3. **`.gitignore`** — Add `logs/`

4. **`docker-compose.yml`** — Mount logs volume:
   ```yaml
   backend:
     volumes:
       - ./logs:/app/logs
   ```

5. **Tests**: `tests/test_logging_config.py` (2+)

---

## Step 3: Infrastructure Deployment

After implementing the gaps, set up the runtime environment:

### 3a. Environment Variables

LLM API keys live in user shell env (`~/.bashrc`), NOT in `.env`:

```bash
# Append to ~/.bashrc (one-time setup)
export DEEPSEEK_API_KEY=sk-xxx          # https://platform.deepseek.com/
export DASHSCOPE_API_KEY=sk-xxx         # https://bailian.console.aliyun.com/ (Qwen)
export MOONSHOT_API_KEY=sk-xxx          # https://platform.moonshot.ai/ (Kimi)
```

`.env` in project root keeps only non-secret runtime config:

```bash
# Database
MONGODB_URI=mongodb://localhost:27017/quantmind
REDIS_URL=redis://localhost:6379/0

# Broker
BROKER_MODE=mock
MOCK_INITIAL_CAPITAL=1000000

# System
AUTHORIZATION_MODE=suggest       # CRITICAL: must be "suggest", not "auto"
LOG_LEVEL=INFO
```

### 3b. Start Infrastructure

```bash
# Start MongoDB + Redis
docker compose up -d mongodb redis

# Verify services
docker compose ps
docker compose logs mongodb --tail 5
docker compose logs redis --tail 5

# Start backend (development mode, outside Docker for easier debugging)
cd /home/ps/papers/QuantMind
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# Start frontend (separate terminal)
cd frontend && npm run dev
```

### 3c. Connectivity Tests

Run via Settings UI (`/settings/llm-router`) or curl:

```bash
# Test LLM providers
curl -X POST http://localhost:8000/api/settings/llm-config/test

# Test data sources
curl -X POST http://localhost:8000/api/settings/data-sources/test

# Detailed health check
curl http://localhost:8000/api/health/detailed
```

---

## Step 4: Populate Watchlist and Start Evaluation

### 4a. Add Initial Watchlist (5-10 stocks for the first week)

Start conservative — a small watchlist reduces LLM costs and makes debugging easier.

```bash
# Add diversified A-share stocks across sectors
curl -X POST http://localhost:8000/api/watchlist \
  -H "Content-Type: application/json" \
  -d '{"code": "600519", "name": "贵州茅台"}'    # Consumer staples

curl -X POST http://localhost:8000/api/watchlist \
  -H "Content-Type: application/json" \
  -d '{"code": "000858", "name": "五粮液"}'      # Consumer

curl -X POST http://localhost:8000/api/watchlist \
  -H "Content-Type: application/json" \
  -d '{"code": "601318", "name": "中国平安"}'    # Finance

curl -X POST http://localhost:8000/api/watchlist \
  -H "Content-Type: application/json" \
  -d '{"code": "000001", "name": "平安银行"}'    # Banking

curl -X POST http://localhost:8000/api/watchlist \
  -H "Content-Type: application/json" \
  -d '{"code": "300750", "name": "宁德时代"}'    # New energy
```

### 4b. Manual Test Run

Before relying on the scheduler, trigger one manual analysis to verify the full pipeline:

```bash
# Trigger immediate analysis of watchlist
curl -X POST http://localhost:8000/api/watchlist/analyze-now

# Check signals were persisted
curl http://localhost:8000/api/analysis/signals?days=1

# Check costs
curl http://localhost:8000/api/settings/cost-stats
```

### 4c. 4-Week Evaluation Schedule

| Week | Focus | Actions |
|------|-------|---------|
| **Week 1** | System stability | Monitor daily runs, fix any pipeline errors, verify data quality. Keep watchlist at 5 stocks. Check logs daily. |
| **Week 2** | Expand & tune | Increase watchlist to 10-15 stocks. Review first week's signals for obvious errors. Tune agent prompts if needed. |
| **Week 3** | Accuracy tracking | Run signal accuracy evaluation. Compare portfolio P&L vs CSI300. Identify model-specific strengths/weaknesses. |
| **Week 4** | Final assessment | Generate performance report. Prepare data for P5-T02 (A/B testing). Document findings and tuning recommendations. |

### 4d. Daily Monitoring Checklist

Each trading day, verify:
- [ ] Analysis scheduler ran at 09:45 (check logs)
- [ ] All watchlist stocks received signals (check `/api/analysis/signals?days=1`)
- [ ] No LLM provider failures (check `/api/health/detailed`)
- [ ] LLM costs within budget (check `/api/settings/cost-stats`)
- [ ] Market data is fresh (check data staleness in health endpoint)

---

## Step 5: P5-T02 and P5-T03 (Plan Ahead, Don't Build Yet)

### P5-T02: Multi-Strategy A/B Testing (Week 3-4)

After accumulating 2+ weeks of suggest-mode data, set up parallel virtual accounts:

- **Account A**: Default 3-model config (DeepSeek V4 Pro + Qwen 3.6 Plus + Kimi K2.6)
- **Account B**: Single-model (all agents use Kimi K2.6)
- **Account C**: Single-model (all agents use DeepSeek V4 Pro, cheapest)

This requires:
- Multi-account support in MockBroker (already has `BrokerRegistry`)
- Per-account config overrides in `agent_models.yaml`
- Comparative performance dashboard

### P5-T03: JoinQuant Cross-Validation (Week 4+)

Run same signals on JoinQuant's free paper trading platform to validate MockBroker accuracy:
- Export daily signals as JoinQuant-compatible format
- Compare fill prices, commission, slippage
- Assess MockBroker fidelity

---

## Constraints

- Python 3.11+ / FastAPI / LangGraph
- Code comments and commits in English
- UI text in Chinese
- Risk engine (`backend/risk/`) is pure Python, no LLM dependency
- Config via YAML, secrets via `.env`
- Coverage: >95% for risk engine, >70% for others
- AUTHORIZATION_MODE must be "suggest" (not "auto") for the entire eval period
- All LLM calls wrapped in try/except with graceful degradation

---

## Recommended Session Breakdown

This is too much for one Claude Code session. Recommended split:

| Session | Scope | Est. Time |
|---------|-------|-----------|
| **Session A** | Step 0 (cleanup) + Sub-Task 2.1 (signal persistence) + Sub-Task 2.2 (orchestrator + watchlist) | 3-4h |
| **Session B** | Sub-Task 2.3 (benchmark) + Sub-Task 2.4 (signal accuracy) + Sub-Task 2.5 (health) | 3-4h |
| **Session C** | Sub-Task 2.6 (cost persistence) + Sub-Task 2.7 (logging) + Step 3 (deploy) + Step 4 (go live) | 2-3h |

Start with **Session A** — it unblocks everything else.
