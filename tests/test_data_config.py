"""Tests for data sources config loader (TDD RED phase)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.data.config import DataSourcesConfig, load_data_sources_config

VALID_CONFIG_YAML = """\
market_data:
  primary: adata
  fallback: akshare
  refresh_interval_seconds: 30

history_data:
  primary: adata
  fallback: baostock
  default_period: 1y

news:
  refresh_interval_seconds: 300
  max_articles_per_fetch: 50
  importance_threshold: 5
"""


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    """Write a valid data_sources.yaml to a temp dir."""
    path = tmp_path / "data_sources.yaml"
    path.write_text(VALID_CONFIG_YAML, encoding="utf-8")
    return path


class TestLoadDataSourcesConfig:
    """Tests for load_data_sources_config function."""

    def test_load_valid(self, config_path: Path) -> None:
        config = load_data_sources_config(config_path)
        assert isinstance(config, DataSourcesConfig)
        assert config.market_data.primary == "adata"
        assert config.market_data.fallback == "akshare"
        assert config.market_data.refresh_interval_seconds == 30

    def test_history_data(self, config_path: Path) -> None:
        config = load_data_sources_config(config_path)
        assert config.history_data.primary == "adata"
        assert config.history_data.fallback == "baostock"
        assert config.history_data.default_period == "1y"

    def test_news_config(self, config_path: Path) -> None:
        config = load_data_sources_config(config_path)
        assert config.news.refresh_interval_seconds == 300
        assert config.news.max_articles_per_fetch == 50
        assert config.news.importance_threshold == 5

    def test_config_is_frozen(self, config_path: Path) -> None:
        config = load_data_sources_config(config_path)
        with pytest.raises(ValidationError):
            config.market_data.primary = "other"  # type: ignore[misc]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_data_sources_config(tmp_path / "nonexistent.yaml")

    def test_invalid_schema_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("invalid_key: true", encoding="utf-8")
        with pytest.raises(ValidationError):
            load_data_sources_config(path)
