"""Tests for isolated payment-test settlement polling."""

import asyncio
from datetime import datetime, timezone
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core import membership_payments
from core.nwc_client import NwcInvoiceClient, NwcInvoiceStatus
from core.payment_test_state import (
    build_payment_test_state,
    load_payment_test_state,
    record_payment_test_invoice,
    reset_payment_test_invoice,
    update_payment_test_invoice,
)


NWC_URI = (
    "nostr+walletconnect://wallet-key?"
    "relay=wss%3A%2F%2Frelay.example&secret=test-secret"
)
TEST_PAYMENT_HASH = (
    "66687aadf862bd776c8fc18b8e9f8e20089714856ee233b3902a591d0d5f2925"
)
TEST_PAYMENT_PREIMAGE = "00" * 32


def _test_state(expires_at=1_800_000_000):
    """Build an isolated state containing one pending 100-sat invoice."""
    return record_payment_test_invoice(
        build_payment_test_state(),
        {
            "invoice": "lnbc-payment-test",
            "payment_hash": TEST_PAYMENT_HASH,
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


class PendingRawNwcSession:
    """Return a pending wallet result that already exposes its preimage."""

    async def __aenter__(self):
        """Open the fake session."""
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Close the fake session."""
        return None

    async def _send_request(self, method, params):
        """Return a wallet-level pending invoice with valid preimage."""
        assert method == "lookup_invoice"
        assert params == {"payment_hash": TEST_PAYMENT_HASH}
        return {
            "payment_hash": TEST_PAYMENT_HASH,
            "state": "pending",
            "amount": 100_000,
            "preimage": TEST_PAYMENT_PREIMAGE,
        }


class SettledRawNwcSession(PendingRawNwcSession):
    """Model the paid NWC response that omitted its optional state field."""

    async def _send_request(self, method, params):
        """Return the receiver's exact timestamp-only settlement shape."""
        result = await super()._send_request(method, params)
        result.pop("state")
        result["type"] = "incoming"
        result["settled_at"] = 1_783_599_600
        return result


class UnpaidRawNwcSession(PendingRawNwcSession):
    """Model an unpaid wallet that omits its optional state field."""

    async def _send_request(self, method, params):
        """Return no settlement signal despite an available preimage."""
        result = await super()._send_request(method, params)
        result.pop("state")
        return result


def _context():
    """Build a context with a private administrator notification sink."""
    return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))


def _archived_test_state():
    """Return one archived test while another invoice remains active."""
    archived = reset_payment_test_invoice(_test_state())
    return record_payment_test_invoice(
        archived,
        {
            "invoice": "lnbc-current-test",
            "payment_hash": "current-test-hash",
            "amount_msat": 100_000,
        },
        100,
        "test-002",
    )


def test_archived_recheck_confirms_paid_test_without_touching_active_or_members(
    monkeypatch, tmp_path
):
    """Exact wallet proof updates only the selected archived test."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=100_000,
        settled_at=1_800_000_100,
        preimage=TEST_PAYMENT_PREIMAGE,
    )
    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", FakeNwcInvoiceClient)
    state = {
        "payment_test_state": _archived_test_state(),
        "payment_state": {"periods": {"example": "unchanged"}},
    }
    active_before = state["payment_test_state"]["active_invoice"].copy()
    config = _config(tmp_path)

    result = asyncio.run(membership_payments.recheck_archived_payment_test_invoice(
        config, state, "test-001"
    ))

    assert result == "settled"
    assert FakeNwcInvoiceClient.calls == [TEST_PAYMENT_HASH]
    assert state["payment_test_state"]["history"][0]["state"] == "settled"
    assert state["payment_test_state"]["active_invoice"] == active_before
    assert state["payment_state"] == {"periods": {"example": "unchanged"}}
    assert load_payment_test_state(config["PAYMENT_TEST_STATE_FILE"]) == state["payment_test_state"]
    assert asyncio.run(membership_payments.recheck_archived_payment_test_invoice(
        config, state, "test-001"
    )) == "already_settled"
    assert FakeNwcInvoiceClient.calls == [TEST_PAYMENT_HASH]


def test_archived_recheck_does_not_confirm_pending_or_invalid_proof(
    monkeypatch, tmp_path
):
    """A preimage alone or an invalid preimage cannot settle history."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", FakeNwcInvoiceClient)
    state = {"payment_test_state": _archived_test_state()}
    config = _config(tmp_path)

    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="pending",
        amount_msat=100_000,
        settled_at=None,
        preimage=TEST_PAYMENT_PREIMAGE,
    )
    assert asyncio.run(membership_payments.recheck_archived_payment_test_invoice(
        config, state, "test-001"
    )) == "pending"
    assert state["payment_test_state"]["history"][0]["state"] != "settled"

    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=100_000,
        settled_at=1_800_000_100,
        preimage="11" * 32,
    )
    assert asyncio.run(membership_payments.recheck_archived_payment_test_invoice(
        config, state, "test-001"
    )) == "unmatched"
    assert state["payment_test_state"]["history"][0]["state"] != "settled"
    assert state["payment_test_state"]["active_invoice"]["state"] == "pending"


