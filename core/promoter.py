import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from telegram.constants import ParseMode
from tools.local.calendar_api import get_calendar_service_with_creds, fetch_upcoming_events, extract_link, friendly_delta
from tools.local.data_manager import save_json

GDL_TZ = ZoneInfo("America/Mexico_City")

def cleanup_sent_reminders(sent_reminders, reminders_file):
    """Remove entries for events that happened more than 24 hours ago."""
    now = datetime.now(timezone.utc)
    to_delete = []
    
    for event_key in sent_reminders:
        try:
            if "_" in event_key:
                time_str = event_key.split("_")[-1]
                event_time = datetime.strptime(time_str, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                if now - event_time > timedelta(hours=24):
                    to_delete.append(event_key)
        except Exception as e:
            print(f"Error parsing event key {event_key} for cleanup: {e}")

    if to_delete:
        for key in to_delete:
            del sent_reminders[key]
        save_json(reminders_file, sent_reminders)
        print(f"[Cleanup] Removed {len(to_delete)} old reminder entries.")
    return sent_reminders

async def check_calendar(context, config, state):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking calendar for upcoming events...")
    
    # Unpack state and config
    sent_reminders = state['sent_reminders']
    subscribers = state['subscribers']
    
    service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])

    # Cleanup old entries once per check
    sent_reminders = cleanup_sent_reminders(sent_reminders, config['REMINDERS_FILE'])

    try:
        events, now = fetch_upcoming_events(service, config['CALENDAR_ID'])
    except Exception as e:
        print(f"Error fetching events from calendar: {e}")
        return

    reminders_sent_this_run = 0

    for event in events:
        event_id = event["id"]
        title = event.get("summary", "Evento")
        
        description = event.get("description", "")
        desc_link = extract_link(description)
        link = desc_link if desc_link else event.get("htmlLink", "https://btcgdl.com")
        
        start_raw = event["start"].get("dateTime", event["start"].get("date"))
        event_time = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        delta = event_time - now

        event_time_gdl = event_time.astimezone(GDL_TZ)
        formatted_date = event_time_gdl.strftime("%d/%m")
        formatted_time = event_time_gdl.strftime("%I:%M %p")

        time_suffix = event_time.strftime("%Y%m%dT%H%M%SZ")
        storage_key = f"{event_id}_{time_suffix}"
        
        sent_for_event = sent_reminders.get(storage_key, [])
        rules_ascending = sorted(config['REMINDER_RULES'].items(), key=lambda x: x[1])

        for key, threshold in rules_ascending:
            if delta <= threshold and delta > timedelta(0):
                if key not in sent_for_event:
                    actual_delta_desc = friendly_delta(delta)
                    
                    message = (
                        f"📅 <b>Próximo evento: {title}</b>\n\n"
                        f"🗓 <b>Fecha:</b> {formatted_date}\n"
                        f"⏰ <b>Hora:</b> {formatted_time}\n"
                        f"⏳ <b>Faltan:</b> {actual_delta_desc}\n\n"
                        f"🔗 <a href='{link}'>Más información y registro</a>"
                    )

                    # Notify
                    for user_id in subscribers:
                        try:
                            await context.bot.send_message(
                                user_id, 
                                message, 
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=False
                            )
                        except Exception:
                            pass

                    # Mark as sent
                    for k, t in rules_ascending:
                        if delta <= t and k not in sent_for_event:
                            sent_for_event.append(k)
                    
                    sent_reminders[storage_key] = sent_for_event
                    save_json(config['REMINDERS_FILE'], sent_reminders)
                    print(f"[Reminder] Sent {key} for {title}")
                    reminders_sent_this_run += 1
                break
    
    if reminders_sent_this_run == 0:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Check finished. No new reminders to send.")
    else:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Check finished. Sent {reminders_sent_this_run} reminders.")
