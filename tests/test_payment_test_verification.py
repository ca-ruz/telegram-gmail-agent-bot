"""Tests for isolated payment-test settlement polling."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core import membership_payments
from core.nwc_client import NwcInvoiceStatus
from core.payment_test_state import build_payment_test_state, record_payment_test_invoice


NWC_URI = (
    "nostr+walletconnect://wallet-key?"
    "relay=wss%3A%2F%2Frelay.example&secret=test-secret"
)


def _test_state(expires_at=1_800_000_000):
    """Build an isolated state containing one pending 100-sat invoice."""
    return record_payment_test_invoice(
        build_payment_test_state(),
        {
            "invoice": "lnbc-payment-test",
            "payment_hash": "payment-test-hash",
            "amount_msat": 100_000,
            "expires_at": expires_at,
        },
        100,
        "test-001",
    )


def _config(tmp_path):
    """Build enabled NWC settings for payment-test polling."""
    return {
        "ADMIN_ID": 42,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
        "PAYMENT_TEST_STATE_FILE": str(tmp_path / "payment_test_state.json"),
        "PAYMENT_VERIFICATION_ENABLED": True,
        "PAYMENT_NWC_URI": NWC_URI,
        "PAYMENT_INVOICE_EXPIRY_SECONDS": 259200,
    }


class FakeNwcInvoiceClient:
    """Return a predetermined test-invoice status."""

    status = None
    calls = []

    def __init__(self, connection_uri):
        """Accept the configured NWC connection."""

    async def lookup_invoice(self, *, payment_hash):
        """Return the configured status for the requested hash."""
        self.calls.append(payment_hash)
        return self.status


def _context():
    """Build a context with a private administrator notification sink."""
    return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))


def test_settled_test_invoice_notifies_admin_without_changing_production_state(
    monkeypatch,
    tmp_path,
):
    """Settlement updates only test state and sends a private confirmation."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash="payment-test-hash",
        state="settled",
        amount_msat=100_000,
        settled_at=1_800_000_100,
    )
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    context = _context()
    state = {
        "payment_test_state": _test_state(),
        "payment_state": {"version": 2, "periods": {}},
    }

    result = asyncio.run(
        membership_payments.check_payment_test_invoice_status(
            context,
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc),
        )
    )

    assert result == {"checked": 1, "settled": 1, "failed": 0}
    assert FakeNwcInvoiceClient.calls == ["payment-test-hash"]
    assert state["payment_test_state"]["active_invoice"]["state"] == "settled"
    assert state["payment_state"] == {"version": 2, "periods": {}}
    assert "no modificó ninguna membresía real" in (
        context.bot.send_message.await_args.kwargs["text"]
    )


def test_pending_test_invoice_records_last_check_without_notification(
    monkeypatch,
    tmp_path,
):
    """Pending test invoices remain pending and do not notify the administrator."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash="payment-test-hash",
        state="pending",
        amount_msat=100_000,
        settled_at=None,
    )
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    context = _context()
    state = {"payment_test_state": _test_state()}
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)

    result = asyncio.run(
        membership_payments.check_payment_test_invoice_status(
            context,
            _config(tmp_path),
            state,
            now=now,
        )
    )

    assert result == {"checked": 1, "settled": 0, "failed": 0}
    assert state["payment_test_state"]["active_invoice"]["state"] == "pending"
    assert context.bot.send_message.await_count == 0


def test_wrong_test_invoice_amount_becomes_unmatched(monkeypatch, tmp_path):
    """A settled response with a wrong amount cannot pass the test."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash="payment-test-hash",
        state="settled",
        amount_msat=99_000,
        settled_at=1_800_000_100,
    )
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    context = _context()
    state = {"payment_test_state": _test_state()}

    result = asyncio.run(
        membership_payments.check_payment_test_invoice_status(
            context,
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc),
        )
    )

    assert result == {"checked": 1, "settled": 0, "failed": 0}
    assert state["payment_test_state"]["active_invoice"]["state"] == "unmatched"
    assert context.bot.send_message.await_count == 0


def test_expired_test_invoice_is_closed_without_lookup(monkeypatch, tmp_path):
    """Expired test invoices are closed without contacting NWC."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    context = _context()
    state = {"payment_test_state": _test_state(expires_at=1)}

    result = asyncio.run(
        membership_payments.check_payment_test_invoice_status(
            context,
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc),
        )
    )

    assert result == {"checked": 1, "settled": 0, "failed": 0}
    assert FakeNwcInvoiceClient.calls == []
    assert state["payment_test_state"]["active_invoice"]["state"] == "expired"
