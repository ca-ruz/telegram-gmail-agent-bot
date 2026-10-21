from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_main_menu_text():
    """Return the public menu explanation shown with the action buttons."""
    return (
        "Elige una opción:\n\n"
        "🔔 Suscripciones: elegir qué notificaciones recibir.\n"
        "🗓 Calendario del mes: consultar el calendario mensual.\n"
        "📅 Meetup: abrir la página de Meetup.\n"
        "🗓 Eventos: consultar los eventos de Bitcoin & Lightning GDL.\n"
        "🛠 Bitdevs: abrir la página de Bitcoin & Lightning GDL BitDevs.\n"
        "🌐 Sitio oficial: abrir el sitio web de Bitcoin & Lightning GDL."
    )

def get_main_keyboard(show_coffee=False):
    """Build the public menu, optionally including the private coffee action."""
    rows = [
        [
            InlineKeyboardButton("Suscripciones", callback_data="subscriptions"),
        ],
        [
            InlineKeyboardButton("Calendario del mes", callback_data="monthly_calendar"),
            InlineKeyboardButton("Próximos eventos", callback_data="upcoming_flyers"),
        ],
        [
            InlineKeyboardButton("Meetup", callback_data="meetup"),
            InlineKeyboardButton("Eventos", callback_data="eventos")
        ],
        [
            InlineKeyboardButton("Bitdevs", callback_data="bitdevs"),
            InlineKeyboardButton("Web", callback_data="web")
        ],
    ]
    if show_coffee:
        rows.insert(1, [InlineKeyboardButton("☕ Café", callback_data="coffee_schedule")])
    return InlineKeyboardMarkup(rows)


NOTIFICATION_LABELS = {
    "events": "Eventos",
    "coffee": "Recordatorios de café",
    "payments": "Avisos de membresía",
    "coworking": "Invitaciones de coworking",
}


def get_subscription_menu_text(preferences):
    """Return the subscription status text shown to a user."""
    enabled = [
        label for category, label in NOTIFICATION_LABELS.items()
        if preferences.get(category, False)
    ]
    active_text = ", ".join(enabled) if enabled else "Ninguna"
    return (
        "🔔 Suscripciones de notificaciones\n\n"
        "Activas: " + active_text + "\n\n"
        "Elige qué notificaciones quieres recibir."
    )


def get_subscription_keyboard(preferences):
    """Build the inline keyboard for notification preferences."""
    buttons = [
        InlineKeyboardButton(
            f"{'✅' if preferences.get(category, False) else '⬜'} {label}",
            callback_data=f"subscription_toggle_{category}",
        )
        for category, label in NOTIFICATION_LABELS.items()
    ]
    return InlineKeyboardMarkup([
        *([button] for button in buttons),
        [InlineKeyboardButton(
            "🚫 Desactivar todas las notificaciones",
            callback_data="subscriptions_disable_all",
        )],
        [InlineKeyboardButton("⬅️ Volver al menú", callback_data="subscriptions_back")],
    ])


def get_calendar_keyboard():
    """Build the navigation keyboard shown with the monthly calendar image."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Volver al menú", callback_data="main_menu")],
    ])


def get_event_registration_keyboard(event_id, registered=False):
    """Build the registration action for one published event flyer."""
    action = "event_unregister" if registered else "event_register"
    label = "✅ Ya me interesa" if registered else "🔔 Me interesa este evento"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"{action}_{event_id}")],
        [InlineKeyboardButton("⬅️ Volver al menú", callback_data="main_menu")],
    ])


def get_coffee_schedule_keyboard():
    """Build the navigation keyboard shown with a private coffee schedule."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Volver al menú", callback_data="main_menu")],
    ])
