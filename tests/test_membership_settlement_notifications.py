"""Tests for private membership settlement notifications."""

import asyncio
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core import membership_payments
from core.membership_payments import (
    build_member,
    build_multi_period_payment_state,
    check_payment_invoice_status,
    deliver_membership_settlement_notifications,
    record_payment_invoice,
)
from core.nwc_client import NwcInvoiceStatus


NWC_URI = (
    "nostr+walletconnect://wallet-key?"
    "relay=wss%3A%2F%2Frelay.example&secret=test-secret"
)
PAYMENT_PREIMAGE = "00" * 32
PAYMENT_HASH = hashlib.sha256(bytes.fromhex(PAYMENT_PREIMAGE)).hexdigest()


class SettledNwcClient:
    """Return one settled lookup response for a membership invoice."""

    def __init__(self, connection_uri):
        """Accept the configured wallet connection."""
        self.connection_uri = connection_uri

    async def lookup_invoice(self, *, payment_hash):
        """Return the matching settled invoice with valid preimage proof."""
        assert payment_hash == PAYMENT_HASH
        return NwcInvoiceStatus(
            payment_hash=PAYMENT_HASH,
            state="settled",
            amount_msat=24_288,
            settled_at=1_800_000_100,
            preimage=PAYMENT_PREIMAGE,
        )


def _state_and_config(tmp_path, *, payment_admin_id=42):
    """Build one pending invoice and payment configuration."""
    member = build_member("Ada", 101, "coworking")
    payment_state = build_multi_period_payment_state(
        {"member-001": member},
        ["2026-11"],
    )
    payment_state = record_payment_invoice(
        payment_state,
        "2026-11",
        "member-001",
        {
            "invoice": "lnbc-private-invoice",
            "payment_hash": PAYMENT_HASH,
            "amount_msat": 25_000,
            "expires_at": 1_800_000_000,
        },
        pricing_snapshot={
            "plan": "coworking",
            "quantity": 1,
            "amount_mxn": "1100",
            "mxn_per_btc": "1600000",
        },
    )
    return (
        {
            "members": {"member-001": member},
            "payment_state": payment_state,
        },
        {
            "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
            "PAYMENT_VERIFICATION_ENABLED": True,
            "PAYMENT_NWC_URI": NWC_URI,
            "PAYMENT_INVOICE_EXPIRY_SECONDS": 259_200,
            "ADMIN_ID": 42,
            "PAYMENT_ADMIN_ID": payment_admin_id,
        },
    )


def _settle_and_queue_notifications(monkeypatch, tmp_path, payment_admin_id=42):
    """Run one verified settlement and return its persisted notification queue."""
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        SettledNwcClient,
    )
    state, config = _state_and_config(
        tmp_path,
        payment_admin_id=payment_admin_id,
    )
    result = asyncio.run(
        check_payment_invoice_status(
            config,
            state,
            now=datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc),
        )
    )
    return state, config, result


def test_settlement_notifies_member_and_deduplicated_admin_once(
    monkeypatch, tmp_path
):
    """Verified settlement sends private messages without invoice secrets."""
    state, config, result = _settle_and_queue_notifications(
        monkeypatch,
        tmp_path,
    )
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    delivery = asyncio.run(
        deliver_membership_settlement_notifications(context, config, state)
    )
    messages = context.bot.send_message.await_args_list

    assert result == {"checked": 1, "settled": 1, "failed": 0}
    assert delivery == {"sent": 2, "failed": 0}
    assert [call.kwargs["chat_id"] for call in messages] == [101, 42]
    assert "noviembre de 2026" in messages[0].kwargs["text"]
    assert "Pago de membresía confirmado" in messages[1].kwargs["text"]
    assert "Ada (member-001)" in messages[1].kwargs["text"]
    assert "MXN 1100" in messages[1].kwargs["text"]
    assert "25.000 sats" in messages[1].kwargs["text"]
    for message_call in messages:
        message = message_call.kwargs["text"]
        assert "lnbc-private-invoice" not in message
        assert PAYMENT_HASH not in message
        assert PAYMENT_PREIMAGE not in message

    repeated = asyncio.run(
        deliver_membership_settlement_notifications(context, config, state)
    )

    assert repeated == {"sent": 0, "failed": 0}
    assert context.bot.send_message.await_count == 2


def test_notification_retry_only_resends_failed_recipient(
    monkeypatch, tmp_path
):
    """Successful recipients stay deduplicated while failures are retried."""
    state, config, _result = _settle_and_queue_notifications(
        monkeypatch,
        tmp_path,
        payment_admin_id=84,
    )
    send_message = AsyncMock(
        side_effect=[
            RuntimeError("member chat unavailable"),
            None,
            None,
            None,
        ]
    )
    context = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))

    first_delivery = asyncio.run(
        deliver_membership_settlement_notifications(context, config, state)
    )
    second_delivery = asyncio.run(
        deliver_membership_settlement_notifications(context, config, state)
    )

    assert first_delivery == {"sent": 2, "failed": 1}
    assert second_delivery == {"sent": 1, "failed": 0}
    assert [call.kwargs["chat_id"] for call in send_message.await_args_list] == [
        101,
        42,
        84,
        101,
    ]


def test_unverified_invoice_has_no_settlement_notifications(
    monkeypatch, tmp_path
):
    """A pending invoice never queues member or administrator notices."""
    state, config = _state_and_config(tmp_path)

    class PendingNwcClient:
        """Return a pending invoice lookup for this test."""

        def __init__(self, connection_uri):
            """Accept the configured wallet connection."""

        async def lookup_invoice(self, *, payment_hash):
            """Return a pending response with the expected identity."""
            return NwcInvoiceStatus(
                payment_hash=payment_hash,
                state="pending",
                amount_msat=25_000,
                settled_at=None,
                preimage=PAYMENT_PREIMAGE,
            )

    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        PendingNwcClient,
    )
    asyncio.run(check_payment_invoice_status(config, state))
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    delivery = asyncio.run(
        deliver_membership_settlement_notifications(context, config, state)
    )

    assert delivery == {"sent": 0, "failed": 0}
    context.bot.send_message.assert_not_awaited()
