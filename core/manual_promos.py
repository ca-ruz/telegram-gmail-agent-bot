"""Persistence helpers for manually staged event flyers and captions."""

import logging
import re
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from tools.local.data_manager import load_json, save_json
from core.event_registrations import (
    cleanup_expired_event_registrations,
    get_registered_users,
    save_event_registrations,
)
from core.linked_events import validate_linked_events
from core.notification_preferences import filter_users_by_category
from core.reminder_timing import get_event_reminder_stage
from core.community import format_community_group_cta
from core.group_notification_routing import get_group_targets

logger = logging.getLogger(__name__)
MANUAL_PROMO_TIMEZONE = ZoneInfo("America/Mexico_City")
SPANISH_WEEKDAYS = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)
SPANISH_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
URL_PATTERN = re.compile(r"https?://[^\s<>]+")


def load_manual_promos(file_path):
    """Load manually staged event promos from a JSON mapping."""
    promos = load_json(file_path, {})
    return promos if isinstance(promos, dict) else {}


def save_manual_promos(file_path, promos):
    """Persist manually staged event promos atomically."""
    for promo_id, promo in (promos or {}).items():
        if not isinstance(promo, dict) or not promo.get("linked_events"):
            continue
        try:
            promo["linked_events"] = validate_linked_events(
                promo["linked_events"]
            )
        except ValueError as exc:
            raise ValueError(f"Invalid linked events for promo {promo_id}.") from exc
    save_json(file_path, promos)


def get_upcoming_published_promos(promos, now=None):
    """Return future flyers that have already been published, ordered by date."""
    current_time = now or datetime.now(MANUAL_PROMO_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=MANUAL_PROMO_TIMEZONE)

    upcoming = []
    for promo in promos.values() if isinstance(promos, dict) else []:
        if not isinstance(promo, dict):
            continue
        if promo.get("status") not in {"active", "published"}:
            continue
        if promo.get("status") != "published" and not promo.get(
            "first_notification_sent",
            False,
        ):
            continue
        try:
            event_time = datetime.fromisoformat(
                str(promo["event_start"]).replace("Z", "+00:00")
            )
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=MANUAL_PROMO_TIMEZONE)
            event_time = event_time.astimezone(MANUAL_PROMO_TIMEZONE)
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping published promo with invalid event time.")
            continue
        if event_time <= current_time or not promo.get("image_file_id"):
            continue
        upcoming.append((event_time, promo))

    return [promo for _, promo in sorted(upcoming, key=lambda item: item[0])]


def _format_manual_promo_datetime(event_start):
    """Return localized date and time strings for a manual promo."""
    event_time = datetime.fromisoformat(str(event_start).replace("Z", "+00:00"))
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=MANUAL_PROMO_TIMEZONE)
    event_time = event_time.astimezone(MANUAL_PROMO_TIMEZONE)

    weekday = SPANISH_WEEKDAYS[event_time.weekday()].capitalize()
    month = SPANISH_MONTHS[event_time.month - 1]
    hour = event_time.hour % 12 or 12
    period = "AM" if event_time.hour < 12 else "PM"
    time = (
        f"{hour}:{event_time.minute:02d} {period}"
        if event_time.minute
        else f"{hour} {period}"
    )
    return f"{weekday} {event_time.day} de {month}", time


def _contains_manual_promo_detail(text, value, marker):
    """Return whether text already contains a formatted promo detail."""
    normalized_text = re.sub(r"\s+", " ", text.casefold()).strip()
    normalized_value = re.sub(r"\s+", " ", value.casefold()).strip()
    return marker in text or normalized_value in normalized_text


def _split_manual_promo_links(text):
    """Return publication text and unique URLs in their original order."""
    links = []

    def remove_link(match):
        link = match.group(0)
        if link not in links:
            links.append(link)
        return ""

    text_without_links = URL_PATTERN.sub(remove_link, text)
    text_without_links = re.sub(r"[ \t]+\n", "\n", text_without_links)
    text_without_links = re.sub(r"\n{3,}", "\n\n", text_without_links).strip()
    return text_without_links, links


