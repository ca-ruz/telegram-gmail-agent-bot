import os
import logging
from telegram import Update
from telegram.ext import ContextTypes
from bot.interface import (
    get_main_keyboard,
    get_main_menu_text,
    get_subscription_keyboard,
    get_subscription_menu_text,
)
from core.notification_preferences import (
    ensure_user_preferences,
    save_notification_preferences,
)
from tools.local.data_manager import load_json, save_json

# Initialize logger for this module
logger = logging.getLogger(__name__)

# Configuration constants
WELCOME_IMAGE_PATH = "btcgdl.png"
SUBSCRIBERS_FILE = "data/subscribers.json"
NOTIFICATION_PREFERENCES_FILE = "data/notification_preferences.json"
WELCOMED_USERS_FILE = "data/welcomed_users.json"

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, include_welcome=False):
    """Send the shared public menu with an optional first-time welcome."""
    menu_text = get_main_menu_text()
    response_text = menu_text
    if include_welcome:
        response_text = (
            "¡Hola Bitcoiner! Bienvenido a Bitcoin & Lightning GDL.\n\n"
            + menu_text
        )

    if include_welcome and os.path.exists(WELCOME_IMAGE_PATH):
        with open(WELCOME_IMAGE_PATH, "rb") as photo:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo,
                caption=response_text,
                reply_markup=get_main_keyboard(),
            )
    else:
        await update.message.reply_text(
            response_text,
            reply_markup=get_main_keyboard(),
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles the /start command. Sends a welcome image with a caption.
    """
    user = update.effective_user
    logger.info(f"User {user.id} (@{user.username}) started the bot.")
    welcomed_users = set(load_json(WELCOMED_USERS_FILE, []))
    is_first_welcome = user.id not in welcomed_users

    await send_main_menu(update, context, include_welcome=is_first_welcome)

    if is_first_welcome:
        welcomed_users.add(user.id)
        save_json(WELCOMED_USERS_FILE, sorted(welcomed_users))

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles the /menu command. Shows the main action buttons without the logo.
    """
    await send_main_menu(update, context)


async def subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE, notification_preferences, subscribers, notification_preferences_file=NOTIFICATION_PREFERENCES_FILE):
    """Show the current notification preferences for the requesting user."""
    user_id = update.effective_user.id
    preferences = ensure_user_preferences(
        notification_preferences,
        user_id,
        events_default=user_id in subscribers,
    )
    save_notification_preferences(notification_preferences_file, notification_preferences)
    await update.message.reply_text(
        get_subscription_menu_text(preferences),
        reply_markup=get_subscription_keyboard(preferences),
    )

async def meetup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /meetup command."""
    await update.message.reply_text(
        "https://www.meetup.com/guadalajara-bitcoin-and-lightning/"
    )

async def eventos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /eventos command."""
    await update.message.reply_text(
        "https://btcgdl.com/eventos.html"
    )

async def bitdevs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /bitdevs command."""
    await update.message.reply_text(
        "https://bitdevs.btcgdl.com/"
    )

async def web(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /web command."""
    await update.message.reply_text(
        "https://btcgdl.com/"
    )

async def button_click(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    subscribers,
    notification_preferences=None,
    notification_preferences_file=None,
):
    """
    Handles callback queries from inline keyboard buttons.
    Edits existing message/caption to reduce clutter.
    """
    query = update.callback_query
    user = query.from_user
    data = query.data
    notification_preferences = notification_preferences if notification_preferences is not None else {}
    preferences = ensure_user_preferences(
        notification_preferences,
        user.id,
        events_default=user.id in subscribers,
    )
    
    await query.answer()

    # Determine if we should edit text or caption
    is_photo_msg = bool(query.message.photo)

    async def edit_subscription_message(text, reply_markup):
        """Edit a callback message while preserving photo and text support."""
        if is_photo_msg:
            await query.edit_message_caption(caption=text, reply_markup=reply_markup)
        else:
            await query.edit_message_text(text=text, reply_markup=reply_markup)

    def persist_preferences():
        """Persist preferences when the application supplies a storage path."""
        if notification_preferences_file:
            save_notification_preferences(
                notification_preferences_file,
                notification_preferences,
            )

    if data == "subscriptions":
        await edit_subscription_message(
            get_subscription_menu_text(preferences),
            get_subscription_keyboard(preferences),
        )

    elif data.startswith("subscription_toggle_"):
        category = data.removeprefix("subscription_toggle_")
        if category in preferences:
            preferences[category] = not preferences[category]
            if category == "events":
                if preferences[category]:
                    subscribers.add(user.id)
                else:
                    subscribers.discard(user.id)
                save_json(SUBSCRIBERS_FILE, list(subscribers))
            persist_preferences()
        await edit_subscription_message(
            get_subscription_menu_text(preferences),
            get_subscription_keyboard(preferences),
        )

    elif data == "subscriptions_disable_all":
        for category in preferences:
            preferences[category] = False
        subscribers.discard(user.id)
        save_json(SUBSCRIBERS_FILE, list(subscribers))
        persist_preferences()
        await edit_subscription_message(
            "✅ Desactivaste todas las notificaciones.\n\n"
            + get_subscription_menu_text(preferences),
            get_subscription_keyboard(preferences),
        )

    elif data == "subscriptions_back":
        await edit_subscription_message(
            get_main_menu_text(),
            get_main_keyboard(),
        )

    elif data == "subscribe":
        preferences["events"] = True
        already_subscribed = user.id in subscribers
        if not already_subscribed:
            subscribers.add(user.id)
            save_json(SUBSCRIBERS_FILE, list(subscribers))
            logger.info(f"User {user.id} (@{user.username}) subscribed.")
        persist_preferences()

        msg = (
            "ℹ️ Ya estás suscrito a las notificaciones de eventos de "
            "Bitcoin & Lightning GDL."
            if already_subscribed
            else "✅ Te suscribiste a las notificaciones de eventos de "
            "Bitcoin & Lightning GDL."
        )
        if is_photo_msg:
            await query.edit_message_caption(caption=msg, reply_markup=get_main_keyboard())
        else:
            await query.edit_message_text(text=msg, reply_markup=get_main_keyboard())

    elif data == "unsubscribe":
        preferences["events"] = False
        was_subscribed = user.id in subscribers
        if was_subscribed:
            subscribers.discard(user.id)
            save_json(SUBSCRIBERS_FILE, list(subscribers))
            logger.info(f"User {user.id} (@{user.username}) unsubscribed.")
        persist_preferences()

        msg = (
            "✅ Te diste de baja de las notificaciones de eventos de "
            "Bitcoin & Lightning GDL."
            if was_subscribed
            else "ℹ️ No estabas suscrito a las notificaciones de eventos de "
            "Bitcoin & Lightning GDL."
        )
        if is_photo_msg:
            await query.edit_message_caption(caption=msg, reply_markup=get_main_keyboard())
        else:
            await query.edit_message_text(text=msg, reply_markup=get_main_keyboard())

    elif data in {"meetup", "eventos", "bitdevs", "web"}:
        links = {
            "meetup": "https://www.meetup.com/guadalajara-bitcoin-and-lightning/",
            "eventos": "https://btcgdl.com/eventos.html",
            "bitdevs": "https://bitdevs.btcgdl.com/",
            "web": "https://btcgdl.com/",
        }
        msg = links[data]
        if is_photo_msg:
            await query.message.delete()
            await context.bot.send_message(chat_id=query.message.chat_id, text=msg)
        else:
            await query.edit_message_text(text=msg)
