import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from tools.local.calendar_api import get_calendar_service_with_creds, fetch_upcoming_events
from tools.local.data_manager import save_json
from core.prompts import PROMOTER_SYSTEM_PROMPT
from datetime import datetime

# Initialize logger for this module
logger = logging.getLogger(__name__)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, subscribers):
    """
    Sends a manual broadcast message to all bot subscribers. 
    Restricted to Admin.
    """
    if update.effective_user.id != admin_id:
        logger.warning(f"Unauthorized broadcast attempt by user {update.effective_user.id}")
        return

    if not context.args:
        await update.message.reply_text("Usage: /broadcast Your message here")
        return

    msg = " ".join(context.args)
    count = 0
    for user_id in subscribers:
        try:
            await context.bot.send_message(user_id, msg)
            count += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to {user_id}: {e}")

    await update.message.reply_text(f"Broadcast sent to {count} users!")
    logger.info(f"Admin {admin_id} sent a broadcast to {count} users.")

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    """
    Admin command to add the current group to the broadcast list.
    """
    if update.effective_user.id != admin_id:
        return

    chat = update.effective_chat
    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("❌ This command must be used inside a Telegram Group.")
        return

    group_id = chat.id
    if group_id not in state['groups']:
        state['groups'].add(group_id)
        save_json(config['GROUPS_FILE'], list(state['groups']))
        logger.info(f"Admin {admin_id} added new group: {chat.title} ({group_id})")
        await update.message.reply_text(f"✅ Group **{chat.title}** added to broadcast list!")
    else:
        await update.message.reply_text("ℹ️ This group is already in the list.")

