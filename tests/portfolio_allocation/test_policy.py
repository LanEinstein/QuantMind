"""Tests for backend.portfolio_allocation.policy (Phase P P-002)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from backend.portfolio_allocation.policy import (
    INVERSE_VOLATILITY,
    AllocationPolicy,
    AllocationPolicyError,
    load_allocation_policy,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _write_alloc(
    tmp_path: Path,
    *,
    method: str = "inverse_volatility",
    deploy_fraction: float | str = 0.33,
    per_name_target_pct: float | str = 0.10,
    cash_buffer_pct: float | str = 0.05,
    vol_lookback: int | str = 20,
    section: str = "allocation",
) -> Path:
    path = tmp_path / "allocation_policy.yaml"
    path.write_text(
        f"{section}:\n"
        f"  method: {method}\n"
        f"  deploy_fraction: {deploy_fraction}\n"
        f"  per_name_target_pct: {per_name_target_pct}\n"
        f"  cash_buffer_pct: {cash_buffer_pct}\n"
        f"  vol_lookback: {vol_lookback}\n",
        encoding="utf-8",
    )
    return path


def _write_risk(
    tmp_path: Path,
    *,
    max_single_stock_pct: float | str = 0.15,
    max_single_instruction_amount: float | str = 50000,
    volume_lot_size: int | str = 100,
    section: str = "position_limits",
) -> Path:
    path = tmp_path / "risk.yaml"
    path.write_text(
        f"{section}:\n"
        f"  max_single_stock_pct: {max_single_stock_pct}\n"
        f"  max_single_instruction_amount: {max_single_instruction_amount}\n"
        f"  volume_lot_size: {volume_lot_size}\n",
        encoding="utf-8",
    )
    return path


class TestConfigLoaderHappy:
    @pytest.mark.unit
    def test_loads_shipped_configs(self) -> None:
        policy = load_allocation_policy(
            REPO_ROOT / "config" / "allocation_policy.yaml",
            REPO_ROOT / "config" / "risk.yaml",
        )
        assert policy.method == INVERSE_VOLATILITY
        assert policy.deploy_fraction == 0.33
        assert policy.per_name_target_pct == 0.10
        assert policy.cash_buffer_pct == 0.05
        assert policy.vol_lookback == 20
        # Single source — these three come from risk.yaml position_limits.
        assert policy.single_stock_cap_pct == 0.15
        assert policy.single_instruction_cap == 50000.0
        assert policy.lot_size == 100

    @pytest.mark.unit
    def test_cap_comes_from_risk_yaml_not_allocation(self, tmp_path: Path) -> None:
        # A non-default 20% cap in risk.yaml must flow through; the allocation
        # YAML never carries the cap (single source of truth).
        alloc = _write_alloc(tmp_path, per_name_target_pct=0.18)
        risk = _write_risk(tmp_path, max_single_stock_pct=0.20)
        policy = load_allocation_policy(alloc, risk)
        assert policy.single_stock_cap_pct == 0.20
        assert policy.per_name_target_pct == 0.18  # ≤ 0.20, accepted


class TestConfigLoaderValidation:
    @pytest.mark.unit
    def test_missing_allocation_file_raises(self, tmp_path: Path) -> None:
        risk = _write_risk(tmp_path)
        with pytest.raises(FileNotFoundError):
            load_allocation_policy(tmp_path / "nope.yaml", risk)

    @pytest.mark.unit
    def test_missing_risk_file_raises(self, tmp_path: Path) -> None:
        alloc = _write_alloc(tmp_path)
        with pytest.raises(FileNotFoundError):
            load_allocation_policy(alloc, tmp_path / "nope.yaml")

    @pytest.mark.unit
    def test_missing_allocation_section(self, tmp_path: Path) -> None:
        alloc = _write_alloc(tmp_path, section="wrong")
        risk = _write_risk(tmp_path)
        with pytest.raises(AllocationPolicyError, match="allocation"):
            load_allocation_policy(alloc, risk)

    @pytest.mark.unit
    def test_missing_position_limits_section(self, tmp_path: Path) -> None:
        alloc = _write_alloc(tmp_path)
        risk = _write_risk(tmp_path, section="wrong")
        with pytest.raises(AllocationPolicyError, match="position_limits"):
            load_allocation_policy(alloc, risk)

    @pytest.mark.unit
    def test_unknown_method_rejected(self, tmp_path: Path) -> None:
        alloc = _write_alloc(tmp_path, method="black_litterman")
        risk = _write_risk(tmp_path)
        with pytest.raises(AllocationPolicyError, match="method"):
            load_allocation_policy(alloc, risk)

    @pytest.mark.unit
    @pytest.mark.parametrize("frac", [0.0, -0.1, 1.5])
    def test_bad_deploy_fraction_rejected(self, tmp_path: Path, frac: float) -> None:
        alloc = _write_alloc(tmp_path, deploy_fraction=frac)
        risk = _write_risk(tmp_path)
        with pytest.raises(AllocationPolicyError, match="deploy_fraction"):
            load_allocation_policy(alloc, risk)

    @pytest.mark.unit
    def test_nonnumeric_deploy_fraction_rejected(self, tmp_path: Path) -> None:
        # A non-numeric (string) fraction must fail fast, not coerce.
        alloc = _write_alloc(tmp_path, deploy_fraction="aggressive")
        risk = _write_risk(tmp_path)
        with pytest.raises(AllocationPolicyError, match="must be a number"):
            load_allocation_policy(alloc, risk)

    @pytest.mark.unit
    def test_per_name_above_single_stock_cap_rejected(self, tmp_path: Path) -> None:
        # per-name 0.20 > 15% hard cap → rejected (allocation only tightens).
        alloc = _write_alloc(tmp_path, per_name_target_pct=0.20)
        risk = _write_risk(tmp_path, max_single_stock_pct=0.15)
        with pytest.raises(AllocationPolicyError, match="per_name_target_pct"):
            load_allocation_policy(alloc, risk)

    @pytest.mark.unit
    @pytest.mark.parametrize("buf", [-0.01, 1.0, 1.2])
    def test_bad_cash_buffer_rejected(self, tmp_path: Path, buf: float) -> None:
        alloc = _write_alloc(tmp_path, cash_buffer_pct=buf)
        risk = _write_risk(tmp_path)
        with pytest.raises(AllocationPolicyError, match="cash_buffer_pct"):
            load_allocation_policy(alloc, risk)

    @pytest.mark.unit
    @pytest.mark.parametrize("lb", [1, 0, -3])
    def test_bad_vol_lookback_rejected(self, tmp_path: Path, lb: int) -> None:
        alloc = _write_alloc(tmp_path, vol_lookback=lb)
        risk = _write_risk(tmp_path)
        with pytest.raises(AllocationPolicyError, match="vol_lookback"):
            load_allocation_policy(alloc, risk)

    @pytest.mark.unit
    def test_bad_single_instruction_amount_rejected(self, tmp_path: Path) -> None:
        alloc = _write_alloc(tmp_path)
        risk = _write_risk(tmp_path, max_single_instruction_amount=0)
        with pytest.raises(
            AllocationPolicyError, match="max_single_instruction_amount"
        ):
            load_allocation_policy(alloc, risk)

    @pytest.mark.unit
    def test_bad_lot_size_rejected(self, tmp_path: Path) -> None:
        alloc = _write_alloc(tmp_path)
        risk = _write_risk(tmp_path, volume_lot_size=0)
        with pytest.raises(AllocationPolicyError, match="volume_lot_size"):
            load_allocation_policy(alloc, risk)

    @pytest.mark.unit
    def test_bad_single_stock_cap_rejected(self, tmp_path: Path) -> None:
        alloc = _write_alloc(tmp_path)
        risk = _write_risk(tmp_path, max_single_stock_pct=1.5)
        with pytest.raises(AllocationPolicyError, match="max_single_stock_pct"):
            load_allocation_policy(alloc, risk)


class TestPolicyConvenienceMethods:
    def _policy(self) -> AllocationPolicy:
        return load_allocation_policy(
            REPO_ROOT / "config" / "allocation_policy.yaml",
            REPO_ROOT / "config" / "risk.yaml",
        )

    @pytest.mark.unit
    def test_inverse_vol_weights_delegates(self) -> None:
        w = self._policy().inverse_vol_weights({"A": 0.02, "B": 0.02})
        assert w == {"A": pytest.approx(0.5), "B": pytest.approx(0.5)}

    @pytest.mark.unit
    def test_deployable_cash_delegates(self) -> None:
        d = self._policy().deployable_cash(30000.0, 100000.0)
        assert d == pytest.approx(9900.0)  # 0.33 * 30000

    @pytest.mark.unit
    def test_target_cash_delegates_with_policy_caps(self) -> None:
        policy = self._policy()
        alloc = policy.target_cash({"A": 1.0}, 100000.0, 1_000_000.0, {})
        # ¥50k single-instruction cap from risk.yaml binds.
        assert alloc["A"] == pytest.approx(50000.0)

    @pytest.mark.unit
    def test_cash_to_lots_uses_policy_lot_size(self) -> None:
        assert self._policy().cash_to_lots(10000.0, 50.0) == 200


class TestImmutability:
    @pytest.mark.unit
    def test_policy_frozen(self) -> None:
        policy = AllocationPolicy(
            method=INVERSE_VOLATILITY,
            deploy_fraction=0.33,
            per_name_target_pct=0.10,
            cash_buffer_pct=0.05,
            vol_lookback=20,
            single_stock_cap_pct=0.15,
            single_instruction_cap=50000.0,
            lot_size=100,
        )
        with pytest.raises(FrozenInstanceError):
            policy.deploy_fraction = 0.99  # type: ignore[misc]
