import os
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from bot.interface import (
    NOTIFICATION_LABELS,
    get_main_keyboard,
    get_main_menu_text,
    get_calendar_keyboard,
    get_subscription_keyboard,
    get_subscription_menu_text,
    get_coffee_schedule_keyboard,
    get_event_registration_keyboard,
)
from core.notification_preferences import (
    NOTIFICATION_CATEGORIES,
    ensure_user_preferences,
    save_notification_preferences,
)
from core.monthly_calendar import format_calendar_title
from core.coffee_hosts import (
    COFFEE_TIMEZONE,
    can_view_coffee_schedule,
    format_private_coffee_schedule,
)
from core.manual_promos import get_upcoming_published_promos, format_manual_promo_caption
from core.event_registrations import (
    get_registered_users,
    register_user,
    save_event_registrations,
    unregister_user,
)
from tools.local.data_manager import load_json, save_json

# Initialize logger for this module
logger = logging.getLogger(__name__)

# Configuration constants
WELCOME_IMAGE_PATH = "btcgdl.png"
SUBSCRIBERS_FILE = "data/subscribers.json"
NOTIFICATION_PREFERENCES_FILE = "data/notification_preferences.json"
WELCOMED_USERS_FILE = "data/welcomed_users.json"

def get_user_main_keyboard(user_id, admin_id, coffee_hosts):
    """Build the main keyboard with the coffee action only for eligible users."""
    return get_main_keyboard(
        show_coffee=can_view_coffee_schedule(user_id, admin_id, coffee_hosts or {})
    )


async def send_main_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    include_welcome=False,
    admin_id=None,
    coffee_hosts=None,
):
    """Send the shared public menu with an optional first-time welcome."""
    menu_text = get_main_menu_text()
    reply_markup = get_user_main_keyboard(
        update.effective_user.id,
        admin_id,
        coffee_hosts,
    )
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
                reply_markup=reply_markup,
            )
    else:
        await update.message.reply_text(
            response_text,
            reply_markup=reply_markup,
        )

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id=None,
    coffee_hosts=None,
):
    """
    Handles the /start command. Sends a welcome image with a caption.
    """
    user = update.effective_user
    logger.info(f"User {user.id} (@{user.username}) started the bot.")
    welcomed_users = set(load_json(WELCOMED_USERS_FILE, []))
    is_first_welcome = user.id not in welcomed_users

    await send_main_menu(
        update,
        context,
        include_welcome=is_first_welcome,
        admin_id=admin_id,
        coffee_hosts=coffee_hosts,
    )

    if is_first_welcome:
        welcomed_users.add(user.id)
        save_json(WELCOMED_USERS_FILE, sorted(welcomed_users))

async def menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id=None,
    coffee_hosts=None,
):
    """
    Handles the /menu command. Shows the main action buttons without the logo.
    """
    await send_main_menu(
        update,
        context,
        admin_id=admin_id,
        coffee_hosts=coffee_hosts,
    )


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


