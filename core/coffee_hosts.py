"""Persistence and configuration helpers for weekly coffee host reminders."""

import logging
import re
from datetime import date, datetime, time, timedelta
from html import escape
from zoneinfo import ZoneInfo

from tools.local.data_manager import load_json, save_json
from core.notification_preferences import is_notification_enabled

logger = logging.getLogger(__name__)

COFFEE_TIMEZONE = ZoneInfo("America/Mexico_City")
VALID_REMINDER_RULES = ("7d", "1d")


def load_coffee_hosts(file_path):
    """Load the coffee host roster from a JSON mapping."""
    hosts = load_json(file_path, {})
    if isinstance(hosts, dict):
        return hosts

    logger.warning("Coffee host roster %s is not a JSON object; using an empty roster.", file_path)
    return {}


def load_coffee_schedule(file_path):
    """Load Saturday coffee host assignments from a JSON mapping."""
    schedule = load_json(file_path, {})
    if isinstance(schedule, dict):
        return schedule

    logger.warning("Coffee host schedule %s is not a JSON object; using an empty schedule.", file_path)
    return {}


def save_coffee_hosts(file_path, hosts):
    """Persist the coffee host roster atomically."""
    save_json(file_path, hosts)


def save_coffee_schedule(file_path, schedule):
    """Persist Saturday coffee host assignments atomically."""
    save_json(file_path, schedule)


def build_coffee_host(name, telegram_id):
    """Build a normalized active coffee host record."""
    return {
        "name": name,
        "telegram_id": telegram_id,
        "active": True,
    }


def next_coffee_host_id(hosts):
    """Return the next available sequential coffee host identifier."""
    used_ids = {
        int(match.group(1))
        for host_id in hosts
        if (match := re.fullmatch(r"host-(\d+)", str(host_id)))
    }
    next_number = max(used_ids, default=0) + 1
    while f"host-{next_number:03d}" in hosts:
        next_number += 1
    return f"host-{next_number:03d}"


def parse_reminder_rules(raw_rules):
    """Parse configured coffee reminder stages and discard unsupported values."""
    configured = [rule.strip() for rule in raw_rules.split(",") if rule.strip()]
    rules = tuple(rule for rule in configured if rule in VALID_REMINDER_RULES)

    if not rules:
        logger.warning(
            "No valid coffee host reminder rules configured; using defaults %s.",
            VALID_REMINDER_RULES,
        )
        return VALID_REMINDER_RULES

    return tuple(dict.fromkeys(rules))


def upcoming_saturdays(reference_date, count=4):
    """Return the next Saturday dates, including the reference date when applicable."""
    days_until_saturday = (5 - reference_date.weekday()) % 7
    first_saturday = reference_date + timedelta(days=days_until_saturday)
    return [first_saturday + timedelta(days=7 * index) for index in range(count)]


def build_coffee_assignment(host_ids, venue, start_time, notes):
    """Build a normalized Saturday assignment record."""
    return {
        "hosts": list(dict.fromkeys(host_ids)),
        "venue": venue,
        "start_time": start_time,
        "notes": notes,
        "reminders_sent": {},
    }


def _coffee_event_datetime(saturday_text, start_time_text):
    """Convert a scheduled Saturday and local start time into a timezone-aware datetime."""
    try:
        saturday = date.fromisoformat(saturday_text)
        start_time = time.fromisoformat(start_time_text)
    except (TypeError, ValueError):
        return None

    if saturday.weekday() != 5:
        return None

    return datetime.combine(saturday, start_time, tzinfo=COFFEE_TIMEZONE)


def _coffee_reminder_stage(event_datetime, now, reminder_rules):
    """Return the most urgent reminder stage currently eligible for a coffee event."""
    remaining = event_datetime - now
    if remaining <= timedelta(0):
        return None

    thresholds = {"7d": timedelta(days=7), "1d": timedelta(days=1)}
    eligible = [
        (thresholds[rule], rule)
        for rule in reminder_rules
        if rule in thresholds and remaining <= thresholds[rule]
    ]
    return min(eligible)[1] if eligible else None


def _coffee_reminder_message(assignment, saturday_text, stage):
    """Build the Spanish private reminder sent to an assigned host."""
    stage_text = "en una semana" if stage == "7d" else "mañana"
    lines = [
        f"☕ <b>Recordatorio de anfitrión</b>: tu turno es {stage_text}.",
        f"<b>Sábado:</b> {escape(saturday_text)}",
        f"<b>Hora:</b> {escape(assignment.get('start_time', 'por confirmar'))}",
    ]

    if assignment.get("venue"):
        lines.append(f"<b>Lugar:</b> {escape(assignment['venue'])}")
    if assignment.get("notes"):
        lines.append(f"<b>Notas:</b> {escape(assignment['notes'])}")

    return "\n".join(lines)


async def check_coffee_host_reminders(context, config, state):
    """Send due private reminders to assigned coffee hosts and persist successful deliveries."""
    now = datetime.now(COFFEE_TIMEZONE)
    schedule = state.setdefault("coffee_host_schedule", {})
    hosts = state.setdefault("coffee_hosts", {})
    notification_preferences = state.get("notification_preferences")
    changed = False

    for saturday_text, assignment in schedule.items():
        if not isinstance(assignment, dict):
            continue

        event_datetime = _coffee_event_datetime(
            saturday_text,
            assignment.get("start_time"),
        )
        if event_datetime is None:
            logger.warning("Skipping invalid coffee host assignment for %s.", saturday_text)
            continue

        stage = _coffee_reminder_stage(
            event_datetime,
            now,
            config.get("COFFEE_HOST_REMINDER_RULES", VALID_REMINDER_RULES),
        )
        if stage is None:
            continue

        reminders_sent = assignment.setdefault("reminders_sent", {})
        sent_hosts = set(reminders_sent.get(stage, []))
        for host_id in assignment.get("hosts", []):
            if host_id in sent_hosts:
                continue

            host = hosts.get(host_id)
            telegram_id = host.get("telegram_id") if isinstance(host, dict) else None
            if not host or not host.get("active", True) or not telegram_id:
                logger.warning("Coffee host %s is missing or has no Telegram ID.", host_id)
                continue
            if not is_notification_enabled(
                notification_preferences,
                telegram_id,
                "coffee",
            ):
                logger.info(
                    "Skipping coffee host reminder for %s because coffee notifications are disabled.",
                    host_id,
                )
                continue

            try:
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text=_coffee_reminder_message(assignment, saturday_text, stage),
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception("Failed to send coffee host reminder to %s.", host_id)
                continue

            sent_hosts.add(host_id)
            reminders_sent[stage] = sorted(sent_hosts)
            changed = True

    if changed:
        save_coffee_schedule(config["COFFEE_HOST_SCHEDULE_FILE"], schedule)
