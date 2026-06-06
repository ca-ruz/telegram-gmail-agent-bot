import json
import logging
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from tools.local.calendar_api import get_calendar_service_with_creds, fetch_upcoming_events
from tools.local.data_manager import load_json, save_json
from core.prompts import PROMOTER_SYSTEM_PROMPT
from datetime import datetime, timezone

# Initialize logger for this module
logger = logging.getLogger(__name__)

def save_prompt_history(config, event, image_prompt):
    """
    Stores a generated image prompt for later admin review.
    """
    prompt_history = load_json(config['PROMPT_HISTORY_FILE'], [])
    prompt_history.append({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "event": event.get("summary", "Sin título"),
        "event_start": event.get("start", {}),
        "image_model": config.get("OPENAI_IMAGE_MODEL"),
        "image_quality": config.get("OPENAI_IMAGE_QUALITY"),
        "image_prompt": image_prompt,
    })
    save_json(config['PROMPT_HISTORY_FILE'], prompt_history[-50:])


def format_prompt_history_entry(entry, prompt_limit=900):
    """
    Formats one prompt history record for Telegram.
    """
    prompt = entry.get("image_prompt", "")
    if len(prompt) > prompt_limit:
        prompt = prompt[:prompt_limit].rstrip() + "..."

    return (
        f"<b>{escape(entry.get('event', 'Sin título'))}</b>\n"
        f"Modelo: <code>{escape(entry.get('image_model', 'unknown'))}</code> / "
        f"<code>{escape(entry.get('image_quality', 'unknown'))}</code>\n"
        f"Fecha: <code>{escape(entry.get('created_at', 'unknown'))}</code>\n\n"
        f"{escape(prompt)}"
    )


async def check_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    """
    Admin-only command that shows the most recent generated image prompt.
    """
    if update.effective_user.id != admin_id:
        return

    reply_message = update.effective_message
    prompt_history = load_json(config['PROMPT_HISTORY_FILE'], [])
    if not prompt_history:
        await reply_message.reply_text("No prompt history yet.")
        return

    await reply_message.reply_text(
        format_prompt_history_entry(prompt_history[-1], prompt_limit=3200),
        parse_mode=ParseMode.HTML,
    )


