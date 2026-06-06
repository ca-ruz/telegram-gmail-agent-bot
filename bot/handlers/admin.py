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

def save_prompt_history(config, event_summary, image_prompt):
    """
    Stores a generated image prompt for later admin review.
    """
    prompt_history = load_json(config['PROMPT_HISTORY_FILE'], [])
    prompt_history.append({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "event": event_summary,
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
        "/groups - List all broadcast groups\n"
        "/addgroup - Add this group to broadcast targets\n"
        "/removegroup - Remove this group from broadcast targets\n"
        "/draft - Generate a promo for an upcoming event\n"
        "/pendingpromo - Show staged promo with publish/delete buttons\n"
        "/checkprompt - Show the latest image prompt\n"
        "/checkprompts - Show the last 5 image prompts\n"
        "/broadcast - Send a manual message to subscribers\n\n"
        "💡 <i>Tip: Puedes responder a un borrador para editarlo con AI.</i>",
        parse_mode=ParseMode.HTML,
    )

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
            f"Borrador pendiente: <b>{escape(staged.get('summary', 'Sin título'))}</b>\n\n"
            f"{escape(staged.get('text', ''))[:900]}\n\n"
            "¿Quieres publicar esto en todos los grupos?\n\n"
            "💡 <i>Responde a este mensaje para pedir cambios con AI.</i>"
        ),
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
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
    await query.edit_message_caption(f"🗑 Borrador eliminado: {staged.get('summary', 'Sin título')}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, subscribers):
    """
    Sends a manual broadcast message to all bot subscribers.
    """
    if update.effective_user.id != admin_id:
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

async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state):
    """
    Admin command to list all registered groups.
    """
    if update.effective_user.id != admin_id:
        return
    if not state['groups']:
        await update.message.reply_text("No groups in broadcast list.")
        return
    msg = "📢 <b>Broadcast Groups</b>\n\n" + "\n".join([f"• <code>{gid}</code>" for gid in state['groups']])
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def remove_group(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    """
    Admin command to remove current group.
    """
    if update.effective_user.id != admin_id:
        return
    chat_id = update.effective_chat.id
    if chat_id in state['groups']:
        state['groups'].discard(chat_id)
        save_json(config['GROUPS_FILE'], list(state['groups']))
        await update.message.reply_text("✅ Group removed from broadcast list.")
    else:
        await update.message.reply_text("❌ This group is not in the list.")

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

async def generate_promo_for_event(event, context, admin_id, ai_service, state, config, instructions=None):
    """
    Orchestrates AI generation (Initial or Refinement).
    """
    event_summary = event.get('summary', 'Sin título')
    event_info = {
        "summary": event_summary,
        "description": event.get("description"),
        "start": event.get("start"),
        "location": event.get("location")
    }

    # Tracking keys
    event_id = event.get("id", "unknown")
    start_raw = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", "unknown"))
    time_suffix = start_raw.replace(":", "").replace("-", "")[:15] if start_raw != "unknown" else "unknown"
    storage_key = f"{event_id}_{time_suffix}"

    # Update state: mark flyer as created (or in progress)
    notified_promos = state['notified_promos']
    promo_state = notified_promos.get(storage_key, {"notified_thresholds": [], "flyer_created": False})
    promo_state["flyer_created"] = True
    notified_promos[storage_key] = promo_state
    save_json(config['PROMOTED_FILE'], notified_promos)

    # PHASE 1: Text Copywriting
    if instructions:
        # Refinement logic
        current_draft = state['pending_promos'].get(str(admin_id), {})
        raw_response = await ai_service.refine_event_promo(
            json.dumps(event_info), 
            json.dumps(current_draft), 
            instructions, 
            PROMOTER_SYSTEM_PROMPT
        )
    else:
        # Initial generation
        raw_response = await ai_service.generate_event_promo(json.dumps(event_info), PROMOTER_SYSTEM_PROMPT)
    
    if raw_response == "QUOTA_EXCEEDED":
        await context.bot.send_message(admin_id, "⚠️ OpenAI Limit Reached: Generation aborted.")
        return
    elif not raw_response:
        await context.bot.send_message(admin_id, "❌ Failed to generate draft from OpenAI.")
        return

    try:
        promo_data = json.loads(raw_response)
        draft_text = promo_data.get("telegram_copy", "Error: No copy.")
        image_prompt = promo_data.get("image_prompt")
        
        if image_prompt:
            save_prompt_history(config, event_summary, image_prompt)
        
        await context.bot.send_message(admin_id, f"<b>📝 DRAFT REFINED:</b>\n\n{draft_text}", parse_mode=ParseMode.HTML) if instructions else await context.bot.send_message(admin_id, f"<b>📝 DRAFT GENERATED:</b>\n\n{draft_text}", parse_mode=ParseMode.HTML)

        # PHASE 2: Image Generation
        if image_prompt:
            await context.bot.send_message(admin_id, "🎨 Generando flyer... espera unos segundos.")
            image_result = await ai_service.generate_image(image_prompt)
            
            if image_result == "QUOTA_EXCEEDED":
                await context.bot.send_message(admin_id, "⚠️ Image limit reached.")
            elif image_result:
                # Stage for publishing
                state['pending_promos'][str(admin_id)] = {
                    "text": draft_text,
                    "image": image_result,
                    "summary": event_summary,
                    "storage_key": storage_key,
                    "event_info": event # Store full event for later refinement
                }
                
                keyboard = [
                    [InlineKeyboardButton("✅ Publicar en Grupos", callback_data="publish_draft")],
                    [InlineKeyboardButton("🗑 Eliminar", callback_data="clear_pending_promo")],
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                preview = await context.bot.send_photo(
                    admin_id,
                    photo=image_result,
                    caption=(
                        f"🎨 Flyer para: {event_summary}\n\n"
                        "¿Publicamos este diseño?\n\n"
                        "💡 <i>Responde a este mensaje para pedir cambios.</i>"
                    ),
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )
                # Store the Telegram file_id for better reliability
                if preview.photo:
                    state['pending_promos'][str(admin_id)]["image"] = preview.photo[-1].file_id
                    save_pending_promos(config, state)
            else:
                await context.bot.send_message(admin_id, "❌ Error generating image.")
    except Exception as e:
        logger.error(f"Generation Error: {e}")
        await context.bot.send_message(admin_id, "❌ Error processing AI response.")

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    """
    Handles Admin replies to bot messages for draft refinement.
    """
    if update.effective_user.id != admin_id or not update.message.reply_to_message:
        return

    instructions = update.message.text
    staged = state['pending_promos'].get(str(admin_id))
    
    if not staged:
        return

    await update.message.reply_text("🔄 Refinando promoción... (Esto generará un nuevo texto e imagen con costo de créditos).")
    await generate_promo_for_event(staged['event_info'], context, admin_id, ai_service, state, config, instructions=instructions)

async def draft(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    """Manual /draft command."""
    if update.effective_user.id != admin_id:
        return
    await update.message.reply_text("🔍 Checking upcoming events...")
    try:
        service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])
        events, _ = fetch_upcoming_events(service, config['CALENDAR_ID'], days=30)
        if not events:
            await update.message.reply_text("📅 No events found.")
            return
        context.user_data['draft_events'] = events
        keyboard = []
        for i, event in enumerate(events[:5]):
            summary = event.get('summary', 'Sin título')
            start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date', ''))
            date_str = start[5:10].replace('-', '/') if start else "??/??"
            keyboard.append([InlineKeyboardButton(f"{date_str} - {summary}", callback_data=f"select_draft_{i}")])
        await update.message.reply_text("¿Para qué evento quieres el borrador?", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_draft_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    query = update.callback_query
    if query.from_user.id != admin_id: return
    await query.answer()
    try:
        index = int(query.data.split('_')[-1])
        events = context.user_data.get('draft_events', [])
        if not events or index >= len(events):
            await query.edit_message_text("❌ Session expired. Run /draft again.")
            return
        await query.edit_message_text(f"🚀 Generating promo for: {events[index].get('summary')}")
        await generate_promo_for_event(events[index], context, admin_id, ai_service, state, config)
    except Exception as e:
        await context.bot.send_message(admin_id, f"❌ Error: {e}")

async def handle_auto_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    query = update.callback_query
    if query.from_user.id != admin_id: return
    await query.answer()
    try:
        index = int(query.data.split('_')[-1])
        events = context.bot_data.get('current_events', [])
        if not events or index >= len(events):
            await query.edit_message_text("❌ Data expired.")
            return
        await query.edit_message_text(f"🚀 Generating promo for: {events[index].get('summary')}")
        await generate_promo_for_event(events[index], context, admin_id, ai_service, state, config)
    except Exception as e:
        await context.bot.send_message(admin_id, f"❌ Error: {e}")

async def handle_publish(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    query = update.callback_query
    if query.from_user.id != admin_id: return
    await query.answer()
    staged = state['pending_promos'].get(str(admin_id))
    if not staged:
        await query.edit_message_caption("❌ No staged draft found.")
        return
    await query.edit_message_caption("🚀 Publishing...")
    for group_id in state['groups']:
        try: await context.bot.send_photo(group_id, photo=staged['image'], caption=staged['text'], parse_mode=ParseMode.HTML)
        except: pass
    for user_id in state['subscribers']: # Fixed to use state
        try: await context.bot.send_photo(user_id, photo=staged['image'], caption=staged['text'], parse_mode=ParseMode.HTML)
        except: pass
    await query.edit_message_caption(f"✅ Published successfully!")
    del state['pending_promos'][str(admin_id)]
    save_pending_promos(config, state)
