"""Tests for the primary-administrator payment test command."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers import admin
from core.nwc_client import NwcInvoice
from core.payment_test_state import build_payment_test_state


NWC_URI = (
    "nostr+walletconnect://wallet-key?"
    "relay=wss%3A%2F%2Frelay.example&secret=test-secret"
)


class FakeNwcInvoiceClient:
    """Return one deterministic invoice for the command test."""

    calls = []

    def __init__(self, connection_uri):
        """Record that the configured connection was used."""
        self.connection_uri = connection_uri

    async def make_invoice(self, *, amount_msat, description, expiry_seconds):
        """Return an invoice containing the requested low test amount."""
        self.calls.append((amount_msat, description, expiry_seconds))
        return NwcInvoice(
            invoice="lnbc-payment-test",
            payment_hash="payment-test-hash",
            amount_msat=amount_msat,
            expires_at=1_800_000_000,
        )


def _update(text="/paymenttest", user_id=42):
    """Build a minimal Telegram update for the command handler."""
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            text=text,
            reply_photo=AsyncMock(),
            reply_text=AsyncMock(),
        ),
    )


def _config(tmp_path):
    """Build enabled isolated test-invoice configuration."""
    return {
        "PAYMENT_VERIFICATION_ENABLED": True,
        "PAYMENT_NWC_URI": NWC_URI,
        "PAYMENT_TEST_INVOICE_AMOUNT_SATS": 100,
        "PAYMENT_TEST_STATE_FILE": str(tmp_path / "payment_test_state.json"),
        "PAYMENT_INVOICE_EXPIRY_SECONDS": 259200,
    }


def test_primary_admin_receives_only_the_isolated_test_invoice(monkeypatch, tmp_path):
    """The command creates a 100-sat invoice without production payment state."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(admin, "NwcInvoiceClient", FakeNwcInvoiceClient)
    update = _update()
    state = {
        "payment_test_state": build_payment_test_state(),
        "payment_state": {"version": 2, "periods": {}},
    }

    asyncio.run(admin.payment_test(update, SimpleNamespace(), 42, state, _config(tmp_path)))

    assert FakeNwcInvoiceClient.calls == [
        (100_000, "Prueba de pago del bot", 259200),
    ]
    assert update.message.reply_text.await_count == 0
    assert "lnbc-payment-test" in update.message.reply_photo.await_args.kwargs[
        "caption"
    ]
    assert state["payment_state"] == {"version": 2, "periods": {}}
    assert state["payment_test_state"]["active_invoice"]["amount_sats"] == 100
    update.message.reply_photo.assert_awaited_once()


def test_paymenttest_keeps_text_fallback_when_qr_generation_fails(
    monkeypatch,
    tmp_path,
):
    """A QR failure still delivers the complete copyable invoice."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(admin, "NwcInvoiceClient", FakeNwcInvoiceClient)

    def fail_qr(invoice):
        """Simulate a QR rendering failure."""
        raise ValueError("invalid invoice")

    monkeypatch.setattr(admin, "build_invoice_qr", fail_qr)
    update = _update()
    state = {"payment_test_state": build_payment_test_state()}

    asyncio.run(admin.payment_test(update, SimpleNamespace(), 42, state, _config(tmp_path)))

    update.message.reply_photo.assert_not_awaited()
    assert "lnbc-payment-test" in update.message.reply_text.await_args.args[0]


def test_paymenttest_reuses_active_invoice_and_status_reports_it(monkeypatch, tmp_path):
    """Repeated command calls do not create duplicate test invoices."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(admin, "NwcInvoiceClient", FakeNwcInvoiceClient)
    state = {"payment_test_state": build_payment_test_state()}
    config = _config(tmp_path)

    asyncio.run(admin.payment_test(_update(), SimpleNamespace(), 42, state, config))
    status_update = _update("/paymenttest status")
    asyncio.run(admin.payment_test(status_update, SimpleNamespace(), 42, state, config))
    duplicate_update = _update()
    asyncio.run(admin.payment_test(duplicate_update, SimpleNamespace(), 42, state, config))

    assert len(FakeNwcInvoiceClient.calls) == 1
    assert status_update.message.reply_text.await_count == 0
    status_caption = status_update.message.reply_photo.await_args.kwargs["caption"]
    assert "payment-test-hash" in status_caption
    assert "lnbc-payment-test" in status_caption
    assert "Estado: pending" in status_caption
    assert "Ya hay una prueba de pago activa" in (
        duplicate_update.message.reply_text.await_args.args[0]
    )


