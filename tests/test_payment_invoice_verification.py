"""Tests for verifying membership invoices through NWC."""

import asyncio
from datetime import datetime, timezone

from core import membership_payments
from core.membership_payments import (
    build_member,
    build_multi_period_payment_state,
    check_payment_invoice_status,
    get_or_create_payment_invoice,
    load_multi_period_payment_state,
    record_payment_invoice,
)
from core.nwc_client import NwcInvoice, NwcInvoiceClient, NwcInvoiceStatus
from tools.local.data_manager import load_json


NWC_URI = (
    "nostr+walletconnect://wallet-key?"
    "relay=wss%3A%2F%2Frelay.example&secret=test-secret"
)
TEST_PAYMENT_HASH = (
    "66687aadf862bd776c8fc18b8e9f8e20089714856ee233b3902a591d0d5f2925"
)
TEST_PAYMENT_PREIMAGE = "00" * 32


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
            "payment_hash": TEST_PAYMENT_HASH,
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


class SettledRawNwcSession:
    """Return a paid wallet response with no optional NIP-47 state field."""

    async def __aenter__(self):
        """Open the fake session."""
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Close the fake session."""
        return None

    async def _send_request(self, method, params):
        """Return the wallet's timestamp and exact invoice proof."""
        assert method == "lookup_invoice"
        assert params == {"payment_hash": TEST_PAYMENT_HASH}
        return {
            "payment_hash": TEST_PAYMENT_HASH,
            "type": "incoming",
            "amount": 25_000,
            "settled_at": 1_783_599_600,
            "preimage": TEST_PAYMENT_PREIMAGE,
        }


class PendingRawNwcSession:
    """Expose a valid preimage while the wallet invoice remains pending."""

    async def __aenter__(self):
        """Open the fake session."""
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Close the fake session."""
        return None

    async def _send_request(self, method, params):
        """Return the pending wallet result without SDK interpretation."""
        assert method == "lookup_invoice"
        assert params == {"payment_hash": TEST_PAYMENT_HASH}
        return {
            "payment_hash": TEST_PAYMENT_HASH,
            "state": "pending",
            "amount": 25_000,
            "preimage": TEST_PAYMENT_PREIMAGE,
        }


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
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=25_000,
        settled_at=1_800_000_100,
        preimage=TEST_PAYMENT_PREIMAGE,
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
    invoice = member["payment_invoices"][TEST_PAYMENT_HASH]
    assert result == {"checked": 1, "settled": 1, "failed": 0}
    assert FakeNwcInvoiceClient.calls == [TEST_PAYMENT_HASH]
    assert member["status"] == "paid"
    assert member["paid_at"] == "2027-01-15T08:01:40+00:00"
    assert invoice["state"] == "settled"
    assert member["active_invoice_payment_hash"] is None
    persisted = load_json(_config(tmp_path)["PAYMENT_STATE_FILE"], {})
    assert persisted["periods"]["2026-11"]["members"]["member-001"][
        "status"
    ] == "paid"


def test_settled_invoice_without_optional_timestamp_uses_verification_time(
    monkeypatch, tmp_path
):
    """A confirmed wallet settlement need not include NIP-47 settled_at."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=25_000,
        settled_at=None,
        preimage=TEST_PAYMENT_PREIMAGE,
    )
    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", FakeNwcInvoiceClient)
    state = {"payment_state": _invoice_state()}
    checked_at = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)

    result = asyncio.run(
        check_payment_invoice_status(_config(tmp_path), state, now=checked_at)
    )

    member = state["payment_state"]["periods"]["2026-11"]["members"][
        "member-001"
    ]
    assert result == {"checked": 1, "settled": 1, "failed": 0}
    assert member["status"] == "paid"
    assert member["paid_at"] == "2026-11-02T10:00:00+00:00"
    assert member["payment_invoices"][TEST_PAYMENT_HASH]["settled_at"] is None


def test_production_invoice_accepts_wallet_timestamp_without_state(
    monkeypatch, tmp_path
):
    """A paid member is recognized from the wallet's timestamp-only response."""
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        lambda uri: NwcInvoiceClient(
            uri, client_factory=lambda _: SettledRawNwcSession()
        ),
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
    assert result == {"checked": 1, "settled": 1, "failed": 0}
    assert member["status"] == "paid"


