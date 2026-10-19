import os
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from tools.local.calendar_api import get_calendar_service_with_creds, fetch_upcoming_events, extract_link, friendly_delta
from tools.local.data_manager import save_json
from core.notification_preferences import filter_users_by_category

# Initialize logger for this module
logger = logging.getLogger(__name__)

# Configuration constants
GDL_TZ = ZoneInfo("America/Mexico_City")

def cleanup_sent_reminders(sent_reminders, reminders_file, now=None):
    """
    Removes entries from the sent_reminders store for events that happened >24h ago.

    Args:
        sent_reminders: Reminder delivery state keyed by event.
        reminders_file: JSON file used to persist reminder state.
        now: Reference time for cleanup, defaulting to the current UTC time.
    """
    now = now or datetime.now(timezone.utc)
    to_delete = []
    
    for event_key in sent_reminders:
        try:
            if "_" in event_key:
                time_str = event_key.split("_")[-1]
                event_time = datetime.strptime(time_str, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                if now - event_time > timedelta(hours=24):
                    to_delete.append(event_key)
        except Exception as e:
            logger.error(f"Error parsing event key {event_key} for cleanup: {e}")

    if to_delete:
        for key in to_delete:
            del sent_reminders[key]
        save_json(reminders_file, sent_reminders)
        logger.info(f"Cleaned up {len(to_delete)} old reminder entries.")
    
    return sent_reminders

def cleanup_pending_promos(pending_promos, pending_promos_file):
    """
    Removes staged promos whose event start time has already passed.
    """
    now = datetime.now(timezone.utc)
    to_delete = []

    for admin_id, promo in pending_promos.items():
        event_start = promo.get("event_start")
        if not event_start:
            continue

        try:
            event_time = datetime.fromisoformat(event_start.replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            if event_time.astimezone(timezone.utc) <= now:
                to_delete.append(admin_id)
        except Exception as e:
            logger.error(f"Error parsing pending promo event_start for {admin_id}: {e}")

    if to_delete:
        for admin_id in to_delete:
            del pending_promos[admin_id]
        save_json(pending_promos_file, pending_promos)
        logger.info(f"Cleaned up {len(to_delete)} expired pending promos.")

    return pending_promos


def _event_storage_key(event):
    """Build the persisted reminder key for a calendar event instance."""
    start_raw = event["start"].get("dateTime", event["start"].get("date"))
    event_time = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
    return f"{event['id']}_{event_time.strftime('%Y%m%dT%H%M%SZ')}"


def _event_reminder_message(event, now):
    """Build the HTML reminder message for one calendar event."""
    start_raw = event["start"].get("dateTime", event["start"].get("date"))
    event_time = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=GDL_TZ)
    description = event.get("description", "")
    link = extract_link(description) or event.get("htmlLink", "https://btcgdl.com")
    event_time_gdl = event_time.astimezone(GDL_TZ)
    formatted_date = event_time_gdl.strftime("%d/%m")
    formatted_time = event_time_gdl.strftime("%I:%M %p")
    return (
        f"📅 <b>Próximo evento: {event.get('summary', 'Evento')}</b>\n\n"
        f"🗓 <b>Fecha:</b> {formatted_date}\n"
        f"⏰ <b>Hora:</b> {formatted_time}\n"
        f"⏳ <b>Faltan:</b> {friendly_delta(event_time - now)}\n\n"
        f"🔗 <a href='{link}'>Más información y registro</a>"
    )


async def send_manual_event_reminder(context, config, state, event_id):
    """Send one manually requested event reminder to eligible DMs and groups."""
    service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])
    try:
        events, now = fetch_upcoming_events(service, config['CALENDAR_ID'])
    except Exception:
        logger.exception("Could not fetch calendar events for manual reminder %s.", event_id)
        return {"found": False, "subscribers_sent": 0, "groups_sent": 0, "failures": 1}

    event = next((item for item in events if item.get("id") == event_id), None)
    if not event:
        return {"found": False, "subscribers_sent": 0, "groups_sent": 0, "failures": 0}

    storage_key = _event_storage_key(event)
    sent_reminders = state.setdefault("sent_reminders", {})
    stored_sent = sent_reminders.get(storage_key, {})
    if isinstance(stored_sent, list):
        stored_sent = {stage: [] for stage in stored_sent}
        sent_reminders[storage_key] = stored_sent
    sent_for_event = stored_sent if isinstance(stored_sent, dict) else {}
    sent_subscribers = set(sent_for_event.get("manual", []))
    sent_groups = set(sent_for_event.get("manual_groups", []))
    subscribers = filter_users_by_category(
        state.get("subscribers", set()),
        state.get("notification_preferences"),
        "events",
    )
    groups = state.get("groups", set())
    message = _event_reminder_message(event, now)
    subscribers_sent = 0
    groups_sent = 0
    failures = 0

    for user_id in subscribers:
        if user_id in sent_subscribers:
            continue
        try:
            await context.bot.send_message(
                user_id,
                message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
            sent_subscribers.add(user_id)
            subscribers_sent += 1
        except Exception:
            failures += 1
            logger.exception("Could not send manual event reminder to %s.", user_id)

    for group_id in groups:
        if group_id in sent_groups:
            continue
        try:
            await context.bot.send_message(
                group_id,
                message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
            sent_groups.add(group_id)
            groups_sent += 1
        except Exception:
            failures += 1
            logger.exception("Could not send manual event reminder to group %s.", group_id)

    sent_for_event["manual"] = sorted(sent_subscribers)
    sent_for_event["manual_groups"] = sorted(sent_groups)
    sent_reminders[storage_key] = sent_for_event
    save_json(config['REMINDERS_FILE'], sent_reminders)
    return {
        "found": True,
        "subscribers_sent": subscribers_sent,
        "groups_sent": groups_sent,
        "failures": failures,
    }

async def notify_admin_summary(context, admin_id, events_to_notify):
    """
    Sends a consolidated summary message to the admin for multiple events.
    """
    if not events_to_notify:
        return

    keyboard = []
    message_lines = ["🆕 <b>Nuevas sugerencias de Flyer</b>\n"]
    
    for item in events_to_notify:
        event = item['event']
        threshold_name = item['threshold']
        index = item['index']
        
        summary = event.get('summary', 'Sin título')
        start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date', ''))
        
        date_str = ""
        if start:
            day_part = start.split('T')[0]
            if '-' in day_part:
                parts = day_part.split('-')
                date_str = f"{parts[2]}/{parts[1]}"

        threshold_desc = f" ({threshold_name})" if threshold_name else ""
        message_lines.append(f"• {date_str}: {summary}{threshold_desc}")
        
        keyboard.append([InlineKeyboardButton(f"🎨 Flyer: {summary} ({date_str})", callback_data=f"auto_draft_{index}")])

    full_message = "\n".join(message_lines)
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        admin_id,
        full_message,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def check_calendar(context, config, state):
    """
    Orchestrator job that runs periodically to poll the calendar and send reminders.
    """
    logger.info("Polling Google Calendar for upcoming events...")
    
    sent_reminders = state['sent_reminders']
    subscribers = filter_users_by_category(
        state['subscribers'],
        state.get('notification_preferences'),
        "events",
    )
    notified_promos = state['notified_promos']
    state['pending_promos'] = cleanup_pending_promos(
        state.get('pending_promos', {}),
        config['PENDING_PROMOS_FILE'],
    )
    
    service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])

    try:
        events, now = fetch_upcoming_events(service, config['CALENDAR_ID'])
    except Exception as e:
        logger.error(f"Terminating calendar check due to fetch error: {e}")
        return

    sent_reminders = cleanup_sent_reminders(
        sent_reminders,
        config['REMINDERS_FILE'],
        now=now,
    )

    context.bot_data['current_events'] = events
    reminders_sent_this_run = 0
    
    events_to_notify_admin = []
    processed_admin_series = set()
    processed_reminder_series = set() 
    pending_promo_keys = {
        promo.get("storage_key")
        for promo in state.get("pending_promos", {}).values()
        if promo.get("storage_key")
    }

    for i, event in enumerate(events):
        # Use recurringEventId if available to correctly group series, otherwise use id
        series_id = event.get("recurringEventId", event["id"])
        event_id = event["id"]
        title = event.get("summary", "Evento")
        
        start_raw = event["start"].get("dateTime", event["start"].get("date"))
        event_time = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        delta = event_time - now

        time_suffix = event_time.strftime("%Y%m%dT%H%M%SZ")
        storage_key = f"{event_id}_{time_suffix}"

        # --- PHASE A: PROACTIVE ADMIN DETECTION ---
        promo_state = notified_promos.get(storage_key, {"notified_thresholds": [], "flyer_created": False})
        
        if (
            storage_key not in pending_promo_keys
            and not promo_state["flyer_created"]
            and series_id not in processed_admin_series
        ):
            rules_ascending = sorted(config['REMINDER_RULES'].items(), key=lambda x: x[1])
            
            for key, threshold in rules_ascending:
                if delta <= threshold and delta > timedelta(0):
                    if key not in promo_state["notified_thresholds"]:
                        events_to_notify_admin.append({'event': event, 'threshold': key, 'index': i})
                        for k, t in rules_ascending:
                            if delta <= t and k not in promo_state["notified_thresholds"]:
                                promo_state["notified_thresholds"].append(k)
                        notified_promos[storage_key] = promo_state
                        save_json(config['PROMOTED_FILE'], notified_promos)
                        processed_admin_series.add(series_id)
                    break 

        # --- PHASE B: SUBSCRIBER REMINDER LOGIC ---
        if series_id not in processed_reminder_series:
            description = event.get("description", "")
            desc_link = extract_link(description)
            link = desc_link if desc_link else event.get("htmlLink", "https://btcgdl.com")
            
            event_time_gdl = event_time.astimezone(GDL_TZ)
            formatted_date = event_time_gdl.strftime("%d/%m")
            formatted_time = event_time_gdl.strftime("%I:%M %p")

            stored_sent = sent_reminders.get(storage_key, {})
            if isinstance(stored_sent, list):
                # Older state recorded only completed stages. Preserve that
                # meaning by treating those stages as delivered to current subscribers.
                sent_for_event = {
                    stage: list(subscribers)
                    for stage in stored_sent
                }
                sent_reminders[storage_key] = sent_for_event
            elif isinstance(stored_sent, dict):
                sent_for_event = stored_sent
            else:
                sent_for_event = {}

            rules_ascending = sorted(config['REMINDER_RULES'].items(), key=lambda x: x[1])

            for key, threshold in rules_ascending:
                if delta <= threshold and delta > timedelta(0):
                    sent_for_stage = set(sent_for_event.get(key, []))
                    pending_users = [
                        user_id for user_id in subscribers
                        if user_id not in sent_for_stage
                    ]
                    if not pending_users:
                        break

                    actual_delta_desc = friendly_delta(delta)
                    message = (
                        f"📅 <b>Próximo evento: {title}</b>\n\n"
                        f"🗓 <b>Fecha:</b> {formatted_date}\n"
                        f"⏰ <b>Hora:</b> {formatted_time}\n"
                        f"⏳ <b>Faltan:</b> {actual_delta_desc}\n\n"
                        f"🔗 <a href='{link}'>Más información y registro</a>"
                    )

                    delivered_users = []
                    for user_id in pending_users:
                        try:
                            await context.bot.send_message(
                                user_id,
                                message,
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=False,
                            )
                            delivered_users.append(user_id)
                        except Exception as e:
                            logger.error(f"Could not send reminder to {user_id}: {e}")

                    if delivered_users:
                        for k, t in rules_ascending:
                            if delta <= t:
                                delivered_for_stage = set(sent_for_event.get(k, []))
                                delivered_for_stage.update(delivered_users)
                                sent_for_event[k] = list(delivered_for_stage)

                        sent_reminders[storage_key] = sent_for_event
                        save_json(config['REMINDERS_FILE'], sent_reminders)
                        logger.info(f"Reminder [{key}] sent for: {title}")
                        reminders_sent_this_run += 1
                    break
            
            # Mark the series as processed even if no reminder was sent
            processed_reminder_series.add(series_id)

    if events_to_notify_admin:
        logger.info(f"Sending consolidated flyer summary to Admin for {len(events_to_notify_admin)} events.")
        await notify_admin_summary(context, config['ADMIN_ID'], events_to_notify_admin)
    
    logger.info(f"Check finished. Sent {reminders_sent_this_run} total reminders.")
