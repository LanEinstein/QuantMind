"""BrokerRegistry — manages multiple virtual trading accounts."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from pydantic import BaseModel, ConfigDict

from backend.broker.mock_broker import MockBroker
from backend.broker.models import BrokerConfig

log = structlog.get_logger(component="broker.registry")


class AccountMeta(BaseModel):
    """Immutable metadata for a virtual trading account."""

    model_config = ConfigDict(frozen=True)

    account_id: str
    label: str
    created_at: str


class BrokerRegistry:
    """Manages multiple MockBroker instances keyed by account_id.

    Each virtual account is an independent MockBroker with its own
    capital, positions, orders, and trades.
    """

    def __init__(self, default_config: BrokerConfig) -> None:
        self._default_config = default_config
        self._brokers: dict[str, MockBroker] = {}
        self._accounts: dict[str, AccountMeta] = {}

        # Auto-create the default account
        self.create_account("default", "策略A (默认)")

    def create_account(
        self,
        account_id: str,
        label: str,
        config: BrokerConfig | None = None,
    ) -> AccountMeta:
        """Create a new virtual trading account.

        Args:
            account_id: Unique identifier for the account.
            label: Human-readable label.
            config: Optional custom broker config; defaults to the registry config.

        Returns:
            Account metadata.

        Raises:
            ValueError: If account_id already exists.
        """
        if account_id in self._brokers:
            raise ValueError(f"Account '{account_id}' already exists")

        effective_config = config or self._default_config
        broker = MockBroker(effective_config)
        now = datetime.now(tz=timezone.utc).isoformat()

        meta = AccountMeta(
            account_id=account_id,
            label=label,
            created_at=now,
        )
        self._brokers[account_id] = broker
        self._accounts[account_id] = meta

        log.info("account_created", account_id=account_id, label=label)
        return meta

    def get_broker(self, account_id: str) -> MockBroker:
        """Get the MockBroker instance for a given account.

        Args:
            account_id: The account identifier.

        Returns:
            The MockBroker instance.

        Raises:
            KeyError: If account_id does not exist.
        """
        try:
            return self._brokers[account_id]
        except KeyError:
            raise KeyError(f"Account '{account_id}' not found") from None

    def list_accounts(self) -> tuple[AccountMeta, ...]:
        """List all registered accounts."""
        return tuple(self._accounts.values())

    def has_account(self, account_id: str) -> bool:
        """Check if an account exists."""
        return account_id in self._brokers
