import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.admin import (
    generate_promo_for_event,
    handle_admin_reply,
    handle_publish,
)
from tools.local.data_manager import load_json


def make_config(tmp_path):
    """Creates the file-backed configuration needed by admin workflows."""
    return {
        "PROMOTED_FILE": str(tmp_path / "notified_promos.json"),
        "PROMPT_HISTORY_FILE": str(tmp_path / "prompt_history.json"),
        "PENDING_PROMOS_FILE": str(tmp_path / "pending_promos.json"),
        "OPENAI_IMAGE_MODEL": "gpt-image-1-mini",
        "OPENAI_IMAGE_QUALITY": "medium",
    }


def test_generate_promo_stores_event_metadata(tmp_path):
    """Generated promos retain cleanup, deduplication, and refinement metadata."""
    config = make_config(tmp_path)
    state = {
        "notified_promos": {},
        "pending_promos": {},
    }
    event = {
        "id": "event-123",
        "summary": "Stand Up",
        "description": "Community event",
        "location": "Guadalajara",
        "start": {"dateTime": "2026-06-05T10:20:00-06:00"},
    }
    ai_service = SimpleNamespace(
        generate_event_promo=AsyncMock(return_value=json.dumps({
            "telegram_copy": "Promo copy",
            "image_prompt": "Flyer prompt",
        })),
        generate_image=AsyncMock(return_value="https://example.com/flyer.png"),
    )
    preview = SimpleNamespace(
        photo=[SimpleNamespace(file_id="telegram-file-id")]
    )
    context = SimpleNamespace(
        bot=SimpleNamespace(
            send_message=AsyncMock(),
            send_photo=AsyncMock(return_value=preview),
        )
    )

    asyncio.run(
        generate_promo_for_event(
            event,
            context,
            admin_id=42,
            ai_service=ai_service,
            state=state,
            config=config,
        )
    )

    staged = state["pending_promos"]["42"]
    expected_key = "event-123_20260605T102000Z"
    assert staged["storage_key"] == expected_key
    assert staged["event_id"] == "event-123"
    assert staged["event_start"] == "2026-06-05T10:20:00-06:00"
    assert staged["event_info"] == event
    assert staged["image"] == "telegram-file-id"
    assert state["notified_promos"][expected_key]["flyer_created"] is True
    assert load_json(config["PENDING_PROMOS_FILE"], {})["42"]["event_info"] == event


def test_handle_publish_reports_counts_and_logs_failures(tmp_path, caplog):
    """Publishing reports successful targets and logs failed deliveries."""
    config = make_config(tmp_path)
    state = {
        "groups": {-200, -100},
        "subscribers": {10, 20},
        "pending_promos": {
            "42": {
                "summary": "Stand Up",
                "text": "Promo copy",
                "image": "telegram-file-id",
            }
        },
    }

    async def send_photo(chat_id, **kwargs):
        if chat_id in {-200, 20}:
            raise RuntimeError(f"delivery failed for {chat_id}")

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(),
        edit_message_caption=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(
        bot=SimpleNamespace(send_photo=AsyncMock(side_effect=send_photo))
    )

    with caplog.at_level(logging.ERROR):
        asyncio.run(
            handle_publish(
                update,
                context,
                admin_id=42,
                state=state,
                config=config,
            )
        )

    final_caption = query.edit_message_caption.await_args_list[-1].args[0]
    assert "Sent to 1 groups and 1 subscribers." in final_caption
    assert "Failed to publish to group -200" in caplog.text
    assert "Failed to publish to subscriber 20" in caplog.text
    assert "42" not in state["pending_promos"]
    assert load_json(config["PENDING_PROMOS_FILE"], None) == {}


def test_handle_admin_reply_rejects_legacy_pending_promo(tmp_path):
    """Legacy staged promos without event_info cannot enter refinement."""
    state = {
        "pending_promos": {
            "42": {
                "summary": "Legacy promo",
                "text": "Old copy",
                "image": "telegram-file-id",
            }
        }
    }
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(
            reply_to_message=object(),
            text="Make it shorter",
            reply_text=reply_text,
        ),
    )
    ai_service = SimpleNamespace(refine_event_promo=AsyncMock())

    asyncio.run(
        handle_admin_reply(
            update,
            SimpleNamespace(),
            admin_id=42,
            ai_service=ai_service,
            state=state,
            config=make_config(tmp_path),
        )
    )

    assert "older draft cannot be edited" in reply_text.await_args.args[0]
    ai_service.refine_event_promo.assert_not_awaited()
