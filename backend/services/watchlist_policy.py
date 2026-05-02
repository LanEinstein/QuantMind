"""Fast/Slow watchlist categorisation policy (Phase 5B-T02).

A single immutable :class:`WatchlistPolicy` describes both buckets:

* ``fast`` runs intraday on a 4-tick cron with a tighter pipeline
  (``max_debate_rounds=1``, ~480s timeout) for short-horizon names.
* ``slow`` runs once per trading day with a deeper pipeline
  (``max_debate_rounds=2``, ~900s timeout) for long-horizon names.

The policy is loaded from ``config/watchlist_policy.yaml`` (template in
SSoT §2.7) and consumed by :class:`backend.data.analysis_scheduler.
AnalysisScheduler`. The YAML is the contract; this module only
validates and exposes it as a frozen dataclass so the scheduler can
hand each cron job its own pipeline knobs without touching disk.

Why a separate module instead of inlining into the scheduler:
- ``assign_category`` is pure-function logic worth unit testing in
  isolation (overrides win, default fallback, fast vs slow precedence).
- The API endpoint that mutates per-code overrides reuses the loader
  to round-trip the file safely.
- Follows the same pattern Phase 5A established for
  ``cost_guard`` / ``authorization`` — extract small services out of
  the scheduler so each piece is independently testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog
import yaml

log = structlog.get_logger(component="watchlist_policy")

Category = Literal["fast", "slow"]
_VALID_CATEGORIES: tuple[Category, ...] = ("fast", "slow")


class WatchlistPolicyError(ValueError):
    """Raised when ``watchlist_policy.yaml`` fails validation."""


@dataclass(frozen=True)
class BucketConfig:
    """Per-bucket cron + pipeline knobs.

    Both buckets share the same shape — what differs is the values
    (cron cadence, debate depth, timeout). Storing them as one
    dataclass keeps the schema honest: the loader cannot accidentally
    load a partial bucket.

    ``pipeline`` is RESERVED for Phase 5B-T03 / Phase 5C: it carries
    an opaque identifier that future routing logic will use to pick
    a graph variant (e.g. ``fast_pipeline`` may skip Stage-2 debate).
    The scheduler currently only consumes ``max_debate_rounds`` and
    ``pipeline_timeout_seconds`` — keep the YAML values stable so
    later phases can wire it up without a config break.
    """

    cron: str
    pipeline: str
    max_debate_rounds: int
    pipeline_timeout_seconds: int
    default_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WatchlistPolicy:
    """Immutable view of ``watchlist_policy.yaml``.

    ``overrides`` maps stock_code → category and wins over
    ``default_category``. ``policy_version`` lets future schema bumps
    be detected before silently misreading old YAML.

    The two ``*_default_set`` frozensets are derived caches built at
    construction (in :func:`load_policy`) so per-tick membership
    checks in :func:`assign_category` stay O(1) even if the YAML
    grows long lists of default codes.
    """

    fast: BucketConfig
    slow: BucketConfig
    overrides: dict[str, Category] = field(default_factory=dict)
    default_category: Category = "slow"
    policy_version: int = 1
    last_updated: str | None = None
    fast_default_set: frozenset[str] = field(default_factory=frozenset)
    slow_default_set: frozenset[str] = field(default_factory=frozenset)

    def cron_for(self, category: Category) -> str:
        """Return the cron string for ``fast`` or ``slow``."""
        return self.fast.cron if category == "fast" else self.slow.cron

    def bucket_for(self, category: Category) -> BucketConfig:
        """Return the BucketConfig for ``fast`` or ``slow``."""
        return self.fast if category == "fast" else self.slow


def _coerce_bucket(name: str, raw: Any) -> BucketConfig:
    """Validate one bucket subdocument into a BucketConfig."""
    if not isinstance(raw, dict):
        raise WatchlistPolicyError(
            f"watchlist_policy.{name} must be a mapping, got {type(raw).__name__}"
        )
    required = ("cron", "pipeline", "max_debate_rounds", "pipeline_timeout_seconds")
    missing = [k for k in required if k not in raw]
    if missing:
        raise WatchlistPolicyError(
            f"watchlist_policy.{name} missing required keys: {missing}"
        )

    rounds = raw["max_debate_rounds"]
    timeout = raw["pipeline_timeout_seconds"]
    if not isinstance(rounds, int) or rounds < 0:
        raise WatchlistPolicyError(
            f"watchlist_policy.{name}.max_debate_rounds must be a non-negative int"
        )
    if not isinstance(timeout, int) or timeout <= 0:
        raise WatchlistPolicyError(
            f"watchlist_policy.{name}.pipeline_timeout_seconds must be a positive int"
        )

    default_codes_raw = raw.get("default_codes", []) or []
    if not isinstance(default_codes_raw, list):
        raise WatchlistPolicyError(
            f"watchlist_policy.{name}.default_codes must be a list"
        )
    default_codes: tuple[str, ...] = tuple(str(c) for c in default_codes_raw)

    return BucketConfig(
        cron=str(raw["cron"]),
        pipeline=str(raw["pipeline"]),
        max_debate_rounds=rounds,
        pipeline_timeout_seconds=timeout,
        default_codes=default_codes,
    )


def _coerce_overrides(raw: Any) -> dict[str, Category]:
    """Validate the ``overrides`` mapping.

    Stock codes are normalised to strings (YAML may parse pure-numeric
    codes like ``600519`` as ints). Category strings are checked against
    the literal set so a typo (``"fas"``) fails loudly at load time.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise WatchlistPolicyError(
            f"watchlist_policy.overrides must be a mapping, got {type(raw).__name__}"
        )
    out: dict[str, Category] = {}
    for code, category in raw.items():
        code_str = str(code)
        if category not in _VALID_CATEGORIES:
            raise WatchlistPolicyError(
                f"watchlist_policy.overrides[{code_str}] must be 'fast' or 'slow', "
                f"got {category!r}"
            )
        out[code_str] = category  # type: ignore[assignment]
    return out


