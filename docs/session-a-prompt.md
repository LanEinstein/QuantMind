# Session A Prompt — Cleanup + Signal Persistence + Daily Orchestrator

将下面的内容复制到新的 Claude Code 会话中执行:

---

## Task: P5-T01 Session A — Cleanup, Signal Persistence, Watchlist & Daily Orchestrator

### Context

- **Project**: QuantMind — 个人A股量化交易系统 (多Agent LLM决策 + MiroFish群体智能仿真)
- **Branch**: `main`
- **Remote**: https://github.com/LanEinstein/QuantMind
- **Blueprint**: `docs/QuantMind_Project_Blueprint_V3.md`
- **Full Phase 5 plan**: `docs/next-session-prompt.md`
- **Current state**: Phase 4 (前端6个任务) 100%完成，116+ tests passing，working tree clean。现在进入 Phase 5 — 将系统从开发模式切换到建议模式实盘运行。

### Session A Scope (3 deliverables)

本会话完成 Phase 5 的关键路径: 清理遗留 → 信号持久化 → 每日分析调度器。这三项完成后，系统就具备了"每天自动分析自选股、记录所有决策"的能力。

---

## Deliverable 0: Housekeeping Cleanup

### 0a. Remove accidentally committed coverage artifacts

`frontend/coverage/` (23 files — HTML reports, CSS, JSON, PNG) was committed in `6c50741`. These are build artifacts.

Actions:
1. Add these patterns to `.gitignore` (if not already present):
   ```
   frontend/coverage/
   frontend/components.d.ts
   *.log
   logs/
   ```
2. Remove from git tracking: `git rm -r --cached frontend/coverage/`
3. If `frontend/components.d.ts` is tracked, also `git rm --cached frontend/components.d.ts`
4. Commit: `chore: remove build artifacts from git, update .gitignore`
5. Push to remote

### 0b. Verify test suite still passes

```bash
cd /home/ps/papers/QuantMind
python -m pytest tests/ -q
cd frontend && npx vitest run
```

Both must remain green before proceeding.

---

## Deliverable 1: Signal Persistence (Gap G2)

### Goal

Every time the 9-agent pipeline produces a `TradingSignal`, persist it to MongoDB. This is the foundation for all Phase 5 metrics (accuracy, P&L, comparison).

### Key files to understand first (READ before coding)

- `backend/data/database.py` — MongoDBService, existing collections & indexes
- `backend/agents/models.py` — TradingSignal Pydantic model
- `backend/agents/graph.py` — `run_analysis()` function that returns TradingSignal
- `backend/api/analysis.py` — POST endpoint that calls run_analysis

### Implementation (TDD)

#### Step 1: Write tests first → `tests/test_signal_persistence.py`

```python
# Test cases (minimum 5):
# 1. save_signal inserts document and returns _id string
# 2. query_signals returns signals from last N days, sorted by trade_date DESC
# 3. query_signals filters by stock_code when provided
# 4. unique constraint: saving same stock_code + trade_date twice raises/upserts correctly
# 5. signal round-trip: save then query preserves all TradingSignal fields
# 6. get_signal_by_id returns correct document
# 7. query_signals with days=0 returns empty list
```

Use the same async test patterns as existing tests (see `tests/test_database.py` for mock setup).

#### Step 2: Implement in `backend/data/database.py`

Add to `MongoDBService`:

```python
async def save_signal(self, signal: dict) -> str:
    """Save a TradingSignal dict to 'trading_signals' collection.

    Uses upsert on (stock_code, trade_date) to prevent duplicates.
    Returns the document _id as string.
    """

async def query_signals(
    self, stock_code: str | None = None, days: int = 30
) -> list[dict]:
    """Query recent trading signals.

    Args:
        stock_code: Filter by stock code. None = all stocks.
        days: Lookback window in days.

    Returns:
        Signals sorted by trade_date DESC, then stock_code ASC.
    """

async def get_signal_by_id(self, signal_id: str) -> dict | None:
    """Retrieve a single signal by MongoDB ObjectId string."""
```

Add index in `initialize()`:
```python
await self._db["trading_signals"].create_index(
    [("stock_code", ASCENDING), ("trade_date", DESCENDING)],
    unique=True,
)
```

