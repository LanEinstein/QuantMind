# U-B2 Amendment — Durable Mongo-backed OutboxRepository

**Date:** 2026-05-29
**Amends:** U-B2 (InstructionDispatcher outbox, original note in main.py U-D2/U-D4 follow-on)
**Status:** ADOPTED

---

## 1. Context

`InstructionDispatcher` (backend/orchestration/instruction_dispatcher.py) guards
outbound Feishu BUY/SELL sends with a durable outbox. The outbox's `try_claim`
is the **at-most-once gate**: only the call that creates the row may send; a SENT
row short-circuits and prevents re-sends.

The original implementation wired `InMemoryOutboxRepository()` in `main.py`
(noted as a known gap: "a durable Mongo outbox is required before feishu
go-live"). Feishu's server-side `uuid` dedup window is only ~1 hour. A process
restart more than one hour after dispatching a BUY (e.g., a nightly restart)
would re-send the same signal to the owner past that window — a **real
double-buy risk**.

---

## 2. Decision

Replace `InMemoryOutboxRepository` with `MongoOutboxRepository`
(`backend/orchestration/mongo_outbox.py`) in the production wiring
(`_init_line2_runners` in `backend/main.py`).

---

## 3. Design — Mutable Claim Table (not append-only)

The outbox is intentionally a **mutable claim table** (one document per
`instruction_id`, `_id = instruction_id`), NOT the append-only `broker_events`
ledger.

**Why mutable:**
- `release` (called after a definitive API rejection) must **delete** a PENDING
  claim so the same signal can be re-claimed and re-sent cleanly later.
- `mark_sent` must **update** the status from PENDING → SENT.
- The append-only invariant of `broker_events` (8 P1-2.A red lines) is
  **not applicable here**: the outbox is a lightweight claim registry, not an
  audit ledger.

**At-most-once SEND invariant is still preserved:**
- SENT is terminal and never deleted (`release` only removes PENDING rows).
- A SENT `_id` blocks any future `try_claim` (DuplicateKeyError) and any future
  `release` (status filter excludes it).

---

## 4. StrEnum Rehydration (U-D6b class of bug)

Real Mongo/BSON has no enum type. A `StrEnum` round-trips through Mongo as a
plain `str`. The in-memory `FakeCollection` preserves the Python enum object,
masking this bug in unit tests.

`_rehydrate_doc` in `mongo_outbox.py` restores `OutboxStatus` from the raw
`str` on every read path. An unrecognised value raises (never silent rewrite —
mirrors `_rehydrate_event_doc` in `broker/persistence/store.py`).

The dispatcher's SENT short-circuit uses `entry.status is OutboxStatus.SENT`
(identity check). Without rehydration, a plain `"sent"` str would compare False
for `is`, the short-circuit would be skipped, and the signal would be re-sent.

---

## 5. Naive → UTC Datetime Coercion

`motor` returns naive UTC datetimes from Mongo (BSON stores UTC but strips
`tzinfo`). `OutboxEntry.claimed_at` / `sent_at` consumers expect tz-aware
values. `_coerce_utc` in `mongo_outbox.py` applies `.replace(tzinfo=UTC)` to
any naive datetime on the read path.

---

## 6. Graceful Degradation

If `app.state.mongodb._db` is `None` (dev loop without a Mongo replica set),
the code falls back to `InMemoryOutboxRepository` with a warning log. This
preserves the simulation_auto boot path in dev. Feishu Interactive mode is
blocked by the lifespan Feishu gate before any real send would fire.

---

## 7. Mongo Collection

Collection name: `instruction_outbox` (constant `MongoOutboxRepository.COLLECTION_NAME`).

This is a **separate collection** from `broker_events` / `broker_snapshots` so
the mutable outbox semantics stay cleanly isolated from the append-only broker
ledger.

---

## 8. Real-Mongo Round-Trip Test (Mandated)

`tests/test_mongo_outbox.py` exercises the repository against **real Mongo**
(rs0 @ localhost:27017). A FakeCollection test is explicitly NOT sufficient to
prove StrEnum rehydration works.

The test specifically includes a **U-D6b regression guard**: after `mark_sent`,
a **fresh** `MongoOutboxRepository` instance reads the row and asserts
`entry.status is OutboxStatus.SENT` (identity, not `==`). This mirrors the
exact check in the dispatcher's SENT short-circuit and would fail if the
StrEnum were returned as a plain str.

Tests skip cleanly (`pytest.mark.skipif`) when Mongo is unavailable so the
general CI suite does not regress.

---

## 9. Red Lines Not Changed

- P1-5 §2 red line 1: the 2 write endpoints are unchanged.
- P1-2.A append-only 8 red lines: `broker_events` / `broker_snapshots` are
  untouched.
- R0 §4 single construction point (M-004): `InstructionPlan` construction is
  unchanged.
- P0-10 LLM field permissions: no LLM writes anywhere near the outbox.
- P0-2 / P0-4 Feishu red lines: the dispatcher contract (`try_claim` /
  `release` / `mark_sent`) is unchanged; only the backing store is swapped.
