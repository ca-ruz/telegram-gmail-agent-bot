"""Regression tests for isolated membership invoice pilot delivery."""

import asyncio
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import membership_payments
from core.membership_payments import (
    PAYMENT_TIMEZONE,
    build_member,
    build_monthly_payment_state,
    build_multi_period_payment_state,
    check_payment_invoice_status,
    check_payment_reminders,
    load_multi_period_payment_state,
)
from core.nwc_client import NwcInvoice, NwcInvoiceStatus
from core.payment_delivery_policy import resolve_payment_delivery_policy


NWC_URI = (
    "nostr+walletconnect://wallet-key?"
    "relay=wss%3A%2F%2Frelay.example&secret=test-secret"
)
REMINDER_TIME = datetime(2026, 11, 6, 10, 0, tzinfo=PAYMENT_TIMEZONE)


class FixedRateProvider:
    """Supply a deterministic MXN/BTC price without an HTTP request."""

    def __init__(self, *args, **kwargs):
        """Accept the production rate-provider configuration."""

    def get_rate(self, now=None):
        """Return the fixed MXN value of one bitcoin."""
        return 1_600_000


class FakeNwcClient:
    """Record invoice creation and return queued wallet lookup results."""

    def __init__(self):
        """Start with no issued invoices or lookup results."""
        self.created = []
        self.lookups = []
        self.statuses = []

    async def make_invoice(self, *, amount_msat, description, expiry_seconds):
        """Return a unique invoice with a matching test preimage."""
        preimage = bytes([len(self.created)]) * 32
        payment_hash = hashlib.sha256(preimage).hexdigest()
        invoice = NwcInvoice(
            invoice=f"lnbc-pilot-{len(self.created) + 1}",
            payment_hash=payment_hash,
            amount_msat=amount_msat,
            expires_at=1_800_000_000,
        )
        self.created.append({
            "invoice": invoice,
            "preimage": preimage.hex(),
            "description": description,
            "expiry_seconds": expiry_seconds,
        })
        return invoice

    async def lookup_invoice(self, *, payment_hash):
        """Return the next queued status for the requested invoice."""
        self.lookups.append(payment_hash)
        return self.statuses.pop(0)


@pytest.fixture
def fake_wallet(monkeypatch):
    """Replace NWC and price services for scheduled payment tests."""
    client = FakeNwcClient()

    def client_factory(connection_uri):
        """Return the isolated wallet client."""
        assert connection_uri == NWC_URI
        return client

    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", client_factory)
    monkeypatch.setattr(membership_payments, "MxnBtcRateProvider", FixedRateProvider)
    return client


def _config(tmp_path, mode="pilot", allowlist="member-001"):
    """Build payment settings for an explicitly configured pilot."""
    return {
        "ENABLE_PAYMENT_REMINDERS": True,
        "PAYMENT_DELIVERY_MODE": mode,
        "PAYMENT_PILOT_MEMBER_IDS": allowlist,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
        "PAYMENT_VERIFICATION_ENABLED": True,
        "PAYMENT_NWC_URI": NWC_URI,
        "PAYMENT_INVOICE_EXPIRY_SECONDS": 259_200,
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_ADMIN_ID": 777,
        "MEMBERS_GROUP_ID": -100123,
        "PAYMENT_GROUP_BROADCASTS_ENABLED": True,
    }


def _state():
    """Build two active unpaid members with different plan prices."""
    members = {
        "member-001": build_member("Ada", 101, "coworking"),
        "member-002": build_member("Lin", 202, "private_office", 1.5),
    }
    return {
        "members": members,
        "membership_plans": {
            "coworking": {"name": "Coworking", "price_mxn": 1100},
            "private_office": {"name": "Private office", "price_mxn": 5500},
        },
        "notification_preferences": {
            "101": {"payments": True},
            "202": {"payments": True},
        },
        "payment_state": build_multi_period_payment_state(members, ["2026-11"]),
    }


def _bot():
    """Return a Telegram test double that records private and group sends."""
    return SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())


def _run_reminders(bot, config, state, now=REMINDER_TIME):
    """Execute one scheduled reminder check against the fake Telegram bot."""
    asyncio.run(
        check_payment_reminders(SimpleNamespace(bot=bot), config, state, now=now)
    )


def _member_record(state, member_id="member-001"):
    """Return the selected member's November payment record."""
    return state["payment_state"]["periods"]["2026-11"]["members"][member_id]