#### Step 3: Wire into API layer — `backend/api/analysis.py`

1. After `run_analysis()` returns the signal, persist it:
   ```python
   from datetime import UTC, datetime

   signal_dict = signal.model_dump()
   signal_dict["created_at"] = datetime.now(UTC)
   if hasattr(request.app.state, "mongodb") and request.app.state.mongodb:
       await request.app.state.mongodb.save_signal(signal_dict)
   ```

2. Add new query endpoint:
   ```python
   @router.get("/api/analysis/signals")
   async def list_signals(
       request: Request,
       stock_code: str | None = None,
       days: int = 30,
   ) -> dict:
       """List historical trading signals for review."""
   ```

#### Step 4: Run tests, verify all pass

```bash
python -m pytest tests/test_signal_persistence.py -v
python -m pytest tests/ -q  # Full suite still green
```

---

## Deliverable 2: Watchlist Service + Daily Analysis Orchestrator (Gap G1)

### Goal

Build a watchlist (自选股) service backed by MongoDB, and a daily scheduler that automatically runs the 9-agent analysis pipeline for each watchlist stock at 09:45 CST every trading day.

### Key files to understand first (READ before coding)

- `backend/data/scheduler.py` — Existing DataScheduler (market + news jobs)
- `backend/data/trading_hours.py` — `is_trading_hours()` + `is_trading_day()`
- `backend/agents/graph.py` — `run_analysis(stock_code, services)` → TradingSignal
- `backend/agents/models.py` — AnalysisServices (bundle of LLM router + data services)
- `backend/main.py` — App lifespan, `_init_data_layer()`, how services are wired

### Implementation (TDD)

#### Part A: Watchlist Service

**Tests first** → `tests/test_watchlist.py` (5+ tests):
```python
# 1. add_stock inserts document with stock_code, stock_name, added_at, active=True
# 2. list_stocks returns only active stocks
# 3. remove_stock sets active=False (soft delete, not hard delete)
# 4. add_stock with duplicate code upserts (reactivates if previously removed)
# 5. clear sets all stocks to active=False
```

**Implement** → `backend/data/watchlist.py` (NEW file):
```python
class WatchlistService:
    """MongoDB-backed stock watchlist management.

    Stocks are soft-deleted (active=False) rather than removed,
    preserving history of what was tracked and when.
    """

    def __init__(self, db) -> None:
        self._collection = db["watchlist"]

    async def initialize(self) -> None:
        """Create indexes."""
        await self._collection.create_index("stock_code", unique=True)

    async def add_stock(self, code: str, name: str) -> None:
    async def remove_stock(self, code: str) -> None:
    async def list_stocks(self) -> list[dict]:
    async def clear(self) -> None:
```

#### Part B: Daily Analysis Orchestrator

**Tests first** → `tests/test_analysis_scheduler.py` (5+ tests):
```python
# 1. run_daily_analysis calls run_analysis for each watchlist stock
# 2. run_daily_analysis persists each signal via mongodb.save_signal
# 3. run_daily_analysis continues to next stock if one fails (no abort)
# 4. run_daily_analysis returns list of successful signals
# 5. run_daily_analysis skips if watchlist is empty (returns [])
# 6. run_daily_analysis publishes results to Redis for WebSocket clients
```

Mock `run_analysis`, `mongodb.save_signal`, and the watchlist service in tests.

**Implement** → `backend/data/analysis_scheduler.py` (NEW file):
```python
class AnalysisScheduler:
    """Daily stock analysis orchestrator.

    Runs at 09:45 CST on trading days. For each active watchlist stock:
    1. Call run_analysis() (9-agent LangGraph pipeline)
    2. Persist signal to MongoDB
    3. Publish signal to Redis for real-time frontend updates
    Rate-limits 10s between stocks to avoid LLM API throttling.
    """

    def __init__(
        self,
        watchlist: WatchlistService,
        services: AnalysisServices,
        mongodb: MongoDBService,
        redis_client: redis.asyncio.Redis | None,
    ) -> None: ...

    async def start(self) -> None:
        """Register cron job: 09:45 Asia/Shanghai, Mon-Fri."""

    async def stop(self) -> None:
        """Shutdown scheduler."""

    async def run_daily_analysis(self) -> list[TradingSignal]:
        """Execute analysis for all watchlist stocks. Called by scheduler or manually."""

    async def run_single_analysis(self, stock_code: str) -> TradingSignal | None:
        """Analyze a single stock on demand."""
```

