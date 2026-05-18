"""ExemplarSelector — FinMem-style in-context exemplar retrieval (P2-2 §1.5 + X-006).

Pulls a tiny number of historical successful decisions out of
``decision_ledger`` and shapes them into a prompt-ready list of
:class:`ExemplarRecord` instances for the four mandatory agents
(``fundamental_analyst`` / ``technical_analyst`` / ``risk_officer`` /
``fund_manager``). The selection pipeline is intentionally a fixed
four-stage filter — *time window → outcome stratify → vector
similarity → agent-specific shaping* — because FinMem-style
exemplars are cheap to over-prompt with, and the
``arxiv:2509.13196`` over-prompting dilemma is the dominant failure
mode the cap at ``k <= 3`` protects against.

Architectural invariants:

* **k <= 3 hard cap** (P2-2 §1.1.1 lock). Anything larger raises
  :class:`ExemplarKCapExceededError`.
* **No LLM rerank** — would punch through the P1-7 cost guard and
  defeat the FinMem rationale (cheap retrieval, not a second LLM
  pass). Selection uses cosine similarity on locally-computed
  Qwen3 embeddings only.
* **Zero new collection** — the exemplar set is derived from
  ``decision_ledger`` via an aggregation/projection that lives in
  the calling code (X-008 wires the Mongo aggregation); this
  module accepts an :class:`ExemplarStore` Protocol so test doubles
  and the X-008 implementation share the same surface.
* **Per-agent stratification** (P2-2 §1.10 Q11):

  | Agent                | Layer split         | Anti-exemplar required |
  | -------------------- | ------------------- | ---------------------- |
  | fundamental_analyst  | deep 2 + interm. 1  | No                     |
  | technical_analyst    | shallow 3           | No                     |
  | risk_officer         | shallow 2 + anti 1  | Yes (>=1)              |
  | fund_manager         | each layer 1        | No                     |

* **Embedding model lazy-loaded** at construction via the
  ``EmbeddingModel`` Protocol so unit tests can inject a
  deterministic stub instead of paying the 600 MB Qwen3-Embedding-0.6B
  load cost on every pytest invocation.
* **Zero ``backend.{api, broker, risk, llm, agents, mirofish, data}``
  imports** — Phase X module isolation (P2-2 §2 red line 17).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------

MAX_K = 3
"""Hard cap on exemplars per prompt (P2-2 §1.1.1; over-prompting
dilemma arxiv:2509.13196 + DeepSeek triage 8k context cap)."""

DEFAULT_WINDOW_DAYS = 90
"""Lookback window for candidate exemplars (P2-2 §1.5 Q5)."""

EMBEDDING_DIM = 1024
"""Qwen3-Embedding-0.6B output dimension. Locked here so test doubles
that produce vectors of the wrong shape fail loudly."""

DEFAULT_MODEL_PATH = Path("data/models/Qwen3-Embedding-0.6B")
"""Repo-relative directory the model weights are downloaded into via
``huggingface-cli download Qwen/Qwen3-Embedding-0.6B --local-dir
data/models/Qwen3-Embedding-0.6B``."""

AGENT_NAMES: tuple[str, ...] = (
    "fundamental_analyst",
    "technical_analyst",
    "risk_officer",
    "fund_manager",
)
"""Four mandatory agents (P0-10 §1.1). Selection strategy keyed by these."""

LAYER_NAMES: tuple[str, ...] = ("shallow", "intermediate", "deep")
"""FinMem decay layers (P2-2 §1.9)."""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ExemplarSelectorError(Exception):
    """Base for exemplar-selector failures."""


class ExemplarKCapExceededError(ExemplarSelectorError):
    """Raised when a caller asks for more than :data:`MAX_K` exemplars."""


class UnknownAgentRoleError(ExemplarSelectorError):
    """Raised when an agent role is not one of the four mandatory agents."""


class EmbeddingModelNotReadyError(ExemplarSelectorError):
    """Raised by the loader when the Qwen3 weights are missing on disk."""


# ---------------------------------------------------------------------------
# Schema — ExemplarRecord (14 fields, P2-2 §1.5 Q6)
# ---------------------------------------------------------------------------


class ExemplarRecord(BaseModel):
    """One in-context exemplar (frozen + strict + ``extra='forbid'``).

    Derived from a single ``decision_ledger`` entry. ``embedding`` is
    optional because it can be recomputed on demand; ``is_anti_exemplar``
    is the surprising-on-purpose flag the risk-officer stratifier relies
    on for the "include >= 1 anti-exemplar" rule (P2-2 §1.5 Q6).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    # --- Identity (3) ---
    exemplar_id: str = Field(min_length=1, max_length=160)
    instruction_id: str = Field(min_length=1, max_length=160)
    decision_date: date

    # --- Decision context (4) ---
    agent_role: Literal[
        "fundamental_analyst",
        "technical_analyst",
        "risk_officer",
        "fund_manager",
    ]
    stock_code: str = Field(min_length=1, max_length=16)
    action: Literal["BUY", "SELL", "HOLD"]
    reasoning_excerpt: str = Field(max_length=2_000)

    # --- Evidence + confidence (2) ---
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    confidence_at_decision: float = Field(ge=0.0, le=1.0)

    # --- Outcome feedback (2) ---
    outcome: Literal["profit", "loss", "neutral", "pending"]
    layer: Literal["shallow", "intermediate", "deep"]

    # --- Mandatory extensions (3, Q6 lock) ---
    outcome_pnl_bp: int | None = None
    embedding: tuple[float, ...] | None = None
    is_anti_exemplar: bool = False

    @model_validator(mode="after")
    def _check_invariants(self) -> ExemplarRecord:
        if self.embedding is not None and len(self.embedding) != EMBEDDING_DIM:
            raise ValueError(
                f"embedding must have {EMBEDDING_DIM} dimensions, "
                f"got {len(self.embedding)}"
            )
        # Pending outcomes cannot also be anti-exemplars — anti-exemplar
        # status is decided after the loss/profit settles.
        if self.outcome == "pending" and self.is_anti_exemplar:
            raise ValueError(
                "pending outcomes cannot be marked is_anti_exemplar; "
                "anti-exemplar status is decided post-settlement"
            )
        return self


