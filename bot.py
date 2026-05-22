import json
import os
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# Load environment variables from .env file
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("TELEGRAM_ADMIN_ID", "0"))
JSON_FILE = "subscribers.json"
WELCOME_IMAGE_PATH = "btcgdl.png"
SERVICE_ACCOUNT_FILE = "google_calendar.json"
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
CHECK_INTERVAL_MINUTES = 30
REMINDERS_FILE = "sent_reminders.json"
REMINDER_CONFIG_FILE = "reminder_config.json"
GDL_TZ = ZoneInfo("America/Mexico_City")


def extract_link(description):

    if not description:
        return None
    # Simple regex to find the first URL in text
    urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', description)
    return urls[0] if urls else None


def friendly_delta(td):
    total_seconds = td.total_seconds()
    
    if total_seconds >= 86400:  # 1 day or more
        rounded_days = round(total_seconds / 86400)
        return f"{rounded_days} {'día' if rounded_days == 1 else 'días'}"
    
    if total_seconds >= 3600:  # 1 hour or more
        rounded_hours = round(total_seconds / 3600)
        return f"{rounded_hours} {'hora' if rounded_hours == 1 else 'horas'}"
    
    rounded_minutes = round(total_seconds / 60)
    if rounded_minutes > 0:
        return f"{rounded_minutes} {'minuto' if rounded_minutes == 1 else 'minutos'}"
    
    return "menos de 1 minuto"


def load_reminder_rules():
    if not os.path.exists(REMINDER_CONFIG_FILE):
        return {}

    try:
        with open(REMINDER_CONFIG_FILE, "r", encoding="utf-8") as f:
            raw_config = json.load(f)

        rules = {}
        for key, value in raw_config.items():
            rules[key] = timedelta(**value)

        # Sort rules by threshold descending to check larger windows first
        return dict(sorted(rules.items(), key=lambda x: x[1], reverse=True))

    except Exception as e:
        print(f"Error loading reminder config: {e}")
        return {}

REMINDER_RULES = load_reminder_rules()

if not REMINDER_RULES:
    print("WARNING: No reminder rules loaded!")


    
def load_sent_reminders():
    if not os.path.exists(REMINDERS_FILE):
        return {}
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_sent_reminders(data):
    tmp = REMINDERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, REMINDERS_FILE)


sent_reminders = load_sent_reminders()


def cleanup_sent_reminders():
    """Remove entries for events that happened more than 24 hours ago."""
    global sent_reminders
    now = datetime.now(timezone.utc)
    to_delete = []
    
    for event_key in sent_reminders:
        try:
            # Keys are expected to be EVENTID_YYYYMMDDTHHMMSSZ
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
        save_sent_reminders(sent_reminders)
        print(f"[Cleanup] Removed {len(to_delete)} old reminder entries.")


def load_subscribers():
    if not os.path.exists(JSON_FILE):
        return set()
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(int(x) for x in data)
    except Exception:
        return set()


def save_subscribers(subs_set):
    tmp = JSON_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(list(subs_set), f, indent=2)
    os.replace(tmp, JSON_FILE)


subscribers = load_subscribers()


def get_main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Suscribirse", callback_data="subscribe"),
            InlineKeyboardButton("Darse de baja", callback_data="unsubscribe")
        ],
        [
            InlineKeyboardButton("Meetup", callback_data="meetup"),
            InlineKeyboardButton("Eventos", callback_data="events")
        ],
        [
            InlineKeyboardButton("Bitdevs", callback_data="bitdevs"),
            InlineKeyboardButton("Sitio oficial", callback_data="website")
        ],
    ])


def get_calendar_service():
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
    )
    return build("calendar", "v3", credentials=credentials)


