"""AE-006 — RuntimeParamStore + lockfile schema v2 + §2.5 frozen baseline.

Covers the param runtime-landing mechanism (AB-003-amendment-2026-06-14):
the immutable boot store, the byte-identical empty path, fail-closed
re-validation, and the frozen-default monotone baseline for safety-adjacent
params on the activation path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.strategy_evolution.evolvable_params import (
    FROZEN_BASELINE,
    validate_param_set,
    validate_param_set_for_activation,
)
from backend.strategy_evolution.live_artifact_registry import (
    ArtifactKind,
    LiveArtifactLockFile,
    LiveArtifactLockFileMalformedError,
    LiveArtifactLockFileNotFoundError,
    LiveArtifactRegistry,
)
from backend.strategy_evolution.runtime_param_store import (
    RuntimeParamStore,
    RuntimeParamValidationError,
)

# selector weights are a valid normalised set for the *non-activation*
# validator, but they have no wired runtime consumer → refused at activation /
# store load (AE-006 consumed-param gate). value_slot_quota is the one param
# with end-to-end plumbing.
VALID_SELECTOR = {
    "selector.weight_momentum": 0.4,
    "selector.weight_volatility": 0.2,
    "selector.weight_liquidity": 0.15,
    "selector.weight_value": 0.15,
    "selector.weight_quality": 0.1,
}
CONSUMED = {"allocation.value_slot_quota": 1.0}


def _write_lock(path: Path, *, version: str, params: dict | None = None) -> None:
    doc: dict = {
        "version": version,
        "updated_at": "2026-06-15T00:00:00+08:00",
        "approved": {kind.value: [] for kind in ArtifactKind},
    }
    doc["approved"]["prompt_version"] = ["a" * 64]
    if params is not None:
        doc["params"] = params
    path.write_text(json.dumps(doc, indent=2))


class TestEmptyStoreByteIdentical:
    def test_empty_store_returns_defaults(self) -> None:
        store = RuntimeParamStore.from_params({})
        assert not store
        assert len(store) == 0
        assert store.get("selector.weight_momentum", 0.4) == 0.4
        assert store.has("selector.weight_momentum") is False

    def test_v1_lockfile_yields_empty_store(self, tmp_path: Path) -> None:
        lock = tmp_path / "live.json"
        _write_lock(lock, version="1.0")  # no params key at all
        store = RuntimeParamStore.from_lockfile(lock)
        assert not store
        assert store.get("allocation.value_slot_quota", 2.0) == 2.0


class TestPopulatedStore:
    def test_valid_params_round_trip(self, tmp_path: Path) -> None:
        lock = tmp_path / "live.json"
        _write_lock(lock, version="2.0", params=dict(CONSUMED))
        store = RuntimeParamStore.from_lockfile(lock)
        assert store.get("allocation.value_slot_quota", 99) == 1.0
        assert len(store) == 1

    def test_as_mapping_is_read_only(self) -> None:
        store = RuntimeParamStore.from_params(dict(CONSUMED))
        view = store.as_mapping()
        with pytest.raises(TypeError):
            view["allocation.value_slot_quota"] = 2.0  # type: ignore[index]

    def test_store_is_immutable(self) -> None:
        store = RuntimeParamStore.from_params({})
        with pytest.raises(AttributeError, match="immutable"):
            store._params = {}  # type: ignore[attr-defined]


class TestFailClosed:
    def test_out_of_clamp_rejected(self) -> None:
        with pytest.raises(RuntimeParamValidationError, match="clamp"):
            RuntimeParamStore.from_params(
                {"allocation.value_slot_quota": 9.0}
            )

    def test_frozen_non_evolvable_rejected(self) -> None:
        with pytest.raises(RuntimeParamValidationError, match="frozen"):
            RuntimeParamStore.from_params(
                {"risk.max_single_stock_pct": 0.5}
            )

    def test_unwired_param_rejected_no_consumer(self) -> None:
        # selector weights are a valid normalised set but have no wired
        # consumer → refused (silent-no-op guard).
        with pytest.raises(RuntimeParamValidationError, match="consumer"):
            RuntimeParamStore.from_params(VALID_SELECTOR)

    def test_unknown_param_rejected(self) -> None:
        with pytest.raises(RuntimeParamValidationError, match="consumer"):
            RuntimeParamStore.from_params({"selector.not_a_param": 0.5})

    def test_missing_lockfile_fails_closed(self, tmp_path: Path) -> None:
        with pytest.raises(LiveArtifactLockFileNotFoundError):
            RuntimeParamStore.from_lockfile(tmp_path / "absent.json")

    def test_malformed_lockfile_fails_closed(self, tmp_path: Path) -> None:
        lock = tmp_path / "live.json"
        lock.write_text("{not json")
        with pytest.raises(LiveArtifactLockFileMalformedError):
            RuntimeParamStore.from_lockfile(lock)

    def test_lockfile_with_out_of_clamp_params_fails_closed(
        self, tmp_path: Path
    ) -> None:
        lock = tmp_path / "live.json"
        _write_lock(
            lock, version="2.0", params={"allocation.value_slot_quota": 9.0}
        )
        with pytest.raises(RuntimeParamValidationError):
            RuntimeParamStore.from_lockfile(lock)


class TestSchemaV2:
    def test_v1_lockfile_parses_with_empty_params(self) -> None:
        lock = LiveArtifactLockFile.model_validate_json(
            json.dumps(
                {"version": "1.0", "updated_at": "2026-06-15T00:00:00+08:00"}
            )
        )
        assert lock.params == {}

    def test_v2_lockfile_parses_params(self) -> None:
        lock = LiveArtifactLockFile.model_validate_json(
            json.dumps(
                {
                    "version": "2.0",
                    "updated_at": "2026-06-15T00:00:00+08:00",
                    "params": {"allocation.value_slot_quota": 1.0},
                }
            )
        )
        assert lock.params["allocation.value_slot_quota"] == 1.0

    def test_registry_ignores_params(self, tmp_path: Path) -> None:
        # The approved-hash gate is unaffected by a v2 params block.
        lock = tmp_path / "live.json"
        _write_lock(
            lock, version="2.0", params={"allocation.value_slot_quota": 1.0}
        )
        registry = LiveArtifactRegistry.from_lockfile(lock)
        assert registry.is_approved(ArtifactKind.PROMPT_VERSION, "a" * 64)
        assert not registry.is_approved(
            ArtifactKind.STRATEGY_CODE, "b" * 64
        )


class TestFrozenBaselineMonotone:
    """§2.5 — safety-adjacent monotone baseline = frozen code default.

    The monotone check sits BEHIND the consumed-param gate (a safety-adjacent
    param is only activatable once wired), so these tests pin a hypothetical
    wired state via ``RUNTIME_CONSUMED_PARAMS`` to exercise the §2.5 logic in
    isolation — it is the behaviour a future amendment wiring atr_stop_mult
    would unlock.
    """

    def _wire(self, monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
        import backend.strategy_evolution.evolvable_params as ep

        monkeypatch.setattr(
            ep,
            "RUNTIME_CONSUMED_PARAMS",
            frozenset(ep.RUNTIME_CONSUMED_PARAMS | set(names)),
        )

    def test_loosen_above_frozen_default_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # atr_stop_mult frozen baseline 2.0, DOWN: a value in clamp [1, 4] but
        # > 2.0 loosens the protective stop → rejected on the activation path.
        self._wire(monkeypatch, "line2.atr_stop_mult")
        result = validate_param_set_for_activation(
            {"line2.atr_stop_mult": 3.0}
        )
        assert not result.passed
        assert any("tighten" in v for v in result.violations)

    def test_tighten_below_frozen_default_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._wire(monkeypatch, "line2.atr_stop_mult")
        result = validate_param_set_for_activation(
            {"line2.atr_stop_mult": 1.5}
        )
        assert result.passed

    def test_safety_adjacent_without_baseline_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # drawdown_quantile is safety-adjacent but has no registered frozen
        # baseline → still not activatable (fail-closed) even once wired.
        self._wire(monkeypatch, "line2.drawdown_quantile")
        result = validate_param_set_for_activation(
            {"line2.drawdown_quantile": 0.85}
        )
        assert not result.passed
        assert any("baseline" in v for v in result.violations)

    def test_non_activation_path_unchanged(self) -> None:
        # The plain validator (used by the search lane) still treats
        # drawdown_quantile as a normal clamped param (no frozen baseline).
        assert validate_param_set({"line2.drawdown_quantile": 0.85}).passed

    def test_non_safety_adjacent_consumed_param_passes(self) -> None:
        # value_slot_quota is not safety-adjacent (no monotone) AND is wired,
        # so it passes the activation validator.
        assert validate_param_set_for_activation(dict(CONSUMED)).passed

    def test_frozen_baseline_is_immutable(self) -> None:
        with pytest.raises(TypeError):
            FROZEN_BASELINE["line2.atr_stop_mult"] = 4.0  # type: ignore[index]


class TestConsumedParamGate:
    """AE-006 — refuse activating a param with no wired runtime consumer
    (lifting the blanket refusal must not silently re-open silent no-ops)."""

    def test_selector_weights_valid_for_search_but_unwired_for_activation(
        self,
    ) -> None:
        # The quant search lane validates with the plain validator (group
        # sum == 1 holds) — selector weights are a legal *search* candidate ...
        assert validate_param_set(VALID_SELECTOR).passed
        # ... but the ACTIVATION path refuses them: no wired consumer yet.
        result = validate_param_set_for_activation(VALID_SELECTOR)
        assert not result.passed
        assert all("consumer" in v for v in result.violations)

    def test_theme_tier_weights_unwired_rejected(self) -> None:
        result = validate_param_set_for_activation(
            {
                "theme.tier1_weight": 1.0,
                "theme.tier2_weight": 0.75,
                "theme.tier3_weight": 0.5,
                "theme.tier4_weight": 0.25,
            }
        )
        assert not result.passed
        assert any("consumer" in v for v in result.violations)

    def test_consumed_param_admitted(self) -> None:
        assert validate_param_set_for_activation(dict(CONSUMED)).passed