def test_pilot_sends_only_one_priced_invoice_and_no_group_or_admin_message(
    fake_wallet, tmp_path
):
    """The pilot uses normal pricing but contacts only the chosen member."""
    state = _state()
    period_state = state["payment_state"]["periods"]["2026-11"]
    period_state["public_reminders_sent"]["2026-11:week_1_1"] = (
        "2026-11-06T12:00:00+00:00"
    )
    bot = _bot()
    config = _config(tmp_path)

    _run_reminders(bot, config, state)

    assert [call.kwargs["chat_id"] for call in bot.send_photo.await_args_list] == [
        101
    ]
    assert bot.send_message.await_count == 0
    assert len(fake_wallet.created) == 1
    issued = fake_wallet.created[0]
    assert issued["invoice"].amount_msat == 68_750_000
    assert issued["description"] == "Ada - membresía noviembre de 2026"
    assert issued["expiry_seconds"] == 259_200
    assert issued["invoice"].invoice in bot.send_photo.await_args.kwargs["caption"]
    record = _member_record(state)
    assert record["payment_invoices"][issued["invoice"].payment_hash][
        "pricing_snapshot"
    ] == {
        "plan": "coworking",
        "quantity": 1,
        "amount_mxn": "1100",
        "mxn_per_btc": "1600000",
    }
    assert "payment_invoices" not in _member_record(state, "member-002")
    saved = load_multi_period_payment_state(config["PAYMENT_STATE_FILE"])
    assert saved["periods"]["2026-11"]["members"]["member-001"][
        "active_invoice_payment_hash"
    ] == issued["invoice"].payment_hash


@pytest.mark.parametrize(
    ("mode", "allowlist"),
    [
        (None, "member-001"),
        ("off", "member-001"),
        ("invalid", "member-001"),
        ("pilot", ""),
        ("pilot", "member-999"),
        ("pilot", "member-001,member-002"),
        ("pilot", "member-001,"),
        ("full", "member-001"),
    ],
)
def test_invalid_or_ambiguous_configuration_sends_nothing(
    fake_wallet, tmp_path, mode, allowlist
):
    """Missing mode, bad pilot IDs, and a leftover full-mode list fail closed."""
    state = _state()
    config = _config(tmp_path, mode=mode, allowlist=allowlist)
    if mode is None:
        config.pop("PAYMENT_DELIVERY_MODE")
    bot = _bot()

    _run_reminders(bot, config, state)

    assert bot.send_photo.await_count == 0
    assert bot.send_message.await_count == 0
    assert fake_wallet.created == []
    assert resolve_payment_delivery_policy(config, state["members"]).mode == "off"


@pytest.mark.parametrize("ineligible_reason", ["paid", "inactive"])
def test_paid_or_inactive_pilot_member_receives_nothing(
    fake_wallet, tmp_path, ineligible_reason
):
    """An allowlist never bypasses normal membership eligibility."""
    state = _state()
    if ineligible_reason == "paid":
        _member_record(state)["status"] = "paid"
    else:
        state["members"]["member-001"]["active"] = False
    bot = _bot()

    _run_reminders(bot, _config(tmp_path), state)

    assert bot.send_photo.await_count == 0
    assert bot.send_message.await_count == 0
    assert fake_wallet.created == []


def test_pilot_filters_roster_before_telegram_id_deduplication(
    fake_wallet, tmp_path
):
    """A non-allowlisted duplicate Telegram ID cannot hide the pilot member."""
    state = _state()
    state["members"]["member-000"] = build_member("Other", 101, "coworking")
    state["payment_state"] = build_multi_period_payment_state(
        state["members"], ["2026-11"]
    )
    bot = _bot()

    _run_reminders(bot, _config(tmp_path), state)

    assert [call.kwargs["chat_id"] for call in bot.send_photo.await_args_list] == [
        101
    ]
    assert fake_wallet.created[0]["description"].startswith("Ada - ")
    assert "payment_invoices" not in _member_record(state, "member-000")


def test_legacy_period_pilot_suppresses_other_recipients_and_broadcasts(
    fake_wallet, tmp_path
):
    """The legacy payment-state path respects the same pilot boundaries."""
    state = _state()
    state["payment_state"] = build_monthly_payment_state(
        state["members"], "2026-11"
    )
    state["payment_state"]["public_reminders_sent"] = {
        "2026-11:first_day": "2026-11-01T12:00:00+00:00"
    }
    bot = _bot()

    _run_reminders(
        bot,
        _config(tmp_path),
        state,
        now=datetime(2026, 11, 1, 10, 0, tzinfo=PAYMENT_TIMEZONE),
    )

    assert [call.kwargs["chat_id"] for call in bot.send_message.await_args_list] == [
        101
    ]
    assert bot.send_photo.await_count == 0
    assert fake_wallet.created == []


