"""Load and validate data_sources.yaml into frozen Pydantic models."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict


class MarketDataConfig(BaseModel):
    """Market data source configuration."""

    model_config = ConfigDict(frozen=True)

    primary: str
    fallback: str
    refresh_interval_seconds: int = 30


class HistoryDataConfig(BaseModel):
    """Historical data source configuration."""

    model_config = ConfigDict(frozen=True)

    primary: str
    fallback: str
    default_period: str = "1y"


class NewsConfig(BaseModel):
    """News crawler configuration."""

    model_config = ConfigDict(frozen=True)

    refresh_interval_seconds: int = 300
    max_articles_per_fetch: int = 50
    importance_threshold: int = 5


class TushareEndpointConfig(BaseModel):
    """A single Tushare Pro endpoint descriptor (K-001).

    ``vip`` marks the 5000档 endpoints; ``key`` is the primary query
    argument (``trade_date`` / ``period`` / ``ts_code``).
    """

    model_config = ConfigDict(frozen=True)

    vip: bool = False
    key: str


class TushareConfig(BaseModel):
    """Tushare Pro full-market scan layer (K-001 / P0-8-amendment-2026-05-24).

    Official SDK only; ``token_env`` names the os.environ var holding the
    heterogeneous ``TUSHARE_TOKEN`` (never .env). ``fallback`` lists the
    best-effort degrade providers in priority order.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    token_env: str = "TUSHARE_TOKEN"
    endpoints: dict[str, TushareEndpointConfig] = {}
    fallback: tuple[str, ...] = ()


class DataSourcesConfig(BaseModel):
    """Root configuration loaded from data_sources.yaml."""

    model_config = ConfigDict(frozen=True)

    market_data: MarketDataConfig
    history_data: HistoryDataConfig
    news: NewsConfig
    tushare: TushareConfig | None = None


def load_data_sources_config(yaml_path: str | Path) -> DataSourcesConfig:
    """Load and validate data sources configuration from YAML.

    Returns an immutable DataSourcesConfig instance.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        pydantic.ValidationError: If the schema is invalid.
    """
    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    return DataSourcesConfig.model_validate(raw)
