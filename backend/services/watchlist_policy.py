"""P0-9 locked watchlist policy: schema + loader + pure assignment helpers.

The single :class:`WatchlistPolicy` aggregate captures everything the
P0-9 decision locked: the 13-code universe split into 10 individual
stocks (沪主 4 / 深主 3 / 创业板 3) plus 3 mandatory ETFs
(510300 / 510500 / 159949), the fast/slow scheduling cadence, the
5-instruction daily cap (traditional 4 + event 1, with a 14:30 slide
rule), the four exclusion thresholds enforced as the
``InstructionPlanBuilder`` fifth early-return, and the strict long-only
direction policy.

Every field is frozen and runtime-immutable. The legacy
``update_override`` / ``save_policy`` helpers were removed in C-002
because the decision (P0-9 §1.3 + P0-7 §1.4) requires changes to go
through ``git diff`` + amendment + process restart — there is no
sanctioned in-process mutation path.

The loader rejects v1 YAML loudly: missing ``policy_version: 2`` or any
P0-9 section raises :class:`WatchlistPolicyError` at boot rather than
silently dropping locked invariants.

``assign_category`` / ``partition_watchlist`` remain pure functions so
the scheduler can categorise a watchlist tick without touching disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog
import yaml

log = structlog.get_logger(component="watchlist_policy")

Category = Literal["fast", "slow"]
_VALID_CATEGORIES: tuple[Category, ...] = ("fast", "slow")

# P0-9 §1.2 mandatory ETF triplet — locked by code.
MANDATORY_ETF_CODES: frozenset[str] = frozenset({"510300", "510500", "159949"})

# P0-9 §4.1 forbidden InstructionSide values — locked exact set.
FORBIDDEN_SIDES: frozenset[str] = frozenset(
    {"SHORT", "COVER", "MARGIN_BUY", "REVERSE_REPO", "ETF_SUBSCRIBE", "ETF_REDEEM"}
)

# P0-9 §1.1 composition lock — total 13 = 4+3+3+3.
LOCKED_COMPOSITION: dict[str, int] = {
    "sh_main": 4,
    "sz_main": 3,
    "chuangye": 3,
    "etf": 3,
}
LOCKED_TOTAL_CODES: int = 13

# P0-9 §3.1 cap-allocation lock (also mirrors P0-7 max_daily_new=5).
LOCKED_TOTAL_DAILY_CAP: int = 5
LOCKED_TRADITIONAL_CAP: int = 4
LOCKED_EVENT_CAP: int = 1
LOCKED_RESERVED_CAP_RELEASE_TIME: str = "14:30"

# P0-9 §2.1 exclusion-rule thresholds — locked exactly. A YAML edit
# that drifts any of these values without a corresponding code change
# would silently widen / narrow which candidates the InstructionPlan
# builder rejects. Requiring lock-step changes (both this constant
# AND the YAML must move together) forces the amendment + git-diff
# discipline the decision demands (§2.4).
LOCKED_EXCLUSION_RULES: dict[str, int | float] = {
    "ipo_min_trading_days": 30,
    "sub_new_min_trading_days": 180,
    "min_avg_amount_20d_yuan": 200_000_000,
    "max_unit_price_yuan": 500.0,
}


class WatchlistPolicyError(ValueError):
    """Raised when ``watchlist_policy.yaml`` fails P0-9 validation."""


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
class WatchlistComposition:
    """Locked board distribution for the 13-code universe."""

    sh_main: int = 4
    sz_main: int = 3
    chuangye: int = 3
    etf: int = 3
    total_codes: int = LOCKED_TOTAL_CODES
    default_category: Category = "slow"


@dataclass(frozen=True)
class RequiredETF:
    """One mandatory ETF (locked by code)."""

    code: str
    name: str
    tracking: str


@dataclass(frozen=True)
class ExclusionRules:
    """Four exclusion thresholds enforced in InstructionPlanBuilder 5th early-return."""

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


def _default_required_etfs() -> tuple[RequiredETF, ...]:
    return (
        RequiredETF(code="510300", name="沪深300 ETF", tracking="沪深300指数"),
        RequiredETF(code="510500", name="中证500 ETF", tracking="中证500指数"),
        RequiredETF(code="159949", name="创业板50 ETF", tracking="创业板50指数"),
    )


@dataclass(frozen=True)
class WatchlistPolicy:
    """Aggregate P0-9 policy view.

    ``fast_default_set`` / ``slow_default_set`` are derived O(1) lookup
    caches built by :func:`load_policy` so per-tick partitioning stays
    cheap regardless of list length.
    """

    fast: BucketConfig
    slow: BucketConfig
    overrides: dict[str, Category] = field(default_factory=dict)
    default_category: Category = "slow"
    policy_version: int = 2
    last_updated: str | None = None
    locked_decision: str = "P0-9"
    composition: WatchlistComposition = field(default_factory=WatchlistComposition)
    required_etfs: tuple[RequiredETF, ...] = field(
        default_factory=_default_required_etfs
    )
    exclusion_rules: ExclusionRules = field(default_factory=ExclusionRules)
    cap_allocation: CapAllocation = field(default_factory=CapAllocation)
    direction_policy: DirectionPolicy = field(default_factory=DirectionPolicy)
    fast_default_set: frozenset[str] = field(default_factory=frozenset)
    slow_default_set: frozenset[str] = field(default_factory=frozenset)

    def cron_for(self, category: Category) -> str:
        return self.fast.cron if category == "fast" else self.slow.cron

    def bucket_for(self, category: Category) -> BucketConfig:
        return self.fast if category == "fast" else self.slow

    def all_watchlist_codes(self) -> frozenset[str]:
        """Union of every code mentioned in fast / slow / overrides.

        Used by callers (boot seed, exit-check helper) that need a single
        canonical view of the active universe without re-implementing
        the resolution rule. Codes only appearing in ``overrides`` still
        count — they bind to a bucket but may not be in either
        ``default_codes`` list yet (e.g. owner-pick rotation in progress).
        """
        return frozenset(self.fast.default_codes).union(
            self.slow.default_codes, self.overrides.keys()
        )


# ---------------------------------------------------------------------------
# Loader — strict v2 validation
# ---------------------------------------------------------------------------


def _coerce_bucket(name: str, raw: Any) -> BucketConfig:
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


def _coerce_composition(raw: Any) -> WatchlistComposition:
    if not isinstance(raw, dict):
        raise WatchlistPolicyError(
            f"watchlist_policy.watchlist must be a mapping, got {type(raw).__name__}"
        )
    total = raw.get("total_codes")
    if total != LOCKED_TOTAL_CODES:
        raise WatchlistPolicyError(
            f"watchlist_policy.watchlist.total_codes must equal {LOCKED_TOTAL_CODES}, "
            f"got {total!r}"
        )
    comp_raw = raw.get("composition")
    if not isinstance(comp_raw, dict):
        raise WatchlistPolicyError(
            "watchlist_policy.watchlist.composition must be a mapping"
        )
    for board, expected in LOCKED_COMPOSITION.items():
        got = comp_raw.get(board)
        if got != expected:
            raise WatchlistPolicyError(
                f"watchlist_policy.watchlist.composition.{board} must equal "
                f"{expected}, got {got!r}"
            )
    extra = set(comp_raw) - set(LOCKED_COMPOSITION)
    if extra:
        raise WatchlistPolicyError(
            f"watchlist_policy.watchlist.composition has unexpected boards: "
            f"{sorted(extra)}"
        )
    default_category = _coerce_default(raw.get("default_category"))
    return WatchlistComposition(
        sh_main=LOCKED_COMPOSITION["sh_main"],
        sz_main=LOCKED_COMPOSITION["sz_main"],
        chuangye=LOCKED_COMPOSITION["chuangye"],
        etf=LOCKED_COMPOSITION["etf"],
        total_codes=LOCKED_TOTAL_CODES,
        default_category=default_category,
    )


def _coerce_required_etfs(raw: Any) -> tuple[RequiredETF, ...]:
    if not isinstance(raw, list):
        raise WatchlistPolicyError(
            "watchlist_policy.required_etfs must be a list of 3 mandatory ETFs"
        )
    if len(raw) != len(MANDATORY_ETF_CODES):
        raise WatchlistPolicyError(
            f"watchlist_policy.required_etfs must have exactly "
            f"{len(MANDATORY_ETF_CODES)} entries, got {len(raw)}"
        )
    etfs: list[RequiredETF] = []
    codes: list[str] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise WatchlistPolicyError(
                f"watchlist_policy.required_etfs[{idx}] must be a mapping"
            )
        for key in ("code", "name", "tracking"):
            if key not in entry:
                raise WatchlistPolicyError(
                    f"watchlist_policy.required_etfs[{idx}] missing key: {key}"
                )
        code = str(entry["code"])
        codes.append(code)
        etfs.append(
            RequiredETF(
                code=code,
                name=str(entry["name"]),
                tracking=str(entry["tracking"]),
            )
        )
    code_set = set(codes)
    if code_set != MANDATORY_ETF_CODES:
        raise WatchlistPolicyError(
            "watchlist_policy.required_etfs must contain exactly "
            f"{sorted(MANDATORY_ETF_CODES)}, got {sorted(code_set)}"
        )
    return tuple(etfs)


def _coerce_exclusion_rules(raw: Any) -> ExclusionRules:
    if not isinstance(raw, dict):
        raise WatchlistPolicyError(
            "watchlist_policy.exclusion_rules must be a mapping"
        )
    required = (
        "ipo_min_trading_days",
        "sub_new_min_trading_days",
        "min_avg_amount_20d_yuan",
        "max_unit_price_yuan",
    )
    missing = [k for k in required if k not in raw]
    if missing:
        raise WatchlistPolicyError(
            f"watchlist_policy.exclusion_rules missing keys: {missing}"
        )
    ipo = raw["ipo_min_trading_days"]
    sub = raw["sub_new_min_trading_days"]
    amount = raw["min_avg_amount_20d_yuan"]
    price = raw["max_unit_price_yuan"]
    if ipo != LOCKED_EXCLUSION_RULES["ipo_min_trading_days"]:
        raise WatchlistPolicyError(
            "exclusion_rules.ipo_min_trading_days must equal "
            f"{LOCKED_EXCLUSION_RULES['ipo_min_trading_days']} "
            f"(P0-9 §2.1 locked), got {ipo!r}"
        )
    if sub != LOCKED_EXCLUSION_RULES["sub_new_min_trading_days"]:
        raise WatchlistPolicyError(
            "exclusion_rules.sub_new_min_trading_days must equal "
            f"{LOCKED_EXCLUSION_RULES['sub_new_min_trading_days']} "
            f"(P0-9 §2.1 locked), got {sub!r}"
        )
    if amount != LOCKED_EXCLUSION_RULES["min_avg_amount_20d_yuan"]:
        raise WatchlistPolicyError(
            "exclusion_rules.min_avg_amount_20d_yuan must equal "
            f"{LOCKED_EXCLUSION_RULES['min_avg_amount_20d_yuan']} "
            f"(P0-9 §2.1 locked), got {amount!r}"
        )
    # Compare as float so YAML int (500) and float (500.0) both work,
    # but reject any other numeric drift like 499.99 or 501.
    locked_price = float(LOCKED_EXCLUSION_RULES["max_unit_price_yuan"])
    if not isinstance(price, int | float) or float(price) != locked_price:
        raise WatchlistPolicyError(
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
        raise WatchlistPolicyError(
            "watchlist_policy.cap_allocation must be a mapping"
        )
    required = (
        "total_daily_cap",
        "traditional_path_default_cap",
        "event_path_reserved_cap",
        "reserved_cap_release_time",
    )
    missing = [k for k in required if k not in raw]
    if missing:
        raise WatchlistPolicyError(
            f"watchlist_policy.cap_allocation missing keys: {missing}"
        )
    total = raw["total_daily_cap"]
    traditional = raw["traditional_path_default_cap"]
    event = raw["event_path_reserved_cap"]
    release = raw["reserved_cap_release_time"]
    if total != LOCKED_TOTAL_DAILY_CAP:
        raise WatchlistPolicyError(
            f"cap_allocation.total_daily_cap must equal "
            f"{LOCKED_TOTAL_DAILY_CAP} (mirrors P0-7 max_daily_new), got {total!r}"
        )
    if traditional != LOCKED_TRADITIONAL_CAP:
        raise WatchlistPolicyError(
            f"cap_allocation.traditional_path_default_cap must equal "
            f"{LOCKED_TRADITIONAL_CAP}, got {traditional!r}"
        )
    if event != LOCKED_EVENT_CAP:
        raise WatchlistPolicyError(
            f"cap_allocation.event_path_reserved_cap must equal "
            f"{LOCKED_EVENT_CAP}, got {event!r}"
        )
    if traditional + event != total:
        raise WatchlistPolicyError(
            "cap_allocation: traditional + event cap must equal total"
        )
    if not isinstance(release, str) or not _RELEASE_TIME_RE.match(release):
        raise WatchlistPolicyError(
            f"cap_allocation.reserved_cap_release_time must be HH:MM (24h), "
            f"got {release!r}"
        )
    if release != LOCKED_RESERVED_CAP_RELEASE_TIME:
        raise WatchlistPolicyError(
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
        raise WatchlistPolicyError(
            "watchlist_policy.direction_policy must be a mapping"
        )
    long_only = raw.get("long_only")
    if long_only is not True:
        raise WatchlistPolicyError(
            "direction_policy.long_only must be true (P0-9 §4.1 strict long-only)"
        )
    forbidden_raw = raw.get("forbidden_sides")
    if not isinstance(forbidden_raw, list):
        raise WatchlistPolicyError(
            "direction_policy.forbidden_sides must be a list"
        )
    forbidden = frozenset(str(s) for s in forbidden_raw)
    if forbidden != FORBIDDEN_SIDES:
        raise WatchlistPolicyError(
            "direction_policy.forbidden_sides must equal "
            f"{sorted(FORBIDDEN_SIDES)}, got {sorted(forbidden)}"
        )
    etf_arb = raw.get("etf_arbitrage_enabled")
    if etf_arb is not False:
        raise WatchlistPolicyError(
            "direction_policy.etf_arbitrage_enabled must be false "
            "(P0-9 §4.4 永锁 — 启用走 amendment)"
        )
    return DirectionPolicy(
        long_only=True,
        forbidden_sides=FORBIDDEN_SIDES,
        etf_arbitrage_enabled=False,
    )


def _validate_constraints_block(raw: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise WatchlistPolicyError(
            "watchlist_policy.constraints must be a mapping when present"
        )
    expectations = {
        "watchlist_size_must_equal": LOCKED_TOTAL_CODES,
        "watchlist_etf_count_must_equal": LOCKED_COMPOSITION["etf"],
        "total_daily_cap_must_equal_p0_7": LOCKED_TOTAL_DAILY_CAP,
        "long_only_must_be_true": True,
    }
    for key, expected in expectations.items():
        if key in raw and raw[key] != expected:
            raise WatchlistPolicyError(
                f"watchlist_policy.constraints.{key} drifted from locked value "
                f"{expected!r} (got {raw[key]!r}) — fix YAML or open amendment"
            )


def load_policy(path: str | Path) -> WatchlistPolicy:
    """Load and validate ``watchlist_policy.yaml`` against P0-9 §5 schema.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        WatchlistPolicyError: any P0-9 invariant is violated.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"watchlist policy file not found: {p}")

    try:
        with p.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise WatchlistPolicyError(
            f"watchlist_policy.yaml is not valid YAML: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise WatchlistPolicyError(
            f"watchlist_policy.yaml root must be a mapping, got {type(raw).__name__}"
        )

    version_raw = raw.get("policy_version")
    if version_raw != 2:
        raise WatchlistPolicyError(
            "watchlist_policy.policy_version must be 2 (P0-9 v2 schema). "
            f"Got {version_raw!r} — v1 schema is no longer accepted."
        )

    locked_decision = raw.get("locked_decision")
    if locked_decision != "P0-9":
        raise WatchlistPolicyError(
            "watchlist_policy.locked_decision must equal 'P0-9', "
            f"got {locked_decision!r}"
        )

    for required_section in (
        "fast",
        "slow",
        "watchlist",
        "required_etfs",
        "exclusion_rules",
        "cap_allocation",
        "direction_policy",
    ):
        if required_section not in raw:
            raise WatchlistPolicyError(
                f"watchlist_policy.yaml missing required section: {required_section}"
            )

    fast = _coerce_bucket("fast", raw["fast"])
    slow = _coerce_bucket("slow", raw["slow"])

    overlap = set(fast.default_codes) & set(slow.default_codes)
    if overlap:
        raise WatchlistPolicyError(
            f"codes appear in both fast.default_codes and slow.default_codes: "
            f"{sorted(overlap)}"
        )

    composition = _coerce_composition(raw["watchlist"])
    required_etfs = _coerce_required_etfs(raw["required_etfs"])
    exclusion_rules = _coerce_exclusion_rules(raw["exclusion_rules"])
    cap_allocation = _coerce_cap_allocation(raw["cap_allocation"])
    direction_policy = _coerce_direction_policy(raw["direction_policy"])

    # Mandatory ETFs must already be seeded in slow.default_codes (P0-9 §1.2
    # locks them as passive long-horizon holdings — fast bucket would force
    # an intraday cadence that adds no value for index ETFs).
    seeded_etfs = MANDATORY_ETF_CODES & set(slow.default_codes)
    missing_etfs = MANDATORY_ETF_CODES - seeded_etfs
    if missing_etfs:
        raise WatchlistPolicyError(
            f"mandatory ETFs not in slow.default_codes: {sorted(missing_etfs)} "
            "(P0-9 §1.2 lock — they must be in the watchlist)"
        )
    # Overrides can re-bucket the ETFs but only across the {fast, slow}
    # set — the YAML loader for overrides already constrains values; no
    # extra check needed.

    overrides = _coerce_overrides(raw.get("overrides"))
    # Overrides must reference codes actually in the watchlist; an
    # override pointing at a code that does not appear in either
    # default_codes list is a silent drift (the cron would run a code
    # that is not in any user-controlled list).
    union_codes = set(fast.default_codes) | set(slow.default_codes)
    dangling = set(overrides.keys()) - union_codes
    if dangling:
        raise WatchlistPolicyError(
            f"overrides reference codes outside default_codes: {sorted(dangling)}"
        )

    # Total unique codes across the policy cannot exceed the 13-code lock.
    total_in_policy = len(union_codes)
    if total_in_policy > LOCKED_TOTAL_CODES:
        raise WatchlistPolicyError(
            f"total unique codes in fast/slow default_codes is "
            f"{total_in_policy}, exceeds locked maximum {LOCKED_TOTAL_CODES}"
        )

    _validate_constraints_block(raw.get("constraints"))

    last_updated = raw.get("last_updated")
    if last_updated is not None and not isinstance(last_updated, str):
        last_updated = str(last_updated)

    policy = WatchlistPolicy(
        fast=fast,
        slow=slow,
        overrides=overrides,
        default_category=composition.default_category,
        policy_version=2,
        last_updated=last_updated,
        locked_decision="P0-9",
        composition=composition,
        required_etfs=required_etfs,
        exclusion_rules=exclusion_rules,
        cap_allocation=cap_allocation,
        direction_policy=direction_policy,
        fast_default_set=frozenset(fast.default_codes),
        slow_default_set=frozenset(slow.default_codes),
    )
    log.info(
        "watchlist_policy_loaded",
        path=str(p),
        fast_default_count=len(fast.default_codes),
        slow_default_count=len(slow.default_codes),
        overrides_count=len(overrides),
        total_codes_provisioned=total_in_policy,
        total_codes_target=LOCKED_TOTAL_CODES,
    )
    return policy


# ---------------------------------------------------------------------------
# Pure assignment helpers (still used by AnalysisScheduler each tick)
# ---------------------------------------------------------------------------


def assign_category(code: str, policy: WatchlistPolicy) -> Category:
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
    codes: list[str], policy: WatchlistPolicy
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


# P0-9 §1.3 forbids runtime mutation; there is intentionally no
# public ``update_override`` / ``save_policy`` here — rebuild via
# load_policy after editing the YAML on disk + process restart.
__all__ = [
    "BucketConfig",
    "CapAllocation",
    "Category",
    "DirectionPolicy",
    "ExclusionRules",
    "FORBIDDEN_SIDES",
    "LOCKED_COMPOSITION",
    "LOCKED_EVENT_CAP",
    "LOCKED_EXCLUSION_RULES",
    "LOCKED_RESERVED_CAP_RELEASE_TIME",
    "LOCKED_TOTAL_CODES",
    "LOCKED_TOTAL_DAILY_CAP",
    "LOCKED_TRADITIONAL_CAP",
    "MANDATORY_ETF_CODES",
    "RequiredETF",
    "WatchlistComposition",
    "WatchlistPolicy",
    "WatchlistPolicyError",
    "assign_category",
    "load_policy",
    "partition_watchlist",
]