def test_archived_recheck_accepts_timestamp_only_wallet_settlement(
    monkeypatch, tmp_path
):
    """A wallet omitting optional state can still prove an archived payment."""
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        lambda uri: NwcInvoiceClient(
            uri, client_factory=lambda _: SettledRawNwcSession()
        ),
    )
    state = {"payment_test_state": _archived_test_state()}

    assert asyncio.run(membership_payments.recheck_archived_payment_test_invoice(
        _config(tmp_path), state, "test-001"
    )) == "settled"
    assert state["payment_test_state"]["history"][0]["state"] == "settled"


def test_archived_recheck_rejects_wrong_amount(monkeypatch, tmp_path):
    """A settled response for another amount cannot confirm the old test."""
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=200_000,
        settled_at=1_800_000_100,
        preimage=TEST_PAYMENT_PREIMAGE,
    )
    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", FakeNwcInvoiceClient)
    state = {"payment_test_state": _archived_test_state()}

    assert asyncio.run(membership_payments.recheck_archived_payment_test_invoice(
        _config(tmp_path), state, "test-001"
    )) == "unmatched"
    assert state["payment_test_state"]["history"][0]["state"] != "settled"


def test_settled_test_invoice_notifies_admin_without_changing_production_state(
    monkeypatch,
    tmp_path,
):
    """Settlement updates only test state and sends a private confirmation."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=100_000,
        settled_at=1_800_000_100,
        preimage=TEST_PAYMENT_PREIMAGE,
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
    assert FakeNwcInvoiceClient.calls == [TEST_PAYMENT_HASH]
    assert state["payment_test_state"]["active_invoice"]["state"] == "settled"
    assert state["payment_state"] == {"version": 2, "periods": {}}
    assert "no modificó ninguna membresía real" in (
        context.bot.send_message.await_args.kwargs["text"]
    )


def test_unmatched_test_rechecks_settlement_without_timestamp(
    monkeypatch, tmp_path, caplog
):
    """A previously inconclusive test recovers when the wallet confirms settlement."""
    caplog.set_level(logging.INFO, logger=membership_payments.__name__)
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="unmatched",
        amount_msat=100_000,
        settled_at=None,
    )
    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", FakeNwcInvoiceClient)
    context = _context()
    state = {"payment_test_state": _test_state()}
    config = _config(tmp_path)

    first_result = asyncio.run(
        membership_payments.check_payment_test_invoice_status(
            context,
            config,
            state,
            now=datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc),
        )
    )
    assert first_result == {"checked": 1, "settled": 0, "failed": 0}
    assert state["payment_test_state"]["active_invoice"]["state"] == "unmatched"

    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=100_000,
        settled_at=None,
        preimage=TEST_PAYMENT_PREIMAGE,
    )
    second_result = asyncio.run(
        membership_payments.check_payment_test_invoice_status(
            context,
            config,
            state,
            now=datetime(2026, 11, 7, 12, 1, tzinfo=timezone.utc),
        )
    )

    assert second_result == {"checked": 1, "settled": 1, "failed": 0}
    assert state["payment_test_state"]["active_invoice"]["state"] == "settled"
    assert state["payment_test_state"]["active_invoice"]["settled_at"] is None
    assert context.bot.send_message.await_count == 1
    assert FakeNwcInvoiceClient.calls == [TEST_PAYMENT_HASH, TEST_PAYMENT_HASH]
    assert "state=settled" in caplog.text
    assert "preimage_valid=True" in caplog.text
    assert TEST_PAYMENT_HASH not in caplog.text
    assert TEST_PAYMENT_PREIMAGE not in caplog.text


def test_unmatched_test_recovers_from_wallet_response_without_state(
    monkeypatch, tmp_path
):
    """A paid test is recognized even when raw NWC omits optional state."""
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        lambda uri: NwcInvoiceClient(
            uri, client_factory=lambda _: SettledRawNwcSession()
        ),
    )
    context = _context()
    test_state = _test_state()
    test_state = update_payment_test_invoice(
        test_state,
        TEST_PAYMENT_HASH,
        state_value="unmatched",
    )
    state = {"payment_test_state": test_state}

    result = asyncio.run(
        membership_payments.check_payment_test_invoice_status(
            context,
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 7, 12, 1, tzinfo=timezone.utc),
        )
    )

    assert result == {"checked": 1, "settled": 1, "failed": 0}
    assert state["payment_test_state"]["active_invoice"]["state"] == "settled"
    assert context.bot.send_message.await_count == 1


def test_pending_test_invoice_records_last_check_without_notification(
    monkeypatch,
    tmp_path,
):
    """Pending test invoices remain pending and do not notify the administrator."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
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


