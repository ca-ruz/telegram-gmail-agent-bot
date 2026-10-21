import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.admin import format_prompt_history_entry, save_prompt_history, status
from tools.local.data_manager import load_json, save_json


def test_status_reports_disabled_services_and_placeholder_openai_key():
    """Admin status accurately reports runtime service configuration."""
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(reply_text=reply_text),
    )
    state = {"subscribers": set(), "groups": set(), "pending_promos": {}}
    config = {
        "OPENAI_API_KEY": "your_openai_api_key_here",
        "ENABLE_CALENDAR_POLLING": False,
        "ENABLE_COFFEE_HOST_REMINDERS": True,
        "ENABLE_MANUAL_PROMO_REMINDERS": False,
        "CHECK_INTERVAL_MINUTES": 30,
    }

    asyncio.run(status(update, SimpleNamespace(), 42, state, config))

    message = reply_text.await_args.args[0]
    assert "Missing or placeholder key" in message
    assert "Calendar polling:</b> ⛔ Disabled" in message
    assert "Coffee reminders:</b> ✅ Enabled" in message
    assert "Manual promo reminders:</b> ⛔ Disabled" in message
    assert "Check Interval:</b> 30 min" in message


def test_status_reports_payment_deployment_configuration():
    """Admin status exposes safe payment scheduler configuration details."""
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(reply_text=reply_text),
    )
    state = {
        "subscribers": set(),
        "groups": set(),
        "pending_promos": {},
        "members": {"member-001": {"name": "Ada", "active": True}},
        "payment_state": {"period": "2026-11", "members": {}},
    }
    config = {
        "OPENAI_API_KEY": "",
        "ENABLE_CALENDAR_POLLING": False,
        "ENABLE_COFFEE_HOST_REMINDERS": False,
        "ENABLE_MANUAL_PROMO_REMINDERS": False,
        "ENABLE_PAYMENT_REMINDERS": True,
        "MEMBERS_GROUP_ID": -100123,
        "PAYMENT_REMINDER_HOUR": 9,
        "CHECK_INTERVAL_MINUTES": 30,
    }

    asyncio.run(status(update, SimpleNamespace(), 42, state, config))

    message = reply_text.await_args.args[0]
    assert "Payment reminders:</b> ✅ Enabled" in message
    assert "Payment group:</b> ✅ Configured" in message
    assert "Payment members:</b> 1" in message
    assert "Payment period:</b> 2026-11" in message
    assert "Payment reminder hour:</b> 09:00" in message


def test_format_prompt_history_entry_escapes_html():
    """Prompt history output is safe for Telegram HTML parse mode."""
    entry = {
        "event": "BTC <GDL>",
        "image_model": "gpt-image-1-mini",
        "image_quality": "medium",
        "created_at": "2026-05-30T00:00:00+00:00",
        "image_prompt": "Use <strong>orange</strong> & black.",
    }

    formatted = format_prompt_history_entry(entry)

    assert "BTC &lt;GDL&gt;" in formatted
    assert "Use &lt;strong&gt;orange&lt;/strong&gt; &amp; black." in formatted


def test_format_prompt_history_entry_truncates_prompt():
    """Long prompt history output is truncated for compact admin messages."""
    entry = {
        "event": "Long prompt",
        "image_model": "gpt-image-1-mini",
        "image_quality": "medium",
        "created_at": "2026-05-30T00:00:00+00:00",
        "image_prompt": "x" * 20,
    }

    formatted = format_prompt_history_entry(entry, prompt_limit=10)

    assert "xxxxxxxxxx..." in formatted
    assert "x" * 20 not in formatted


def test_save_prompt_history_appends_record(tmp_path):
    """Generated image prompts are appended to prompt history."""
    file_path = tmp_path / "prompt_history.json"
    config = {
        "PROMPT_HISTORY_FILE": str(file_path),
        "OPENAI_IMAGE_MODEL": "gpt-image-1-mini",
        "OPENAI_IMAGE_QUALITY": "medium",
    }
    event = {
        "summary": "Stand Up",
        "start": {"dateTime": "2026-06-05T10:20:00-06:00"},
    }

    save_prompt_history(config, event, "Create a flyer.")

    history = load_json(str(file_path), [])
    assert len(history) == 1
    assert history[0]["event"] == "Stand Up"
    assert history[0]["event_start"] == event["start"]
    assert history[0]["image_model"] == "gpt-image-1-mini"
    assert history[0]["image_quality"] == "medium"
    assert history[0]["image_prompt"] == "Create a flyer."


def test_save_prompt_history_keeps_last_50_records(tmp_path):
    """Prompt history is capped to the most recent 50 entries."""
    file_path = tmp_path / "prompt_history.json"
    config = {
        "PROMPT_HISTORY_FILE": str(file_path),
        "OPENAI_IMAGE_MODEL": "gpt-image-1-mini",
        "OPENAI_IMAGE_QUALITY": "medium",
    }
    existing = [
        {"event": f"Event {index}", "image_prompt": f"Prompt {index}"}
        for index in range(55)
    ]
    save_json(str(file_path), existing)

    save_prompt_history(config, {"summary": "Newest", "start": {}}, "Newest prompt")

    history = load_json(str(file_path), [])
    assert len(history) == 50
    assert history[0]["event"] == "Event 6"
    assert history[-1]["event"] == "Newest"
