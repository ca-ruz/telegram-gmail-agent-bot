"""Persistence helpers for manually staged event flyers and captions."""

import logging
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from tools.local.data_manager import load_json, save_json

logger = logging.getLogger(__name__)
MANUAL_PROMO_TIMEZONE = ZoneInfo("America/Mexico_City")


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
        "reminders_sent": {},
    }


def _get_manual_promo_stage(event_time, now, reminder_rules):
    """Return the most urgent configured reminder stage currently due."""
    remaining = event_time - now
    if remaining <= timedelta(0):
        return None

    eligible = [
        (threshold, name)
        for name, threshold in reminder_rules.items()
        if remaining <= threshold
    ]
    return min(eligible)[1] if eligible else None


async def check_manual_promo_reminders(context, config, state):
    """Deliver active manual promos to subscribers with per-stage deduplication."""
    now = datetime.now(MANUAL_PROMO_TIMEZONE)
    promos = state.setdefault("manual_promos", {})
    subscribers = state.setdefault("subscribers", set())
    reminder_rules = config.get("REMINDER_RULES", {})
    changed = False

    for promo_id, promo in promos.items():
        if not isinstance(promo, dict) or promo.get("status") != "active":
            continue

        try:
            event_time = datetime.fromisoformat(
                str(promo["event_start"]).replace("Z", "+00:00")
            )
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=MANUAL_PROMO_TIMEZONE)
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping manual promo %s with invalid event time.", promo_id)
            continue

        stage = _get_manual_promo_stage(event_time, now, reminder_rules)
        if stage is None:
            continue

        reminders_sent = promo.setdefault("reminders_sent", {})
        sent_for_stage = set(reminders_sent.get(stage, []))
        for subscriber_id in subscribers:
            if subscriber_id in sent_for_stage:
                continue

            try:
                await context.bot.send_photo(
                    chat_id=subscriber_id,
                    photo=promo["image_file_id"],
                    caption=escape(str(promo.get("text", "")))[:1024],
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception(
                    "Failed to send manual promo %s to subscriber %s.",
                    promo_id,
                    subscriber_id,
                )
                continue

            sent_for_stage.add(subscriber_id)
            reminders_sent[stage] = sorted(sent_for_stage)
            changed = True

        if not subscribers:
            reminders_sent.setdefault(stage, [])
            changed = True

    if changed:
        save_manual_promos(config["MANUAL_PROMOS_FILE"], promos)
