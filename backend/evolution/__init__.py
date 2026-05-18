"""Phase X evolution package (P2-2).

Hosts the eighteen self-evolution modules described in
``docs/decisions/P2-2-implementation-plan-2026-05-18.md`` §1.13. Every
module in this package and its subpackages is forbidden from importing
``backend.{api, broker, risk, llm, agents, mirofish, data}`` — the
three-layer import gate (ruff + AST scan + ``scripts/redline-check.sh``)
enforces this red line so an LLM-authored module cannot reach back into
the decision path it is supposed to evolve from a distance.
"""