async def check_calendar(context: ContextTypes.DEFAULT_TYPE):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking calendar for upcoming events...")
    service = get_calendar_service()

    now = datetime.now(timezone.utc)

    # Cleanup old entries once per check
    cleanup_sent_reminders()

    try:
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=now.isoformat(),
            timeMax=(now + timedelta(days=8)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()
    except Exception as e:
        print(f"Error fetching events from calendar: {e}")
        return

    events = events_result.get("items", [])
    pending_reminders = []

    for event in events:
        event_id = event["id"]
        title = event.get("summary", "Evento")
        
        # Priority: link in description -> calendar link
        description = event.get("description", "")
        desc_link = extract_link(description)
        link = desc_link if desc_link else event.get("htmlLink", "https://btcgdl.com")
        
        start_raw = event["start"].get("dateTime", event["start"].get("date"))

        # event_time is UTC
        event_time = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        delta = event_time - now

        # For display in GDL timezone
        event_time_gdl = event_time.astimezone(GDL_TZ)
        formatted_date = event_time_gdl.strftime("%d/%m")
        formatted_time = event_time_gdl.strftime("%I:%M %p")

        # Key for sent_reminders includes start time to handle recurring events correctly
        time_suffix = event_time.strftime("%Y%m%dT%H%M%SZ")
        storage_key = f"{event_id}_{time_suffix}"
        
        sent_for_event = sent_reminders.get(storage_key, [])

        # Sort rules by threshold ascending (smallest window first: 1h, 24h, 3d, 1w)
        rules_ascending = sorted(REMINDER_RULES.items(), key=lambda x: x[1])

        for key, threshold in rules_ascending:
            if delta <= threshold and delta > timedelta(0):
                # We are within this threshold window.
                if key not in sent_for_event:
                    # This is the closest reminder we haven't sent.
                    actual_delta_desc = friendly_delta(delta)

                    # Mark this one AND all larger thresholds as sent/skipped
                    for k, t in rules_ascending:
                        if delta <= t and k not in sent_for_event:
                            sent_for_event.append(k)

                    pending_reminders.append({
                        "key": key,
                        "storage_key": storage_key,
                        "sent_for_event": sent_for_event,
                        "title": title,
                        "date": formatted_date,
                        "time": formatted_time,
                        "delta": actual_delta_desc,
                        "link": link,
                    })
                
                # Once we find the smallest applicable threshold, we stop checking others for this event in this run
                break

    if pending_reminders:
        message = build_reminder_digest(pending_reminders)
        await notify_subscribers(context, message)

        for reminder in pending_reminders:
            sent_reminders[reminder["storage_key"]] = reminder["sent_for_event"]
            print(f"[Reminder] Sent {reminder['key']} for {reminder['title']}")

        save_sent_reminders(sent_reminders)

    reminders_sent_this_run = len(pending_reminders)

    if reminders_sent_this_run == 0:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Check finished. No new reminders to send.")
    else:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Check finished. Sent 1 digest with {reminders_sent_this_run} reminders.")


def build_reminder_digest(reminders):
    event_label = "evento" if len(reminders) == 1 else "eventos"
    parts = [
        f"<b>Próximos {event_label} BTC GDL</b>",
        "",
    ]

    for reminder in reminders:
        safe_title = escape(reminder["title"])
        safe_link = escape(reminder["link"], quote=True)
        parts.extend([
            f"• <b>{reminder['date']} · {reminder['time']}</b>",
            f"  <a href=\"{safe_link}\">{safe_title}</a>",
        ])

    return "\n".join(parts).strip()



async def notify_subscribers(context, message):
    for user_id in subscribers:
        try:
            await context.bot.send_message(
                user_id, 
                message, 
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
        except Exception as e:
            print(f"Error sending message to {user_id}: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if os.path.exists(WELCOME_IMAGE_PATH):
        with open(WELCOME_IMAGE_PATH, "rb") as photo:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo
            )

    await update.message.reply_text(
        "¡Hola Bitcoiner! Bienvenido al bot de btc gdl, "
        "suscríbete para recibir notificaciones de nuestros eventos.",
        reply_markup=get_main_keyboard()
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Hola Bitcoiner! ¿Qué deseas hacer?.",
        reply_markup=get_main_keyboard()
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        await update.message.reply_text("Usage: /broadcast Your message here")
        return

    msg = " ".join(context.args)
    for user_id in subscribers:
        try:
            await context.bot.send_message(user_id, msg)
        except Exception:
            pass

    await update.message.reply_text("Broadcast sent!")


async def meetup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "https://www.meetup.com/guadalajara-bitcoin-and-lightning/"
    )


async def events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "https://btcgdl.com/eventos.html"
    )


async def bitdevs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "https://bitdevs.btcgdl.com/"
    )


async def website(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "https://btcgdl.com/"
    )


async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data == "subscribe":
        subscribers.add(user_id)
        try:
            save_subscribers(subscribers)
        except Exception:
            pass

        await query.edit_message_text(
            "¡Gracias por suscribirte!",
            reply_markup=get_main_keyboard()
        )

    elif query.data == "unsubscribe":
        subscribers.discard(user_id)
        try:
            save_subscribers(subscribers)
        except Exception:
            pass

        await query.edit_message_text(
            "Te has dado de baja del servcio, puedes volver a suscribirte en cualquier momento.",
            reply_markup=get_main_keyboard()
        )

    elif query.data == "meetup":
        await query.edit_message_text(
            "https://www.meetup.com/guadalajara-bitcoin-and-lightning/"
        )

    elif query.data == "events":
        await query.edit_message_text(    
            "https://btcgdl.com/eventos.html"
        )

    elif query.data == "bitdevs":
        await query.edit_message_text(
            "https://bitdevs.btcgdl.com/"
        )

    elif query.data == "website":
        await query.edit_message_text(
            "https://btcgdl.com/"
        )


def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    job_queue = app.job_queue
    job_queue.run_repeating(
        check_calendar,
        interval=CHECK_INTERVAL_MINUTES * 60,
        first=10,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("meetup", meetup))
    app.add_handler(CommandHandler("events", events))
    app.add_handler(CommandHandler("bitdevs", bitdevs))
    app.add_handler(CommandHandler("website", website))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CallbackQueryHandler(button_click))

    app.run_polling()


if __name__ == "__main__":
    main()
