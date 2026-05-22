import os
from functools import partial
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler
from bot.handlers.user import start, menu, meetup, events, bitdevs, website, button_click
from bot.handlers.admin import broadcast
from core.promoter import check_calendar
from tools.local.data_manager import load_json, load_reminder_rules

# Load environment
load_dotenv()

# Config
CONFIG = {
    'BOT_TOKEN': os.getenv("TELEGRAM_BOT_TOKEN"),
    'ADMIN_ID': int(os.getenv("TELEGRAM_ADMIN_ID", "0")),
    'CALENDAR_ID': os.getenv("GOOGLE_CALENDAR_ID"),
    'SERVICE_ACCOUNT_FILE': "google_calendar.json",
    'SCOPES': ["https://www.googleapis.com/auth/calendar.readonly"],
    'CHECK_INTERVAL_MINUTES': 30,
    'REMINDERS_FILE': "data/sent_reminders.json",
    'REMINDER_CONFIG_FILE': "data/reminder_config.json",
    'SUBSCRIBERS_FILE': "data/subscribers.json"
}

# Load state
STATE = {
    'subscribers': set(load_json(CONFIG['SUBSCRIBERS_FILE'], [])),
    'sent_reminders': load_json(CONFIG['REMINDERS_FILE'], {})
}

# Add reminder rules to config
CONFIG['REMINDER_RULES'] = load_reminder_rules(CONFIG['REMINDER_CONFIG_FILE'])

def main():
    if not CONFIG['BOT_TOKEN']:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env")
        return

    app = ApplicationBuilder().token(CONFIG['BOT_TOKEN']).build()

    # Wrapper for the calendar job to avoid partial() name attribute issues
    async def calendar_job(context):
        await check_calendar(context, config=CONFIG, state=STATE)

    # Schedule Calendar Polling
    job_queue = app.job_queue
    job_queue.run_repeating(
        calendar_job,
        interval=CONFIG['CHECK_INTERVAL_MINUTES'] * 60,
        first=10,
    )

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("meetup", meetup))
    app.add_handler(CommandHandler("events", events))
    app.add_handler(CommandHandler("bitdevs", bitdevs))
    app.add_handler(CommandHandler("website", website))
    
    # Custom handlers for passing extra arguments
    app.add_handler(CommandHandler("broadcast", 
        partial(broadcast, admin_id=CONFIG['ADMIN_ID'], subscribers=STATE['subscribers'])))
    
    app.add_handler(CallbackQueryHandler(
        partial(button_click, subscribers=STATE['subscribers'])))

    print(r"""
  ____ _____ ____    ____ ____  _     
 | __ )_   _/ ___|  / ___|  _ \| |    
 |  _ \ | || |     | |  _| | | | |    
 | |_) || || |___  | |_| | |_| | |___ 
 |____/ |_| \____|  \____|____/|_____|
    """)
    print("------------------------------------------")
    print("Bot started. Press Ctrl+C to stop.")
    print(f"Polling interval: {CONFIG['CHECK_INTERVAL_MINUTES']} minutes.")
    print("First calendar check will run in 10 seconds...")
    print("------------------------------------------")
    app.run_polling()

if __name__ == "__main__":
    main()
