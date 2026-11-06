"""Tests for creating and delivering membership payment invoices."""

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
    get_or_create_payment_invoice,
)
from core.nwc_client import NwcInvoice


NWC_URI = (
    "nostr+walletconnect://wallet-key?"
    "relay=wss%3A%2F%2Frelay.example&secret=test-secret"
)


def _config(tmp_path):
    """Build enabled invoice settings for scheduler tests."""
    return {
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
        "PAYMENT_VERIFICATION_ENABLED": True,
        "PAYMENT_NWC_URI": NWC_URI,
        "PAYMENT_INVOICE_AMOUNT_MSAT": 25_000,
        "PAYMENT_INVOICE_EXPIRY_SECONDS": 604_800,
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_ADVANCE_REMINDER_DAYS": 3,
    }


class FakeNwcInvoiceClient:
    """Return deterministic invoices without contacting a wallet service."""

    calls = []

    def __init__(self, connection_uri):
        """Record the configured connection without exposing it in assertions."""
        self.connection_uri = connection_uri

    async def make_invoice(self, *, amount_msat, description, expiry_seconds):
        """Return a representative invoice response."""
        self.calls.append((amount_msat, description, expiry_seconds))
        return NwcInvoice(
            invoice="lnbc-test-invoice",
            payment_hash="hash-test-invoice",
            amount_msat=amount_msat,
            expires_at=1_800_000_000,
        )


def test_invoice_creation_is_reused_for_the_same_member_period(monkeypatch, tmp_path):
    """A pending invoice is reused instead of creating a second invoice."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    member = build_member("Ada", 101)
    state = build_multi_period_payment_state(
        {"member-001": member},
        ["2026-11"],
    )
    config = _config(tmp_path)
    now = datetime(2026, 11, 1, 10, 0, tzinfo=PAYMENT_TIMEZONE)

    first, first_created = asyncio.run(
        get_or_create_payment_invoice(
            state,
            "2026-11",
            "member-001",
            config,
            now=now,
        )
    )
    second, second_created = asyncio.run(
        get_or_create_payment_invoice(
            state,
            "2026-11",
            "member-001",
            config,
            now=now,
        )
    )

    assert first["invoice"] == second["invoice"]
    assert first_created is True
    assert second_created is False
    assert len(FakeNwcInvoiceClient.calls) == 1


def test_due_private_reminder_includes_created_invoice(monkeypatch, tmp_path):
    """The private reminder contains the invoice and persists its metadata."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    member = build_member("Ada", 101)
    state = {
        "members": {"member-001": member},
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member},
            ["2026-11"],
        ),
    }

    asyncio.run(
        check_payment_reminders(
            context,
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 1, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )

    text = bot.send_message.await_args.kwargs["text"]
    assert "lnbc-test-invoice" in text
    member_state = state["payment_state"]["periods"]["2026-11"]["members"][
        "member-001"
    ]
    assert member_state["active_invoice_payment_hash"] == "hash-test-invoice"
    assert "2026-11:first_day:member-001" in state["payment_state"]["periods"][
        "2026-11"
    ]["personal_reminders_sent"]


def test_invoice_failure_does_not_block_normal_private_reminder(
    monkeypatch,
    tmp_path,
):
    """A temporary wallet failure still delivers the ordinary reminder."""

    class FailingNwcInvoiceClient:
        """Simulate an unavailable wallet service."""

        def __init__(self, connection_uri):
            """Accept the connection URI without storing or logging it."""

        async def make_invoice(self, **kwargs):
            """Fail as an unavailable wallet would."""
            raise RuntimeError("wallet unavailable")

    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FailingNwcInvoiceClient,
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    member = build_member("Ada", 101)
    state = {
        "members": {"member-001": member},
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member},
            ["2026-11"],
        ),
    }

    asyncio.run(
        check_payment_reminders(
            context,
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 1, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )

    text = bot.send_message.await_args.kwargs["text"]
    assert "Ada" in text
    assert "lnbc-" not in text
    member_state = state["payment_state"]["periods"]["2026-11"]["members"][
        "member-001"
    ]
    assert "payment_invoices" not in member_state
