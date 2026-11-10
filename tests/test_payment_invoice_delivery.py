"""Tests for creating and delivering membership payment invoices."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core import membership_payments
from core.membership_payments import (
    PAYMENT_TIMEZONE,
    build_member,
    build_multi_period_payment_state,
    check_payment_reminders,
    check_payment_invoice_status,
    get_or_create_payment_invoice,
    load_multi_period_payment_state,
)
from core.nwc_client import NwcInvoice, NwcInvoiceStatus


NWC_URI = (
    "nostr+walletconnect://wallet-key?"
    "relay=wss%3A%2F%2Frelay.example&secret=test-secret"
)


def _config(tmp_path):
    """Build enabled invoice settings for scheduler tests."""
    return {
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
        "PAYMENT_VERIFICATION_ENABLED": True,
        "PAYMENT_NWC_URI": NWC_URI,
        "PAYMENT_INVOICE_EXPIRY_SECONDS": 259_200,
        "PAYMENT_EXCHANGE_RATE_URL": "https://example.test/rate",
        "PAYMENT_EXCHANGE_RATE_CACHE_FILE": str(tmp_path / "rate.json"),
        "PAYMENT_EXCHANGE_RATE_MAX_AGE_SECONDS": 900,
        "PAYMENT_EXCHANGE_RATE_TIMEOUT_SECONDS": 10,
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_ADVANCE_REMINDER_DAYS": 3,
    }


MEMBERSHIP_PLANS = {
    "coworking": {"name": "Coworking space", "price_mxn": 1100},
}


class FakeMxnBtcRateProvider:
    """Return a deterministic exchange rate without making an HTTP request."""

    def __init__(self, *args, **kwargs):
        """Accept production provider settings for the test double."""

    def get_rate(self, now=None):
        """Return the configured test rate in MXN per BTC."""
        return 1_600_000


class FakeNwcInvoiceClient:
    """Return deterministic invoices without contacting a wallet service."""

    calls = []

    def __init__(self, connection_uri):
        """Record the configured connection without exposing it in assertions."""
        self.connection_uri = connection_uri

    async def make_invoice(self, *, amount_msat, description, expiry_seconds):
        """Return a representative invoice response."""
        self.calls.append((amount_msat, description, expiry_seconds))
        return NwcInvoice(
            invoice="lnbc-test-invoice",
            payment_hash="hash-test-invoice",
            amount_msat=amount_msat,
            expires_at=1_800_000_000,
        )


def test_invoice_creation_is_reused_for_the_same_member_period(monkeypatch, tmp_path):
    """A pending invoice is reused instead of creating a second invoice."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
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
    config = _config(tmp_path)
    now = datetime(2026, 11, 1, 10, 0, tzinfo=PAYMENT_TIMEZONE)

    first, first_created = asyncio.run(
        get_or_create_payment_invoice(
            state,
            "2026-11",
            "member-001",
            config,
            now=now,
            member=member,
            membership_plans=MEMBERSHIP_PLANS,
        )
    )
    second, second_created = asyncio.run(
        get_or_create_payment_invoice(
            state,
            "2026-11",
            "member-001",
            config,
            now=now,
            member=member,
            membership_plans=MEMBERSHIP_PLANS,
        )
    )

    assert first["invoice"] == second["invoice"]
    assert first_created is True
    assert second_created is False
    assert len(FakeNwcInvoiceClient.calls) == 1
    assert FakeNwcInvoiceClient.calls[0][0] == 68_750_000
    assert FakeNwcInvoiceClient.calls[0][1] == "Ada - membresía noviembre de 2026"
    assert first["pricing_snapshot"] == {
        "plan": "coworking",
        "quantity": 1,
        "amount_mxn": "1100",
        "mxn_per_btc": "1600000",
    }


