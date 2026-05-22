import os
from telegram import Update
from telegram.ext import ContextTypes
from bot.interface import get_main_keyboard
from tools.local.data_manager import save_json

WELCOME_IMAGE_PATH = "btcgdl.png"
JSON_FILE = "data/subscribers.json"

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

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE, subscribers):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data == "subscribe":
        subscribers.add(user_id)
        try:
            save_json(JSON_FILE, list(subscribers))
        except Exception:
            pass

        await query.edit_message_text(
            "¡Gracias por suscribirte!",
            reply_markup=get_main_keyboard()
        )

    elif query.data == "unsubscribe":
        subscribers.discard(user_id)
        try:
            save_json(JSON_FILE, list(subscribers))
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
