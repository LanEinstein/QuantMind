# U-D9 — Feishu WS inbound receiver crash fix · review summary

> Review tool: **`claude /code-review high`** (codex CLI rate-limited until ~2026-05-31,
> owner-mandated fallback per [[feedback_codex_rate_limit_fallback]]). 1 cycle,
> 7 finder angles (3 correctness + 3 cleanup + 1 altitude) × ≤6 candidates →
> recall-biased verify.

## Scope

`backend/integrations/feishu/events.py` + `tests/test_feishu_events.py` — fix the
lark-oapi WebSocket inbound receiver that crashed on startup with
`RuntimeError: this event loop is already running`.

## Root cause (confirmed against installed lark-oapi 1.5.3)

1. `lark_oapi.ws.client.Client.start()` is **synchronous and blocking**: it drives a
   **module-global** `loop = asyncio.get_event_loop()` (captured at import time) via
   `loop.run_until_complete(self._connect())` and ends in
   `loop.run_until_complete(_select())` (never returns). When `lark_oapi.ws.client` is
   first imported inside the running uvicorn loop, that module-global `loop` **is** the
   uvicorn loop → `run_until_complete` on an already-running loop → the crash. The old
   code `await self._client.start()` was doubly wrong (`start()` is sync, returns `None`).
2. **Second latent bug**: the SDK's `P2ImMessageReceiveV1Processor.do` calls the
   registered handler `self.f(data)` **synchronously and discards the return**. The old
   handler was `async def _handle`, so it produced a coroutine that was **never awaited**
   — inbound events would never have been processed even if the WS had connected.

## Fix

- Run the blocking `client.start()` on a **dedicated daemon thread** with its **own**
  event loop; rebind the SDK's module-global `loop` to that loop.
- The SDK callback `_on_sdk_event` is now a **synchronous bridge** that marshals
  `_handle_event` onto the main uvicorn loop via `asyncio.run_coroutine_threadsafe`
  (keeps motor/redis on their creation loop; returns immediately → 3s ack honoured).
- Message-processing logic (`_handle_event`/`_dispatch`/`_extract_message`/dedupe) is
  unchanged — only the startup/lifecycle bridge changed (P0-2 red lines intact:
  WS-only inbound, no HTTPS callback, `tenant_access_token` SDK-owned, no LLM imports).

## Findings addressed (CONFIRMED/PLAUSIBLE → fixed)

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | HIGH | start→stop race: `_serve` set `_thread_loop` only after the thread ran, so a `stop()` in that window skipped the real-SDK stop signal → join timeout → `_thread=None` → next `start()` opens a **duplicate WS connection** (double-delivery). | Create the loop + rebind the lark global in **`start()`** (main thread, before spawning). `_thread_loop` is set synchronously, so the stop signal is never lost. `_serve` only adopts the loop via `set_event_loop`. |
| 2 | HIGH (red line) | `log_level=INFO` made lark log the connect URL containing per-connection `ticket`/`access_key` **session credentials** in plaintext — violates CLAUDE.md §2.9 (no plaintext secrets in logs). | Reverted to `WARNING`. Failures (`connect failed`, `receive message loop exit`) still surface at WARNING/ERROR, so a dying connection stays observable; only the credential-bearing success URL is suppressed. |
| 3 | MEDIUM | `run_coroutine_threadsafe` can raise `RuntimeError` if the main loop closes between the `is_closed()` guard and the call (shutdown race); it would escape into the SDK's message loop and be mis-attributed as a generic SDK failure. | Wrapped in `try/except RuntimeError` → logs a secret-free `feishu_event_dropped_loop_unavailable`. |
| 4 | LOW | Join-timeout invisibility: if the thread wedges past the grace window, `_thread` was nulled with no signal. | Log `feishu_event_receiver_thread_join_timeout` when `is_alive()` after join (daemon thread dies with process). |
| 5 | LOW (test gap) | No test exercised the production `_on_sdk_event` cross-thread bridge or the drop path. | Added `TestSdkEventBridge`: marshal-to-main-loop (invoked from a worker thread), drop-on-no-main-loop, drop-on-closed-loop. |

## Not fixed (REFUTED / out of scope, with rationale)

- **Multi-receiver global-loop clobber**: the architecture runs exactly **one**
  receiver per process (mode switch re-inits at startup, not concurrently). The
  single-instance assumption is documented; structural change deferred.
- **Drain micro-window** (event scheduled just before `stop()` not yet in `_tasks`):
  pre-existing shutdown semantics; `EventDedupe` covers any double-apply. Not worth
  complicating the shutdown path.
- **`_tasks` "two-thread" race**: verified single-loop-only — `_handle_event` and its
  done-callback both run on the main loop; the WS thread only calls
  `run_coroutine_threadsafe`. No real cross-thread mutation.
- **Ungraceful WS close on stop** (`loop.stop()` skips a close frame): pre-existing (the
  old `cancel()` didn't close either); the TCP socket is reaped on process exit. Not a
  regression.

## Real-start verification (criterion B — offline tests had masked this bug)

- **INFO run** (pre-final): `[Lark] [INFO] connected to wss://msg-frontier.feishu.cn/...`
  + `feishu_event_receiver_wired` + `Application startup complete`; WS resident **~87s**,
  exactly one connect (no reconnect loop), **no** `feishu_event_receiver_crashed`, **no**
  `this event loop is already running`; clean SIGTERM shutdown
  (`feishu_event_receiver_stopped` → `application_stopped`).
- **WARNING run** (final code, with the loop-in-`start()` race fix): clean **≥82s**
  uptime, gate passed (mode→feishu_interactive + broker_reset + receiver wired), **zero**
  crash/leak/reconnect signatures; the secret-bearing connect URL is correctly no longer
  logged; clean SIGTERM shutdown.

## Gates

`ruff` clean (changed files) · `scripts/redline-check.sh` ALL PASS (P0-2 WS-only, only 2
write endpoints, M-004 single construction point intact) · `pytest` **4160 passed / 13
skipped** (baseline 4157 + 3 new bridge tests, zero regression).
