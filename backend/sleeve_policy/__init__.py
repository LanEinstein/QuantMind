"""Value-sleeve capital allocation (AF-005).

The long-term value sleeve runs as an **independent capital sub-account** beside
the short-term ≤5-slot sleeve once the account grows past ¥50k
(value-sleeve-amendment-2026-06-22 §2.1/§2.2). This package is the deterministic,
pure policy that decides:

* whether the value sleeve is **active** (a master switch AND total equity ≥ the
  ¥50k trigger, with a one-way latch so a dip below ¥50k stops adds but never
  force-liquidates);
* the value sleeve's **target capital** via a glide path (the bigger the account,
  the larger the value weight — ballast grows with size);
* the **per-sleeve position caps** (short ≤5 / value ≤3) that the RiskEngine
  check #6 will consume, replacing the single ≤5 pool *only when active* (so the
  current sub-¥50k, switch-off state stays byte-identical to today).

Pure + deterministic + 0 LLM, config-driven (``config/sleeve_policy.yaml``,
runtime-immutable). Must NOT import ``backend.{llm,agents,mirofish}`` — it is a
quant policy, like ``backend.budget_policy``.
"""

from backend.sleeve_policy.policy import (
    Sleeve,
    SleeveCaps,
    SleevePolicy,
    SleevePolicyConfig,
    SleevePolicyError,
    load_sleeve_policy_config,
)

__all__ = [
    "Sleeve",
    "SleeveCaps",
    "SleevePolicy",
    "SleevePolicyConfig",
    "SleevePolicyError",
    "load_sleeve_policy_config",
]
