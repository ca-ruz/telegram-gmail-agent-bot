"""Integration coverage for plan-based membership invoice creation."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core import membership_payments
from core.membership_payments import (
    PAYMENT_TIMEZONE,
    build_member,
    build_multi_period_payment_state,
    check_payment_reminders,
)
from core.nwc_client import NwcInvoice


class IntegrationNwcClient:
    """Capture generated invoices without contacting a wallet."""

    calls = []

    def __init__(self, connection_uri):
        """Accept the configured NWC connection."""

    async def make_invoice(self, *, amount_msat, description, expiry_seconds):
        """Return an invoice containing the calculated amount and description."""
        self.calls.append((amount_msat, description, expiry_seconds))
        return NwcInvoice(
            invoice="lnbc-integration-test",
            payment_hash="integration-hash",
            amount_msat=amount_msat,
            expires_at=1_800_000_000,
        )


class IntegrationRateProvider:
    """Return a deterministic exchange rate for the integration test."""

    def __init__(self, *args, **kwargs):
        """Accept production provider settings."""

    def get_rate(self, now=None):
        """Return the fixed MXN-per-BTC test rate."""
        return 1_600_000


def test_payment_reminder_creates_invoice_from_member_plan(monkeypatch, tmp_path):
    """A scheduled reminder uses the member plan through the full invoice path."""
    IntegrationNwcClient.calls = []
    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", IntegrationNwcClient)
    monkeypatch.setattr(
        membership_payments,
        "MxnBtcRateProvider",
        IntegrationRateProvider,
    )
    member = build_member("Ada", 101, "private_office", 1.5)
    state = {
        "members": {"member-001": member},
        "membership_plans": {
            "private_office": {"name": "Private office", "price_mxn": 5500},
        },
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member},
            ["2026-11"],
        ),
    }
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    config = {
        "ENABLE_PAYMENT_REMINDERS": True,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
        "PAYMENT_VERIFICATION_ENABLED": True,
        "PAYMENT_NWC_URI": (
            "nostr+walletconnect://wallet-key?"
            "relay=wss%3A%2F%2Frelay.example&secret=test"
        ),
        "PAYMENT_INVOICE_EXPIRY_SECONDS": 259200,
        "PAYMENT_EXCHANGE_RATE_URL": "https://example.test/rate",
        "PAYMENT_EXCHANGE_RATE_CACHE_FILE": str(tmp_path / "rate.json"),
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_ADVANCE_REMINDER_DAYS": 3,
    }

    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 11, 1, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )

    assert IntegrationNwcClient.calls == [
        (515_625_000, "Ada - membresía noviembre de 2026", 259200),
        (515_625_000, "Ada - membresía octubre de 2026", 259200),
    ]
    invoice = state["payment_state"]["periods"]["2026-11"]["members"][
        "member-001"
    ]["payment_invoices"]["integration-hash"]
    assert invoice["pricing_snapshot"] == {
        "plan": "private_office",
        "quantity": 1.5,
        "amount_mxn": "8250.0",
        "mxn_per_btc": "1600000",
    }
