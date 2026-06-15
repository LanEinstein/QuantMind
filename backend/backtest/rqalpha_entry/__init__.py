"""rqalpha subprocess entry — runs ONLY inside the isolated oracle venv.

R-002-amendment-2026-06-14: the differential oracle executes rqalpha in a
separate venv (``QUANTMIND_RQALPHA_VENV_PYTHON``) whose numpy/pandas are newer
than the main env's; the subprocess reads a self-contained ``spec.json`` +
``bars.csv`` and writes ``result.json``. The modules in this package therefore:

* import ``rqalpha`` (legal here — the venv has it; the main env does not, and no
  main-env module imports this package — asserted by the R-002 AST contract);
* import **zero** ``backend.*`` (the venv has no backend install — the package is
  executed as the top-level ``rqalpha_entry`` with ``PYTHONPATH`` pointing at
  ``backend/backtest``, never imported as ``backend.backtest.rqalpha_entry``);
* are excluded from the ``[BACKTEST]`` decision-path lints (float friction math +
  the rqalpha import are legitimate here, unlike the deterministic harness).

This ``__init__`` is intentionally empty of rqalpha imports so that an accidental
``import backend.backtest.rqalpha_entry`` in the main env fails loudly on the
submodules (which DO import rqalpha) rather than appearing to succeed.
"""