def _format_linked_event_section(event):
    """Format one linked event with its structured publication details."""
    date_text, time_text = _format_manual_promo_datetime(event["event_start"])
    event_end = event.get("event_end")
    if event_end:
        end_date, end_time = _format_manual_promo_datetime(event_end)
        if end_date == date_text:
            time_text = f"{time_text} - {end_time}"
        else:
            time_text = f"{time_text} - {end_date}, {end_time}"

    lines = [f"🎟️ {event.get('summary', 'Evento')}"]
    relationship = str(event.get("relationship", "")).strip()
    if relationship:
        lines.append(f"➡️ {relationship}")
    lines.append(f"📅 {date_text}")
    lines.append(f"🕕 {time_text}")
    location = str(event.get("location", "")).strip()
    if location:
        lines.append(f"📍 {location}")
    return escape("\n".join(lines))


def format_manual_promo_caption(
    promo,
    community_group_url="",
    reminder_event=None,
):
    """Build the final HTML caption for a manually staged event promo."""
    publication_text, links = _split_manual_promo_links(
        str(promo.get("text", "")).strip()
    )

    linked_events = promo.get("linked_events") or []
    if linked_events:
        sections = [escape(publication_text)] if publication_text else []
        if reminder_event:
            sections.append(
                escape(
                    f"🔔 Recordatorio: {reminder_event.get('summary', 'Evento')}"
                )
            )
        for event in linked_events:
            sections.append(_format_linked_event_section(event))
            event_url = str(event.get("url", "")).strip()
            if event_url and event_url not in links:
                links.append(event_url)
        if links:
            sections.append(escape("\n".join(links)))
        caption = "\n\n".join(sections)
        community_cta = format_community_group_cta(community_group_url)
        if community_cta:
            caption = f"{caption}\n\n{community_cta}"
        return caption[:1024]

    date_text, time_text = _format_manual_promo_datetime(promo["event_start"])
    details = []

    if not _contains_manual_promo_detail(publication_text, date_text, "📅"):
        details.append(f"📅 {date_text}")
    if not _contains_manual_promo_detail(publication_text, time_text, "🕕"):
        details.append(f"🕕 {time_text}")

    location = str(promo.get("location", "")).strip()
    if location and not _contains_manual_promo_detail(publication_text, location, "📍"):
        details.append(f"📍 {location}")

    sections = [publication_text] if publication_text else []
    if details:
        sections.append("\n".join(details))
    if links:
        sections.append("\n".join(links))
    caption = escape("\n\n".join(sections))
    community_cta = format_community_group_cta(community_group_url)
    if community_cta:
        caption = f"{caption}\n\n{community_cta}"
    return caption[:1024]


def build_manual_promo(
    event_id,
    summary,
    event_start,
    text,
    image_file_id,
    location="",
    linked_events=None,
):
    """Build a JSON-serializable manually staged event promo record."""
    normalized_linked_events = []
    if linked_events is not None:
        normalized_linked_events = validate_linked_events(linked_events)
    return {
        "event_id": event_id,
        "summary": summary,
        "location": location,
        "event_start": event_start,
        "text": text,
        "image_file_id": image_file_id,
        "linked_events": normalized_linked_events,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reminders_sent": {},
        "group_reminders_sent": {},
        "registered_reminders_sent": {},
        "first_notification_at": None,
        "first_notification_sent": False,
    }


def set_first_notification_schedule(promo, scheduled_for):
    """Store the requested first-notification time and reset its delivery state."""
    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=MANUAL_PROMO_TIMEZONE)
    promo["first_notification_at"] = scheduled_for.isoformat()
    promo["first_notification_sent"] = False


def get_manual_promo_stage(
    promo,
    now,
    reminder_rules,
    same_day_hour=13,
    morning_lead_hours=2,
    reminder_event=None,
):
    """Return the configured reminder stage for a manual promo."""
    event_source = reminder_event or promo
    event_time = datetime.fromisoformat(
        str(event_source["event_start"]).replace("Z", "+00:00")
    )
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=MANUAL_PROMO_TIMEZONE)
    return _get_manual_promo_stage(
        event_time,
        now,
        reminder_rules,
        {"first_notification_sent": True},
        same_day_hour,
        morning_lead_hours,
    )


def _get_manual_promo_stage(
    event_time,
    now,
    reminder_rules,
    promo=None,
    same_day_hour=13,
    morning_lead_hours=2,
):
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

    return get_event_reminder_stage(
        event_time,
        now,
        reminder_rules,
        same_day_hour,
        morning_lead_hours,
    )


