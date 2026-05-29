import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from tools.local.calendar_api import get_calendar_service_with_creds, fetch_upcoming_events
from core.prompts import PROMOTER_SYSTEM_PROMPT

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

async def draft(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    """Initial /draft command: lists events to choose from."""
    if update.effective_user.id != admin_id:
        return

    await update.message.reply_text("🔍 Checking upcoming events for drafting...")

    try:
        service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])
        events, _ = fetch_upcoming_events(service, config['CALENDAR_ID'], days=30)
        
        if not events:
            await update.message.reply_text("📅 No upcoming events found in the next 30 days.")
            return

        # Store events in context to retrieve later
        context.user_data['draft_events'] = events

        keyboard = []
        for i, event in enumerate(events[:5]): # Show up to 5
            summary = event.get('summary', 'Sin título')
            start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date', ''))
            date_str = ""
            if 'T' in start:
                date_str = start.split('T')[0][5:].replace('-', '/')
            
            keyboard.append([InlineKeyboardButton(f"{date_str} - {summary}", callback_data=f"select_draft_{i}")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Which event would you like to draft a promo for?", reply_markup=reply_markup)

    except Exception as e:
        print(f"Error listing events for draft: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def handle_draft_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service):
    """Handles the button click when an admin selects an event."""
    query = update.callback_query
    if query.from_user.id != admin_id:
        await query.answer("Access denied.")
        return

    await query.answer()
    
    try:
        index = int(query.data.split('_')[-1])
        events = context.user_data.get('draft_events', [])
        
        if not events or index >= len(events):
            await query.edit_message_text("❌ Error: Event list expired. Please run /draft again.")
            return

        event = events[index]
        await query.edit_message_text(f"🚀 Generating promo for: **{event.get('summary')}**\nPlease wait...")

        event_info = {
            "summary": event.get("summary"),
            "description": event.get("description"),
            "start": event.get("start"),
            "location": event.get("location")
        }

        # 1. GENERATE TEXT & PROMPT
        raw_response = await ai_service.generate_event_promo(json.dumps(event_info), PROMOTER_SYSTEM_PROMPT)
        
        if raw_response == "QUOTA_EXCEEDED":
            await context.bot.send_message(admin_id, "⚠️ OpenAI Limit Reached: Please check your credit balance. Draft and flyer generation aborted.")
            return
        elif not raw_response:
            await context.bot.send_message(admin_id, "❌ Failed to generate draft from OpenAI.")
            return

        promo_data = json.loads(raw_response)
        draft_text = promo_data.get("telegram_copy", "Error: No copy generated.")
        image_prompt = promo_data.get("image_prompt")
        
        await context.bot.send_message(
            admin_id,
            f"<b>📝 DRAFT GENERATED:</b>\n\n{draft_text}",
            parse_mode=ParseMode.HTML
        )

        # 2. GENERATE IMAGE
        if image_prompt:
            await context.bot.send_message(admin_id, "🎨 Generating your flyer... please wait ~20 seconds.")
            image_result = await ai_service.generate_image(image_prompt)
            
            if image_result == "QUOTA_EXCEEDED":
                await context.bot.send_message(admin_id, "⚠️ Flyer generation failed: Insufficient OpenAI credits.")
            elif image_result:
                await context.bot.send_photo(
                    admin_id,
                    photo=image_result,
                    caption=f"🎨 Suggested Flyer for: {event.get('summary')}"
                )
            else:
                await context.bot.send_message(admin_id, "❌ Error: Failed to generate flyer image.")
        
    except Exception as e:
        print(f"Error in handle_draft_selection: {e}")
        await context.bot.send_message(admin_id, f"❌ Error: {str(e)}")
