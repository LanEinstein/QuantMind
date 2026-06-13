"""O-002 MiroFish sector rise-probability forecast (evidence-only).

The LLM layer of the MiroFish core: it reads the deterministic info
digest (O-001) and produces a "which sectors are likely to outperform"
research view — bounded sector scores + causal chains + an *uncalibrated*
probability estimate + an explicit uncertainty label per sector.

Boundary (P0-8-amendment-2026-05-24, locked):

* Output is **evidence only** — persisted through
  :mod:`backend.mirofish.output_writer` with the ``MIROFISH-`` prefix;
  zero decision / candidate-inclusion / size / direction / risk fields.
* The forecast can only name sectors that exist in the digest's
  sector-heat vocabulary — a hallucinated sector name is dropped at
  parse time, so the downstream bounded rerank (O-003) can never be
  steered by an out-of-universe label.
* All failure modes (LLM error, parse failure, empty digest) degrade to
  ``None`` — the pure-quant pipeline never depends on this module.
* ``probability_up`` is explicitly labeled uncalibrated; the O-005
  forecast ledger scores it against realized sector returns so its
  actual calibration is measured, not assumed.

The LLM caller is injected (``LlmCaller``) so tests run with zero real
LLM and the orchestration layer decides routing/cost-gating.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.mirofish.info_digest import InfoDigest, render_digest_text

log = structlog.get_logger(component="mirofish.sector_forecast")

# Forecast horizon in trading days — the O-005 ledger scores a forecast
# exactly this many trading days after its trade_date.
DEFAULT_HORIZON_DAYS = 5

# Hard cap on sectors per forecast (prompt asks for it; the parser
# enforces it) — keeps the advisory surface small and the evidence doc
# readable.
MAX_FORECAST_SECTORS = 8

# Versioned payload marker — bump when the payload shape changes so the
# O-005 scorer / O-003 reader can fail closed on an unknown shape.
FORECAST_SCHEMA_VERSION = "mirofish.sector_forecast/v1"

LlmCaller = Callable[[str, str], Awaitable[str]]
"""Injected async LLM call: ``(system_prompt, user_content) -> raw text``."""

UncertaintyLabel = Literal["low", "medium", "high"]

SECTOR_FORECAST_PROMPT = """你是市场情报研判员(MiroFish 板块推演)。
下面是一份确定性聚合的市场信息汇总文档
(指数趋势/情绪代理/板块热度/关联板块/多域新闻)。

任务:研判未来 {horizon} 个交易日内,哪些板块的股票/ETF 大概率相对走强或走弱。

硬性要求:
1. 只能从文档『板块热度』一节出现过的板块名中选择,最多 {max_sectors} 个。
2. 只输出严格 JSON,不要任何 JSON 之外的文字:
{{"forecasts": [{{"sector": "板块名", "score": 0.0,
  "probability_up": 0.5,
  "causal_chain": "信息→板块的因果传导链,引用文档内容",
  "uncertainty": "low|medium|high"}}]}}
3. score ∈ [-1, 1]:相对强弱观点(正=相对走强);
   probability_up ∈ [0, 1]:未经校准的主观概率估计;
   uncertainty:你对该判断的不确定性。
4. 这是研究观点(evidence),不是交易指令——
   禁止输出任何买卖方向、数量、价格、仓位字段。"""


class SectorForecastEntry(BaseModel):
    """One sector's research view (strict, frozen, bounded fields)."""

    model_config = ConfigDict(frozen=True, strict=False, extra="forbid")

    sector: str = Field(min_length=1, max_length=64)
    score: float = Field(ge=-1.0, le=1.0)
    probability_up: float = Field(ge=0.0, le=1.0)
    causal_chain: str = Field(min_length=1, max_length=2000)
    uncertainty: UncertaintyLabel


class SectorForecast(BaseModel):
    """A full forecast for one trade date (frozen)."""

    model_config = ConfigDict(frozen=True, strict=False, extra="forbid")

    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    horizon_days: int = Field(ge=1, le=20)
    digest_sha256: str = Field(min_length=64, max_length=64)
    entries: tuple[SectorForecastEntry, ...] = Field(min_length=1)

    def to_payload(self) -> dict[str, object]:
        """Structured payload stored inside the MIROFISH- evidence doc.

        This is the single machine-readable channel: O-003 reads it to
        build bounded advisory signals; O-005 reads it to score the
        forecast after the horizon. It stays inside ``evidence_collection``
        so the "MiroFish writes only evidence" red line holds.
        """
        return {
            "schema_version": FORECAST_SCHEMA_VERSION,
            "trade_date": self.trade_date,
            "horizon_days": self.horizon_days,
            "digest_sha256": self.digest_sha256,
            "probability_note": "uncalibrated_llm_estimate",
            "entries": [
                {
                    "sector": e.sector,
                    "score": e.score,
                    "probability_up": e.probability_up,
                    "causal_chain": e.causal_chain,
                    "uncertainty": e.uncertainty,
                }
                for e in self.entries
            ],
        }


