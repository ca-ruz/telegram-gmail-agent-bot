"""Tests for the targeted production membership-invoice command."""

import asyncio
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers import admin
from core import membership_payments
from core.membership_payments import (
    PAYMENT_TIMEZONE,
    build_member,
    build_multi_period_payment_state,
    check_payment_invoice_status,
    check_payment_reminders,
    current_billing_period,
    load_multi_period_payment_state,
)
from core.nwc_client import NwcInvoice, NwcInvoiceStatus


NWC_URI = (
    "nostr+walletconnect://wallet-key?"
    "relay=wss%3A%2F%2Frelay.example&secret=test-secret"
)


class FixedRateProvider:
    """Supply a deterministic exchange rate without network access."""

    def __init__(self, *args, **kwargs):
        """Accept the production rate-provider arguments."""

    def get_rate(self, now=None):
        """Return the fixed MXN-per-BTC rate."""
        return 1_600_000


class FakeWallet:
    """Create unique invoices and return queued lookup statuses."""

    def __init__(self):
        """Initialize empty invoice and lookup histories."""
        self.created = []
        self.statuses = []
        self.lookups = []

    async def make_invoice(self, *, amount_msat, description, expiry_seconds):
        """Create an invoice with a cryptographically matching test preimage."""
        preimage = bytes([len(self.created)]) * 32
        payment_hash = hashlib.sha256(preimage).hexdigest()
        invoice = NwcInvoice(
            invoice=f"lnbc-member-{len(self.created) + 1}",
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
        """Return the next wallet result for the requested hash."""
        self.lookups.append(payment_hash)
        return self.statuses.pop(0)


@pytest.fixture
def fake_wallet(monkeypatch):
    """Replace external rate and NWC clients with deterministic fakes."""
    wallet = FakeWallet()

    def wallet_factory(connection_uri):
        """Return the fake wallet for the configured connection."""
        assert connection_uri == NWC_URI
        return wallet

    monkeypatch.setattr(membership_payments, "NwcInvoiceClient", wallet_factory)
    monkeypatch.setattr(membership_payments, "MxnBtcRateProvider", FixedRateProvider)
    return wallet


def _state():
    """Build a current-period roster with one pilot target and one bystander."""
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
        "payment_state": build_multi_period_payment_state(
            members, [current_billing_period()]
        ),
    }


def _config(tmp_path, *, mode="pilot", allowlist="member-001"):
    """Build pilot configuration with scheduled reminders enabled."""
    return {
        "ENABLE_PAYMENT_REMINDERS": True,
        "PAYMENT_DELIVERY_MODE": mode,
        "PAYMENT_PILOT_MEMBER_IDS": allowlist,
        "PAYMENT_GROUP_BROADCASTS_ENABLED": True,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
        "PAYMENT_VERIFICATION_ENABLED": True,
        "PAYMENT_NWC_URI": NWC_URI,
        "PAYMENT_INVOICE_EXPIRY_SECONDS": 259_200,
        "PAYMENT_EXCHANGE_RATE_URL": "https://example.test/rate",
        "PAYMENT_EXCHANGE_RATE_CACHE_FILE": str(tmp_path / "rate.json"),
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_ADMIN_ID": 777,
        "MEMBERS_GROUP_ID": -100123,
    }


def _command_update(text, *, user_id=42, chat_type="private"):
    """Build a private or group command update."""
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(type=chat_type),
        message=SimpleNamespace(text=text, reply_text=AsyncMock()),
    )


def _confirmation_update(member_id="member-001", *, user_id=42, chat_type="private"):
    """Build the callback update for invoice confirmation or cancellation."""
    return SimpleNamespace(
        callback_query=SimpleNamespace(
            data=f"member_invoice_confirm:{member_id}",
            from_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(chat=SimpleNamespace(type=chat_type)),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
    )


def _context():
    """Build shared callback state and fake Telegram delivery methods."""
    return SimpleNamespace(
        user_data={},
        bot=SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock()),
    )


def _member_record(state, member_id="member-001"):
    """Return a member's record in the current billing period."""
    return state["payment_state"]["periods"][current_billing_period()][
        "members"
    ][member_id]


