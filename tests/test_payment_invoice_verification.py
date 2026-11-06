"""Tests for verifying membership invoices through NWC."""

import asyncio
from datetime import datetime, timezone

from core import membership_payments
from core.membership_payments import (
    build_member,
    build_multi_period_payment_state,
    check_payment_invoice_status,
    record_payment_invoice,
)
from core.nwc_client import NwcInvoiceStatus
from tools.local.data_manager import load_json


NWC_URI = (
    "nostr+walletconnect://wallet-key?"
    "relay=wss%3A%2F%2Frelay.example&secret=test-secret"
)


def _invoice_state():
    """Build payment state with one pending invoice."""
    member = build_member("Ada", 101)
    payment_state = build_multi_period_payment_state(
        {"member-001": member},
        ["2026-11"],
    )
    return record_payment_invoice(
        payment_state,
        "2026-11",
        "member-001",
        {
            "invoice": "lnbc-test-invoice",
            "payment_hash": "hash-test-invoice",
            "amount_msat": 25_000,
            "expires_at": 1_800_000_000,
        },
    )


def _config(tmp_path):
    """Build enabled verification settings for tests."""
    return {
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
        "PAYMENT_VERIFICATION_ENABLED": True,
        "PAYMENT_NWC_URI": NWC_URI,
        "PAYMENT_INVOICE_AMOUNT_MSAT": 25_000,
        "PAYMENT_INVOICE_EXPIRY_SECONDS": 604_800,
    }


class FakeNwcInvoiceClient:
    """Return a predetermined invoice status without contacting NWC."""

    status = None
    calls = []

    def __init__(self, connection_uri):
        """Accept the configured wallet connection."""
        self.connection_uri = connection_uri

    async def lookup_invoice(self, *, payment_hash):
        """Return the configured status for the requested payment hash."""
        self.calls.append(payment_hash)
        return self.status


def test_settled_invoice_marks_matching_member_period_paid(monkeypatch, tmp_path):
    """Only a matching settled invoice changes the member payment status."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash="hash-test-invoice",
        state="settled",
        amount_msat=25_000,
        settled_at=1_800_000_100,
    )
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    state = {"payment_state": _invoice_state()}
    now = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)

    result = asyncio.run(
        check_payment_invoice_status(_config(tmp_path), state, now=now)
    )

    member = state["payment_state"]["periods"]["2026-11"]["members"][
        "member-001"
    ]
    invoice = member["payment_invoices"]["hash-test-invoice"]
    assert result == {"checked": 1, "settled": 1, "failed": 0}
    assert FakeNwcInvoiceClient.calls == ["hash-test-invoice"]
    assert member["status"] == "paid"
    assert member["paid_at"] == "2027-01-15T08:01:40+00:00"
    assert invoice["state"] == "settled"
    assert member["active_invoice_payment_hash"] is None
    persisted = load_json(_config(tmp_path)["PAYMENT_STATE_FILE"], {})
    assert persisted["periods"]["2026-11"]["members"]["member-001"][
        "status"
    ] == "paid"


def test_pending_invoice_is_checked_but_member_remains_unpaid(monkeypatch, tmp_path):
    """A pending wallet response records the check without marking payment."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash="hash-test-invoice",
        state="pending",
        amount_msat=25_000,
        settled_at=None,
    )
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    state = {"payment_state": _invoice_state()}
    now = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)

    result = asyncio.run(
        check_payment_invoice_status(_config(tmp_path), state, now=now)
    )

    member = state["payment_state"]["periods"]["2026-11"]["members"][
        "member-001"
    ]
    assert result == {"checked": 1, "settled": 0, "failed": 0}
    assert member["status"] == "unpaid"
    assert member["payment_invoices"]["hash-test-invoice"]["state"] == "pending"
    assert member["payment_invoices"]["hash-test-invoice"]["last_checked_at"] == (
        "2026-11-02T10:00:00+00:00"
    )


def test_mismatched_payment_hash_is_not_marked_paid(monkeypatch, tmp_path):
    """A response for another hash is recorded as unmatched and stays unpaid."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash="different-hash",
        state="settled",
        amount_msat=25_000,
        settled_at=1_800_000_100,
    )
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    state = {"payment_state": _invoice_state()}

    result = asyncio.run(
        check_payment_invoice_status(
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc),
        )
    )

    member = state["payment_state"]["periods"]["2026-11"]["members"][
        "member-001"
    ]
    assert result == {"checked": 1, "settled": 0, "failed": 0}
    assert member["status"] == "unpaid"
    assert member["payment_invoices"]["hash-test-invoice"]["state"] == "unmatched"
    assert member["active_invoice_payment_hash"] is None