def digest_sha256(digest_text: str) -> str:
    """Lineage hash of the exact digest text the LLM saw (PIT replay aid)."""
    return hashlib.sha256(digest_text.encode("utf-8")).hexdigest()


def _extract_json(raw: str) -> dict[str, object] | None:
    """Pull the first JSON object out of an LLM response (fences tolerated)."""
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_forecast_response(
    raw: str,
    *,
    allowed_sectors: Iterable[str],
    trade_date: str,
    digest_sha: str,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> SectorForecast | None:
    """Strict-parse an LLM response into a :class:`SectorForecast`.

    Fail-closed per entry and overall: malformed entries are dropped,
    out-of-vocabulary sectors are dropped (anti-hallucination), duplicate
    sectors keep the first occurrence, the entry count is capped at
    :data:`MAX_FORECAST_SECTORS`, and zero surviving entries → ``None``.
    """
    allowed = {s for s in allowed_sectors if s}
    payload = _extract_json(raw)
    if payload is None:
        log.warning("forecast_parse_no_json", trade_date=trade_date)
        return None
    forecasts = payload.get("forecasts")
    if not isinstance(forecasts, list):
        log.warning("forecast_parse_bad_shape", trade_date=trade_date)
        return None

    entries: list[SectorForecastEntry] = []
    seen: set[str] = set()
    for item in forecasts:
        if not isinstance(item, dict):
            continue
        try:
            entry = SectorForecastEntry.model_validate(item)
        except ValidationError:
            log.info("forecast_entry_invalid", trade_date=trade_date)
            continue
        if entry.sector not in allowed:
            log.info(
                "forecast_entry_out_of_vocabulary",
                trade_date=trade_date,
                sector=entry.sector,
            )
            continue
        if entry.sector in seen:
            continue
        seen.add(entry.sector)
        entries.append(entry)
        if len(entries) >= MAX_FORECAST_SECTORS:
            break

    if not entries:
        log.warning("forecast_parse_zero_entries", trade_date=trade_date)
        return None
    try:
        return SectorForecast(
            trade_date=trade_date,
            horizon_days=horizon_days,
            digest_sha256=digest_sha,
            entries=tuple(entries),
        )
    except ValidationError:
        log.warning("forecast_model_invalid", trade_date=trade_date)
        return None


class SectorForecaster:
    """One-LLM-call sector forecaster over the O-001 digest.

    Cost gating is the caller's job (the EOD runner reserves a
    ``cost_guard`` slot before invoking :meth:`forecast`); this class
    only shapes the prompt, calls the injected LLM, and strict-parses
    the response. It never raises — every failure returns ``None``.
    """

    def __init__(
        self,
        caller: LlmCaller,
        *,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
    ) -> None:
        self._caller = caller
        self._horizon_days = horizon_days
        self._log = log

    async def forecast(self, digest: InfoDigest) -> SectorForecast | None:
        """Produce a forecast for ``digest``; ``None`` on any degradation."""
        if not digest.sector_heat:
            self._log.info(
                "forecast_skipped_no_sectors", trade_date=digest.trade_date
            )
            return None
        digest_text = render_digest_text(digest)
        sha = digest_sha256(digest_text)
        prompt = SECTOR_FORECAST_PROMPT.format(
            horizon=self._horizon_days, max_sectors=MAX_FORECAST_SECTORS
        )
        try:
            raw = await self._caller(prompt, digest_text)
        except Exception as exc:  # noqa: BLE001 — fail-closed to None
            self._log.warning(
                "forecast_llm_call_failed",
                trade_date=digest.trade_date,
                error=str(exc),
            )
            return None
        if not raw or not raw.strip():
            self._log.warning(
                "forecast_llm_empty_response", trade_date=digest.trade_date
            )
            return None
        return parse_forecast_response(
            raw,
            allowed_sectors=digest.sector_names,
            trade_date=digest.trade_date,
            digest_sha=sha,
            horizon_days=self._horizon_days,
        )


__all__ = [
    "DEFAULT_HORIZON_DAYS",
    "FORECAST_SCHEMA_VERSION",
    "LlmCaller",
    "MAX_FORECAST_SECTORS",
    "SECTOR_FORECAST_PROMPT",
    "SectorForecast",
    "SectorForecastEntry",
    "SectorForecaster",
    "digest_sha256",
    "parse_forecast_response",
]
