"""End-to-end coverage for creating and settling an isolated payment test."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers import admin
from core import membership_payments
from core.nwc_client import NwcInvoice, NwcInvoiceStatus
from core.payment_test_state import build_payment_test_state


NWC_URI = (
    "nostr+walletconnect://wallet-key?"
    "relay=wss%3A%2F%2Frelay.example&secret=test-secret"
)


class EndToEndNwcClient:
    """Simulate an NWC wallet before and after the test invoice is paid."""

    paid = False

    def __init__(self, connection_uri):
        """Accept the configured NWC connection."""

    async def make_invoice(self, *, amount_msat, description, expiry_seconds):
        """Return the isolated test invoice."""
        return NwcInvoice(
            invoice="lnbc-e2e-payment-test",
            payment_hash="e2e-payment-test-hash",
            amount_msat=amount_msat,
            expires_at=1_800_000_000,
        )

    async def lookup_invoice(self, *, payment_hash):
        """Return pending or settled status based on the simulated payment."""
        return NwcInvoiceStatus(
            payment_hash=payment_hash,
            state="settled" if self.paid else "pending",
            amount_msat=100_000,
            settled_at=1_800_000_100 if self.paid else None,
        )


def test_payment_test_flow_creates_then_verifies_without_production_mutation(
    monkeypatch,
    tmp_path,
):
    """The command and polling job complete without changing real payment state."""
    EndToEndNwcClient.paid = False
    monkeypatch.setattr(admin, "NwcInvoiceClient", EndToEndNwcClient)
    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", EndToEndNwcClient)
    config = {
        "ADMIN_ID": 42,
        "PAYMENT_VERIFICATION_ENABLED": True,
        "PAYMENT_NWC_URI": NWC_URI,
        "PAYMENT_TEST_INVOICE_AMOUNT_SATS": 100,
        "PAYMENT_TEST_STATE_FILE": str(tmp_path / "payment_test_state.json"),
        "PAYMENT_INVOICE_EXPIRY_SECONDS": 259200,
    }
    state = {
        "payment_test_state": build_payment_test_state(),
        "payment_state": {"version": 2, "periods": {}},
    }
    command_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(
            text="/paymenttest",
            reply_text=AsyncMock(),
        ),
    )

    asyncio.run(admin.payment_test(command_update, SimpleNamespace(), 42, state, config))
    assert state["payment_test_state"]["active_invoice"]["state"] == "pending"

    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    EndToEndNwcClient.paid = True
    result = asyncio.run(
        membership_payments.check_payment_test_invoice_status(
            context,
            config,
            state,
        )
    )

    assert result == {"checked": 1, "settled": 1, "failed": 0}
    assert state["payment_test_state"]["active_invoice"]["state"] == "settled"
    assert state["payment_state"] == {"version": 2, "periods": {}}
    assert "correctamente" in context.bot.send_message.await_args.kwargs["text"]
