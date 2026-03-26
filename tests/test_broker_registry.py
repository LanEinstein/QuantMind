"""Tests for BrokerRegistry multi-account management."""

from __future__ import annotations

import pytest

from backend.broker.models import BrokerConfig
from backend.broker.registry import BrokerRegistry


@pytest.fixture()
def config() -> BrokerConfig:
    return BrokerConfig(initial_capital=500_000.0)


@pytest.fixture()
def registry(config: BrokerConfig) -> BrokerRegistry:
    return BrokerRegistry(config)


class TestBrokerRegistry:
    def test_default_account_created(self, registry: BrokerRegistry) -> None:
        accounts = registry.list_accounts()
        assert len(accounts) == 1
        assert accounts[0].account_id == "default"
        assert accounts[0].label == "策略A (默认)"

    def test_create_account(self, registry: BrokerRegistry) -> None:
        meta = registry.create_account("conservative", "策略B (保守)")
        assert meta.account_id == "conservative"
        assert meta.label == "策略B (保守)"
        assert len(registry.list_accounts()) == 2

    def test_create_duplicate_raises(self, registry: BrokerRegistry) -> None:
        with pytest.raises(ValueError, match="already exists"):
            registry.create_account("default", "Duplicate")

    def test_get_broker(self, registry: BrokerRegistry) -> None:
        broker = registry.get_broker("default")
        assert broker is not None

    def test_get_broker_missing_raises(self, registry: BrokerRegistry) -> None:
        with pytest.raises(KeyError, match="not found"):
            registry.get_broker("nonexistent")

    def test_has_account(self, registry: BrokerRegistry) -> None:
        assert registry.has_account("default") is True
        assert registry.has_account("missing") is False

    def test_custom_config(self, registry: BrokerRegistry) -> None:
        custom = BrokerConfig(initial_capital=2_000_000.0)
        registry.create_account("big", "Big Fund", config=custom)
        broker = registry.get_broker("big")
        assert broker._config.initial_capital == 2_000_000.0


class TestAccountIsolation:
    @pytest.mark.asyncio
    async def test_accounts_have_independent_capital(
        self, registry: BrokerRegistry
    ) -> None:
        registry.create_account("second", "Account 2")
        acct1 = await registry.get_broker("default").get_account()
        acct2 = await registry.get_broker("second").get_account()
        assert acct1.initial_capital == acct2.initial_capital
        # They are distinct instances
        assert acct1 is not acct2

    @pytest.mark.asyncio
    async def test_positions_are_independent(
        self, registry: BrokerRegistry
    ) -> None:
        pos1 = await registry.get_broker("default").get_positions()
        registry.create_account("other", "Other")
        pos2 = await registry.get_broker("other").get_positions()
        assert pos1 == ()
        assert pos2 == ()
