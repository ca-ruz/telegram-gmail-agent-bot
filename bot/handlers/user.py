import os
import logging
from telegram import Update
from telegram.ext import ContextTypes
from bot.interface import get_main_keyboard
from tools.local.data_manager import save_json

# Initialize logger for this module
logger = logging.getLogger(__name__)

# Configuration constants
WELCOME_IMAGE_PATH = "btcgdl.png"
SUBSCRIBERS_FILE = "data/subscribers.json"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles the /start command. Sends a welcome image and message.
    """
    user = update.effective_user
    logger.info(f"User {user.id} (@{user.username}) started the bot.")

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
    """
    Handles the /menu command. Shows the main action buttons.
    """
    await update.message.reply_text(
        "¡Hola Bitcoiner! ¿Qué deseas hacer?.",
        reply_markup=get_main_keyboard()
    )

async def meetup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /meetup command."""
    await update.message.reply_text(
        "https://www.meetup.com/guadalajara-bitcoin-and-lightning/"
    )

async def events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /events command."""
    await update.message.reply_text(
        "https://btcgdl.com/eventos.html"
    )

async def bitdevs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /bitdevs command."""
    await update.message.reply_text(
        "https://bitdevs.btcgdl.com/"
    )

async def website(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /website command."""
    await update.message.reply_text(
        "https://btcgdl.com/"
    )

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE, subscribers):
    """
    Handles callback queries from inline keyboard buttons.
    """
    query = update.callback_query
    user = query.from_user
    data = query.data
    
    await query.answer()

    if data == "subscribe":
        if user.id not in subscribers:
            subscribers.add(user.id)
            save_json(SUBSCRIBERS_FILE, list(subscribers))
            logger.info(f"User {user.id} (@{user.username}) subscribed.")
        
        await query.edit_message_text(
            "¡Gracias por suscribirte!",
            reply_markup=get_main_keyboard()
        )

    elif data == "unsubscribe":
        if user.id in subscribers:
            subscribers.discard(user.id)
            save_json(SUBSCRIBERS_FILE, list(subscribers))
            logger.info(f"User {user.id} (@{user.username}) unsubscribed.")
        
        await query.edit_message_text(
            "Te has dado de baja del servcio, puedes volver a suscribirte en cualquier momento.",
            reply_markup=get_main_keyboard()
        )

    elif data == "meetup":
        await query.edit_message_text(
            "https://www.meetup.com/guadalajara-bitcoin-and-lightning/"
        )

    elif data == "events":
        await query.edit_message_text(    
            "https://btcgdl.com/eventos.html"
        )

    elif data == "bitdevs":
        await query.edit_message_text(
            "https://bitdevs.btcgdl.com/"
        )

    elif query.data == "website":
        await query.edit_message_text(
            "https://btcgdl.com/"
        )
