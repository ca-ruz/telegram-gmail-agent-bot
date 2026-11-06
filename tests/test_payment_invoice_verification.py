"""Tests for verifying membership invoices through NWC."""

import asyncio
from datetime import datetime, timezone

from core import membership_payments
from core.membership_payments import (
    build_member,
    build_multi_period_payment_state,
    check_payment_invoice_status,
    get_or_create_payment_invoice,
    record_payment_invoice,
)
from core.nwc_client import NwcInvoice, NwcInvoiceStatus
from tools.local.data_manager import load_json


NWC_URI = (
    "nostr+walletconnect://wallet-key?"
    "relay=wss%3A%2F%2Frelay.example&secret=test-secret"
)


def _invoice_state():
    """Build payment state with one pending invoice."""
    member = build_member("Ada", 101, "coworking")
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
        "PAYMENT_INVOICE_EXPIRY_SECONDS": 259_200,
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


class FakeMxnBtcRateProvider:
    """Return the fixed rate used by invoice creation tests."""

    def __init__(self, *args, **kwargs):
        """Accept production provider settings for the test double."""

    def get_rate(self, now=None):
        """Return a deterministic MXN-per-BTC rate."""
        return 1_600_000


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


def test_expired_invoice_is_closed_without_a_wallet_lookup(monkeypatch, tmp_path):
    """A persisted expiry prevents stale invoices from being queried forever."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    state = {"payment_state": _invoice_state()}
    member = state["payment_state"]["periods"]["2026-11"]["members"][
        "member-001"
    ]
    member["payment_invoices"]["hash-test-invoice"]["expires_at"] = 1

    result = asyncio.run(
        check_payment_invoice_status(
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc),
        )
    )

    assert result == {"checked": 1, "settled": 0, "failed": 0}
    assert FakeNwcInvoiceClient.calls == []
    assert member["status"] == "unpaid"
    assert member["payment_invoices"]["hash-test-invoice"]["state"] == "expired"
    assert member["active_invoice_payment_hash"] is None


def test_invoice_creation_rejects_unexpected_amount(monkeypatch, tmp_path):
    """A wallet response with the wrong amount is never persisted."""

    class WrongAmountNwcClient:
        """Return an invoice that does not match configured pricing."""

        def __init__(self, connection_uri):
            """Accept the configured wallet connection."""

        async def make_invoice(self, **kwargs):
            """Return an invoice with a conflicting amount."""
            return NwcInvoice(
                invoice="lnbc-wrong-amount",
                payment_hash="hash-wrong-amount",
                amount_msat=1_000,
                expires_at=1_800_000_000,
            )

    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        WrongAmountNwcClient,
    )
    monkeypatch.setattr(
        membership_payments,
        "MxnBtcRateProvider",
        FakeMxnBtcRateProvider,
    )
    member = build_member("Ada", 101, "coworking")
    state = build_multi_period_payment_state(
        {"member-001": member},
        ["2026-11"],
    )

    invoice, created = asyncio.run(
        get_or_create_payment_invoice(
            state,
            "2026-11",
            "member-001",
            _config(tmp_path),
            member=member,
            membership_plans={"coworking": {"price_mxn": 1100}},
        )
    )

    assert invoice is None
    assert created is False
    assert "payment_invoices" not in state["periods"]["2026-11"]["members"][
        "member-001"
    ]


def test_settled_invoice_with_wrong_amount_stays_unpaid(monkeypatch, tmp_path):
    """A conflicting settled amount cannot confirm the member payment."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash="hash-test-invoice",
        state="settled",
        amount_msat=1_000,
        settled_at=1_800_000_100,
    )
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    state = {"payment_state": _invoice_state()}

    asyncio.run(
        check_payment_invoice_status(
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc),
        )
    )

    member = state["payment_state"]["periods"]["2026-11"]["members"][
        "member-001"
    ]
    assert member["status"] == "unpaid"
    assert member["payment_invoices"]["hash-test-invoice"]["state"] == "unmatched"


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
