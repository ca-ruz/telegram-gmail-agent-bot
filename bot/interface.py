from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_main_menu_text():
    """Return the public menu explanation shown with the action buttons."""
    return (
        "Elige una opción:\n\n"
        "🔔 Suscribirse: recibir notificaciones de eventos de Bitcoin & Lightning GDL.\n"
        "🔕 Darse de baja: dejar de recibir esas notificaciones.\n"
        "📅 Meetup: abrir la página de Meetup.\n"
        "🗓 Eventos: consultar los eventos de Bitcoin & Lightning GDL.\n"
        "🛠 Bitdevs: abrir la página de Bitcoin & Lightning GDL BitDevs.\n"
        "🌐 Sitio oficial: abrir el sitio web de Bitcoin & Lightning GDL."
    )

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