async def draft(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    """
    Admin-only command to list upcoming events and choose one for AI promo generation.
    """
    if update.effective_user.id != admin_id:
        return

    logger.info(f"Admin {admin_id} requested a manual draft list.")
    await update.message.reply_text("🔍 Checking upcoming events for drafting...")

    try:
        # Fetch events from the next 30 days
        service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])
        events, _ = fetch_upcoming_events(service, config['CALENDAR_ID'], days=30)
        
        if not events:
            await update.message.reply_text("📅 No upcoming events found in the next 30 days.")
            return

        # Cache events in user_data so the callback handler can find the specific one chosen
        context.user_data['draft_events'] = events

        keyboard = []
        for i, event in enumerate(events[:5]): # List the next 5 events
            summary = event.get('summary', 'Sin título')
            start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date', ''))
            
            # Format date string (DD/MM) for the button label
            date_str = ""
            if start:
                day_part = start.split('T')[0]
                if '-' in day_part:
                    parts = day_part.split('-')
                    date_str = f"{parts[2]}/{parts[1]}"
            
            keyboard.append([InlineKeyboardButton(f"{date_str} - {summary}", callback_data=f"select_draft_{i}")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Which event would you like to draft a promo for?", reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Error listing events for manual draft: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def generate_promo_for_event(event, context, admin_id, ai_service, state, config):
    """
    Helper function to orchestrate the actual text and image generation.
    """
    event_info = {
        "summary": event.get("summary"),
        "description": event.get("description"),
        "start": event.get("start"),
        "location": event.get("location")
    }

    # Mark as flyer created in state
    event_id = event["id"]
    start_raw = event["start"].get("dateTime", event["start"].get("date"))
    event_time = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
    time_suffix = event_time.strftime("%Y%m%dT%H%M%SZ")
    storage_key = f"{event_id}_{time_suffix}"
    
    notified_promos = state['notified_promos']
    promo_state = notified_promos.get(storage_key, {"notified_thresholds": [], "flyer_created": False})
    promo_state["flyer_created"] = True
    notified_promos[storage_key] = promo_state
    save_json(config['PROMOTED_FILE'], notified_promos)

    # PHASE 1: Generate the Telegram Post Copy and DALL-E Prompt
    logger.info(f"Generating AI copy for event: {event.get('summary')}")
    raw_response = await ai_service.generate_event_promo(json.dumps(event_info), PROMOTER_SYSTEM_PROMPT)
    
    if raw_response == "QUOTA_EXCEEDED":
        await context.bot.send_message(admin_id, "⚠️ OpenAI Limit Reached: Please check your credit balance. Draft and flyer generation aborted.")
        return
    elif not raw_response:
        await context.bot.send_message(admin_id, "❌ Failed to generate draft from OpenAI.")
        return

    try:
        promo_data = json.loads(raw_response)
        draft_text = promo_data.get("telegram_copy", "Error: No copy generated.")
        image_prompt = promo_data.get("image_prompt")
        
        # Show the text draft first
        await context.bot.send_message(
            admin_id,
            f"<b>📝 DRAFT GENERATED:</b>\n\n{draft_text}",
            parse_mode=ParseMode.HTML
        )

        # PHASE 2: Generate the flyer image
        if image_prompt:
            logger.info(f"Generating AI image for event: {event.get('summary')}")
            await context.bot.send_message(admin_id, "🎨 Generating your flyer... please wait ~20 seconds.")
            image_result = await ai_service.generate_image(image_prompt)
            
            if image_result == "QUOTA_EXCEEDED":
                await context.bot.send_message(admin_id, "⚠️ Flyer generation failed: Insufficient OpenAI credits.")
            elif image_result:
                # Stage the draft for publishing
                state['pending_promos'][admin_id] = {
                    "text": draft_text,
                    "image": image_result,
                    "summary": event.get('summary')
                }
                
                keyboard = [[InlineKeyboardButton("✅ Publicar en Grupos", callback_data="publish_draft")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_photo(
                    admin_id,
                    photo=image_result,
                    caption=f"🎨 Suggested Flyer for: {event.get('summary')}\n\nDo you want to publish this to all groups and subscribers?",
                    reply_markup=reply_markup
                )
                logger.info(f"Flyer successfully generated and staged for: {event.get('summary')}")
            else:
                await context.bot.send_message(admin_id, "❌ Error: Failed to generate flyer image.")
    except Exception as e:
        logger.error(f"Error processing AI response: {e}")
        await context.bot.send_message(admin_id, "❌ Error processing the AI response.")

async def handle_publish(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state):
    """
    Handles the 'Publicar' button. Sends the staged draft to all groups and subscribers.
    """
    query = update.callback_query
    if query.from_user.id != admin_id:
        await query.answer("Access denied.")
        return

    await query.answer()
    
    staged = state['pending_promos'].get(admin_id)
    if not staged:
        await query.edit_message_text("❌ Error: No staged draft found. Please generate a new one.")
        return

    await query.edit_message_caption("🚀 Publishing to community... please wait.")
    
    text = staged['text']
    image = staged['image']
    
    # 1. Blast to Groups
    group_count = 0
    for group_id in state['groups']:
        try:
            await context.bot.send_photo(group_id, photo=image, caption=text, parse_mode=ParseMode.HTML)
            group_count += 1
        except Exception as e:
            logger.error(f"Failed to publish to group {group_id}: {e}")

    # 2. Blast to Subscribers
    sub_count = 0
    for user_id in state['subscribers']:
        try:
            await context.bot.send_photo(user_id, photo=image, caption=text, parse_mode=ParseMode.HTML)
            sub_count += 1
        except Exception as e:
            logger.error(f"Failed to publish to subscriber {user_id}: {e}")

    await query.edit_message_caption(f"✅ Published successfully!\nSent to {group_count} groups and {sub_count} subscribers.")
    logger.info(f"Admin {admin_id} published promo for '{staged['summary']}' to {group_count} groups and {sub_count} subs.")
    
    # Clear the staged draft
    del state['pending_promos'][admin_id]

async def handle_draft_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    """
    Handles the button click when an admin selects an event from the manual /draft list.
    """
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
        await query.edit_message_text(f"🚀 Generating promo for: {event.get('summary')}\nPlease wait...")
        await generate_promo_for_event(event, context, admin_id, ai_service, state, config)

    except Exception as e:
        logger.error(f"Error in handle_draft_selection callback: {e}")
        await context.bot.send_message(admin_id, f"❌ Error: {str(e)}")

async def handle_auto_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    """
    Handles the 'Crear Flyer' button when a new event was proactively detected by the bot.
    """
    query = update.callback_query
    if query.from_user.id != admin_id:
        await query.answer("Access denied.")
        return

    await query.answer()
    
    try:
        index = int(query.data.split('_')[-1])
        # Pull events from bot_data (stored during the last calendar check)
        events = context.bot_data.get('current_events', [])
        
        if not events or index >= len(events):
            await query.edit_message_text("❌ Error: Event data not found or session expired.")
            return

        event = events[index]
        logger.info(f"Admin {admin_id} triggered automated draft for: {event.get('summary')}")
        await query.edit_message_text(f"🚀 Generating promo for: {event.get('summary')}\nPlease wait...")
        await generate_promo_for_event(event, context, admin_id, ai_service, state, config)

    except Exception as e:
        logger.error(f"Error in handle_auto_draft callback: {e}")
        await context.bot.send_message(admin_id, f"❌ Error: {str(e)}")