def test_failed_delivery_reuses_persisted_invoice_after_restart(
    fake_wallet, tmp_path
):
    """A restart retries the same invoice without consuming delivery history."""
    state = _state()
    config = _config(tmp_path)
    failed_bot = SimpleNamespace(
        send_photo=AsyncMock(side_effect=RuntimeError("photo unavailable")),
        send_message=AsyncMock(side_effect=RuntimeError("text unavailable")),
    )

    _run_reminders(failed_bot, config, state)

    saved = load_multi_period_payment_state(config["PAYMENT_STATE_FILE"])
    assert len(fake_wallet.created) == 1
    assert saved["periods"]["2026-11"]["personal_reminders_sent"] == {}
    state["payment_state"] = saved
    retry_bot = _bot()
    _run_reminders(retry_bot, config, state)

    assert len(fake_wallet.created) == 1
    assert [call.kwargs["chat_id"] for call in retry_bot.send_photo.await_args_list] == [
        101
    ]
    assert retry_bot.send_message.await_count == 0
    invoice = fake_wallet.created[0]["invoice"]
    assert invoice.invoice in retry_bot.send_photo.await_args.kwargs["caption"]
    assert _member_record(state)["active_invoice_payment_hash"] == invoice.payment_hash
    assert len(_member_record(state)["payment_invoices"]) == 1
    assert "2026-11:daily:2026-11-06:member-001" in state["payment_state"][
        "periods"
    ]["2026-11"]["personal_reminders_sent"]


def test_pilot_invoice_stays_pending_then_scheduled_verifier_marks_paid(
    fake_wallet, tmp_path
):
    """Two unpaid polls precede a proven settlement without manual recheck."""
    state = _state()
    config = _config(tmp_path)
    bot = _bot()
    _run_reminders(bot, config, state)
    state["payment_state"] = load_multi_period_payment_state(
        config["PAYMENT_STATE_FILE"]
    )
    issued = fake_wallet.created[0]
    invoice = issued["invoice"]
    pending = NwcInvoiceStatus(
        payment_hash=invoice.payment_hash,
        state="pending",
        amount_msat=invoice.amount_msat,
        settled_at=None,
        preimage=issued["preimage"],
    )
    settled_at = int(datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc).timestamp())
    settled = NwcInvoiceStatus(
        payment_hash=invoice.payment_hash,
        state="settled",
        amount_msat=invoice.amount_msat,
        settled_at=settled_at,
        preimage=issued["preimage"],
    )
    fake_wallet.statuses = [pending, pending, settled]

    for minute in (0, 5):
        result = asyncio.run(check_payment_invoice_status(
            config,
            state,
            now=datetime(2026, 11, 6, 17, minute, tzinfo=timezone.utc),
        ))
        assert result == {"checked": 1, "settled": 0, "failed": 0}
        assert _member_record(state)["status"] == "unpaid"

    result = asyncio.run(check_payment_invoice_status(
        config,
        state,
        now=datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc),
    ))

    assert result == {"checked": 1, "settled": 1, "failed": 0}
    assert fake_wallet.lookups == [invoice.payment_hash] * 3
    assert _member_record(state)["status"] == "paid"
    assert _member_record(state)["payment_invoices"][invoice.payment_hash][
        "state"
    ] == "settled"
    persisted = load_multi_period_payment_state(config["PAYMENT_STATE_FILE"])
    assert persisted["periods"]["2026-11"]["members"]["member-001"][
        "status"
    ] == "paid"
    assert persisted["periods"]["2026-11"]["members"]["member-002"][
        "status"
    ] == "unpaid"
    _run_reminders(
        bot,
        config,
        state,
        now=datetime(2026, 11, 7, 10, 0, tzinfo=PAYMENT_TIMEZONE),
    )
    assert bot.send_photo.await_count == 1
    assert len(fake_wallet.created) == 1


def test_full_delivery_requires_clearing_the_pilot_allowlist(
    fake_wallet, tmp_path
):
    """Switching to full is explicit and does not replay the pilot reminder."""
    state = _state()
    config = _config(tmp_path)
    bot = _bot()
    _run_reminders(bot, config, state)
    assert [call.kwargs["chat_id"] for call in bot.send_photo.await_args_list] == [
        101
    ]

    config["PAYMENT_DELIVERY_MODE"] = "full"
    _run_reminders(bot, config, state)
    assert bot.send_photo.await_count == 1
    assert bot.send_message.await_count == 0

    config["PAYMENT_PILOT_MEMBER_IDS"] = ""
    _run_reminders(bot, config, state)

    assert [call.kwargs["chat_id"] for call in bot.send_photo.await_args_list] == [
        101, 202
    ]
    assert [call.kwargs["chat_id"] for call in bot.send_message.await_args_list] == [
        -100123
    ]
    assert len(fake_wallet.created) == 2
    assert "payment_invoices" in _member_record(state, "member-002")
