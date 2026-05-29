import os
import logging
from functools import partial
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler
from bot.handlers.user import start, menu, meetup, events, bitdevs, website, button_click
from bot.handlers.admin import broadcast, draft, handle_draft_selection, handle_auto_draft, handle_publish, add_group
from core.promoter import check_calendar
from tools.local.data_manager import load_json, load_reminder_rules
from services.openai_service import OpenAIService

# Load environment
load_dotenv()

# --- LOGGING CONFIGURATION ---
log_file = "data/bot.log"
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%m/%d %H:%M',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("BTCGDL_Bot")

# Silence noisy third-party libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("googleapiclient").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.ERROR)

# Config
CONFIG = {
    'BOT_TOKEN': os.getenv("TELEGRAM_BOT_TOKEN"),
    'ADMIN_ID': int(os.getenv("TELEGRAM_ADMIN_ID", "0")),
    'OPENAI_API_KEY': os.getenv("OPENAI_API_KEY"),
    'CALENDAR_ID': os.getenv("GOOGLE_CALENDAR_ID"),
    'SERVICE_ACCOUNT_FILE': "google_calendar.json",
    'SCOPES': ["https://www.googleapis.com/auth/calendar.readonly"],
    'CHECK_INTERVAL_MINUTES': 30,
    'REMINDERS_FILE': "data/sent_reminders.json",
    'REMINDER_CONFIG_FILE': "data/reminder_config.json",
    'SUBSCRIBERS_FILE': "data/subscribers.json",
    'PROMOTED_FILE': "data/notified_promos.json",
    'GROUPS_FILE': "data/groups.json"
}

# Load state
raw_notified = load_json(CONFIG['PROMOTED_FILE'], {})
if isinstance(raw_notified, list):
    notified_dict = {event_id: {"notified_thresholds": ["initial"], "flyer_created": False} for event_id in raw_notified}
    raw_notified = notified_dict

STATE = {
    'subscribers': set(load_json(CONFIG['SUBSCRIBERS_FILE'], [])),
    'groups': set(load_json(CONFIG['GROUPS_FILE'], [])),
    'sent_reminders': load_json(CONFIG['REMINDERS_FILE'], {}),
    'notified_promos': raw_notified,
    'pending_promos': {}
}

# Add reminder rules to config
CONFIG['REMINDER_RULES'] = load_reminder_rules(CONFIG['REMINDER_CONFIG_FILE'])

# Initialize OpenAI Service
AI_SERVICE = OpenAIService(CONFIG['OPENAI_API_KEY']) if CONFIG['OPENAI_API_KEY'] else None

def main():
    """Main entry point for the bot."""
    if not CONFIG['BOT_TOKEN']:
        logger.error("TELEGRAM_BOT_TOKEN not found in .env")
        return

    app = ApplicationBuilder().token(CONFIG['BOT_TOKEN']).build()

    async def calendar_job(context):
        await check_calendar(context, config=CONFIG, state=STATE)

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
    
    # Admin Handlers
    app.add_handler(CommandHandler("broadcast", 
        partial(broadcast, admin_id=CONFIG['ADMIN_ID'], subscribers=STATE['subscribers'])))
    
    app.add_handler(CommandHandler("draft",
        partial(draft, admin_id=CONFIG['ADMIN_ID'], config=CONFIG)))
    
    app.add_handler(CommandHandler("addgroup",
        partial(add_group, admin_id=CONFIG['ADMIN_ID'], state=STATE, config=CONFIG)))
    
    # Callback Handlers
    app.add_handler(CallbackQueryHandler(
        partial(handle_draft_selection, admin_id=CONFIG['ADMIN_ID'], ai_service=AI_SERVICE, state=STATE, config=CONFIG),
        pattern="^select_draft_"))

    app.add_handler(CallbackQueryHandler(
        partial(handle_auto_draft, admin_id=CONFIG['ADMIN_ID'], ai_service=AI_SERVICE, state=STATE, config=CONFIG),
        pattern="^auto_draft_"))

    app.add_handler(CallbackQueryHandler(
        partial(handle_publish, admin_id=CONFIG['ADMIN_ID'], state=STATE),
        pattern="^publish_draft$"))

    app.add_handler(CallbackQueryHandler(
        partial(button_click, subscribers=STATE['subscribers'])))

    print(r"""
  ____ _____ ____    ____ ____  _       ____   ___ _____ 
 | __ )_   _/ ___|  / ___|  _ \| |     | __ ) / _ \_   _|
 |  _ \ | || |     | |  _| | | | |     |  _ \| | | || |  
 | |_) || || |___  | |_| | |_| | |___  | |_) | |_| || |  
 |____/ |_| \____|  \____|____/|_____| |____/ \___/ |_|  
    """)
    logger.info("------------------------------------------")
    logger.info("Bot started. Press Ctrl+C to stop.")
    logger.info(f"Polling interval: {CONFIG['CHECK_INTERVAL_MINUTES']} minutes.")
    logger.info("First calendar check will run in 10 seconds...")
    logger.info("------------------------------------------")
    
    app.run_polling()

if __name__ == "__main__":
    main()
