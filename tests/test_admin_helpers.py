from bot.handlers.admin import format_prompt_history_entry, save_prompt_history
from tools.local.data_manager import load_json, save_json


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