def test_pending_invoice_is_checked_but_member_remains_unpaid(monkeypatch, tmp_path):
    """A pending wallet response records the check without marking payment."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
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
    assert member["payment_invoices"][TEST_PAYMENT_HASH]["state"] == "pending"
    assert member["payment_invoices"][TEST_PAYMENT_HASH]["last_checked_at"] == (
        "2026-11-02T10:00:00+00:00"
    )


def test_pending_wallet_preimage_cannot_mark_member_paid(monkeypatch, tmp_path):
    """The full lookup path never treats an unpaid preimage as settlement."""
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        lambda uri: NwcInvoiceClient(
            uri, client_factory=lambda _: PendingRawNwcSession()
        ),
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
    assert member["payment_invoices"][TEST_PAYMENT_HASH]["state"] == "pending"


def test_settled_invoice_without_preimage_remains_unmatched(monkeypatch, tmp_path):
    """A settled response without cryptographic proof cannot mark payment."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
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
    assert member["payment_invoices"][TEST_PAYMENT_HASH]["state"] == "unmatched"


def test_settled_invoice_with_invalid_preimage_remains_unmatched(monkeypatch, tmp_path):
    """A mismatched preimage cannot confirm a membership invoice."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=25_000,
        settled_at=1_800_000_100,
        preimage="11" * 32,
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
    assert member["payment_invoices"][TEST_PAYMENT_HASH]["state"] == "unmatched"


def test_mismatched_hash_never_marks_membership_invoice_paid(monkeypatch, tmp_path):
    """A settled response for another invoice remains unmatched."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash="another-invoice",
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
    assert member["payment_invoices"][TEST_PAYMENT_HASH]["state"] == "unmatched"


def test_late_settlement_is_recognized_after_local_expiry(monkeypatch, tmp_path):
    """Local expiry cannot discard a payment settled before reconciliation."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=25_000,
        settled_at=1_800_000_100,
        preimage=TEST_PAYMENT_PREIMAGE,
    )
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    state = {"payment_state": _invoice_state()}
    member = state["payment_state"]["periods"]["2026-11"]["members"][
        "member-001"
    ]
    member["payment_invoices"][TEST_PAYMENT_HASH]["expires_at"] = 1

    result = asyncio.run(
        check_payment_invoice_status(
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc),
        )
    )

    assert result == {"checked": 1, "settled": 1, "failed": 0}
    assert FakeNwcInvoiceClient.calls == [TEST_PAYMENT_HASH]
    assert member["status"] == "paid"
    assert member["payment_invoices"][TEST_PAYMENT_HASH]["state"] == "settled"
    assert member["active_invoice_payment_hash"] is None


def test_wallet_confirmed_expiry_allows_replacement(monkeypatch, tmp_path):
    """An exact wallet expiry releases the old invoice without marking it paid."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="expired",
        amount_msat=25_000,
        settled_at=None,
    )
    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", FakeNwcInvoiceClient)
    state = {"payment_state": _invoice_state()}

    result = asyncio.run(check_payment_invoice_status(_config(tmp_path), state))

    member = state["payment_state"]["periods"]["2026-11"]["members"]["member-001"]
    assert result == {"checked": 1, "settled": 0, "failed": 0}
    assert FakeNwcInvoiceClient.calls == [TEST_PAYMENT_HASH]
    assert member["status"] == "unpaid"
    assert member["payment_invoices"][TEST_PAYMENT_HASH]["expiry_confirmed"] is True
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


def test_settled_invoice_with_amount_discrepancy_is_paid_with_valid_proof(
    monkeypatch, tmp_path, caplog
):
    """A lookup amount discrepancy does not override verified settlement."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=24_288,
        settled_at=1_800_000_100,
        preimage=TEST_PAYMENT_PREIMAGE,
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
    assert member["status"] == "paid"
    assert member["payment_invoices"][TEST_PAYMENT_HASH]["state"] == "settled"
    assert "NWC lookup amount differs for settled membership invoice" in caplog.text


def test_settled_invoice_amount_discrepancy_still_requires_valid_preimage(
    monkeypatch, tmp_path
):
    """A mismatched lookup amount cannot bypass cryptographic settlement proof."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=24_288,
        settled_at=1_800_000_100,
        preimage="11" * 32,
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
    assert member["payment_invoices"][TEST_PAYMENT_HASH]["state"] == "unmatched"


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
    assert member["payment_invoices"][TEST_PAYMENT_HASH]["state"] == "unmatched"
    assert member["active_invoice_payment_hash"] == TEST_PAYMENT_HASH