def test_command_previews_exact_price_then_delivers_only_to_selected_member(
    fake_wallet, tmp_path
):
    """Confirmation creates one correctly priced invoice for one member."""
    state = _state()
    config = _config(tmp_path)
    context = _context()
    update = _command_update("/memberinvoice member-001")
    asyncio.run(admin.member_invoice_test(update, context, 42, 77, state, config))

    assert fake_wallet.created == []
    preview = update.message.reply_text.await_args.args[0]
    assert "Ada (member-001)" in preview
    assert current_billing_period() in preview
    assert "MXN 1100" in preview
    assert "68,750.000 sats (68750000 msat)" in preview

    confirmation = _confirmation_update()
    asyncio.run(
        admin.handle_member_invoice_confirmation(
            confirmation, context, 42, 77, state, config
        )
    )

    assert len(fake_wallet.created) == 1
    assert fake_wallet.created[0]["invoice"].amount_msat == 68_750_000
    assert fake_wallet.created[0]["description"].startswith("Ada - ")
    context.bot.send_photo.assert_awaited_once()
    assert context.bot.send_photo.await_args.kwargs["chat_id"] == 101
    context.bot.send_message.assert_not_awaited()
    assert "lnbc-member-1" in context.bot.send_photo.await_args.kwargs["caption"]
    assert "payment_invoices" not in _member_record(state, "member-002")

    persisted = load_multi_period_payment_state(config["PAYMENT_STATE_FILE"])
    assert persisted["periods"][current_billing_period()]["members"][
        "member-001"
    ]["active_invoice_payment_hash"] == fake_wallet.created[0][
        "invoice"
    ].payment_hash


def test_command_delivery_is_deduplicated_against_same_day_scheduler(
    fake_wallet, tmp_path
):
    """The scheduled path does not resend the command's invoice that day."""
    state = _state()
    config = _config(tmp_path)
    context = _context()
    update = _command_update("/memberinvoice member-001")
    asyncio.run(admin.member_invoice_test(update, context, 42, 77, state, config))
    asyncio.run(
        admin.handle_member_invoice_confirmation(
            _confirmation_update(), context, 42, 77, state, config
        )
    )

    scheduler_bot = SimpleNamespace(
        send_photo=AsyncMock(),
        send_message=AsyncMock(),
    )
    scheduler_time = datetime.now(PAYMENT_TIMEZONE).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    asyncio.run(
        check_payment_reminders(
            SimpleNamespace(bot=scheduler_bot),
            config,
            state,
            now=scheduler_time,
        )
    )

    assert scheduler_bot.send_photo.await_count == 0
    assert scheduler_bot.send_message.await_count == 0
    assert len(fake_wallet.created) == 1


@pytest.mark.parametrize(
    ("mode", "allowlist", "target_status", "active"),
    [
        ("full", "", "unpaid", True),
        ("pilot", "member-002", "unpaid", True),
        ("pilot", "member-001", "paid", True),
        ("pilot", "member-001", "unpaid", False),
    ],
)
def test_command_refuses_wrong_mode_target_paid_or_inactive_member(
    fake_wallet, tmp_path, mode, allowlist, target_status, active
):
    """The command refuses unsafe targets before creating an invoice."""
    state = _state()
    state["members"]["member-001"]["active"] = active
    _member_record(state)["status"] = target_status
    config = _config(tmp_path, mode=mode, allowlist=allowlist)
    update = _command_update("/memberinvoice member-001")
    asyncio.run(
        admin.member_invoice_test(update, _context(), 42, 77, state, config)
    )

    assert fake_wallet.created == []
    assert update.message.reply_text.await_count == 1


def test_command_requires_payment_admin_and_private_chat(fake_wallet, tmp_path):
    """Unauthorized users and group chats cannot initiate member invoices."""
    state = _state()
    config = _config(tmp_path)
    unauthorized = _command_update("/memberinvoice member-001", user_id=999)
    asyncio.run(
        admin.member_invoice_test(unauthorized, _context(), 42, 77, state, config)
    )
    group_update = _command_update(
        "/memberinvoice member-001", chat_type="group"
    )
    asyncio.run(
        admin.member_invoice_test(group_update, _context(), 42, 77, state, config)
    )

    assert unauthorized.message.reply_text.await_count == 0
    assert "privado" in group_update.message.reply_text.await_args.args[0]
    assert fake_wallet.created == []


