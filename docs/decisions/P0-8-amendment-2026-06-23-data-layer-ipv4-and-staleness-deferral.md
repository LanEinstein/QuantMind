# P0-8 Amendment (2026-06-23) — Data-layer IPv4 egress (D6) + staleness/suspension deferral (D1/D3)

> **Status**: proposed → implementing (production-hardening audit pass).
> **Amends**: P0-8 §2.5 (data intelligence) + §2.9 (IPv4-only egress). Safety-base
> redlines unchanged (official Tushare SDK only, no akshare 节假日 API at runtime,
> no LLM in the data path, PIT byte-archive).
> **Driver**: the 2026-06-23 audit found (D2) one un-paginated `*_vip` endpoint,
> (D6) the data SDKs not actually pinned to IPv4, and two acknowledged limitations
> (D1 fetch-time staleness, D3 hardcoded `is_suspended=False`).

## 1. Decisions

### 1.1 D2 — `fina_indicator_vip` pagination (pure bug, no boundary change)
Routed through `_fetch_paginated` like its statement siblings. It was the only
`*_vip` endpoint still on a single `_fetch`; its ~12000-row single-call cap
silently truncates the universe once listings approach it, corrupting the PIT
survivorship denominator (CLAUDE.md §2.5 redline 6 / memory
`reference-tushare-statement-vip-row-cap`). Shipped in this batch (not part of
this amendment's boundary change — it restores intended behaviour).

### 1.2 D6 — enforce IPv4-only egress for the data SDKs (boundary change)
§2.9 requires IPv4-only egress; the LLM/alerter `httpx` clients pin
`local_address='0.0.0.0'`, but the data SDKs (tushare/adata/akshare via
`requests`→`urllib3`) only *assumed* the vendors publish no AAAA record. New
`backend/data/ipv4_egress.force_ipv4_egress()` overrides
`urllib3.util.connection.allowed_gai_family` to `AF_INET`; called once at the top
of `_init_data_layer` before any data fetch. Makes the invariant **enforced**, not
assumed (a future vendor AAAA record would otherwise stall each full-market fetch
on a dead IPv6 path until connect-timeout). Idempotent; safe on the IPv4-only host.

### 1.3 D1 — staleness gate uses fetch-time, not quote age (DEFERRED, task #12)
`MarketDataQuoteProbe` computes `age = now − leg.timestamp`, but the parsers stamp
`timestamp` at fetch time, so the 5s/60s staleness gates measure RTT, not true
quote age. A correct fix needs a new `StockQuote.quote_time` field + parsing the
vendor exchange-clock timestamp (sina carries a DATE+TIME header per the parser
docstring; **adata exposes no timestamp column** — must be verified) + the probe
using `quote_time` with a sanity fallback. **Deferred**: the parser docstring
itself scopes this out as needing a P0-8 amendment + new field, and a *wrong*
timestamp parse would SPURIOUSLY HALT trading — so it needs empirical
vendor-format validation, not an offline guess. The divergence gate
(|primary − backup| ≤ 0.3%) remains the effective backstop (two vendors won't
agree on a frozen price); the residual is the narrow both-legs-stall-identically
case. No behaviour change this pass.

### 1.4 D3 — quote probe hardcodes `is_suspended=False` (DEFERRED, task #13)
A halted stock echoing a positive pre-halt price slips the gate. `is_suspended`
exists but takes a `WatchlistMarketSnapshot`, not the probe's `StockQuote`; a
proper fix wires a `suspend_d` (Tushare daily suspension list) provider per code
into the probe. **Deferred**: partial coverage already exists (the price≤0/NaN
sentinel + the sina parser fail-closing on a halted price), and the proper wiring
is dedicated work. No behaviour change this pass.

## 2. Redline impact
No new write endpoint, no LLM in the data path, official Tushare SDK only. D6
**strengthens** §2.9 (extends IPv4-only egress to `urllib3`). D2 restores intended
PIT completeness. D1/D3 are deferred with no behaviour change (the current
acknowledged-limitation behaviour stands, now with tracked follow-ups).

## 3. TDD plan
- D2: `fina_indicator_vip` pages with limit+offset and assembles the complete
  period (mirrors the statement-vip pagination test).
- D6: `force_ipv4_egress()` makes `urllib3` `allowed_gai_family()` return
  `AF_INET` (test restores the original to avoid global pollution).
- Full suite + ruff + redline green; codex review gate per §3.
