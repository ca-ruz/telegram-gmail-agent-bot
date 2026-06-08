import os
import logging
from functools import partial
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from bot.handlers.user import start, menu, meetup, events, bitdevs, website, button_click
from bot.handlers.admin import (
    broadcast, draft, handle_draft_selection, handle_auto_draft, 
    handle_publish, handle_clear_pending_promo, add_group, remove_group,
    list_groups, check_prompt, check_prompts, help_admin, pending_promo, status, 
    handle_admin_reply, add_event, handle_add_confirm, delete_event, handle_delete_selection,
    edit_event_start, handle_edit_selection, handle_edit_confirm, handle_admin_chat, handle_voice_admin
)
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

# Configuration
CONFIG = {
    'BOT_TOKEN': os.getenv("TELEGRAM_BOT_TOKEN"),
    'ADMIN_ID': int(os.getenv("TELEGRAM_ADMIN_ID", "0")),
    'OPENAI_API_KEY': os.getenv("OPENAI_API_KEY"),
    'OPENAI_IMAGE_MODEL': os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1-mini"),
    'OPENAI_IMAGE_QUALITY': os.getenv("OPENAI_IMAGE_QUALITY", "medium"),
    'OPENAI_IMAGE_SIZE': os.getenv("OPENAI_IMAGE_SIZE", "1024x1536"),
    'CALENDAR_ID': os.getenv("GOOGLE_CALENDAR_ID"),
    'SERVICE_ACCOUNT_FILE': "google_calendar.json",
    'SCOPES': ["https://www.googleapis.com/auth/calendar"],
    'CHECK_INTERVAL_MINUTES': 30,
    'REMINDERS_FILE': "data/sent_reminders.json",
    'REMINDER_CONFIG_FILE': "data/reminder_config.json",
    'SUBSCRIBERS_FILE': "data/subscribers.json",
    'PROMOTED_FILE': "data/notified_promos.json",
    'GROUPS_FILE': "data/groups.json",
    'PROMPT_HISTORY_FILE': "data/prompt_history.json",
    'PENDING_PROMOS_FILE': "data/pending_promos.json"
}

# --- STATE INITIALIZATION & MIGRATION ---
raw_notified = load_json(CONFIG['PROMOTED_FILE'], {})
# Migration: If the file was an old list format, convert it to the new dict format
if isinstance(raw_notified, list):
    logger.info("Migrating notified_promos.json from list to dictionary format...")
    notified_dict = {event_id: {"notified_thresholds": ["initial"], "flyer_created": False} for event_id in raw_notified}
    raw_notified = notified_dict

STATE = {
    'subscribers': set(load_json(CONFIG['SUBSCRIBERS_FILE'], [])),
    'groups': set(load_json(CONFIG['GROUPS_FILE'], [])),
    'sent_reminders': load_json(CONFIG['REMINDERS_FILE'], {}),
    'notified_promos': raw_notified,
    'pending_promos': load_json(CONFIG['PENDING_PROMOS_FILE'], {})
}

# Add reminder rules to config
CONFIG['REMINDER_RULES'] = load_reminder_rules(CONFIG['REMINDER_CONFIG_FILE'])

# Initialize OpenAI Service
AI_SERVICE = (
    OpenAIService(
        CONFIG['OPENAI_API_KEY'],
        image_model=CONFIG['OPENAI_IMAGE_MODEL'],
        image_quality=CONFIG['OPENAI_IMAGE_QUALITY'],
        image_size=CONFIG['OPENAI_IMAGE_SIZE'],
    )
    if CONFIG['OPENAI_API_KEY']
    else None
)

