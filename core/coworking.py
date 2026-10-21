"""Persistence and timing helpers for weekly coworking invitations."""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from core.notification_preferences import filter_users_by_category
from tools.local.data_manager import load_json, save_json


COWORKING_TIMEZONE = ZoneInfo("America/Mexico_City")
logger = logging.getLogger(__name__)


def load_coworking_state(file_path):
    """Load weekly coworking notification state from JSON."""
    state = load_json(file_path, {})
    return state if isinstance(state, dict) else {}


def save_coworking_state(file_path, state):
    """Persist weekly coworking notification state atomically."""
    save_json(file_path, state)


def current_coworking_week(now=None):
    """Return the local ISO week key for a timestamp."""
    current_time = now or datetime.now(COWORKING_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=COWORKING_TIMEZONE)
    return current_time.astimezone(COWORKING_TIMEZONE).strftime("%G-W%V")


def get_coworking_notification_week(now=None, weekday=2, hour=10):
    """Return the due week key for the configured weekly notification slot."""
    current_time = now or datetime.now(COWORKING_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=COWORKING_TIMEZONE)
    local_time = current_time.astimezone(COWORKING_TIMEZONE)
    if local_time.weekday() != weekday or local_time.hour < hour:
        return None
    return current_coworking_week(local_time)


def _parse_event_time(raw_value):
    """Parse an ISO event timestamp into the coworking timezone."""
    event_time = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=COWORKING_TIMEZONE)
    return event_time.astimezone(COWORKING_TIMEZONE)


def has_nearby_event(now, events, manual_promos, quiet_window_hours):
    """Return whether a calendar or manual event is inside the quiet window."""
    current_time = now or datetime.now(COWORKING_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=COWORKING_TIMEZONE)
    current_time = current_time.astimezone(COWORKING_TIMEZONE)
    quiet_window = timedelta(hours=quiet_window_hours)

    event_times = []
    for event in events or []:
        try:
            event_times.append(_parse_event_time(event["start"].get("dateTime", event["start"].get("date"))))
        except (AttributeError, KeyError, TypeError, ValueError):
            logger.warning("Skipping event with invalid start time in coworking quiet-window check.")

    for promo in (manual_promos or {}).values():
        if not isinstance(promo, dict) or promo.get("status") != "active":
            continue
        try:
            event_times.append(_parse_event_time(promo["event_start"]))
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping manual promo with invalid start time in coworking quiet-window check.")

    return any(abs(event_time - current_time) <= quiet_window for event_time in event_times)


def format_coworking_invitation():
    """Build the weekly coworking invitation message."""
    return (
        "🏠 <b>Espacio de coworking</b>\n\n"
        "Recuerda que tenemos un espacio para trabajar, aprender y conectar "
        "con la comunidad. Ven a compartir proyectos, ideas y buenas conversaciones.\n\n"
        "¡Te esperamos! ⚡"
    )


async def check_coworking_notifications(context, config, state, now=None):
    """Send one weekly coworking invitation when its quiet-window checks pass."""
    if not config.get("ENABLE_COWORKING_NOTIFICATIONS", True):
        return

    current_time = now or datetime.now(COWORKING_TIMEZONE)
    week_key = get_coworking_notification_week(
        current_time,
        config.get("COWORKING_WEEKDAY", 2),
        config.get("COWORKING_HOUR", 10),
    )
    if not week_key:
        return

    coworking_state = state.setdefault("coworking", {})
    if coworking_state.get("last_sent_week") == week_key:
        return

    if has_nearby_event(
        current_time,
        getattr(context, "bot_data", {}).get("current_events", []),
        state.get("manual_promos", {}),
        config.get("COWORKING_QUIET_WINDOW_HOURS", 24),
    ):
        return

    failures = 0
    subscribers = filter_users_by_category(
        state.get("subscribers", set()),
        state.get("notification_preferences"),
        "coworking",
    )
    for user_id in subscribers:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=format_coworking_invitation(),
                parse_mode="HTML",
            )
        except Exception:
            failures += 1
            logger.exception("Failed to send coworking invitation to subscriber %s.", user_id)

    if failures == 0:
        coworking_state["last_sent_week"] = week_key
        coworking_state["sent_at"] = datetime.now(timezone.utc).isoformat()
        save_coworking_state(config["COWORKING_STATE_FILE"], coworking_state)
