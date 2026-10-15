"""Persistence helpers for manually staged event flyers and captions."""

from datetime import datetime, timezone

from tools.local.data_manager import load_json, save_json


def load_manual_promos(file_path):
    """Load manually staged event promos from a JSON mapping."""
    promos = load_json(file_path, {})
    return promos if isinstance(promos, dict) else {}


def save_manual_promos(file_path, promos):
    """Persist manually staged event promos atomically."""
    save_json(file_path, promos)


def build_manual_promo(event_id, summary, event_start, text, image_file_id):
    """Build a JSON-serializable manually staged event promo record."""
    return {
        "event_id": event_id,
        "summary": summary,
        "event_start": event_start,
        "text": text,
        "image_file_id": image_file_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reminders_sent": [],
    }