async def deliver_manual_promo(
    context,
    promo,
    subscribers,
    groups,
    stage,
    is_first_notification=False,
    notification_preferences=None,
    community_group_url="",
    reminder_event=None,
    reminder_key=None,
    group_notification_categories=None,
):
    """Deliver one promo stage to subscribers and broadcast groups."""
    reminders_sent = promo.setdefault("reminders_sent", {})
    group_reminders_sent = promo.setdefault("group_reminders_sent", {})
    state_key = reminder_key or stage
    subscribers = filter_users_by_category(
        subscribers,
        notification_preferences,
        "events",
    )
    sent_subscribers = set(reminders_sent.get(state_key, []))
    if group_notification_categories is not None:
        groups = get_group_targets(
            {"group_notification_categories": group_notification_categories},
            "events",
        )
    sent_groups = set(group_reminders_sent.get(state_key, []))
    result = {
        "subscribers_sent": 0,
        "groups_sent": 0,
        "subscriber_failures": 0,
        "group_failures": 0,
    }
    caption = format_manual_promo_caption(
        promo,
        community_group_url,
        reminder_event=reminder_event,
    )

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

    reminders_sent[state_key] = sorted(sent_subscribers)
    group_reminders_sent[state_key] = sorted(sent_groups)
    if is_first_notification and not (
        result["subscriber_failures"] or result["group_failures"]
    ):
        promo["first_notification_sent"] = True

    return result


async def deliver_manual_promo_test(context, promo, admin_id, community_group_url=""):
    """Send a manual promo test only to the administrator without persisting state."""
    caption = format_manual_promo_caption(promo, community_group_url)
    test_caption = f"🧪 <b>MODO PRUEBA</b>\n\n{caption}"[:1024]
    try:
        await context.bot.send_photo(
            chat_id=admin_id,
            photo=promo["image_file_id"],
            caption=test_caption,
            parse_mode="HTML",
        )
    except Exception:
        logger.exception(
            "Failed to send manual promo test to administrator %s.",
            admin_id,
        )
        return {"sent": 0, "failures": 1}

    return {"sent": 1, "failures": 0}


async def deliver_registered_manual_promo(
    context,
    promo,
    registered_users,
    stage,
    community_group_url="",
    reminder_event=None,
    reminder_key=None,
):
    """Deliver an additional event reminder to registered users by DM only."""
    reminders_sent = promo.setdefault("registered_reminders_sent", {})
    state_key = reminder_key or stage
    sent_users = set(reminders_sent.get(state_key, []))
    result = {"sent": 0, "failures": 0}
    caption = format_manual_promo_caption(
        promo,
        community_group_url,
        reminder_event=reminder_event,
    )

    for user_id in registered_users:
        if user_id in sent_users:
            continue
        try:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=promo["image_file_id"],
                caption=caption,
                parse_mode="HTML",
            )
        except Exception:
            result["failures"] += 1
            logger.exception(
                "Failed to send registered reminder for promo %s to user %s.",
                promo.get("event_id", "unknown"),
                user_id,
            )
            continue
        sent_users.add(user_id)
        result["sent"] += 1

    reminders_sent[state_key] = sorted(sent_users)
    return result


def _parse_manual_event_time(event_start):
    """Parse a manual event timestamp in the configured local timezone."""
    event_time = datetime.fromisoformat(str(event_start).replace("Z", "+00:00"))
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=MANUAL_PROMO_TIMEZONE)
    return event_time


def _linked_event_reminder_key(event, stage):
    """Build a persistent reminder key for one linked event segment."""
    return f"linked:{event['event_id']}:{stage}"


