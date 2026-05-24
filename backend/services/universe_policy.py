"""P0-9 + P0-9-amendment-2026-05-24 locked universe policy: schema + loader.

The single :class:`UniversePolicy` aggregate captures the full-market
*ruleset* the 2026-05-24 two-line rearchitecture locked: the universe is
no longer 13 enumerated codes but a board whitelist
(沪主 / 深主 / 创业板 / ETF) with a permanently-forbidden board set
(科创 688 / 北交 8 / ST / 可转债), plus the unchanged four exclusion
thresholds, the 5-instruction daily cap (traditional 4 + event 1 with a
14:30 slide), the fast/slow scheduling cadence, and the strict long-only
direction policy.

Every field is frozen and runtime-immutable. There is no sanctioned
in-process mutation path — changes go through ``git diff`` + a
``docs/decisions/P0-9-amendment-*`` doc + a process restart (P0-9 §1.3 /
P0-7 §1.4).

The loader rejects v1/v2 YAML loudly: it requires ``policy_version: 3``
and the ``universe`` ruleset section. The dead 13-code sections
(``watchlist`` composition / ``required_etfs`` triplet /
``watchlist_size_must_equal``) are gone — a v2 file fails fast at boot
rather than silently honouring invariants the amendment removed.

``assign_category`` / ``partition_watchlist`` remain pure functions so
the scheduler can categorise a tick of dynamically-screened codes
without touching disk. ``all_watchlist_codes`` now returns only the
manually-pinned fast/slow codes (empty by default in full-market mode).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog
import yaml

log = structlog.get_logger(component="universe_policy")

Category = Literal["fast", "slow"]
_VALID_CATEGORIES: tuple[Category, ...] = ("fast", "slow")

# P0-9 §4.1 forbidden InstructionSide values — locked exact set.
FORBIDDEN_SIDES: frozenset[str] = frozenset(
    {"SHORT", "COVER", "MARGIN_BUY", "REVERSE_REPO", "ETF_SUBSCRIBE", "ETF_REDEEM"}
)

# P0-9-amendment-2026-05-24 §2.1 — the exact set of allowed `Board` enum
# values (backend/data/stock_metadata.py Board). Locked: narrowing this
# is honoured downstream (Builder 5th early-return) but widening it past
# these four boards requires an amendment (科创/北交/ST/可转债 永禁).
BOARD_WHITELIST: frozenset[str] = frozenset({"sh_main", "sz_main", "chuangye", "etf"})

# P0-9-amendment-2026-05-24 §2.1 — the permanently-forbidden board set,
# mirroring the data-layer `classify_board` ForbiddenCodeError reasons.
# Locked exactly: this is the grep target the redline check asserts is
# never narrowed (科创 688 / 北交 8 / ST / 可转债 永禁, P0-7 §2.4).
FORBIDDEN_BOARDS: frozenset[str] = frozenset(
    {"kechuang_688", "beijiao_8", "st", "convertible_bond"}
)

# P0-9 §3.1 cap-allocation lock (also mirrors P0-7 max_daily_new=5).
LOCKED_TOTAL_DAILY_CAP: int = 5
LOCKED_TRADITIONAL_CAP: int = 4
LOCKED_EVENT_CAP: int = 1
LOCKED_RESERVED_CAP_RELEASE_TIME: str = "14:30"

# P0-9 §2.1 exclusion-rule thresholds — locked exactly (UNCHANGED by the
# 2026-05-24 amendment, only the enforcement *location* moved to
# backend/screening). A YAML edit that drifts any of these without a
# matching code change would silently widen / narrow which candidates the
# screener and the InstructionPlan builder reject; requiring lock-step
# changes forces the amendment + git-diff discipline (§2.4).
LOCKED_EXCLUSION_RULES: dict[str, int | float] = {
    "ipo_min_trading_days": 30,
    "sub_new_min_trading_days": 180,
    "min_avg_amount_20d_yuan": 200_000_000,
    "max_unit_price_yuan": 500.0,
}


class UniversePolicyError(ValueError):
    """Raised when ``universe_policy.yaml`` fails P0-9 (+amendment) validation."""


# ---------------------------------------------------------------------------
# Dataclasses (all frozen — runtime-immutable per P0-9 §1.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BucketConfig:
    """Per-bucket cron + pipeline knobs (fast or slow)."""

    cron: str
    pipeline: str
    max_debate_rounds: int
    pipeline_timeout_seconds: int
    default_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class UniverseRules:
    """The full-market universe ruleset (board whitelist + forbidden set).

    Replaces the v2 ``WatchlistComposition`` + ``required_etfs`` triplet.
    ``board_whitelist`` is the exact set of allowed boards;
    ``forbidden_boards`` mirrors the data-layer forbidden set so audit /
    redline tooling has a declared-intent source to grep against.
    """

    board_whitelist: frozenset[str] = field(default_factory=lambda: BOARD_WHITELIST)
    forbidden_boards: frozenset[str] = field(default_factory=lambda: FORBIDDEN_BOARDS)


@dataclass(frozen=True)
class ExclusionRules:
    """Four exclusion thresholds — screening hard-filter + Builder 5th early-return."""

    ipo_min_trading_days: int = 30
    sub_new_min_trading_days: int = 180
    min_avg_amount_20d_yuan: int = 200_000_000
    max_unit_price_yuan: float = 500.0


@dataclass(frozen=True)
class CapAllocation:
    """Daily 5-instruction cap split between traditional and event paths."""

    total_daily_cap: int = LOCKED_TOTAL_DAILY_CAP
    traditional_path_default_cap: int = LOCKED_TRADITIONAL_CAP
    event_path_reserved_cap: int = LOCKED_EVENT_CAP
    reserved_cap_release_time: str = "14:30"


@dataclass(frozen=True)
class DirectionPolicy:
    """Long-only side restrictions + ETF arbitrage P1 reservation."""

    long_only: bool = True
    forbidden_sides: frozenset[str] = field(default_factory=lambda: FORBIDDEN_SIDES)
    etf_arbitrage_enabled: bool = False


@dataclass(frozen=True)
class UniversePolicy:
    """Aggregate P0-9 (+amendment) policy view.

    ``fast_default_set`` / ``slow_default_set`` are derived O(1) lookup
    caches built by :func:`load_policy` so per-tick partitioning stays
    cheap regardless of list length.
    """

    fast: BucketConfig
    slow: BucketConfig
    overrides: dict[str, Category] = field(default_factory=dict)
    default_category: Category = "slow"
    policy_version: int = 3
    last_updated: str | None = None
    locked_decision: str = "P0-9"
    universe: UniverseRules = field(default_factory=UniverseRules)
    exclusion_rules: ExclusionRules = field(default_factory=ExclusionRules)
    cap_allocation: CapAllocation = field(default_factory=CapAllocation)
    direction_policy: DirectionPolicy = field(default_factory=DirectionPolicy)
    fast_default_set: frozenset[str] = field(default_factory=frozenset)
    slow_default_set: frozenset[str] = field(default_factory=frozenset)

    def cron_for(self, category: Category) -> str:
        return self.fast.cron if category == "fast" else self.slow.cron

    def bucket_for(self, category: Category) -> BucketConfig:
        return self.fast if category == "fast" else self.slow

    def is_board_whitelisted(self, board: str) -> bool:
        """True if ``board`` (a ``Board`` value string) is in the whitelist.

        Used by the InstructionPlanBuilder 5th early-return as the
        last-line universe membership check (replaces the v2
        membership-in-13-codes test). Narrowing the whitelist via
        amendment is enforced here even though ``classify_board`` already
        rejects forbidden boards upstream — defense-in-depth.
        """
        return board in self.universe.board_whitelist

    def all_watchlist_codes(self) -> frozenset[str]:
        """Union of every manually-pinned code in fast / slow / overrides.

        In full-market mode this is empty by default — the analysis
        universe is produced by ``backend/screening`` rather than
        enumerated here. Codes only appearing in ``overrides`` still
        count (they bind to a bucket). Used by the boot seed to reconcile
        any owner-pinned codes into the Mongo watchlist collection.
        """
        return frozenset(self.fast.default_codes).union(
            self.slow.default_codes, self.overrides.keys()
        )


# ---------------------------------------------------------------------------
# Loader — strict v3 validation
# ---------------------------------------------------------------------------


def _coerce_bucket(name: str, raw: Any) -> BucketConfig:
    if not isinstance(raw, dict):
        raise UniversePolicyError(
            f"universe_policy.{name} must be a mapping, got {type(raw).__name__}"
        )
    required = ("cron", "pipeline", "max_debate_rounds", "pipeline_timeout_seconds")
    missing = [k for k in required if k not in raw]
    if missing:
        raise UniversePolicyError(
            f"universe_policy.{name} missing required keys: {missing}"
        )

    rounds = raw["max_debate_rounds"]
    timeout = raw["pipeline_timeout_seconds"]
    if not isinstance(rounds, int) or rounds < 0:
        raise UniversePolicyError(
            f"universe_policy.{name}.max_debate_rounds must be a non-negative int"
        )
    if not isinstance(timeout, int) or timeout <= 0:
        raise UniversePolicyError(
            f"universe_policy.{name}.pipeline_timeout_seconds must be a positive int"
        )

    default_codes_raw = raw.get("default_codes", []) or []
    if not isinstance(default_codes_raw, list):
        raise UniversePolicyError(
            f"universe_policy.{name}.default_codes must be a list"
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
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise UniversePolicyError(
            f"universe_policy.overrides must be a mapping, got {type(raw).__name__}"
        )
    out: dict[str, Category] = {}
    for code, category in raw.items():
        code_str = str(code)
        if category not in _VALID_CATEGORIES:
            raise UniversePolicyError(
                f"universe_policy.overrides[{code_str}] must be 'fast' or 'slow', "
                f"got {category!r}"
            )
        out[code_str] = category  # type: ignore[assignment]
    return out


def _coerce_default(raw: Any) -> Category:
    if raw is None:
        return "slow"
    if raw not in _VALID_CATEGORIES:
        raise UniversePolicyError(
            f"universe_policy.default_category must be 'fast' or 'slow', got {raw!r}"
        )
    return raw  # type: ignore[return-value]


def _coerce_universe(raw: Any) -> UniverseRules:
    if not isinstance(raw, dict):
        raise UniversePolicyError(
            f"universe_policy.universe must be a mapping, got {type(raw).__name__}"
        )
    for key in ("board_whitelist", "forbidden_boards"):
        if key not in raw:
            raise UniversePolicyError(f"universe_policy.universe missing key: {key}")
        if not isinstance(raw[key], list):
            raise UniversePolicyError(f"universe_policy.universe.{key} must be a list")

    whitelist = frozenset(str(b) for b in raw["board_whitelist"])
    forbidden = frozenset(str(b) for b in raw["forbidden_boards"])

    # The whitelist must equal the four allowed boards exactly. Widening
    # past these (e.g. adding 'kechuang_688') would smuggle a forbidden
    # board into the tradable universe — P0-7 §2.4 / amendment §2.1 lock.
    if whitelist != BOARD_WHITELIST:
        raise UniversePolicyError(
            "universe.board_whitelist must equal "
            f"{sorted(BOARD_WHITELIST)} (P0-9-amendment-2026-05-24 §2.1 locked), "
            f"got {sorted(whitelist)}"
        )
    # The forbidden set is locked exactly so a YAML edit cannot quietly
    # drop a permanently-banned board (科创/北交/ST/可转债 永禁).
    if forbidden != FORBIDDEN_BOARDS:
        raise UniversePolicyError(
            "universe.forbidden_boards must equal "
            f"{sorted(FORBIDDEN_BOARDS)} (P0-7 §2.4 永禁 locked), "
            f"got {sorted(forbidden)}"
        )
    return UniverseRules(board_whitelist=whitelist, forbidden_boards=forbidden)


def _coerce_exclusion_rules(raw: Any) -> ExclusionRules:
    if not isinstance(raw, dict):
        raise UniversePolicyError(
            "universe_policy.exclusion_rules must be a mapping"
        )
    required = (
        "ipo_min_trading_days",
        "sub_new_min_trading_days",
        "min_avg_amount_20d_yuan",
        "max_unit_price_yuan",
    )
    missing = [k for k in required if k not in raw]
    if missing:
        raise UniversePolicyError(
            f"universe_policy.exclusion_rules missing keys: {missing}"
        )
    ipo = raw["ipo_min_trading_days"]
    sub = raw["sub_new_min_trading_days"]
    amount = raw["min_avg_amount_20d_yuan"]
    price = raw["max_unit_price_yuan"]
    if ipo != LOCKED_EXCLUSION_RULES["ipo_min_trading_days"]:
        raise UniversePolicyError(
            "exclusion_rules.ipo_min_trading_days must equal "
            f"{LOCKED_EXCLUSION_RULES['ipo_min_trading_days']} "
            f"(P0-9 §2.1 locked), got {ipo!r}"
        )
    if sub != LOCKED_EXCLUSION_RULES["sub_new_min_trading_days"]:
        raise UniversePolicyError(
            "exclusion_rules.sub_new_min_trading_days must equal "
            f"{LOCKED_EXCLUSION_RULES['sub_new_min_trading_days']} "
            f"(P0-9 §2.1 locked), got {sub!r}"
        )
    if amount != LOCKED_EXCLUSION_RULES["min_avg_amount_20d_yuan"]:
        raise UniversePolicyError(
            "exclusion_rules.min_avg_amount_20d_yuan must equal "
            f"{LOCKED_EXCLUSION_RULES['min_avg_amount_20d_yuan']} "
            f"(P0-9 §2.1 locked), got {amount!r}"
        )
    # Compare as float so YAML int (500) and float (500.0) both work,
    # but reject any other numeric drift like 499.99 or 501.
    locked_price = float(LOCKED_EXCLUSION_RULES["max_unit_price_yuan"])
    if not isinstance(price, int | float) or float(price) != locked_price:
        raise UniversePolicyError(
            "exclusion_rules.max_unit_price_yuan must equal "
            f"{locked_price} (P0-9 §2.1 locked), got {price!r}"
        )
    return ExclusionRules(
        ipo_min_trading_days=ipo,
        sub_new_min_trading_days=sub,
        min_avg_amount_20d_yuan=amount,
        max_unit_price_yuan=float(price),
    )


_RELEASE_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _coerce_cap_allocation(raw: Any) -> CapAllocation:
    if not isinstance(raw, dict):
        raise UniversePolicyError(
            "universe_policy.cap_allocation must be a mapping"
        )
    required = (
        "total_daily_cap",
        "traditional_path_default_cap",
        "event_path_reserved_cap",
        "reserved_cap_release_time",
    )
    missing = [k for k in required if k not in raw]
    if missing:
        raise UniversePolicyError(
            f"universe_policy.cap_allocation missing keys: {missing}"
        )
    total = raw["total_daily_cap"]
    traditional = raw["traditional_path_default_cap"]
    event = raw["event_path_reserved_cap"]
    release = raw["reserved_cap_release_time"]
    if total != LOCKED_TOTAL_DAILY_CAP:
        raise UniversePolicyError(
            f"cap_allocation.total_daily_cap must equal "
            f"{LOCKED_TOTAL_DAILY_CAP} (mirrors P0-7 max_daily_new), got {total!r}"
        )
    if traditional != LOCKED_TRADITIONAL_CAP:
        raise UniversePolicyError(
            f"cap_allocation.traditional_path_default_cap must equal "
            f"{LOCKED_TRADITIONAL_CAP}, got {traditional!r}"
        )
    if event != LOCKED_EVENT_CAP:
        raise UniversePolicyError(
            f"cap_allocation.event_path_reserved_cap must equal "
            f"{LOCKED_EVENT_CAP}, got {event!r}"
        )
    if traditional + event != total:
        raise UniversePolicyError(
            "cap_allocation: traditional + event cap must equal total"
        )
    if not isinstance(release, str) or not _RELEASE_TIME_RE.match(release):
        raise UniversePolicyError(
            f"cap_allocation.reserved_cap_release_time must be HH:MM (24h), "
            f"got {release!r}"
        )
    if release != LOCKED_RESERVED_CAP_RELEASE_TIME:
        raise UniversePolicyError(
            "cap_allocation.reserved_cap_release_time must equal "
            f"{LOCKED_RESERVED_CAP_RELEASE_TIME!r} "
            "(P0-9 §3.1 14:30 slide rule locked), "
            f"got {release!r}"
        )
    return CapAllocation(
        total_daily_cap=total,
        traditional_path_default_cap=traditional,
        event_path_reserved_cap=event,
        reserved_cap_release_time=release,
    )


def _coerce_direction_policy(raw: Any) -> DirectionPolicy:
    if not isinstance(raw, dict):
        raise UniversePolicyError(
            "universe_policy.direction_policy must be a mapping"
        )
    long_only = raw.get("long_only")
    if long_only is not True:
        raise UniversePolicyError(
            "direction_policy.long_only must be true (P0-9 §4.1 strict long-only)"
        )
    forbidden_raw = raw.get("forbidden_sides")
    if not isinstance(forbidden_raw, list):
        raise UniversePolicyError(
            "direction_policy.forbidden_sides must be a list"
        )
    forbidden = frozenset(str(s) for s in forbidden_raw)
    if forbidden != FORBIDDEN_SIDES:
        raise UniversePolicyError(
            "direction_policy.forbidden_sides must equal "
            f"{sorted(FORBIDDEN_SIDES)}, got {sorted(forbidden)}"
        )
    etf_arb = raw.get("etf_arbitrage_enabled")
    if etf_arb is not False:
        raise UniversePolicyError(
            "direction_policy.etf_arbitrage_enabled must be false "
            "(P0-9 §4.4 永锁 — 启用走 amendment)"
        )
    return DirectionPolicy(
        long_only=True,
        forbidden_sides=FORBIDDEN_SIDES,
        etf_arbitrage_enabled=False,
    )


def load_policy(path: str | Path) -> UniversePolicy:
    """Load and validate ``universe_policy.yaml`` against P0-9 v3 schema.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        UniversePolicyError: any P0-9 (+amendment) invariant is violated.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"universe policy file not found: {p}")

    try:
        with p.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise UniversePolicyError(
            f"universe_policy.yaml is not valid YAML: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise UniversePolicyError(
            f"universe_policy.yaml root must be a mapping, got {type(raw).__name__}"
        )

    version_raw = raw.get("policy_version")
    if version_raw != 3:
        raise UniversePolicyError(
            "universe_policy.policy_version must be 3 (P0-9 v3 schema). "
            f"Got {version_raw!r} — v1/v2 (13-code lock) schema is no longer accepted."
        )

    locked_decision = raw.get("locked_decision")
    if locked_decision != "P0-9":
        raise UniversePolicyError(
            "universe_policy.locked_decision must equal 'P0-9', "
            f"got {locked_decision!r}"
        )

    for required_section in (
        "fast",
        "slow",
        "universe",
        "exclusion_rules",
        "cap_allocation",
        "direction_policy",
    ):
        if required_section not in raw:
            raise UniversePolicyError(
                f"universe_policy.yaml missing required section: {required_section}"
            )

    fast = _coerce_bucket("fast", raw["fast"])
    slow = _coerce_bucket("slow", raw["slow"])

    overlap = set(fast.default_codes) & set(slow.default_codes)
    if overlap:
        raise UniversePolicyError(
            f"codes appear in both fast.default_codes and slow.default_codes: "
            f"{sorted(overlap)}"
        )

    universe = _coerce_universe(raw["universe"])
    exclusion_rules = _coerce_exclusion_rules(raw["exclusion_rules"])
    cap_allocation = _coerce_cap_allocation(raw["cap_allocation"])
    direction_policy = _coerce_direction_policy(raw["direction_policy"])

    overrides = _coerce_overrides(raw.get("overrides"))
    # Overrides must reference codes actually pinned in a default list;
    # an override pointing at a code in neither list is a silent drift
    # (the cron would run a code no user-controlled list declares).
    union_codes = set(fast.default_codes) | set(slow.default_codes)
    dangling = set(overrides.keys()) - union_codes
    if dangling:
        raise UniversePolicyError(
            f"overrides reference codes outside default_codes: {sorted(dangling)}"
        )

    default_category = _coerce_default(raw.get("default_category"))

    last_updated = raw.get("last_updated")
    if last_updated is not None and not isinstance(last_updated, str):
        last_updated = str(last_updated)

    policy = UniversePolicy(
        fast=fast,
        slow=slow,
        overrides=overrides,
        default_category=default_category,
        policy_version=3,
        last_updated=last_updated,
        locked_decision="P0-9",
        universe=universe,
        exclusion_rules=exclusion_rules,
        cap_allocation=cap_allocation,
        direction_policy=direction_policy,
        fast_default_set=frozenset(fast.default_codes),
        slow_default_set=frozenset(slow.default_codes),
    )
    log.info(
        "universe_policy_loaded",
        path=str(p),
        fast_default_count=len(fast.default_codes),
        slow_default_count=len(slow.default_codes),
        overrides_count=len(overrides),
        board_whitelist=sorted(universe.board_whitelist),
    )
    return policy


