from telegram import Update
from telegram.ext import ContextTypes

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, subscribers):
    if update.effective_user.id != admin_id:
        return

    if not context.args:
        await update.message.reply_text("Usage: /broadcast Your message here")
        return

    msg = " ".join(context.args)
    for user_id in subscribers:
        try:
            await context.bot.send_message(user_id, msg)
        except Exception:
            pass

    await update.message.reply_text("Broadcast sent!")