#### Part C: Watchlist REST API

**Implement** → `backend/api/watchlist.py` (NEW file):
```python
router = APIRouter(tags=["watchlist"])

@router.get("/api/watchlist")
async def list_watchlist(request: Request) -> dict:
    """List all active watchlist stocks."""

@router.post("/api/watchlist")
async def add_to_watchlist(request: Request, body: AddStockRequest) -> dict:
    """Add a stock to the watchlist. Body: {"code": "600519", "name": "贵州茅台"}"""

@router.delete("/api/watchlist/{code}")
async def remove_from_watchlist(request: Request, code: str) -> dict:
    """Remove a stock from the watchlist (soft delete)."""

@router.post("/api/watchlist/analyze-now")
async def trigger_analysis_now(request: Request) -> dict:
    """Manually trigger analysis for all watchlist stocks. Returns signals."""

@router.post("/api/watchlist/analyze/{code}")
async def trigger_single_analysis(request: Request, code: str) -> dict:
    """Manually trigger analysis for a single stock."""
```

#### Part D: Wire into main.py

In `backend/main.py`:

1. In `_init_data_layer()`, after MongoDB/scheduler setup:
   ```python
   from backend.data.watchlist import WatchlistService
   from backend.data.analysis_scheduler import AnalysisScheduler

   watchlist_service = WatchlistService(db)
   await watchlist_service.initialize()
   application.state.watchlist = watchlist_service
   ```

2. Create AnalysisScheduler after trading layer is ready (it needs AnalysisServices):
   ```python
   # Build AnalysisServices for the scheduler
   analysis_services = AnalysisServices(
       llm_router=router,
       market_data=market_data,
       history_data=history_data,
       news_crawler=news_crawler,
       mongodb=mongodb_service,
       pipeline_config=PipelineConfig(),
   )
   analysis_scheduler = AnalysisScheduler(
       watchlist=watchlist_service,
       services=analysis_services,
       mongodb=mongodb_service,
       redis_client=redis_pool,
   )
   await analysis_scheduler.start()
   application.state.analysis_scheduler = analysis_scheduler
   ```

3. Include the watchlist router:
   ```python
   from backend.api.watchlist import router as watchlist_router
   app.include_router(watchlist_router)
   ```

4. In shutdown, stop the analysis scheduler.

#### Part E: Run all tests

```bash
python -m pytest tests/test_watchlist.py tests/test_analysis_scheduler.py -v
python -m pytest tests/ -q  # Full backend suite
cd frontend && npx vitest run  # Frontend unchanged but verify
```

---

## Final Step: Commit & Push

After all tests pass:

```
feat: add signal persistence, watchlist service, and daily analysis orchestrator (P5-T01 Session A)
```

Push to remote.

---

## Constraints (must follow)

- **TDD**: Write tests FIRST (RED), implement (GREEN), then refactor
- **Immutability**: All new Pydantic models use `frozen=True` or are plain dicts
- **File size**: Keep each new file under 400 lines; extract if larger
- **Error handling**: All LLM calls wrapped in try/except; log error, continue to next stock
- **No hardcoded secrets**: Config via YAML, keys via .env
- **Risk engine isolation**: `backend/risk/` must NOT be modified or imported by new code
- **Type hints**: All public functions must have full type annotations
- **Docstrings**: English docstrings on all public functions
- **Code comments**: English
- **Test coverage**: Minimum 5 tests per new module, >70% coverage
- **Existing tests must not break**: Run full suite before committing

## Architecture Notes

- `AnalysisServices` (in `backend/agents/models.py`) is the dependency bundle that `run_analysis()` needs. Read it to understand what fields are required.
- `DataScheduler` already uses APScheduler `AsyncIOScheduler` — follow the same pattern for `AnalysisScheduler`, but use a `cron` trigger instead of `interval`.
- The watchlist API follows the same envelope pattern as other APIs (see `backend/api/trading.py` for reference).
- Use structlog for all logging: `log = structlog.get_logger(component="analysis_scheduler")`