async def coffee_schedule(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id,
    coffee_hosts,
    coffee_host_schedule,
):
    """Show the private coffee schedule to the admin or an assigned host."""
    user_id = update.effective_user.id
    if not can_view_coffee_schedule(user_id, admin_id, coffee_hosts):
        await update.message.reply_text(
            "☕ Esta sección es solo para el administrador y anfitriones de café."
        )
        return

    await update.message.reply_text(
        format_private_coffee_schedule(
            coffee_host_schedule,
            coffee_hosts,
            user_id,
            admin_id,
            datetime.now(COFFEE_TIMEZONE).date(),
        ),
        parse_mode="HTML",
        reply_markup=get_coffee_schedule_keyboard(),
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
    monthly_calendar=None,
    manual_promos=None,
    admin_id=None,
    coffee_hosts=None,
    coffee_host_schedule=None,
    event_registrations=None,
    event_registrations_file=None,
):
    """
    Handles callback queries from inline keyboard buttons.
    Edits existing message/caption to reduce clutter.
    """
    query = update.callback_query
    user = query.from_user
    data = query.data
    notification_preferences = notification_preferences if notification_preferences is not None else {}
    had_saved_preferences = str(user.id) in notification_preferences
    preferences = ensure_user_preferences(
        notification_preferences,
        user.id,
        events_default=user.id in subscribers,
    )
    
    is_registration_action = data.startswith(
        ("event_register_", "event_unregister_")
    )
    if not is_registration_action:
        await query.answer()

    # Determine if we should edit text or caption
    is_photo_msg = bool(query.message.photo)
    main_keyboard = get_user_main_keyboard(user.id, admin_id, coffee_hosts)

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

    def persist_event_registrations():
        """Persist event registrations when the application supplies a path."""
        if event_registrations_file:
            save_event_registrations(
                event_registrations_file,
                event_registrations if event_registrations is not None else {},
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
            main_keyboard,
        )

    elif data == "monthly_calendar":
        calendar = monthly_calendar or {}
        if not calendar:
            await edit_subscription_message(
                "🗓 Todavía no hay un calendario del mes disponible.",
                main_keyboard,
            )
        else:
            caption = calendar.get("caption") or format_calendar_title(calendar)
            await query.message.delete()
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=calendar["file_id"],
                caption=caption,
                reply_markup=get_calendar_keyboard(),
            )

    elif data == "coffee_schedule":
        coffee_hosts = coffee_hosts or {}
        coffee_host_schedule = coffee_host_schedule or {}
        if not can_view_coffee_schedule(user.id, admin_id, coffee_hosts):
            await edit_subscription_message(
                "☕ Esta sección es solo para el administrador y anfitriones de café.",
                main_keyboard,
            )
        else:
            await edit_subscription_message(
                format_private_coffee_schedule(
                    coffee_host_schedule,
                    coffee_hosts,
                    user.id,
                    admin_id,
                    datetime.now(COFFEE_TIMEZONE).date(),
                ),
                get_coffee_schedule_keyboard(),
            )

    elif data == "upcoming_flyers":
        upcoming_promos = get_upcoming_published_promos(manual_promos or {})[:5]
        if not upcoming_promos:
            await edit_subscription_message(
                "📣 No hay flyers publicados para próximos eventos.",
                main_keyboard,
            )
        else:
            await query.message.delete()
            for promo in upcoming_promos:
                event_id = str(promo.get("event_id", ""))
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=promo["image_file_id"],
                    caption=format_manual_promo_caption(promo),
                    parse_mode="HTML",
                    reply_markup=get_event_registration_keyboard(
                        event_id,
                        user.id in get_registered_users(
                            event_registrations or {},
                            event_id,
                        ),
                    ),
                )

    elif data.startswith("event_register_") or data.startswith("event_unregister_"):
        action, event_id = data.split("_", 2)[1:]
        if event_registrations is None:
            event_registrations = {}
        if action == "register":
            changed = register_user(event_registrations, event_id, user.id)
            message = (
                "✅ Te registraste para recibir recordatorios adicionales de este evento."
                if changed
                else "ℹ️ Ya estabas registrado para este evento."
            )
        else:
            changed = unregister_user(event_registrations, event_id, user.id)
            message = (
                "✅ Ya no recibirás los recordatorios adicionales de este evento."
                if changed
                else "ℹ️ No tenías un registro para este evento."
            )
        persist_event_registrations()
        await query.answer(message, show_alert=True)
        await query.edit_message_reply_markup(
            reply_markup=get_event_registration_keyboard(
                event_id,
                user.id in get_registered_users(event_registrations, event_id),
            )
        )

    elif data == "main_menu":
        if is_photo_msg:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=get_main_menu_text(),
                reply_markup=main_keyboard,
            )
        else:
            await query.edit_message_text(
                text=get_main_menu_text(),
                reply_markup=main_keyboard,
            )

    elif data == "subscribe":
        already_subscribed = user.id in subscribers
        if not had_saved_preferences:
            for category in NOTIFICATION_CATEGORIES:
                preferences[category] = True
        else:
            preferences["events"] = True
        if not already_subscribed:
            subscribers.add(user.id)
            save_json(SUBSCRIBERS_FILE, list(subscribers))
            logger.info(f"User {user.id} (@{user.username}) subscribed.")
        persist_preferences()

        enabled_categories = ", ".join(
            label for category, label in NOTIFICATION_LABELS.items()
            if preferences.get(category, False)
        ) or "Ninguna"
        msg = (
            "ℹ️ Ya estás suscrito a: " + enabled_categories + "."
            if already_subscribed
            else "✅ Te suscribiste a: " + enabled_categories + "."
        )
        if is_photo_msg:
            await query.edit_message_caption(caption=msg, reply_markup=main_keyboard)
        else:
            await query.edit_message_text(text=msg, reply_markup=main_keyboard)

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
            await query.edit_message_caption(caption=msg, reply_markup=main_keyboard)
        else:
            await query.edit_message_text(text=msg, reply_markup=main_keyboard)

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
