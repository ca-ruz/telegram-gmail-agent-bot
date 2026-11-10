"""Tests for persisted membership payment invoice records."""

from datetime import datetime, timezone

import pytest

from core.membership_payments import (
    PaymentInvoiceConflict,
    build_member,
    build_multi_period_payment_state,
    get_active_payment_invoice,
    migrate_payment_state,
    record_payment_invoice,
    save_multi_period_payment_state,
    update_payment_invoice,
    load_multi_period_payment_state,
)


def _state():
    """Build a two-period state with one active member."""
    member = build_member("Ada", 101)
    return build_multi_period_payment_state(
        {"member-001": member},
        ["2026-11"],
    )


def _invoice(payment_hash="hash-001", invoice="lnbc-test"):
    """Build a representative NWC invoice response."""
    return {
        "invoice": invoice,
        "payment_hash": payment_hash,
        "amount_msat": 25_000,
        "expires_at": 1_800_000_000,
    }


def test_record_invoice_persists_active_invoice_by_period_and_member():
    """Invoice metadata is attached to only the selected member and period."""
    state = record_payment_invoice(
        _state(),
        "2026-11",
        "member-001",
        _invoice(),
        created_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )

    active = get_active_payment_invoice(state, "2026-11", "member-001")
    assert active["payment_hash"] == "hash-001"
    assert active["amount_msat"] == 25_000
    assert active["created_at"] == "2026-11-01T00:00:00+00:00"
    assert state["periods"]["2026-11"]["members"]["member-001"][
        "active_invoice_payment_hash"
    ] == "hash-001"


def test_recording_same_invoice_is_idempotent():
    """Retrying invoice persistence does not create duplicate records."""
    state = record_payment_invoice(_state(), "2026-11", "member-001", _invoice())
    state = record_payment_invoice(state, "2026-11", "member-001", _invoice())

    invoices = state["periods"]["2026-11"]["members"]["member-001"][
        "payment_invoices"
    ]
    assert list(invoices) == ["hash-001"]


def test_new_pending_invoice_conflicts_with_existing_active_invoice():
    """A pending invoice cannot be silently replaced by another invoice."""
    state = record_payment_invoice(_state(), "2026-11", "member-001", _invoice())

    with pytest.raises(PaymentInvoiceConflict):
        record_payment_invoice(
            state,
            "2026-11",
            "member-001",
            _invoice("hash-002", "lnbc-second"),
        )


@pytest.mark.parametrize("uncertain_state", ["unmatched", "failed"])
def test_uncertain_invoice_blocks_replacement(uncertain_state):
    """An inconclusive wallet result cannot authorize a second invoice."""
    state = record_payment_invoice(_state(), "2026-11", "member-001", _invoice())
    state = update_payment_invoice(
        state, "2026-11", "member-001", "hash-001", state=uncertain_state
    )

    with pytest.raises(PaymentInvoiceConflict):
        record_payment_invoice(
            state, "2026-11", "member-001", _invoice("hash-002", "lnbc-second")
        )
    assert get_active_payment_invoice(state, "2026-11", "member-001")[
        "payment_hash"
    ] == "hash-001"


def test_expired_invoice_can_be_replaced_while_history_is_preserved():
    """Only wallet-confirmed expiry permits a replacement invoice."""
    state = record_payment_invoice(_state(), "2026-11", "member-001", _invoice())
    state = update_payment_invoice(
        state,
        "2026-11",
        "member-001",
        "hash-001",
        state="expired",
    )
    with pytest.raises(PaymentInvoiceConflict):
        record_payment_invoice(
            state, "2026-11", "member-001", _invoice("hash-002", "lnbc-second")
        )
    assert get_active_payment_invoice(state, "2026-11", "member-001")[
        "payment_hash"
    ] == "hash-001"
    state = update_payment_invoice(
        state,
        "2026-11",
        "member-001",
        "hash-001",
        state="expired",
        expiry_confirmed=True,
    )
    state = record_payment_invoice(
        state,
        "2026-11",
        "member-001",
        _invoice("hash-002", "lnbc-second"),
    )

    member = state["periods"]["2026-11"]["members"]["member-001"]
    assert member["active_invoice_payment_hash"] == "hash-002"
    assert member["payment_invoices"]["hash-001"]["state"] == "expired"
    assert member["payment_invoices"]["hash-001"]["expiry_confirmed"] is True
    assert member["payment_invoices"]["hash-002"]["state"] == "pending"


def test_settlement_update_clears_active_pointer_but_preserves_history():
    """Terminal invoice state remains auditable after it is no longer active."""
    state = record_payment_invoice(_state(), "2026-11", "member-001", _invoice())
    state = update_payment_invoice(
        state,
        "2026-11",
        "member-001",
        "hash-001",
        state="settled",
        settled_at="2026-11-02T10:00:00+00:00",
        last_checked_at="2026-11-02T10:00:01+00:00",
    )

    member = state["periods"]["2026-11"]["members"]["member-001"]
    assert member["active_invoice_payment_hash"] is None
    assert member["payment_invoices"]["hash-001"]["settled_at"] == (
        "2026-11-02T10:00:00+00:00"
    )


def test_invoice_metadata_survives_json_round_trip(tmp_path):
    """Invoice history persists through the existing atomic JSON storage."""
    state = record_payment_invoice(_state(), "2026-11", "member-001", _invoice())
    file_path = tmp_path / "payment_state.json"

    save_multi_period_payment_state(str(file_path), state)
    loaded = load_multi_period_payment_state(str(file_path))

    assert get_active_payment_invoice(loaded, "2026-11", "member-001")[
        "payment_hash"
    ] == "hash-001"


def test_legacy_members_migrate_without_invoice_fields():
    """Legacy member records remain valid before invoice automation is enabled."""
    migrated = migrate_payment_state(
        {
            "version": 2,
            "periods": {
                "2026-11": {
                    "members": {
                        "member-001": {
                            "status": "unpaid",
                            "paid_at": None,
                            "reminders_sent": [],
                        }
                    }
                }
            },
        }
    )

    member = migrated["periods"]["2026-11"]["members"]["member-001"]
    assert member["status"] == "unpaid"
    assert "payment_invoices" not in member
