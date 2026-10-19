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
        "group_reminders_sent": {},
        "first_notification_at": None,
        "first_notification_sent": False,
    }


def set_first_notification_schedule(promo, scheduled_for):
    """Store the requested first-notification time and reset its delivery state."""
    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=MANUAL_PROMO_TIMEZONE)
    promo["first_notification_at"] = scheduled_for.isoformat()
    promo["first_notification_sent"] = False


def get_manual_promo_stage(promo, now, reminder_rules):
    """Return the configured reminder stage for a manual promo."""
    event_time = datetime.fromisoformat(
        str(promo["event_start"]).replace("Z", "+00:00")
    )
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=MANUAL_PROMO_TIMEZONE)
    return _get_manual_promo_stage(
        event_time,
        now,
        reminder_rules,
        {"first_notification_sent": True},
    )


def _get_manual_promo_stage(event_time, now, reminder_rules, promo=None):
    """Return the most urgent configured reminder stage currently due."""
    promo = promo or {}
    first_notification_at = promo.get("first_notification_at")
    if first_notification_at and not promo.get("first_notification_sent", False):
        try:
            first_notification_time = datetime.fromisoformat(
                str(first_notification_at).replace("Z", "+00:00")
            )
            if first_notification_time.tzinfo is None:
                first_notification_time = first_notification_time.replace(
                    tzinfo=MANUAL_PROMO_TIMEZONE
                )
            if now < first_notification_time:
                return None
            return "initial"
        except ValueError:
            logger.warning("Invalid first notification time: %s", first_notification_at)

    remaining = event_time - now
    if remaining <= timedelta(0):
        return None

    eligible = [
        (threshold, name)
        for name, threshold in reminder_rules.items()
        if remaining <= threshold
    ]
    return min(eligible)[1] if eligible else None


async def deliver_manual_promo(
    context,
    promo,
    subscribers,
    groups,
    stage,
    is_first_notification=False,
):
    """Deliver one promo stage to subscribers and broadcast groups."""
    reminders_sent = promo.setdefault("reminders_sent", {})
    group_reminders_sent = promo.setdefault("group_reminders_sent", {})
    sent_subscribers = set(reminders_sent.get(stage, []))
    sent_groups = set(group_reminders_sent.get(stage, []))
    result = {
        "subscribers_sent": 0,
        "groups_sent": 0,
        "subscriber_failures": 0,
        "group_failures": 0,
    }
    caption = escape(str(promo.get("text", "")))[:1024]

    for subscriber_id in subscribers:
        if subscriber_id in sent_subscribers:
            continue
        try:
            await context.bot.send_photo(
                chat_id=subscriber_id,
                photo=promo["image_file_id"],
                caption=caption,
                parse_mode="HTML",
            )
        except Exception:
            result["subscriber_failures"] += 1
            logger.exception(
                "Failed to send manual promo %s to subscriber %s.",
                promo.get("event_id", "unknown"),
                subscriber_id,
            )
            continue
        sent_subscribers.add(subscriber_id)
        result["subscribers_sent"] += 1

    for group_id in groups:
        if group_id in sent_groups:
            continue
        try:
            await context.bot.send_photo(
                chat_id=group_id,
                photo=promo["image_file_id"],
                caption=caption,
                parse_mode="HTML",
            )
        except Exception:
            result["group_failures"] += 1
            logger.exception(
                "Failed to send manual promo %s to group %s.",
                promo.get("event_id", "unknown"),
                group_id,
            )
            continue
        sent_groups.add(group_id)
        result["groups_sent"] += 1

    reminders_sent[stage] = sorted(sent_subscribers)
    group_reminders_sent[stage] = sorted(sent_groups)
    if is_first_notification and not (
        result["subscriber_failures"] or result["group_failures"]
    ):
        promo["first_notification_sent"] = True

    return result


async def check_manual_promo_reminders(context, config, state):
    """Deliver active manual promos to subscribers with per-stage deduplication."""
    now = datetime.now(MANUAL_PROMO_TIMEZONE)
    promos = state.setdefault("manual_promos", {})
    subscribers = state.setdefault("subscribers", set())
    groups = state.setdefault("groups", set())
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

        stage = _get_manual_promo_stage(event_time, now, reminder_rules, promo)
        if stage is None:
            continue

        is_first_notification = stage == "initial"
        if is_first_notification:
            stage = _get_manual_promo_stage(
                event_time,
                now,
                reminder_rules,
                {"first_notification_sent": True},
            ) or "initial"

        result = await deliver_manual_promo(
            context,
            promo,
            subscribers,
            groups,
            stage,
            is_first_notification=is_first_notification,
        )
        if any(result.values()):
            changed = True

    if changed:
        save_manual_promos(config["MANUAL_PROMOS_FILE"], promos)