def test_due_private_reminder_includes_created_invoice(monkeypatch, tmp_path):
    """The private reminder contains the invoice and persists its metadata."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    monkeypatch.setattr(
        membership_payments,
        "MxnBtcRateProvider",
        FakeMxnBtcRateProvider,
    )
    bot = SimpleNamespace(
        send_message=AsyncMock(),
        send_photo=AsyncMock(),
    )
    context = SimpleNamespace(bot=bot)
    member = build_member("Ada", 101, "coworking")
    state = {
        "members": {"member-001": member},
        "membership_plans": MEMBERSHIP_PLANS,
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member},
            ["2026-11"],
        ),
    }

    asyncio.run(
        check_payment_reminders(
            context,
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 2, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )

    assert bot.send_photo.await_count == 1
    assert bot.send_message.await_count == 0
    caption = bot.send_photo.await_args.kwargs["caption"]
    assert "Pago de membresía: noviembre de 2026" in caption
    assert "68750 sats (68750000 msat)" in caption
    assert "Expira:" in caption
    assert "lnbc-test-invoice" in caption
    member_state = state["payment_state"]["periods"]["2026-11"]["members"][
        "member-001"
    ]
    assert member_state["active_invoice_payment_hash"] == "hash-test-invoice"
    assert "2026-11:daily:2026-11-02:member-001" in state["payment_state"][
        "periods"
    ]["2026-11"]["personal_reminders_sent"]


def test_qr_delivery_falls_back_to_one_text_invoice(monkeypatch, tmp_path):
    """A photo-delivery failure still sends one copyable invoice message."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    monkeypatch.setattr(
        membership_payments,
        "MxnBtcRateProvider",
        FakeMxnBtcRateProvider,
    )
    bot = SimpleNamespace(
        send_message=AsyncMock(),
        send_photo=AsyncMock(side_effect=RuntimeError("photo unavailable")),
    )
    context = SimpleNamespace(bot=bot)
    member = build_member("Ada", 101, "coworking")
    state = {
        "members": {"member-001": member},
        "membership_plans": MEMBERSHIP_PLANS,
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member},
            ["2026-11"],
        ),
    }

    asyncio.run(
        check_payment_reminders(
            context,
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 2, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )

    assert bot.send_photo.await_count == 1
    assert bot.send_message.await_count == 1
    assert "lnbc-test-invoice" in bot.send_message.await_args.kwargs["text"]
    assert "2026-11:daily:2026-11-02:member-001" in state["payment_state"][
        "periods"
    ]["2026-11"]["personal_reminders_sent"]


def test_multiple_private_periods_get_separate_qr_messages(monkeypatch, tmp_path):
    """Each invoice is labeled by period when one member owes two periods."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    monkeypatch.setattr(
        membership_payments,
        "MxnBtcRateProvider",
        FakeMxnBtcRateProvider,
    )
    bot = SimpleNamespace(
        send_message=AsyncMock(),
        send_photo=AsyncMock(),
    )
    context = SimpleNamespace(bot=bot)
    member = build_member("Ada", 101, "coworking")
    state = {
        "members": {"member-001": member},
        "membership_plans": MEMBERSHIP_PLANS,
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member},
            ["2026-11", "2026-12"],
        ),
    }

    asyncio.run(
        check_payment_reminders(
            context,
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 28, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )

    assert bot.send_photo.await_count == 2
    captions = [call.kwargs["caption"] for call in bot.send_photo.await_args_list]
    assert any("noviembre de 2026" in caption for caption in captions)
    assert any("diciembre de 2026" in caption for caption in captions)
    assert bot.send_message.await_count == 0


def test_public_payment_reminder_never_includes_invoice(monkeypatch, tmp_path):
    """Public payment reminders do not expose private invoice data."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FakeNwcInvoiceClient,
    )
    monkeypatch.setattr(
        membership_payments,
        "MxnBtcRateProvider",
        FakeMxnBtcRateProvider,
    )
    bot = SimpleNamespace(
        send_message=AsyncMock(),
        send_photo=AsyncMock(),
    )
    context = SimpleNamespace(bot=bot)
    member = build_member("Ada", 101, "coworking")
    state = {
        "members": {"member-001": member},
        "membership_plans": MEMBERSHIP_PLANS,
        "notification_preferences": {"101": {"payments": True}},
        "group_notification_categories": {"payments": [-100123]},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member},
            ["2026-11"],
        ),
    }

    asyncio.run(
        check_payment_reminders(
            context,
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 1, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )

    public_messages = [
        call.kwargs["text"]
        for call in bot.send_message.await_args_list
        if call.kwargs["chat_id"] == -100123
    ]
    assert len(public_messages) == 1
    assert "lnbc-test-invoice" not in public_messages[0]


def test_invoice_failure_does_not_block_normal_private_reminder(
    monkeypatch,
    tmp_path,
):
    """A temporary wallet failure still delivers the ordinary reminder."""

    class FailingNwcInvoiceClient:
        """Simulate an unavailable wallet service."""

        def __init__(self, connection_uri):
            """Accept the connection URI without storing or logging it."""

        async def make_invoice(self, **kwargs):
            """Fail as an unavailable wallet would."""
            raise RuntimeError("wallet unavailable")

    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        FailingNwcInvoiceClient,
    )
    monkeypatch.setattr(
        membership_payments,
        "MxnBtcRateProvider",
        FakeMxnBtcRateProvider,
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    member = build_member("Ada", 101, "coworking")
    state = {
        "members": {"member-001": member},
        "membership_plans": MEMBERSHIP_PLANS,
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member},
            ["2026-11"],
        ),
    }

    asyncio.run(
        check_payment_reminders(
            context,
            _config(tmp_path),
            state,
            now=datetime(2026, 11, 1, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )

    text = bot.send_message.await_args.kwargs["text"]
    assert "Ada" in text
    assert "lnbc-" not in text
    member_state = state["payment_state"]["periods"]["2026-11"]["members"][
        "member-001"
    ]
    assert "payment_invoices" not in member_state


def test_expired_invoice_is_replaced_only_after_wallet_confirmation(monkeypatch, tmp_path):
    """A local expiry blocks replacement until the wallet confirms expiry."""

    class RotatingNwcInvoiceClient:
        """Return a different invoice identity after expiry."""

        calls = 0
        lookup_calls = []

        def __init__(self, connection_uri):
            """Accept the configured wallet connection."""

        async def make_invoice(self, *, amount_msat, description, expiry_seconds):
            """Create a short-lived deterministic invoice for the test."""
            type(self).calls += 1
            invoice_number = type(self).calls
            return NwcInvoice(
                invoice=f"lnbc-test-{invoice_number}",
                payment_hash=f"hash-test-{invoice_number}",
                amount_msat=amount_msat,
                expires_at=1 if invoice_number == 1 else 1_800_000_000,
            )

        async def lookup_invoice(self, *, payment_hash):
            """Confirm the old invoice expired in the wallet."""
            type(self).lookup_calls.append(payment_hash)
            return NwcInvoiceStatus(
                payment_hash=payment_hash,
                state="expired",
                amount_msat=68_750_000,
                settled_at=None,
            )

    monkeypatch.setattr(
        membership_payments,
        "NwcInvoiceClient",
        RotatingNwcInvoiceClient,
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
    config = _config(tmp_path)
    first, _ = asyncio.run(
        get_or_create_payment_invoice(
            state,
            "2026-11",
            "member-001",
            config,
            now=datetime(2026, 11, 1, 10, 0, tzinfo=PAYMENT_TIMEZONE),
            member=member,
            membership_plans=MEMBERSHIP_PLANS,
        )
    )
    deferred, deferred_created = asyncio.run(
        get_or_create_payment_invoice(
            state,
            "2026-11",
            "member-001",
            config,
            now=datetime(2026, 11, 2, 10, 0, tzinfo=PAYMENT_TIMEZONE),
            member=member,
            membership_plans=MEMBERSHIP_PLANS,
        )
    )
    assert deferred is None
    assert deferred_created is False
    assert RotatingNwcInvoiceClient.calls == 1

    verified = {"payment_state": state}
    result = asyncio.run(
        check_payment_invoice_status(
            config,
            verified,
            now=datetime(2026, 11, 2, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )
    assert result == {"checked": 1, "settled": 0, "failed": 0}
    assert RotatingNwcInvoiceClient.lookup_calls == ["hash-test-1"]

    second, created = asyncio.run(
        get_or_create_payment_invoice(
            state,
            "2026-11",
            "member-001",
            config,
            now=datetime(2026, 11, 2, 10, 0, tzinfo=PAYMENT_TIMEZONE),
            member=member,
            membership_plans=MEMBERSHIP_PLANS,
        )
    )

    member_state = state["periods"]["2026-11"]["members"]["member-001"]
    assert created is True
    assert first["payment_hash"] == "hash-test-1"
    assert second["payment_hash"] == "hash-test-2"
    assert member_state["payment_invoices"]["hash-test-1"]["state"] == "expired"
    assert member_state["payment_invoices"]["hash-test-1"]["expiry_confirmed"] is True


def test_invoice_is_persisted_before_private_delivery(monkeypatch, tmp_path):
    """Telegram sees an invoice only after the matching record is on disk."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", FakeNwcInvoiceClient)
    monkeypatch.setattr(membership_payments, "MxnBtcRateProvider", FakeMxnBtcRateProvider)
    config = _config(tmp_path)

    async def inspect_delivery(**kwargs):
        """Assert durable invoice identity at the delivery boundary."""
        persisted = load_multi_period_payment_state(config["PAYMENT_STATE_FILE"])
        saved = persisted["periods"]["2026-11"]["members"]["member-001"]
        assert saved["active_invoice_payment_hash"] == "hash-test-invoice"
        assert "lnbc-test-invoice" in kwargs["caption"]

    bot = SimpleNamespace(
        send_message=AsyncMock(),
        send_photo=AsyncMock(side_effect=inspect_delivery),
    )
    member = build_member("Ada", 101, "coworking")
    state = {
        "members": {"member-001": member},
        "membership_plans": MEMBERSHIP_PLANS,
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member}, ["2026-11"]
        ),
    }

    asyncio.run(check_payment_reminders(
        SimpleNamespace(bot=bot), config, state,
        now=datetime(2026, 11, 2, 10, 0, tzinfo=PAYMENT_TIMEZONE),
    ))

    assert bot.send_photo.await_count == 1
    assert bot.send_message.await_count == 0


def test_failed_delivery_reuses_saved_invoice_after_restart(monkeypatch, tmp_path):
    """Failed QR and text delivery do not mint a second invoice on retry."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", FakeNwcInvoiceClient)
    monkeypatch.setattr(membership_payments, "MxnBtcRateProvider", FakeMxnBtcRateProvider)
    config = _config(tmp_path)
    member = build_member("Ada", 101, "coworking")
    state = {
        "members": {"member-001": member},
        "membership_plans": MEMBERSHIP_PLANS,
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member}, ["2026-11"]
        ),
    }
    failed_bot = SimpleNamespace(
        send_photo=AsyncMock(side_effect=RuntimeError("photo unavailable")),
        send_message=AsyncMock(side_effect=RuntimeError("text unavailable")),
    )
    now = datetime(2026, 11, 2, 10, 0, tzinfo=PAYMENT_TIMEZONE)

    asyncio.run(check_payment_reminders(SimpleNamespace(bot=failed_bot), config, state, now=now))
    saved = load_multi_period_payment_state(config["PAYMENT_STATE_FILE"])
    assert saved["periods"]["2026-11"]["personal_reminders_sent"] == {}
    state["payment_state"] = saved
    retry_bot = SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock())

    asyncio.run(check_payment_reminders(SimpleNamespace(bot=retry_bot), config, state, now=now))

    assert len(FakeNwcInvoiceClient.calls) == 1
    assert retry_bot.send_photo.await_count == 1
    assert retry_bot.send_message.await_count == 0
    period_state = state["payment_state"]["periods"]["2026-11"]
    assert "2026-11:daily:2026-11-02:member-001" in period_state[
        "personal_reminders_sent"
    ]


def test_persistence_failure_prevents_invoice_delivery(monkeypatch, tmp_path):
    """An unrecorded invoice is never exposed to a member."""
    FakeNwcInvoiceClient.calls = []
    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", FakeNwcInvoiceClient)
    monkeypatch.setattr(membership_payments, "MxnBtcRateProvider", FakeMxnBtcRateProvider)

    original_save = membership_payments.save_multi_period_payment_state

    def fail_save(*args, **kwargs):
        """Reject only the strict pre-delivery invoice persistence step."""
        if kwargs.get("raise_on_error"):
            raise OSError("storage unavailable")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(membership_payments, "save_multi_period_payment_state", fail_save)
    member = build_member("Ada", 101, "coworking")
    state = {
        "members": {"member-001": member},
        "membership_plans": MEMBERSHIP_PLANS,
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member}, ["2026-11"]
        ),
    }
    bot = SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock())

    asyncio.run(check_payment_reminders(
        SimpleNamespace(bot=bot), _config(tmp_path), state,
        now=datetime(2026, 11, 2, 10, 0, tzinfo=PAYMENT_TIMEZONE),
    ))

    assert bot.send_photo.await_count == 0
    assert all(
        "lnbc-test-invoice" not in call.kwargs["text"]
        for call in bot.send_message.await_args_list
    )
    member_state = state["payment_state"]["periods"]["2026-11"]["members"]["member-001"]
    assert "payment_invoices" not in member_state