def test_unmatched_invoice_can_settle_after_restart(monkeypatch, tmp_path):
    """An inconclusive lookup remains tracked and can settle on the next run."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash="different-hash",
        state="settled",
        amount_msat=25_000,
        settled_at=1_800_000_100,
        preimage=TEST_PAYMENT_PREIMAGE,
    )
    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", FakeNwcInvoiceClient)
    config = _config(tmp_path)
    state = {"payment_state": _invoice_state()}
    asyncio.run(check_payment_invoice_status(config, state))
    saved = load_multi_period_payment_state(config["PAYMENT_STATE_FILE"])
    member = saved["periods"]["2026-11"]["members"]["member-001"]
    assert member["status"] == "unpaid"
    assert member["active_invoice_payment_hash"] == TEST_PAYMENT_HASH
    assert member["payment_invoices"][TEST_PAYMENT_HASH]["state"] == "unmatched"

    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=25_000,
        settled_at=1_800_000_100,
        preimage=TEST_PAYMENT_PREIMAGE,
    )
    restarted = {"payment_state": saved}
    result = asyncio.run(check_payment_invoice_status(config, restarted))

    assert result == {"checked": 1, "settled": 1, "failed": 0}
    assert FakeNwcInvoiceClient.calls == [TEST_PAYMENT_HASH, TEST_PAYMENT_HASH]
    restarted_member = restarted["payment_state"]["periods"]["2026-11"]["members"][
        "member-001"
    ]
    assert restarted_member["status"] == "paid"


def test_transient_lookup_failure_keeps_invoice_for_retry(monkeypatch, tmp_path):
    """A temporary wallet error does not clear the invoice or mint another."""

    class FlakyNwcClient(FakeNwcInvoiceClient):
        """Fail once, then return exact settlement proof."""

        calls = []

        async def lookup_invoice(self, *, payment_hash):
            """Simulate one failed wallet lookup."""
            type(self).calls.append(payment_hash)
            if len(type(self).calls) == 1:
                raise RuntimeError("wallet unavailable")
            return NwcInvoiceStatus(
                payment_hash=payment_hash,
                state="settled",
                amount_msat=25_000,
                settled_at=1_800_000_100,
                preimage=TEST_PAYMENT_PREIMAGE,
            )

    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", FlakyNwcClient)
    config = _config(tmp_path)
    state = {"payment_state": _invoice_state()}

    failed = asyncio.run(check_payment_invoice_status(config, state))
    member = state["payment_state"]["periods"]["2026-11"]["members"]["member-001"]
    assert failed == {"checked": 1, "settled": 0, "failed": 1}
    assert member["active_invoice_payment_hash"] == TEST_PAYMENT_HASH
    assert member["status"] == "unpaid"

    recovered = asyncio.run(check_payment_invoice_status(config, state))
    assert recovered == {"checked": 1, "settled": 1, "failed": 0}
    assert FlakyNwcClient.calls == [TEST_PAYMENT_HASH, TEST_PAYMENT_HASH]
    assert member["status"] == "paid"


def test_legacy_locally_expired_invoice_is_rechecked(monkeypatch, tmp_path):
    """A legacy expiry without wallet confirmation is still reconcilable."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=25_000,
        settled_at=1_800_000_100,
        preimage=TEST_PAYMENT_PREIMAGE,
    )
    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", FakeNwcInvoiceClient)
    payment_state = _invoice_state()
    member = payment_state["periods"]["2026-11"]["members"]["member-001"]
    member["payment_invoices"][TEST_PAYMENT_HASH]["state"] = "expired"
    member["payment_invoices"][TEST_PAYMENT_HASH].pop("expiry_confirmed")
    member["active_invoice_payment_hash"] = None
    state = {"payment_state": payment_state}

    result = asyncio.run(check_payment_invoice_status(_config(tmp_path), state))

    assert result == {"checked": 1, "settled": 1, "failed": 0}
    assert FakeNwcInvoiceClient.calls == [TEST_PAYMENT_HASH]
    assert member["status"] == "paid"