# ---------------------------------------------------------------------------
# Embedding model — Protocol + local Qwen3 implementation
# ---------------------------------------------------------------------------


class EmbeddingModel(Protocol):
    """Minimum interface the selector needs from an embedding backend.

    Two methods so tests can inject a deterministic stub:

    * ``embed(text)`` — single-string variant; returns a tuple of
      :data:`EMBEDDING_DIM` floats.
    * ``embed_batch(texts)`` — batched variant; returns a tuple of
      same-shape vectors. Default implementations may simply loop
      over ``embed``.
    """

    def embed(self, text: str) -> tuple[float, ...]: ...

    def embed_batch(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


@dataclass(frozen=True)
class LocalQwen3Embedding:
    """Local Qwen3-Embedding-0.6B wrapper using ``sentence_transformers``.

    Construction is lazy — the heavy ``SentenceTransformer`` import
    happens inside ``_lazy_model`` so tests that never call
    ``embed()`` (or that inject the stub via the Protocol) don't pay
    the load cost.

    Production wiring loads the model exactly once at boot and
    passes the instance into the selector. The frozen dataclass is
    safe because ``_lazy_model`` caches the loaded model in an
    object-attribute via ``object.__setattr__`` (the standard
    "lazy on frozen" idiom).
    """

    model_dir: Path = DEFAULT_MODEL_PATH
    device: str = "cpu"

    @classmethod
    def fail_fast_validate(cls, model_dir: Path | str = DEFAULT_MODEL_PATH) -> None:
        """Boot-time check: model directory exists and looks like a
        sentence-transformers checkpoint.

        We do not load the full weights here (boot is hot path); we
        only verify the layout so a misconfigured deploy refuses to
        start. The actual ``SentenceTransformer(...)`` load happens
        lazily on the first ``embed`` call.
        """
        root = Path(model_dir)
        if not root.is_dir():
            raise EmbeddingModelNotReadyError(
                f"Qwen3 embedding model directory {root} is missing; "
                f"run `huggingface-cli download Qwen/Qwen3-Embedding-0.6B "
                f"--local-dir {root}` before boot"
            )
        # sentence-transformers checkpoints always carry a config.json
        # (model architecture) and at least one weights file.
        if not (root / "config.json").is_file():
            raise EmbeddingModelNotReadyError(
                f"Qwen3 embedding model directory {root} is missing "
                f"config.json — re-run huggingface-cli download to "
                f"restore the checkpoint"
            )

    def _lazy_model(self) -> object:
        cached = getattr(self, "_cached_model", None)
        if cached is not None:
            return cached
        # Imported here so the rest of the module is import-safe even
        # when sentence-transformers is not installed (tests rely on
        # the stub Protocol implementation).
        from sentence_transformers import SentenceTransformer

        self.fail_fast_validate(self.model_dir)
        model = SentenceTransformer(str(self.model_dir), device=self.device)
        object.__setattr__(self, "_cached_model", model)
        return model

    def embed(self, text: str) -> tuple[float, ...]:
        return self.embed_batch([text])[0]

    def embed_batch(
        self, texts: Sequence[str]
    ) -> tuple[tuple[float, ...], ...]:
        model = self._lazy_model()
        # SentenceTransformer.encode returns numpy 2-D array under the
        # default normalize_embeddings=False; we explicitly normalize
        # so downstream cosine similarity is a plain dot product.
        encoded = model.encode(  # type: ignore[attr-defined]
            list(texts),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return tuple(tuple(float(v) for v in row) for row in encoded)


# ---------------------------------------------------------------------------
# Candidate store — Protocol bridging decision_ledger to the selector
# ---------------------------------------------------------------------------


class ExemplarStore(Protocol):
    """Returns the candidate pool for a given window.

    The X-008 EvolutionDispatcher implements this via a Mongo
    aggregation over ``decision_ledger`` joined with
    ``instruction_plans`` + ``execution_reports`` +
    ``agent_debate_records``. Tests inject an in-memory list.
    """

    def candidates(
        self,
        *,
        agent_role: str,
        as_of: date,
        window_days: int,
    ) -> Sequence[ExemplarRecord]: ...


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AgentStrategy:
    layer_distribution: dict[str, int]
    require_anti_exemplar: bool


_AGENT_STRATEGIES: dict[str, _AgentStrategy] = {
    "fundamental_analyst": _AgentStrategy(
        layer_distribution={"deep": 2, "intermediate": 1},
        require_anti_exemplar=False,
    ),
    "technical_analyst": _AgentStrategy(
        layer_distribution={"shallow": 3},
        require_anti_exemplar=False,
    ),
    "risk_officer": _AgentStrategy(
        layer_distribution={"shallow": 2},
        require_anti_exemplar=True,
    ),
    "fund_manager": _AgentStrategy(
        layer_distribution={"shallow": 1, "intermediate": 1, "deep": 1},
        require_anti_exemplar=False,
    ),
}


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Dot product; assumes both inputs are unit-normalised vectors.

    ``LocalQwen3Embedding`` normalises by default, and the stub
    embeddings used in tests also produce unit vectors, so a bare
    dot product matches cosine similarity exactly.
    """
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


@dataclass(frozen=True)
class ExemplarSelector:
    """Returns up to :data:`MAX_K` exemplars for the requested agent.

    Frozen so swapping the embedding model at runtime is a setattr
    error — the registry pattern (one instance constructed at boot)
    is the only sanctioned wiring.
    """

    embedding_model: EmbeddingModel
    store: ExemplarStore
    window_days: int = DEFAULT_WINDOW_DAYS

    def retrieve(
        self,
        *,
        agent_role: str,
        query_context: str,
        as_of: date | datetime | None = None,
        k: int = MAX_K,
    ) -> tuple[ExemplarRecord, ...]:
        """Return up to ``k`` exemplars matched to ``query_context``.

        Five steps in order:

        1. Cap ``k`` at :data:`MAX_K`; raise on overshoot.
        2. Look up the per-agent strategy; raise on unknown agent.
        3. Ask the store for candidates inside the window.
        4. Score candidates by cosine similarity against the query
           embedding (Qwen3-normalised inputs => dot product is
           cosine).
        5. Layer-stratified top-k:
           - Bucket candidates by layer.
           - Take the strategy's quota from each bucket in similarity
             order.
           - For risk_officer, force at least one anti-exemplar in
             the final tuple (replacing the weakest non-anti slot if
             needed).
        """
        if k > MAX_K:
            raise ExemplarKCapExceededError(
                f"k={k} exceeds hard cap MAX_K={MAX_K}; over-prompting "
                f"dilemma (arxiv:2509.13196) makes large in-context "
                f"sets actively harmful"
            )
        if k <= 0:
            return ()
        if agent_role not in _AGENT_STRATEGIES:
            raise UnknownAgentRoleError(
                f"agent_role {agent_role!r} is not one of {AGENT_NAMES}"
            )
        strategy = _AGENT_STRATEGIES[agent_role]
        as_of_date = self._as_of_date(as_of)

        candidates = list(
            self.store.candidates(
                agent_role=agent_role,
                as_of=as_of_date,
                window_days=self.window_days,
            )
        )
        if not candidates:
            return ()

        query_vec = self.embedding_model.embed(query_context)
        # Score every candidate; missing embeddings fall back to 0.0
        # so they participate but lose to anything with an embedding.
        scored: list[tuple[float, ExemplarRecord]] = []
        for cand in candidates:
            if cand.embedding is None:
                score = 0.0
            else:
                score = _cosine(query_vec, cand.embedding)
            scored.append((score, cand))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        # Layer stratify: pull from each layer in descending similarity.
        buckets: dict[str, list[tuple[float, ExemplarRecord]]] = {
            layer: [] for layer in LAYER_NAMES
        }
        for score, cand in scored:
            buckets[cand.layer].append((score, cand))

        chosen: list[ExemplarRecord] = []
        seen_ids: set[str] = set()
        for layer, quota in strategy.layer_distribution.items():
            for _, cand in buckets[layer][:quota]:
                if cand.exemplar_id in seen_ids:
                    continue
                chosen.append(cand)
                seen_ids.add(cand.exemplar_id)

        # If we under-filled (e.g. risk_officer has only one shallow
        # candidate), backfill from the overall scored list until k.
        if len(chosen) < k:
            for _, cand in scored:
                if cand.exemplar_id in seen_ids:
                    continue
                chosen.append(cand)
                seen_ids.add(cand.exemplar_id)
                if len(chosen) >= k:
                    break

        chosen = chosen[:k]

        if strategy.require_anti_exemplar and not any(
            c.is_anti_exemplar for c in chosen
        ):
            chosen = self._force_anti_exemplar(chosen, scored, k=k)

        return tuple(chosen)

    @staticmethod
    def _as_of_date(as_of: date | datetime | None) -> date:
        if as_of is None:
            return date.today()
        if isinstance(as_of, datetime):
            return as_of.date()
        return as_of

    @staticmethod
    def _force_anti_exemplar(
        chosen: list[ExemplarRecord],
        scored: list[tuple[float, ExemplarRecord]],
        *,
        k: int,
    ) -> list[ExemplarRecord]:
        """Guarantee at least one anti-exemplar in the risk_officer set.

        Replaces the weakest non-anti exemplar with the strongest
        candidate that is_anti_exemplar=True. If no anti-exemplar
        exists in the candidate pool, the selection is returned
        unchanged — the caller (X-013 amendment drafter) is
        responsible for surfacing this to the operator as a
        candidate-pool deficiency.
        """
        best_anti = next(
            (cand for _, cand in scored if cand.is_anti_exemplar),
            None,
        )
        if best_anti is None:
            return chosen
        if best_anti in chosen:
            return chosen
        # Drop the weakest non-anti exemplar (chosen is ordered by the
        # stratified pull above; weakest is the last element).
        chosen_without_anti = [c for c in chosen if not c.is_anti_exemplar]
        if chosen_without_anti:
            chosen.remove(chosen_without_anti[-1])
        chosen.append(best_anti)
        return chosen[:k]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_unit_vector(seed: int) -> tuple[float, ...]:
    """Deterministic :data:`EMBEDDING_DIM`-sized unit vector for tests.

    Pure Python (no numpy). Returns a vector that is reproducibly
    distinct for each ``seed`` so test assertions about similarity
    ordering are stable across runs.
    """
    raw = [math.sin((seed + i) * 0.01 + 0.5) for i in range(EMBEDDING_DIM)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return tuple(v / norm for v in raw)


def date_window(as_of: date, window_days: int = DEFAULT_WINDOW_DAYS) -> date:
    """Earliest date inside the candidate window — used by ExemplarStore impls."""
    return as_of - timedelta(days=window_days)


def list_candidate_pool(
    *,
    candidates: Iterable[ExemplarRecord],
    agent_role: str,
    as_of: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[ExemplarRecord]:
    """In-memory candidate filter shared by tests and reference impls.

    Used directly by :class:`InMemoryExemplarStore` and by the X-008
    Mongo aggregation as a final sanity pass.
    """
    earliest = date_window(as_of, window_days)
    return [
        cand
        for cand in candidates
        if cand.agent_role == agent_role
        and earliest <= cand.decision_date <= as_of
    ]


@dataclass
class InMemoryExemplarStore:
    """Reference :class:`ExemplarStore` for tests and bootstrap deploys.

    Production wiring (X-008) replaces this with a Mongo aggregation
    over decision_ledger; this in-memory variant exists so the
    selector can be regressed without a Mongo dependency.
    """

    pool: list[ExemplarRecord]
    window_days: int = DEFAULT_WINDOW_DAYS

    def candidates(
        self,
        *,
        agent_role: str,
        as_of: date,
        window_days: int,
    ) -> list[ExemplarRecord]:
        return list_candidate_pool(
            candidates=self.pool,
            agent_role=agent_role,
            as_of=as_of,
            window_days=window_days,
        )


__all__ = [
    "AGENT_NAMES",
    "DEFAULT_MODEL_PATH",
    "DEFAULT_WINDOW_DAYS",
    "EMBEDDING_DIM",
    "EmbeddingModel",
    "EmbeddingModelNotReadyError",
    "ExemplarKCapExceededError",
    "ExemplarRecord",
    "ExemplarSelector",
    "ExemplarSelectorError",
    "ExemplarStore",
    "InMemoryExemplarStore",
    "LAYER_NAMES",
    "LocalQwen3Embedding",
    "MAX_K",
    "UnknownAgentRoleError",
    "date_window",
    "list_candidate_pool",
    "make_unit_vector",
]
