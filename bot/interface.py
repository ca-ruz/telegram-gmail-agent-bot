from telegram import InlineKeyboardButton, InlineKeyboardMarkup

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
