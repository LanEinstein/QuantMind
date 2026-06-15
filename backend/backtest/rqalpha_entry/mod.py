"""Injector mod — wires the PIT data source + friction into rqalpha (AE-002).

rqalpha loads this via ``config.mod.qm_inject.lib = "rqalpha_entry.mod"``. The
main entry (:mod:`rqalpha_entry.__main__`) prepares the data source + friction
tables and stashes them in :data:`PENDING`; ``start_up`` consumes them, replacing
rqalpha's default ``BaseDataSource`` (which would need a 米筐 bundle) and its
``sys_transaction_cost`` deciders (which charge rqalpha's default rates, not
ours). The slippage model is wired separately via the run config
(``sys_simulation.slippage_model``) because rqalpha builds it inside the matcher.
"""

from __future__ import annotations

from typing import Any

from rqalpha.const import INSTRUMENT_TYPE
from rqalpha.interface import AbstractMod
from rqalpha_entry.data_source import PitExportDataSource
from rqalpha_entry.friction import QuantMindStockCostDecider

# Set by rqalpha_entry.__main__ before run_func; consumed in start_up.
PENDING: dict[str, Any] = {}


class QuantMindInjectorMod(AbstractMod):
    """Replace the data source + transaction-cost deciders with QuantMind's."""

    def start_up(self, env: Any, mod_config: Any) -> None:
        data_source: PitExportDataSource = PENDING["data_source"]
        env.set_data_source(data_source)
        decider = QuantMindStockCostDecider()
        # Our universe is stocks (CS) + ETFs; set both so the default
        # sys_transaction_cost (disabled in the run config) is not relied on.
        env.set_transaction_cost_decider(INSTRUMENT_TYPE.CS, decider)
        env.set_transaction_cost_decider(INSTRUMENT_TYPE.ETF, decider)
        env.set_transaction_cost_decider(INSTRUMENT_TYPE.PUBLIC_FUND, decider)

    def tear_down(self, code: int, exception: Any = None) -> None:
        return None


def load_mod() -> QuantMindInjectorMod:
    return QuantMindInjectorMod()


__all__ = ["PENDING", "QuantMindInjectorMod", "load_mod"]