def _coerce_default(raw: Any) -> Category:
    if raw is None:
        return "slow"
    if raw not in _VALID_CATEGORIES:
        raise WatchlistPolicyError(
            f"watchlist_policy.default_category must be 'fast' or 'slow', got {raw!r}"
        )
    return raw  # type: ignore[return-value]


def load_policy(path: str | Path) -> WatchlistPolicy:
    """Load and validate ``watchlist_policy.yaml`` into a WatchlistPolicy.

    Performs structural validation up front so a malformed file fails
    on startup rather than at the first cron firing. Codes that appear
    in BOTH ``fast.default_codes`` and ``slow.default_codes`` are
    rejected — a code can only belong to one bucket.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        WatchlistPolicyError: If the YAML schema is invalid.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"watchlist policy file not found: {p}")

    try:
        with p.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        # Wrap PyYAML's internal exception so callers (main lifespan,
        # API handlers) can catch a single project-defined error type
        # instead of importing yaml just for the failure mode.
        raise WatchlistPolicyError(
            f"watchlist_policy.yaml is not valid YAML: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise WatchlistPolicyError(
            f"watchlist_policy.yaml root must be a mapping, got {type(raw).__name__}"
        )

    if "fast" not in raw or "slow" not in raw:
        raise WatchlistPolicyError(
            "watchlist_policy.yaml must define both 'fast' and 'slow' buckets"
        )

    fast = _coerce_bucket("fast", raw["fast"])
    slow = _coerce_bucket("slow", raw["slow"])

    overlap = set(fast.default_codes) & set(slow.default_codes)
    if overlap:
        raise WatchlistPolicyError(
            f"codes appear in both fast.default_codes and slow.default_codes: "
            f"{sorted(overlap)}"
        )

    overrides = _coerce_overrides(raw.get("overrides"))
    default_category = _coerce_default(raw.get("default_category"))

    version_raw = raw.get("policy_version", 1)
    if not isinstance(version_raw, int):
        raise WatchlistPolicyError(
            "watchlist_policy.policy_version must be an int"
        )

    last_updated = raw.get("last_updated")
    if last_updated is not None and not isinstance(last_updated, str):
        last_updated = str(last_updated)

    policy = WatchlistPolicy(
        fast=fast,
        slow=slow,
        overrides=overrides,
        default_category=default_category,
        policy_version=version_raw,
        last_updated=last_updated,
        fast_default_set=frozenset(fast.default_codes),
        slow_default_set=frozenset(slow.default_codes),
    )
    log.info(
        "watchlist_policy_loaded",
        path=str(p),
        fast_default_count=len(fast.default_codes),
        slow_default_count=len(slow.default_codes),
        overrides_count=len(overrides),
        version=version_raw,
    )
    return policy


_NO_OVERRIDE: Category | None = None


def assign_category(code: str, policy: WatchlistPolicy) -> Category:
    """Return ``'fast'`` or ``'slow'`` for a single stock code.

    Resolution order (first match wins):
      1. ``policy.overrides[code]``
      2. ``code in policy.fast_default_set``  → fast
      3. ``code in policy.slow_default_set``  → slow
      4. ``policy.default_category``

    Uses ``dict.get`` (single hash) and frozenset membership (O(1))
    so a per-tick partition over a large watchlist stays cheap.
    """
    override = policy.overrides.get(code)
    if override is not None:
        return override
    if code in policy.fast_default_set:
        return "fast"
    if code in policy.slow_default_set:
        return "slow"
    return policy.default_category


def partition_watchlist(
    codes: list[str], policy: WatchlistPolicy
) -> tuple[list[str], list[str]]:
    """Split ``codes`` into ``(fast_codes, slow_codes)`` lists.

    Order is preserved so downstream logging and rate-limiting stays
    deterministic across runs.
    """
    fast_codes: list[str] = []
    slow_codes: list[str] = []
    for code in codes:
        if assign_category(code, policy) == "fast":
            fast_codes.append(code)
        else:
            slow_codes.append(code)
    return fast_codes, slow_codes


def update_override(
    policy: WatchlistPolicy, code: str, category: Category | None
) -> WatchlistPolicy:
    """Return a new policy with ``code`` set to ``category`` (or removed).

    Pure function — does NOT touch disk. Callers that want persistence
    should follow with :func:`save_policy`. Passing ``category=None``
    removes any existing override for ``code`` so it falls back to the
    default rules.
    """
    if category is not None and category not in _VALID_CATEGORIES:
        raise WatchlistPolicyError(
            f"category must be 'fast', 'slow', or None; got {category!r}"
        )
    new_overrides = dict(policy.overrides)
    if category is None:
        new_overrides.pop(code, None)
    else:
        new_overrides[code] = category
    return WatchlistPolicy(
        fast=policy.fast,
        slow=policy.slow,
        overrides=new_overrides,
        default_category=policy.default_category,
        policy_version=policy.policy_version,
        last_updated=policy.last_updated,
        fast_default_set=policy.fast_default_set,
        slow_default_set=policy.slow_default_set,
    )


def save_policy(policy: WatchlistPolicy, path: str | Path) -> None:
    """Persist ``policy`` back to ``path`` as YAML.

    Round-trip safe: ``load_policy(save_policy(p))`` yields an equal
    policy. Comments in the source file are NOT preserved (PyYAML
    limitation) — operators editing the file by hand should expect the
    canonical re-emission.
    """
    p = Path(path)
    payload: dict[str, Any] = {
        "fast": _bucket_to_dict(policy.fast),
        "slow": _bucket_to_dict(policy.slow),
        "overrides": dict(policy.overrides),
        "default_category": policy.default_category,
        "policy_version": policy.policy_version,
    }
    if policy.last_updated is not None:
        payload["last_updated"] = policy.last_updated

    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)
    tmp.replace(p)


def _bucket_to_dict(bucket: BucketConfig) -> dict[str, Any]:
    return {
        "cron": bucket.cron,
        "pipeline": bucket.pipeline,
        "max_debate_rounds": bucket.max_debate_rounds,
        "pipeline_timeout_seconds": bucket.pipeline_timeout_seconds,
        "default_codes": list(bucket.default_codes),
    }
