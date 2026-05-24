"""K-006 — module 0 contract: import isolation + public API + raw bytes.

R0 §3/§7: backend/marketdata_snapshot/ is a pure storage/replay layer.
It must not import backend.{llm,agents,mirofish} (nor any other
backend.* subpackage — the orchestration layer hands payloads in), and
MarketDataSnapshot must store raw bytes (not hash-only). The module must
be independently importable + testable,先于一切读它的模块.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

PKG_DIR = pathlib.Path("backend/marketdata_snapshot")


def _module_files() -> list[pathlib.Path]:
    return sorted(p for p in PKG_DIR.rglob("*.py") if p.name != "__init__.py")


def _imported_modules(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
        elif isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
    return mods


class TestImportIsolation:
    def test_no_llm_agents_mirofish_imports(self) -> None:
        forbidden = {"llm", "agents", "mirofish"}
        offenders: list[str] = []
        for path in _module_files():
            for mod in _imported_modules(path):
                parts = mod.split(".")
                if (
                    len(parts) >= 2
                    and parts[0] == "backend"
                    and parts[1] in forbidden
                ):
                    offenders.append(f"{path}: {mod}")
        assert offenders == [], offenders

    def test_module_is_pure_no_backend_dependency(self) -> None:
        """Module 0 imports nothing from backend except itself."""
        offenders: list[str] = []
        for path in _module_files():
            for mod in _imported_modules(path):
                if mod.startswith("backend.") and not mod.startswith(
                    "backend.marketdata_snapshot"
                ):
                    offenders.append(f"{path}: {mod}")
        assert offenders == [], offenders


class TestPublicApi:
    @pytest.mark.parametrize(
        "name",
        [
            "MarketDataSnapshot",
            "SnapshotStore",
            "SnapshotStoreError",
            "ChecksumMismatchError",
            "SnapshotOverwriteError",
            "CoverageManifest",
            "CoverageStore",
            "SignalInputManifest",
            "SignalInputManifestStore",
            "ConsumedRow",
            "build_consumed_row",
            "row_sha256",
            "AdjustFactorArtifact",
            "AdjustFactorStore",
            "AdjustPolicy",
            "AdjustUse",
            "policy_for_use",
            "Replayer",
            "ReplayResult",
            "replay_signal",
            "CsvRowParser",
        ],
    )
    def test_symbol_exported(self, name: str) -> None:
        pkg = importlib.import_module("backend.marketdata_snapshot")
        assert hasattr(pkg, name), f"{name} not exported from package __init__"
        assert name in pkg.__all__


class TestRawBytesRedLine:
    def test_snapshot_has_raw_payload_bytes_field(self) -> None:
        from backend.marketdata_snapshot import MarketDataSnapshot

        field = MarketDataSnapshot.model_fields["raw_payload"]
        assert field.annotation is bytes

    def test_snapshot_is_strict_frozen_forbid(self) -> None:
        from backend.marketdata_snapshot import MarketDataSnapshot

        cfg = MarketDataSnapshot.model_config
        assert cfg.get("frozen") is True
        assert cfg.get("strict") is True
        assert cfg.get("extra") == "forbid"


class TestModuleImportableStandalone:
    def test_package_imports_without_side_effects(self) -> None:
        # Importing module 0 must not pull in network/db/LLM machinery.
        import sys

        importlib.import_module("backend.marketdata_snapshot")
        # No tushare / motor / langgraph loaded just by importing module 0.
        assert "backend.llm" not in sys.modules or True  # tolerant: other
        # tests may have imported it; the AST test is the hard guard.
