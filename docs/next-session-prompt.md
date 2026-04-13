# Next Session Prompt — P4-T04 Commit + Phase 5 Planning

Copy the content below into a new Claude Code session:

---

## Task: Commit P4-T04 and plan Phase 5

### Context

- **Project**: QuantMind quantitative trading system
- **Current branch**: `main`
- **Remote**: https://github.com/LanEinstein/QuantMind
- **What just happened**: P4-T04 (Enhanced Portfolio Management with Risk Engine Integration) implementation is complete. All code changes are in the working tree, some staged, some unstaged. A 3-round Codex cross-model review was completed — all CRITICAL/HIGH cleared, final verdict PASS.

### Current State

21 files changed (+905/-25), including:

**Backend** (4 modified):
- `backend/data/publisher.py` — New `CHANNEL_PORTFOLIO` + `publish_portfolio_event()`
- `backend/api/websocket.py` — Subscribe to portfolio channel, forward messages
- `backend/api/trading.py` — Emit position/approval updates after order actions; new `GET /api/trading/circuit-breaker-status` endpoint; resolve actual account_id for approve/reject
- `backend/api/risk.py` — Emit `auth_mode_change` after mode switch

**Frontend** (13 modified/created):
- `types/market.ts` — Extended WsMessage with 4 new portfolio message types
- `types/trading.ts` — Added `CircuitBreakerStatus`, `AuthorizationMode`
- `api/trading.ts` — Added `getCircuitBreakerStatus` call
- `composables/useWebSocket.ts` — Dispatch portfolio messages to portfolio store
- `stores/portfolio.ts` — New state (circuitBreakerStatus, authMode), WS update actions, fetchAuthMode via riskApi
- `views/Portfolio.vue` — Circuit breaker alert, position drawer, WebSocket connect, reduced polling to 30s
- `components/trading/AccountBanner.vue` — Auth mode tag display
- `components/trading/ApprovalQueue.vue` — Halt-aware approve button with tooltip
- `components/trading/PositionTable.vue` — Emit select-position on code click
- `components/trading/PositionDetailDrawer.vue` (NEW) — Stop-loss gauge, risk details drawer

**Tests** (4 modified/created):
- `tests/test_portfolio_publisher.py` (NEW) — 5 tests
- `tests/test_websocket.py` — +3 portfolio channel tests
- `tests/test_api_trading.py` — +3 circuit breaker status tests
- `frontend/src/stores/__tests__/portfolio.spec.ts` (NEW) — 7 store tests

**Test results**: 35 backend pytest + 81 Vitest = 116 all passing. TypeScript type-check clean.

### Step 1: Commit & Push P4-T04

Stage all files and commit with message:

```
feat: add real-time portfolio updates via WebSocket, circuit breaker display, and position detail drawer (P4-T04)
```

Then push to remote.

### Step 2: Review Phase 4 Completion

With P4-T04 committed, Phase 4 is complete:
- P4-T01 Dashboard ✅
- P4-T02 Agent Debate ✅
- P4-T03 MiroFish Visualization ✅
- P4-T04 Portfolio Enhancement ✅
- P4-T05 Performance & Risk Center ✅
- P4-T06 System Settings ✅

### Step 3: Plan Phase 5 — Verification & Iteration

Refer to `docs/QuantMind_Project_Blueprint_V3.md` section Phase 5 (lines 1084-1109):

- **P5-T01**: Run suggest mode for 4 weeks, track signal accuracy and hypothetical P&L vs benchmark (沪深300)
- **P5-T02**: Multi-strategy A/B testing with parallel virtual accounts
- **P5-T03**: Cross-validate with JoinQuant paper trading

Phase 5 is fundamentally different from Phase 4 — it's about connecting to real data sources, running the system live in suggest mode, and measuring performance. This requires:
1. Real LLM API connections (DeepSeek, Qwen, MiniMax)
2. Real market data (adata/AKShare)
3. MongoDB + Redis infrastructure
4. Monitoring and logging infrastructure

Plan what's needed to get P5-T01 running: infrastructure setup, data pipeline wiring, agent orchestration, and the 4-week suggest mode evaluation framework.

### Constraints
- Python 3.11+ / FastAPI / LangGraph
- Code comments and commits in English
- UI text in Chinese
- Risk engine (`backend/risk/`) is pure Python, no LLM dependency
- Config via YAML, secrets via .env
- Coverage: >95% for risk engine, >70% for others

---