# ---------------------------------------------------------------------------
# Pure assignment helpers (still used by AnalysisScheduler each tick)
# ---------------------------------------------------------------------------


def assign_category(code: str, policy: UniversePolicy) -> Category:
    """Return ``'fast'`` or ``'slow'`` for ``code``.

    Resolution order (first match wins):
      1. ``policy.overrides[code]``
      2. ``code in policy.fast_default_set`` → fast
      3. ``code in policy.slow_default_set`` → slow
      4. ``policy.default_category``
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
    codes: list[str], policy: UniversePolicy
) -> tuple[list[str], list[str]]:
    """Split ``codes`` into ``(fast_codes, slow_codes)`` preserving input order."""
    fast_codes: list[str] = []
    slow_codes: list[str] = []
    for code in codes:
        if assign_category(code, policy) == "fast":
            fast_codes.append(code)
        else:
            slow_codes.append(code)
    return fast_codes, slow_codes


# P0-9 §1.3 forbids runtime mutation; there is intentionally no public
# ``update_override`` / ``save_policy`` here — rebuild via load_policy
# after editing the YAML on disk + process restart.
__all__ = [
    "BOARD_WHITELIST",
    "FORBIDDEN_BOARDS",
    "FORBIDDEN_SIDES",
    "LOCKED_EVENT_CAP",
    "LOCKED_EXCLUSION_RULES",
    "LOCKED_RESERVED_CAP_RELEASE_TIME",
    "LOCKED_TOTAL_DAILY_CAP",
    "LOCKED_TRADITIONAL_CAP",
    "BucketConfig",
    "CapAllocation",
    "Category",
    "DirectionPolicy",
    "ExclusionRules",
    "UniversePolicy",
    "UniversePolicyError",
    "UniverseRules",
    "assign_category",
    "load_policy",
    "partition_watchlist",
]
