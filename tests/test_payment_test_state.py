"""Tests for isolated low-value payment-test state."""

from datetime import datetime, timezone

import pytest

from core.payment_test_state import (
    PaymentTestInvoiceConflict,
    build_payment_test_state,
    cancel_payment_test_invoice,
    get_active_payment_test_invoice,
    get_latest_payment_test_invoice,
    load_payment_test_state,
    normalize_payment_test_amount_sats,
    parse_payment_test_amount_sats,
    record_payment_test_invoice,
    reset_payment_test_invoice,
    save_payment_test_state,
)


def _invoice(amount_sats=100, payment_hash="hash-test"):
    """Build a representative low-value invoice response."""
    return {
        "invoice": "lnbc-test-invoice",
        "payment_hash": payment_hash,
        "amount_msat": amount_sats * 1000,
        "expires_at": 1_800_000_000,
    }


def test_payment_test_amount_is_capped_at_100_sats():
    """Valid whole-sat amounts are accepted and larger amounts are rejected."""
    assert normalize_payment_test_amount_sats("100") == 100
    assert normalize_payment_test_amount_sats(1) == 1
    with pytest.raises(ValueError):
        normalize_payment_test_amount_sats(101)
    with pytest.raises(ValueError):
        normalize_payment_test_amount_sats("1.5")


def test_invalid_configured_test_amount_uses_safe_default(caplog):
    """Invalid configuration cannot increase the test invoice amount."""
    assert parse_payment_test_amount_sats("1000") == 100
    assert "safe default" in caplog.text


def test_test_invoice_state_is_separate_and_round_trips(tmp_path):
    """Test invoice state persists independently from production payment state."""
    state = record_payment_test_invoice(
        build_payment_test_state(),
        _invoice(),
        100,
        "test-20261107-001",
        created_at=datetime(2026, 11, 7, tzinfo=timezone.utc),
    )
    path = tmp_path / "payment_test_state.json"

    save_payment_test_state(str(path), state)
    loaded = load_payment_test_state(str(path))

    active = get_active_payment_test_invoice(loaded)
    assert active["test_id"] == "test-20261107-001"
    assert active["amount_sats"] == 100
    assert active["amount_msat"] == 100_000


def test_pending_test_invoice_cannot_be_duplicated():
    """A second pending test invoice is rejected before command integration."""
    state = record_payment_test_invoice(
        build_payment_test_state(),
        _invoice(),
        100,
        "test-001",
    )

    with pytest.raises(PaymentTestInvoiceConflict):
        record_payment_test_invoice(state, _invoice(payment_hash="hash-002"), 100, "test-002")


def test_cancelled_test_invoice_is_preserved_but_not_active():
    """Cancelling a pending invoice stops it from blocking a new test."""
    state = record_payment_test_invoice(
        build_payment_test_state(),
        _invoice(),
        100,
        "test-001",
    )

    cancelled = cancel_payment_test_invoice(
        state,
        cancelled_at=datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc),
    )

    assert get_active_payment_test_invoice(cancelled) is None
    latest = get_latest_payment_test_invoice(cancelled)
    assert latest["state"] == "cancelled"
    assert latest["last_checked_at"] == "2026-11-07T12:00:00+00:00"


def test_reset_archives_current_test_and_allows_a_new_one():
    """Reset preserves history and clears the slot used for new tests."""
    state = record_payment_test_invoice(
        build_payment_test_state(),
        _invoice(),
        100,
        "test-001",
    )

    reset = reset_payment_test_invoice(
        state,
        reset_at=datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc),
    )
    reset = record_payment_test_invoice(
        reset,
        _invoice(payment_hash="hash-002"),
        100,
        "test-002",
    )

    assert len(reset["history"]) == 1
    assert reset["history"][0]["state"] == "cancelled"
    assert reset["active_invoice"]["test_id"] == "test-002"


def test_test_invoice_amount_mismatch_is_rejected():
    """The isolated state layer rejects invoices with a different amount."""
    with pytest.raises(ValueError):
        record_payment_test_invoice(
            build_payment_test_state(),
            _invoice(amount_sats=99),
            100,
            "test-001",
        )