async def _check_linked_manual_promo_reminders(
    context,
    promo,
    subscribers,
    groups,
    event_registrations,
    reminder_rules,
    notification_preferences,
    community_group_url,
    now,
    same_day_hour,
    morning_lead_hours,
    group_notification_categories,
):
    """Deliver due reminders for each event in a linked promo."""
    linked_events = promo.get("linked_events") or []

    if promo.get("first_notification_at") and not promo.get(
        "first_notification_sent",
        False,
    ):
        initial_stage = _get_manual_promo_stage(
            _parse_manual_event_time(promo["event_start"]),
            now,
            reminder_rules,
            promo,
            same_day_hour,
            morning_lead_hours,
        )
        if initial_stage is None:
            return False

        result = await deliver_manual_promo(
            context,
            promo,
            subscribers,
            groups,
            "initial",
            is_first_notification=True,
            notification_preferences=notification_preferences,
            community_group_url=community_group_url,
            group_notification_categories=group_notification_categories,
        )
        return bool(any(result.values()))

    changed = False
    for event in linked_events:
        stage = get_manual_promo_stage(
            promo,
            now,
            reminder_rules,
            same_day_hour,
            morning_lead_hours,
            reminder_event=event,
        )
        if stage is None:
            continue

        result = await deliver_manual_promo(
            context,
            promo,
            subscribers,
            groups,
            stage,
            notification_preferences=notification_preferences,
            community_group_url=community_group_url,
            reminder_event=event,
            reminder_key=_linked_event_reminder_key(event, stage),
            group_notification_categories=group_notification_categories,
        )
        if any(result.values()):
            changed = True

        try:
            event_time = _parse_manual_event_time(event["event_start"])
            seconds_until_event = (event_time - now).total_seconds()
        except (KeyError, TypeError, ValueError):
            seconds_until_event = -1

        if 0 < seconds_until_event <= timedelta(hours=1).total_seconds():
            event_id = str(event["event_id"])
            registered_users = get_registered_users(
                event_registrations,
                event_id,
            )
            registered_result = await deliver_registered_manual_promo(
                context,
                promo,
                registered_users,
                "registered_1h",
                community_group_url,
                reminder_event=event,
                reminder_key=f"registered_1h:{event_id}",
            )
            if any(registered_result.values()):
                changed = True

    return changed


async def check_manual_promo_reminders(context, config, state):
    """Deliver active manual promos to subscribers with per-stage deduplication."""
    now = datetime.now(MANUAL_PROMO_TIMEZONE)
    promos = state.setdefault("manual_promos", {})
    subscribers = state.setdefault("subscribers", set())
    groups = state.setdefault("groups", set())
    group_notification_categories = state.get("group_notification_categories")
    event_registrations = state.get("event_registrations", {})
    registrations_removed = cleanup_expired_event_registrations(
        event_registrations,
        promos,
        now,
    )
    if registrations_removed and config.get("EVENT_REGISTRATIONS_FILE"):
        save_event_registrations(
            config["EVENT_REGISTRATIONS_FILE"],
            event_registrations,
        )
    notification_preferences = state.get("notification_preferences")
    reminder_rules = config.get("REMINDER_RULES", {})
    changed = False

    for promo_id, promo in promos.items():
        if not isinstance(promo, dict) or promo.get("status") != "active":
            continue

        if promo.get("linked_events"):
            linked_changed = await _check_linked_manual_promo_reminders(
                context,
                promo,
                subscribers,
                groups,
                event_registrations,
                reminder_rules,
                notification_preferences,
                config.get("COMMUNITY_GROUP_URL", ""),
                now,
                config.get("EVENT_SAME_DAY_REMINDER_HOUR", 13),
                config.get("EVENT_MORNING_REMINDER_LEAD_HOURS", 2),
                group_notification_categories,
            )
            changed = changed or linked_changed
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

        stage = _get_manual_promo_stage(
            event_time,
            now,
            reminder_rules,
            promo,
            config.get("EVENT_SAME_DAY_REMINDER_HOUR", 13),
            config.get("EVENT_MORNING_REMINDER_LEAD_HOURS", 2),
        )
        if stage is None:
            continue

        is_first_notification = stage == "initial"
        if is_first_notification:
            stage = _get_manual_promo_stage(
                event_time,
                now,
                reminder_rules,
                {"first_notification_sent": True},
                config.get("EVENT_SAME_DAY_REMINDER_HOUR", 13),
                config.get("EVENT_MORNING_REMINDER_LEAD_HOURS", 2),
            ) or "initial"

        result = await deliver_manual_promo(
            context,
            promo,
            subscribers,
            groups,
            stage,
            is_first_notification=is_first_notification,
            notification_preferences=notification_preferences,
            community_group_url=config.get("COMMUNITY_GROUP_URL", ""),
            group_notification_categories=group_notification_categories,
        )
        if any(result.values()):
            changed = True

        try:
            seconds_until_event = (event_time - now).total_seconds()
        except TypeError:
            seconds_until_event = -1
        if 0 < seconds_until_event <= timedelta(hours=1).total_seconds():
            event_id = str(promo.get("event_id", promo_id))
            registered_users = get_registered_users(
                event_registrations,
                event_id,
            )
            registered_result = await deliver_registered_manual_promo(
                context,
                promo,
                registered_users,
                "registered_1h",
                config.get("COMMUNITY_GROUP_URL", ""),
            )
            if any(registered_result.values()):
                changed = True

    if changed:
        save_manual_promos(config["MANUAL_PROMOS_FILE"], promos)