def test_command_refuses_unresolved_or_previously_settled_invoice(
    fake_wallet, tmp_path
):
    """Invoice history prevents a second charge for the same member-period."""
    state = _state()
    record = _member_record(state)
    record["payment_invoices"] = {
        "existing-hash": {
            "invoice": "lnbc-existing",
            "payment_hash": "existing-hash",
            "amount_msat": 68_750_000,
            "state": "pending",
        }
    }
    update = _command_update("/memberinvoice member-001")

    asyncio.run(
        admin.member_invoice_test(
            update, _context(), 42, 77, state, _config(tmp_path)
        )
    )

    assert fake_wallet.created == []
    assert "pendiente" in update.message.reply_text.await_args.args[0]


def test_failed_telegram_delivery_keeps_invoice_and_prevents_recreation(
    fake_wallet, tmp_path
):
    """A delivery failure preserves the invoice and blocks duplicate issuance."""
    state = _state()
    config = _config(tmp_path)
    context = _context()
    context.bot.send_photo.side_effect = RuntimeError("photo delivery failed")
    context.bot.send_message.side_effect = RuntimeError("text delivery failed")
    update = _command_update("/memberinvoice member-001")
    asyncio.run(admin.member_invoice_test(update, context, 42, 77, state, config))

    confirmation = _confirmation_update()
    asyncio.run(
        admin.handle_member_invoice_confirmation(
            confirmation, context, 42, 77, state, config
        )
    )

    assert len(fake_wallet.created) == 1
    assert "no se pudo entregar" in confirmation.callback_query.edit_message_text.await_args.args[0]
    assert _member_record(state)["active_invoice_payment_hash"] == (
        fake_wallet.created[0]["invoice"].payment_hash
    )

    retry = _command_update("/memberinvoice member-001")
    asyncio.run(admin.member_invoice_test(retry, context, 42, 77, state, config))

    assert len(fake_wallet.created) == 1
    assert "pendiente" in retry.message.reply_text.await_args.args[0]


def test_cancel_and_changed_quote_never_create_or_send_invoice(
    fake_wallet, monkeypatch, tmp_path
):
    """Cancellation or price drift requires a fresh explicit confirmation."""
    state = _state()
    config = _config(tmp_path)
    context = _context()
    update = _command_update("/memberinvoice member-001")
    asyncio.run(admin.member_invoice_test(update, context, 42, 77, state, config))

    cancel = _confirmation_update()
    cancel.callback_query.data = "member_invoice_cancel:member-001"
    asyncio.run(
        admin.handle_member_invoice_confirmation(cancel, context, 42, 77, state, config)
    )
    assert fake_wallet.created == []

    asyncio.run(admin.member_invoice_test(update, context, 42, 77, state, config))

    def changed_quote(*args, **kwargs):
        """Simulate an exchange-rate or plan change during confirmation."""
        return 1, {"plan": "changed"}

    monkeypatch.setattr(admin, "quote_member_payment_invoice", changed_quote)
    changed = _confirmation_update()
    asyncio.run(
        admin.handle_member_invoice_confirmation(
            changed, context, 42, 77, state, config
        )
    )
    assert fake_wallet.created == []
    context.bot.send_photo.assert_not_awaited()


def test_settlement_verifier_marks_only_selected_member_paid_after_two_pending_polls(
    fake_wallet, tmp_path
):
    """The verifier keeps the invoice pending, then marks only its member paid."""
    state = _state()
    config = _config(tmp_path)
    context = _context()
    asyncio.run(
        admin.member_invoice_test(
            _command_update("/memberinvoice member-001"),
            context,
            42,
            77,
            state,
            config,
        )
    )
    asyncio.run(
        admin.handle_member_invoice_confirmation(
            _confirmation_update(), context, 42, 77, state, config
        )
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
    settled = NwcInvoiceStatus(
        payment_hash=invoice.payment_hash,
        state="settled",
        amount_msat=invoice.amount_msat,
        settled_at=int(datetime.now(timezone.utc).timestamp()),
        preimage=issued["preimage"],
    )
    fake_wallet.statuses = [pending, pending, settled]

    for _ in range(2):
        result = asyncio.run(check_payment_invoice_status(config, state))
        assert result == {"checked": 1, "settled": 0, "failed": 0}
        assert _member_record(state)["status"] == "unpaid"

    result = asyncio.run(check_payment_invoice_status(config, state))

    assert result == {"checked": 1, "settled": 1, "failed": 0}
    assert _member_record(state)["status"] == "paid"
    assert _member_record(state, "member-002")["status"] == "unpaid"
    assert fake_wallet.lookups == [invoice.payment_hash] * 3