def test_pending_wallet_preimage_cannot_confirm_payment_test(monkeypatch, tmp_path):
    """The full lookup path ignores a preimage on an unpaid invoice."""
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        lambda uri: NwcInvoiceClient(
            uri, client_factory=lambda _: PendingRawNwcSession()
        ),
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
    assert state["payment_test_state"]["active_invoice"]["state"] == "pending"
    assert context.bot.send_message.await_count == 0


def test_missing_wallet_state_without_timestamp_keeps_test_pending(
    monkeypatch, tmp_path
):
    """An unpaid wallet's omitted state must stay retryable, not unmatched."""
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        lambda uri: NwcInvoiceClient(
            uri, client_factory=lambda _: UnpaidRawNwcSession()
        ),
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
    assert state["payment_test_state"]["active_invoice"]["state"] == "pending"
    assert context.bot.send_message.await_count == 0


def test_settled_test_invoice_without_preimage_stays_unmatched(
    monkeypatch,
    tmp_path,
):
    """A settled test response without proof cannot notify the administrator."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
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


def test_settled_test_invoice_with_invalid_preimage_stays_unmatched(
    monkeypatch,
    tmp_path,
):
    """A mismatched preimage cannot trigger the payment-test confirmation."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=100_000,
        settled_at=1_800_000_100,
        preimage="11" * 32,
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


def test_wrong_test_invoice_amount_becomes_unmatched(monkeypatch, tmp_path):
    """A settled response with a wrong amount cannot pass the test."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
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


def test_missing_test_invoice_amount_becomes_unmatched(monkeypatch, tmp_path):
    """A settled response without an amount cannot pass the test."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="settled",
        amount_msat=None,
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


def test_mismatched_test_invoice_hash_becomes_unmatched(monkeypatch, tmp_path):
    """A settled response for a different hash cannot pass the test."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash="another-payment-hash",
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


def test_unknown_test_invoice_state_becomes_unmatched(monkeypatch, tmp_path):
    """An unknown wallet state cannot trigger a paid confirmation."""
    FakeNwcInvoiceClient.calls = []
    FakeNwcInvoiceClient.status = NwcInvoiceStatus(
        payment_hash=TEST_PAYMENT_HASH,
        state="mysterious",
        amount_msat=100_000,
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