async def check_prompts(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    """
    Admin-only command that shows the most recent generated image prompts.
    """
    if update.effective_user.id != admin_id:
        return

    reply_message = update.effective_message
    prompt_history = load_json(config['PROMPT_HISTORY_FILE'], [])
    if not prompt_history:
        await reply_message.reply_text("No prompt history yet.")
        return

    recent_prompts = prompt_history[-5:]
    prompt_message = "\n\n---\n\n".join(
        format_prompt_history_entry(entry, prompt_limit=500)
        for entry in reversed(recent_prompts)
    )
    await reply_message.reply_text(prompt_message, parse_mode=ParseMode.HTML)


async def help_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id):
    """
    Admin-only command that lists available admin commands.
    """
    if update.effective_user.id != admin_id:
        return

    reply_message = update.effective_message
    await reply_message.reply_text(
        "<b>Admin commands</b>\n\n"
        "/status - Show bot health and configuration\n"
        "/draft - Generate a promo for an upcoming event\n"
        "/pendingpromo - Show staged promo with publish/delete buttons\n"
        "/checkprompt - Show the latest image prompt\n"
        "/checkprompts - Show the last 5 image prompts\n"
        "/broadcast - Send a manual message to subscribers\n"
        "/addgroup - Add this group to broadcast targets",
        parse_mode=ParseMode.HTML,
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    """
    Admin-only command to show the bot's current status and configuration.
    """
    if update.effective_user.id != admin_id:
        return

    openai_status = "✅ Configured" if config.get('OPENAI_API_KEY') else "❌ Missing API Key"
    image_model = config.get('OPENAI_IMAGE_MODEL', 'Not set')
    
    msg = (
        "📊 <b>BTC GDL Bot Status</b>\n\n"
        f"👥 <b>Subscribers:</b> {len(state['subscribers'])}\n"
        f"📢 <b>Broadcast Groups:</b> {len(state['groups'])}\n"
        f"📝 <b>Pending Promos:</b> {len(state.get('pending_promos', {}))}\n\n"
        f"🤖 <b>OpenAI:</b> {openai_status}\n"
        f"🎨 <b>Image Model:</b> <code>{image_model}</code>\n"
        f"⏱ <b>Poll Interval:</b> {config.get('CHECK_INTERVAL_MINUTES')} min"
    )

    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


def save_pending_promos(config, state):
    """
    Persists staged promos so publish buttons can survive bot restarts.
    """
    save_json(config['PENDING_PROMOS_FILE'], state['pending_promos'])


async def pending_promo(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state):
    """
    Admin-only command that shows the currently staged promo with a publish button.
    """
    if update.effective_user.id != admin_id:
        return

    reply_message = update.effective_message
    staged = state['pending_promos'].get(str(admin_id))
    if not staged:
        await reply_message.reply_text("No pending promo is staged right now.")
        return

    keyboard = [
        [InlineKeyboardButton("✅ Publicar en Grupos", callback_data="publish_draft")],
        [InlineKeyboardButton("🗑 Eliminar promo pendiente", callback_data="clear_pending_promo")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await reply_message.reply_photo(
        photo=staged['image'],
        caption=(
            f"Pending promo: {staged.get('summary', 'Sin título')}\n\n"
            "Do you want to publish this to all groups and subscribers?"
        ),
        reply_markup=reply_markup,
    )

async def handle_clear_pending_promo(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    """
    Handles the pending promo delete button.
    """
    query = update.callback_query
    if query.from_user.id != admin_id:
        await query.answer("Access denied.")
        return

    await query.answer()

    if str(admin_id) not in state['pending_promos']:
        await query.edit_message_caption("No pending promo is staged right now.")
        return

    staged = state['pending_promos'].pop(str(admin_id))
    save_pending_promos(config, state)
    await query.edit_message_caption(f"🗑 Pending promo deleted: {staged.get('summary', 'Sin título')}")

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

    # PHASE 1: Generate the Telegram post copy and image prompt
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
        if image_prompt:
            save_prompt_history(config, event, image_prompt)
        
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
                state['pending_promos'][str(admin_id)] = {
                    "text": draft_text,
                    "image": image_result,
                    "summary": event.get('summary'),
                    "storage_key": storage_key,
                    "event_id": event_id,
                    "event_start": start_raw,
                }
                
                keyboard = [
                    [InlineKeyboardButton("✅ Publicar en Grupos", callback_data="publish_draft")],
                    [InlineKeyboardButton("🗑 Eliminar promo pendiente", callback_data="clear_pending_promo")],
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                preview_message = await context.bot.send_photo(
                    admin_id,
                    photo=image_result,
                    caption=f"🎨 Suggested Flyer for: {event.get('summary')}\n\nDo you want to publish this to all groups and subscribers?",
                    reply_markup=reply_markup
                )
                if preview_message.photo:
                    state['pending_promos'][str(admin_id)]["image"] = preview_message.photo[-1].file_id
                    save_pending_promos(config, state)

                logger.info(f"Flyer successfully generated and staged for: {event.get('summary')}")
            else:
                await context.bot.send_message(admin_id, "❌ Error: Failed to generate flyer image.")
    except Exception as e:
        logger.error(f"Error processing AI response: {e}")
        await context.bot.send_message(admin_id, "❌ Error processing the AI response.")

async def handle_publish(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    """
    Handles the 'Publicar' button. Sends the staged draft to all groups and subscribers.
    """
    query = update.callback_query
    if query.from_user.id != admin_id:
        await query.answer("Access denied.")
        return

    await query.answer()
    
    staged = state['pending_promos'].get(str(admin_id))
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
    del state['pending_promos'][str(admin_id)]
    save_pending_promos(config, state)

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
