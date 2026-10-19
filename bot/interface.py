from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_main_menu_text():
    """Return the public menu explanation shown with the action buttons."""
    return (
        "Elige una opción:\n\n"
        "🔔 Suscripciones: elegir qué notificaciones recibir.\n"
        "📅 Meetup: abrir la página de Meetup.\n"
        "🗓 Eventos: consultar los eventos de Bitcoin & Lightning GDL.\n"
        "🛠 Bitdevs: abrir la página de Bitcoin & Lightning GDL BitDevs.\n"
        "🌐 Sitio oficial: abrir el sitio web de Bitcoin & Lightning GDL."
    )

def get_main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Suscripciones", callback_data="subscriptions"),
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


NOTIFICATION_LABELS = {
    "events": "Eventos",
    "coffee": "Recordatorios de café",
    "payments": "Avisos de membresía",
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
        [buttons[0]],
        [buttons[1]],
        [buttons[2]],
        [InlineKeyboardButton(
            "🚫 Desactivar todas las notificaciones",
            callback_data="subscriptions_disable_all",
        )],
        [InlineKeyboardButton("⬅️ Volver al menú", callback_data="subscriptions_back")],
    ])