def test_paymenttest_status_falls_back_to_full_text(monkeypatch, tmp_path):
    """Status still exposes the full invoice when QR delivery fails."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(admin, "NwcInvoiceClient", FakeNwcInvoiceClient)
    monkeypatch.setattr(
        admin,
        "build_invoice_qr",
        lambda invoice: (_ for _ in ()).throw(ValueError("invalid QR")),
    )
    state = {"payment_test_state": build_payment_test_state()}
    config = _config(tmp_path)

    asyncio.run(admin.payment_test(_update(), SimpleNamespace(), 42, state, config))
    status_update = _update("/paymenttest status")
    asyncio.run(admin.payment_test(status_update, SimpleNamespace(), 42, state, config))

    assert status_update.message.reply_photo.await_count == 0
    status_text = status_update.message.reply_text.await_args.args[0]
    assert "Estado: pending" in status_text
    assert "payment-test-hash" in status_text
    assert "lnbc-payment-test" in status_text


def test_paymenttest_cancel_preserves_history_and_allows_new_test(monkeypatch, tmp_path):
    """Cancelling a pending test preserves it and permits a replacement."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(admin, "NwcInvoiceClient", FakeNwcInvoiceClient)
    state = {"payment_test_state": build_payment_test_state()}
    config = _config(tmp_path)

    asyncio.run(admin.payment_test(_update(), SimpleNamespace(), 42, state, config))
    cancel_update = _update("/paymenttest cancel")
    asyncio.run(admin.payment_test(cancel_update, SimpleNamespace(), 42, state, config))

    assert "cancelada" in cancel_update.message.reply_text.await_args.args[0]
    assert state["payment_test_state"]["active_invoice"]["state"] == "cancelled"

    asyncio.run(admin.payment_test(_update(), SimpleNamespace(), 42, state, config))

    assert len(FakeNwcInvoiceClient.calls) == 2
    assert len(state["payment_test_state"]["history"]) == 1
    assert state["payment_test_state"]["active_invoice"]["state"] == "pending"


def test_paymenttest_reset_archives_settled_test_and_allows_new_test(
    monkeypatch,
    tmp_path,
):
    """Reset archives a completed test and clears it before recreation."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(admin, "NwcInvoiceClient", FakeNwcInvoiceClient)
    state = {"payment_test_state": build_payment_test_state()}
    config = _config(tmp_path)

    asyncio.run(admin.payment_test(_update(), SimpleNamespace(), 42, state, config))
    state["payment_test_state"]["active_invoice"]["state"] = "settled"
    reset_update = _update("/paymenttest reset")
    asyncio.run(admin.payment_test(reset_update, SimpleNamespace(), 42, state, config))

    assert "reiniciada" in reset_update.message.reply_text.await_args.args[0]
    assert state["payment_test_state"]["active_invoice"] is None
    assert state["payment_test_state"]["history"][0]["state"] == "settled"

    asyncio.run(admin.payment_test(_update(), SimpleNamespace(), 42, state, config))

    assert len(FakeNwcInvoiceClient.calls) == 2
    assert state["payment_test_state"]["active_invoice"]["state"] == "pending"


def test_paymenttest_is_primary_admin_only(tmp_path):
    """Non-primary users cannot create or inspect test invoices."""
    update = _update(user_id=99)

    asyncio.run(
        admin.payment_test(
            update,
            SimpleNamespace(),
            42,
            {"payment_test_state": build_payment_test_state()},
            _config(tmp_path),
        )
    )

    update.message.reply_text.assert_not_awaited()