def main():
    """Main entry point for the bot."""
    if not CONFIG['BOT_TOKEN']:
        logger.error("TELEGRAM_BOT_TOKEN not found in .env. Initialization aborted.")
        return

    app = ApplicationBuilder().token(CONFIG['BOT_TOKEN']).build()

    # Schedule the proactive calendar checker
    async def calendar_job(context):
        await check_calendar(context, config=CONFIG, state=STATE)

    job_queue = app.job_queue
    job_queue.run_repeating(
        calendar_job,
        interval=CONFIG['CHECK_INTERVAL_MINUTES'] * 60,
        first=10,
    )

    # User Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("meetup", meetup))
    app.add_handler(CommandHandler("events", events))
    app.add_handler(CommandHandler("bitdevs", bitdevs))
    app.add_handler(CommandHandler("website", website))
    
    # Admin Command Handlers
    app.add_handler(CommandHandler("broadcast", 
        partial(broadcast, admin_id=CONFIG['ADMIN_ID'], subscribers=STATE['subscribers'])))
    app.add_handler(CommandHandler("status",
        partial(status, admin_id=CONFIG['ADMIN_ID'], state=STATE, config=CONFIG)))
    app.add_handler(CommandHandler("groups",
        partial(list_groups, admin_id=CONFIG['ADMIN_ID'], state=STATE)))
    app.add_handler(CommandHandler("draft",
        partial(draft, admin_id=CONFIG['ADMIN_ID'], config=CONFIG)))
    app.add_handler(CommandHandler("addevent",
        partial(add_event, admin_id=CONFIG['ADMIN_ID'], ai_service=AI_SERVICE)))
    app.add_handler(CommandHandler("editevent",
        partial(edit_event_start, admin_id=CONFIG['ADMIN_ID'], config=CONFIG)))
    app.add_handler(CommandHandler("deleteevent",
        partial(delete_event, admin_id=CONFIG['ADMIN_ID'], config=CONFIG)))
    app.add_handler(CommandHandler("addgroup",
        partial(add_group, admin_id=CONFIG['ADMIN_ID'], state=STATE, config=CONFIG)))
    app.add_handler(CommandHandler("removegroup",
        partial(remove_group, admin_id=CONFIG['ADMIN_ID'], state=STATE, config=CONFIG)))
    app.add_handler(CommandHandler("checkprompt",
        partial(check_prompt, admin_id=CONFIG['ADMIN_ID'], config=CONFIG)))
    app.add_handler(CommandHandler("checkprompts",
        partial(check_prompts, admin_id=CONFIG['ADMIN_ID'], config=CONFIG)))
    app.add_handler(CommandHandler("helpadmin",
        partial(help_admin, admin_id=CONFIG['ADMIN_ID'])))
    app.add_handler(CommandHandler("pendingpromo",
        partial(pending_promo, admin_id=CONFIG['ADMIN_ID'], state=STATE)))
    
    # Callback Handlers
    app.add_handler(CallbackQueryHandler(
        partial(handle_draft_selection, admin_id=CONFIG['ADMIN_ID'], ai_service=AI_SERVICE, state=STATE, config=CONFIG),
        pattern="^select_draft_"))
    app.add_handler(CallbackQueryHandler(
        partial(handle_auto_draft, admin_id=CONFIG['ADMIN_ID'], ai_service=AI_SERVICE, state=STATE, config=CONFIG),
        pattern="^auto_draft_"))
    app.add_handler(CallbackQueryHandler(
        partial(handle_publish, admin_id=CONFIG['ADMIN_ID'], state=STATE, config=CONFIG),
        pattern="^publish_draft$"))
    app.add_handler(CallbackQueryHandler(
        partial(handle_clear_pending_promo, admin_id=CONFIG['ADMIN_ID'], state=STATE, config=CONFIG),
        pattern="^clear_pending_promo$"))
    app.add_handler(CallbackQueryHandler(
        partial(handle_add_confirm, admin_id=CONFIG['ADMIN_ID'], config=CONFIG),
        pattern="^(confirm_add|cancel_add)$"))
    app.add_handler(CallbackQueryHandler(
        partial(handle_delete_selection, admin_id=CONFIG['ADMIN_ID'], config=CONFIG),
        pattern="^confirm_del_"))
    app.add_handler(CallbackQueryHandler(
        partial(handle_edit_selection, admin_id=CONFIG['ADMIN_ID']),
        pattern="^select_edit_"))
    app.add_handler(CallbackQueryHandler(
        partial(handle_edit_confirm, admin_id=CONFIG['ADMIN_ID'], config=CONFIG),
        pattern="^(confirm_edit|cancel_edit)$"))
    app.add_handler(CallbackQueryHandler(
        partial(button_click, subscribers=STATE['subscribers'])))

    # Smart Admin Chat Handler (Natural Language)
    app.add_handler(MessageHandler(
        filters.TEXT
        & ~filters.COMMAND
        & filters.User(user_id=CONFIG['ADMIN_ID']),
        partial(
            handle_admin_chat,
            admin_id=CONFIG['ADMIN_ID'],
            ai_service=AI_SERVICE,
            state=STATE,
            config=CONFIG,
        ),
    ))

    # Voice Admin Handler
    app.add_handler(MessageHandler(
        filters.VOICE
        & filters.User(user_id=CONFIG['ADMIN_ID']),
        partial(
            handle_voice_admin,
            admin_id=CONFIG['ADMIN_ID'],
            ai_service=AI_SERVICE,
            state=STATE,
            config=CONFIG,
        ),
    ))

    # Global Error Handler
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log the error and send a telegram message to notify the developer."""
        logger.error(f"Exception while handling an update: {context.error}")
        
        # If the error is a NetworkError (like the httpx.ReadError seen), we just log it
        # and let the bot continue polling.
        if "httpx.ReadError" in str(context.error) or "NetworkError" in str(context.error):
            logger.warning("Network interruption detected. Bot will retry automatically.")
            return

    app.add_error_handler(error_handler)

    # Terminal Branding
    print(r"""
  ____ _____ ____    ____ ____  _       ____   ___ _____ 
 | __ )_   _/ ___|  / ___|  _ \| |     | __ ) / _ \_   _|
 |  _ \ | || |     | |  _| | | | |     |  _ \| | | || |  
 | |_) || || |___  | |_| | |_| | |___  | |_) | |_| || |  
 |____/ |_| \____|  \____|____/|_____| |____/ \___/ |_|  
    """)
    logger.info("------------------------------------------")
    logger.info("Bot started successfully. Press Ctrl+C to stop.")
    logger.info(f"Polling Google Calendar every {CONFIG['CHECK_INTERVAL_MINUTES']} minutes.")
    logger.info("First calendar check will run in 10 seconds...")
    logger.info("------------------------------------------")
    
    app.run_polling()

if __name__ == "__main__":
    main()
