import json
import logging
from io import BytesIO
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from tools.local.calendar_api import get_calendar_service_with_creds, fetch_upcoming_events, create_calendar_event
from tools.local.data_manager import load_json, save_json
from core.prompts import PROMOTER_SYSTEM_PROMPT, EVENT_PARSER_SYSTEM_PROMPT, INTENT_DETECTOR_PROMPT
from datetime import datetime, timezone

# Initialize logger for this module
logger = logging.getLogger(__name__)

def save_prompt_history(config, event_summary, image_prompt):
    """
    Stores a generated image prompt for later admin review.
    
    Args:
        config (dict): Bot configuration dictionary.
        event_summary (str): The summary/title of the event.
        image_prompt (str): The prompt used for DALL-E generation.
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
    
    Args:
        entry (dict): The history record.
        prompt_limit (int): Character limit for the prompt text.
        
    Returns:
        str: Formatted HTML string.
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
    if update.effective_user.id != admin_id: return
    prompt_history = load_json(config['PROMPT_HISTORY_FILE'], [])
    if not prompt_history:
        await update.message.reply_text("No prompt history yet.")
        return
    await update.message.reply_text(format_prompt_history_entry(prompt_history[-1], prompt_limit=3200), parse_mode=ParseMode.HTML)

async def check_prompts(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    """
    Admin-only command that shows the last 5 generated image prompts.
    """
    if update.effective_user.id != admin_id: return
    prompt_history = load_json(config['PROMPT_HISTORY_FILE'], [])
    if not prompt_history:
        await update.message.reply_text("No prompt history yet.")
        return
    recent_prompts = prompt_history[-5:]
    prompt_message = "\n\n---\n\n".join(format_prompt_history_entry(entry, prompt_limit=500) for entry in reversed(recent_prompts))
    await update.message.reply_text(prompt_message, parse_mode=ParseMode.HTML)

async def help_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id):
    """
    Admin-only command that lists all available administrative commands.
    """
    if update.effective_user.id != admin_id: return
    await update.message.reply_text(
        "<b>Admin commands</b>\n\n"
        "/status - Show bot health and configuration\n"
        "/groups - List all broadcast groups\n"
        "/addgroup - Add this group to broadcast targets\n"
        "/removegroup - Remove this group from broadcast targets\n"
        "/addevent - Create a Google Calendar event (natural language)\n"
        "/editevent - Interactively edit an existing event\n"
        "/deleteevent - Interactively delete an event\n"
        "/draft - Manually generate a promo for an upcoming event\n"
        "/pendingpromo - Show currently staged promo with publish/delete buttons\n"
        "/checkprompt - Show the most recent image prompt\n"
        "/checkprompts - Show the last 5 image prompts\n"
        "/broadcast - Send a manual message to all subscribers\n\n"
        "💡 <i>Tip: Puedes responder a un borrador o a un evento seleccionado para editarlo con AI.</i>",
        parse_mode=ParseMode.HTML
    )

def save_pending_promos(config, state):
    """
    Saves only JSON-serializable data to disk, ensuring BytesIO buffers are stripped.
    """
    clean_pending = {}
    for uid, data in state['pending_promos'].items():
        clean_data = data.copy()
        # If the image is a buffer, we don't save it (it should have been converted to file_id)
        if isinstance(clean_data.get('image'), BytesIO):
            continue 
        clean_pending[uid] = clean_data
    save_json(config['PENDING_PROMOS_FILE'], clean_pending)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    """
    Admin-only command to show the bot's current status and configuration.
    """
    if update.effective_user.id != admin_id: return
    msg = (
        f"📊 <b>BTC GDL Bot Status</b>\n\n"
        f"👥 <b>Subscribers:</b> {len(state['subscribers'])}\n"
        f"📢 <b>Broadcast Groups:</b> {len(state['groups'])}\n"
        f"📝 <b>Pending Promos:</b> {len(state['pending_promos'])}\n\n"
        f"🤖 <b>OpenAI:</b> {'✅ Configured' if config.get('OPENAI_API_KEY') else '❌ Missing Key'}\n"
        f"⏱ <b>Check Interval:</b> {config.get('CHECK_INTERVAL_MINUTES')} min"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def pending_promo(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state):
    """
    Admin-only command that shows the currently staged promo draft.
    """
    if update.effective_user.id != admin_id: return
    staged = state['pending_promos'].get(str(admin_id))
    if not staged:
        await update.message.reply_text("No pending promo staged right now.")
        return
    keyboard = [[InlineKeyboardButton("✅ Publicar en Grupos", callback_data="publish_draft"), InlineKeyboardButton("🗑 Eliminar", callback_data="clear_pending_promo")]]
    
    image = staged['image']
    if isinstance(image, BytesIO):
        image.seek(0)

    await update.message.reply_photo(
        photo=image, 
        caption=(
            f"Borrador: <b>{escape(staged.get('summary', 'Sin título'))}</b>\n\n"
            f"{escape(staged.get('text', ''))[:900]}\n\n"
            "¿Quieres publicar esto ahora?\n\n"
            "💡 <i>Responde para pedir cambios con AI.</i>"
        ), 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode=ParseMode.HTML
    )

# --- BROADCAST & PUBLISH ---

async def handle_publish(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    """
    Handles the 'Publish' button, sending the staged draft to all targets.
    """
    query = update.callback_query
    if query.from_user.id != admin_id: return
    await query.answer()
    staged = state['pending_promos'].get(str(admin_id))
    if not staged:
        await query.edit_message_caption("❌ Error: No staged draft found.")
        return
    await query.edit_message_caption("🚀 Publishing to community... please wait.")
    
    image = staged['image']
    text = staged['text']

    # Groups
    for group_id in state['groups']:
        try:
            if isinstance(image, BytesIO): image.seek(0)
            await context.bot.send_photo(group_id, photo=image, caption=text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to publish to group {group_id}: {e}")
    
    # Individual Subscribers
    for user_id in state['subscribers']:
        try:
            if isinstance(image, BytesIO): image.seek(0)
            await context.bot.send_photo(user_id, photo=image, caption=text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to publish to user {user_id}: {e}")

    await query.edit_message_caption(f"✅ Published successfully to groups and subscribers!")
    del state['pending_promos'][str(admin_id)]
    save_pending_promos(config, state)

async def handle_clear_pending_promo(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    """
    Handles the 'Delete' button for a staged promo.
    """
    query = update.callback_query
    await query.answer()
    if str(admin_id) in state['pending_promos']:
        summary = state['pending_promos'][str(admin_id)].get('summary', 'Sin título')
        del state['pending_promos'][str(admin_id)]
        save_pending_promos(config, state)
        await query.edit_message_caption(f"🗑 Borrador eliminado: {summary}")

# --- CALENDAR ACTIONS (DELETE & EDIT) ---

async def delete_event(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    """
    Admin-only command to list events and choose one for deletion.
    """
    if update.effective_user.id != admin_id: return
    await update.message.reply_text("🔍 Buscando eventos próximos para eliminar...")
    try:
        service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])
        events, _ = fetch_upcoming_events(service, config['CALENDAR_ID'], days=30)
        processed = set()
        unique = []
        for e in events:
            sid = e.get("recurringEventId", e["id"])
            if sid not in processed:
                unique.append(e); processed.add(sid)
        context.user_data['delete_list'] = unique
        keyboard = [[InlineKeyboardButton(f"🗑 {e.get('summary')}", callback_data=f"confirm_del_{i}")] for i, e in enumerate(unique[:10])]
        await update.message.reply_text("¿Qué evento quieres ELIMINAR del Google Calendar?", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Delete List Error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_delete_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    """
    Handles final confirmation of event deletion.
    """
    query = update.callback_query
    if query.from_user.id != admin_id: return
    await query.answer()
    idx = int(query.data.split('_')[-1])
    events = context.user_data.get('delete_list', [])
    if idx >= len(events): return
    event = events[idx]
    try:
        service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])
        service.events().delete(calendarId=config['CALENDAR_ID'], eventId=event['id']).execute()
        await query.edit_message_text(f"✅ Evento eliminado exitosamente: {event.get('summary')}")
        logger.info(f"Admin {admin_id} deleted event: {event.get('summary')}")
    except Exception as e: await query.edit_message_text(f"❌ Error: {e}")

async def edit_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    """
    Initiates the interactive editing workflow for a calendar event.
    """
    if update.effective_user.id != admin_id: return
    await update.message.reply_text("🔍 Selecciona un evento para editar sus detalles:")
    try:
        service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])
        events, _ = fetch_upcoming_events(service, config['CALENDAR_ID'], days=30)
        processed = set()
        unique = []
        for e in events:
            sid = e.get("recurringEventId", e["id"])
            if sid not in processed:
                unique.append(e); processed.add(sid)
        context.user_data['edit_list'] = unique
        keyboard = [[InlineKeyboardButton(f"✏️ {e.get('summary')}", callback_data=f"select_edit_{i}")] for i, e in enumerate(unique[:10])]
        await update.message.reply_text("Elige el evento que quieres cambiar:", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e: await update.message.reply_text(f"❌ Error: {e}")

async def handle_edit_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id):
    """
    Captures the selected event for editing and prompts for instructions.
    """
    query = update.callback_query
    if query.from_user.id != admin_id: return
    await query.answer()
    idx = int(query.data.split('_')[-1])
    events = context.user_data.get('edit_list', [])
    if idx >= len(events): return
    context.user_data['editing_event'] = events[idx]
    await query.edit_message_text(f"Has seleccionado: <b>{events[idx].get('summary')}</b>\n\nResponde a este mensaje con los cambios que quieras hacer.\n<i>Ejemplo: 'Cambia la hora a las 7pm y la ubicación a Zoom.'</i>", parse_mode=ParseMode.HTML)

async def handle_edit_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    """
    Applies the AI-parsed changes to the Google Calendar event.
    """
    query = update.callback_query
    if query.from_user.id != admin_id: return
    await query.answer()
    if query.data == "cancel_edit":
        await query.edit_message_text("❌ Edición cancelada.")
        return
    data = context.user_data.get('pending_edit')
    event = context.user_data.get('editing_event')
    try:
        service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])
        service.events().update(calendarId=config['CALENDAR_ID'], eventId=event['id'], body=data).execute()
        await query.edit_message_text(f"✅ Evento actualizado: <b>{data['summary']}</b>", parse_mode=ParseMode.HTML)
        context.user_data.pop('editing_event', None)
        logger.info(f"Admin {admin_id} updated event: {data['summary']}")
    except Exception as e: await query.edit_message_text(f"❌ Error al actualizar: {e}")

# --- ADD LOGIC ---

async def add_event(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, forced_text=None):
    """
    Admin-only command to create a new calendar event using AI parsing.
    """
    if update.effective_user.id != admin_id: return
    if not forced_text and not context.args:
        await update.message.reply_text("Uso: /addevent [descripción del evento]\nEjemplo: /addevent Meetup este viernes a las 6pm en Starbucks")
        return
    desc = forced_text if forced_text else " ".join(context.args)
    await update.message.reply_text("⏳ Procesando detalles... Por favor espera.")
    
    current_date = datetime.now().strftime("%Y-%m-%d")
    prompt = EVENT_PARSER_SYSTEM_PROMPT.format(current_date=current_date)
    res = await ai_service.generate_event_promo(desc, prompt)
    if not res: 
        await update.message.reply_text("❌ Falló la generación del evento.")
        return
    try:
        data = json.loads(res)
        context.user_data['pending_event'] = data
        summary = (
            f"📅 <b>CONFIRMAR NUEVO EVENTO</b>\n\n"
            f"<b>Título:</b> {escape(data['summary'])}\n"
            f"<b>Inicio:</b> {escape(data['start']['dateTime'])}\n"
            f"<b>Ubicación:</b> {escape(data.get('location', 'N/A'))}\n\n"
            f"¿Confirmar creación en Google Calendar?"
        )
        keyboard = [[InlineKeyboardButton("✅ Confirmar", callback_data="confirm_add"), InlineKeyboardButton("❌ Cancelar", callback_data="cancel_add")]]
        await update.message.reply_text(summary, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Add Event Parse Error: {e}")
        await update.message.reply_text("❌ Error al procesar los detalles del evento.")

async def handle_add_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    """
    Finalizes creation of a new calendar event.
    """
    query = update.callback_query
    if query.from_user.id != admin_id: return
    await query.answer()
    if query.data == "cancel_add":
        await query.edit_message_text("❌ Creación cancelada.")
        return
    data = context.user_data.get('pending_event')
    try:
        service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])
        create_calendar_event(service, config['CALENDAR_ID'], data)
        await query.edit_message_text(f"✅ Evento creado exitosamente: <b>{data['summary']}</b>", parse_mode=ParseMode.HTML)
        logger.info(f"Admin {admin_id} created event: {data['summary']}")
    except Exception as e: await query.edit_message_text(f"❌ Error al crear el evento: {e}")

# --- DRAFT & PROMO LOGIC ---

async def generate_promo_for_event(event, context, admin_id, ai_service, state, config, instructions=None):
    """
    Core logic to orchestrate AI text and image generation for a promo flyer.
    """
    event_summary = event.get('summary', 'Sin título')
    event_info = {"summary": event_summary, "description": event.get("description"), "start": event.get("start"), "location": event.get("location")}
    
    # Persistence tracking
    eid = event.get("id", "unk")
    start_raw = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", "unk"))
    storage_key = f"{eid}_{start_raw[:10]}"
    
    notified = state['notified_promos']
    promo_state = notified.get(storage_key, {"notified_thresholds": [], "flyer_created": False})
    promo_state["flyer_created"] = True
    notified[storage_key] = promo_state
    save_json(config['PROMOTED_FILE'], notified)

    logger.info(f"{'Refining' if instructions else 'Generating'} AI promo for: {event_summary}")

    if instructions:
        await context.bot.send_message(admin_id, "🔄 Refinando promoción... Por favor espera. (Costo de créditos aplicado).")
        current = state['pending_promos'].get(str(admin_id), {})
        raw = await ai_service.refine_event_promo(json.dumps(event_info), json.dumps(current), instructions, PROMOTER_SYSTEM_PROMPT)
    else:
        raw = await ai_service.generate_event_promo(json.dumps(event_info), PROMOTER_SYSTEM_PROMPT)

    if not raw:
        await context.bot.send_message(admin_id, "❌ Falló la generación de OpenAI.")
        return
        
    try:
        data = json.loads(raw)
        txt = data.get("telegram_copy", "Error: No copy generated.")
        prmpt = data.get("image_prompt")
        
        if prmpt:
            save_prompt_history(config, event_summary, prmpt)
            
        label = "DRAFT REFINED" if instructions else "DRAFT GENERATED"
        await context.bot.send_message(admin_id, f"<b>📝 {label}:</b>\n\n{txt}", parse_mode=ParseMode.HTML)
        
        if prmpt:
            await context.bot.send_message(admin_id, "🎨 Generando flyer... espera unos segundos (~20s).")
            img = await ai_service.generate_image(prmpt)
            if img:
                if isinstance(img, BytesIO): img.seek(0)
                keyboard = [[InlineKeyboardButton("✅ Publicar", callback_data="publish_draft"), InlineKeyboardButton("🗑 Eliminar", callback_data="clear_pending_promo")]]
                msg = await context.bot.send_photo(
                    admin_id, 
                    photo=img, 
                    caption=f"🎨 Flyer para: {event_summary}\n\n¿Publicamos este diseño?\n\n💡 <i>Responde a este mensaje para pedir cambios.</i>", 
                    reply_markup=InlineKeyboardMarkup(keyboard), 
                    parse_mode=ParseMode.HTML
                )
                # Store file_id for JSON safety and reuse
                stored_image = msg.photo[-1].file_id if msg.photo else img
                state['pending_promos'][str(admin_id)] = {
                    "text": txt, 
                    "image": stored_image, 
                    "summary": event_summary, 
                    "event_id": eid,
                    "event_start": start_raw,
                    "event_info": event
                }
                save_pending_promos(config, state)
                logger.info(f"AI Promo staged for {event_summary}")
    except Exception as e:
        logger.error(f"Promo Generation Error: {e}")
        await context.bot.send_message(admin_id, "❌ Error al procesar la respuesta de la IA.")

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    """
    Handles Admin replies to bot messages for draft refinement or event editing.
    """
    if update.effective_user.id != admin_id or not update.message.reply_to_message: return
    
    # 1. Handle draft refinement
    staged = state['pending_promos'].get(str(admin_id))
    if staged:
        await generate_promo_for_event(staged['event_info'], context, admin_id, ai_service, state, config, instructions=update.message.text)
        return

    # 2. Handle calendar event editing
    editing_event = context.user_data.get('editing_event')
    if editing_event:
        await update.message.reply_text("⏳ Procesando cambios al evento... Por favor espera.")
        prompt = f"You are editing an existing event. Details: {json.dumps(editing_event)}\n\nApply these changes: {update.message.text}\n\nOutput only the updated JSON."
        res = await ai_service.generate_event_promo(update.message.text, prompt)
        if res:
            try:
                updated_data = json.loads(res)
                context.user_data['pending_edit'] = updated_data
                summary = f"📅 <b>CONFIRMAR CAMBIOS</b>\n\n<b>Título:</b> {escape(updated_data['summary'])}\n<b>Inicio:</b> {escape(updated_data['start']['dateTime'])}\n\n¿Confirmar cambios?"
                keyboard = [[InlineKeyboardButton("✅ Confirmar", callback_data="confirm_edit"), InlineKeyboardButton("❌ Cancelar", callback_data="cancel_edit")]]
                await update.message.reply_text(summary, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            except: await update.message.reply_text("❌ Error al procesar los cambios.")
        return

async def draft(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    """
    Manual /draft command to pick an event and generate marketing content.
    """
    if update.effective_user.id != admin_id: return
    logger.info(f"Admin {admin_id} requested manual draft list.")
    await update.message.reply_text("🔍 Buscando eventos próximos en el calendario...")
    try:
        service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])
        events, _ = fetch_upcoming_events(service, config['CALENDAR_ID'], days=30)
        processed = set()
        unique = []
        for e in events:
            sid = e.get("recurringEventId", e["id"])
            if sid not in processed:
                unique.append(e); processed.add(sid)
        keyboard = [[InlineKeyboardButton(f"{e.get('summary')}", callback_data=f"select_draft_{i}")] for i, e in enumerate(unique[:5])]
        context.user_data['draft_events'] = unique
        await update.message.reply_text("¿Para qué evento quieres generar un borrador?", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e: await update.message.reply_text(f"❌ Error: {e}")

async def handle_draft_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    """
    Processes the selection from the manual draft list.
    """
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split('_')[-1])
    events = context.user_data.get('draft_events', [])
    if idx < len(events):
        await query.edit_message_text(f"🚀 Generando promoción para: {events[idx].get('summary')}\nPor favor espera...")
        await generate_promo_for_event(events[idx], context, admin_id, ai_service, state, config)

async def handle_auto_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    """
    Processes the 'Crear Flyer' button from a proactive detection notice.
    """
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split('_')[-1])
    events = context.bot_data.get('current_events', [])
    if idx < len(events):
        await query.edit_message_text(f"🚀 Generando promoción para: {events[idx].get('summary')}\nPor favor espera...")
        await generate_promo_for_event(events[idx], context, admin_id, ai_service, state, config)

async def handle_admin_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config, forced_text=None):
    """
    Handles natural language messages from the Admin (no slash required).
    Determines intent (Add/Edit/Delete/Draft) using AI.
    """
    text = forced_text or (update.message.text if update.message else None)
    
    if update.effective_user.id != admin_id or not text:
        return

    # Replies go to refinement logic (only for text messages, not forced voice text)
    if not forced_text and update.message and update.message.reply_to_message:
        await handle_admin_reply(update, context, admin_id, ai_service, state, config)
        return

    await update.message.reply_text("🤔 Entendiendo tu solicitud... Por favor espera.")
    
    res = await ai_service.generate_event_promo(text, INTENT_DETECTOR_PROMPT)
    if not res:
        await update.message.reply_text("❌ No pude entender tu solicitud. Intenta con un comando /helpadmin.")
        return

    try:
        intent_data = json.loads(res)
        intent = intent_data.get("intent", "UNKNOWN")
        logger.info(f"AI detected admin intent: {intent}")
        
        if intent == "ADD_EVENT":
            await add_event(update, context, admin_id, ai_service, forced_text=text)
        elif intent == "DELETE_EVENT":
            await delete_event(update, context, admin_id, config)
        elif intent == "EDIT_EVENT":
            await edit_event_start(update, context, admin_id, config)
        elif intent == "DRAFT_PROMO":
            await draft(update, context, admin_id, config)
        elif intent == "STATUS":
            await status(update, context, admin_id, state, config)
        else:
            await update.message.reply_text("❓ No estoy seguro de qué quieres hacer. ¿Quieres agregar, editar o borrar un evento?")
            
    except Exception as e:
        logger.error(f"Intent Error: {e}")
        await update.message.reply_text("❌ Error al procesar tu mensaje de chat.")

async def handle_voice_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    """
    Downloads and transcribes voice notes from the admin, then routes them to the chat handler.
    """
    if update.effective_user.id != admin_id or not update.message.voice:
        return

    # Inform the user we're listening
    await update.message.reply_chat_action("record_voice")
    
    try:
        # Download the voice file
        voice_file = await update.message.voice.get_file()
        audio_buffer = BytesIO()
        await voice_file.download_to_memory(out=audio_buffer)
        audio_buffer.seek(0)
        audio_buffer.name = "voice.ogg" # Whisper needs an extension to infer format

        # Transcribe
        await update.message.reply_text("🎤 Escuchando tu audio...")
        text = await ai_service.transcribe_voice(audio_buffer)
        
        if not text or len(text.strip()) < 2:
            await update.message.reply_text("❌ No pude entender el audio. ¿Podrías repetirlo o escribirlo?")
            return

        # Echo what we heard and process as text
        await update.message.reply_text(f"📝 <i>Transripción:</i> \"{text}\"", parse_mode=ParseMode.HTML)
        
        # Process via handle_admin_chat using the forced_text parameter
        await handle_admin_chat(update, context, admin_id, ai_service, state, config, forced_text=text)

    except Exception as e:
        logger.error(f"Voice Processing Error: {e}")
        await update.message.reply_text("❌ Hubo un problema al procesar tu mensaje de voz.")

# --- SHARED UTILS ---

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, subscribers):
    """
    Sends a manual message to all subscribers.
    """
    if update.effective_user.id != admin_id: 
        logger.warning(f"Unauthorized broadcast attempt by user {update.effective_user.id}")
        return
    if not context.args: 
        await update.message.reply_text("Uso: /broadcast [mensaje]")
        return
    msg = " ".join(context.args)
    count = 0
    for uid in subscribers:
        try: 
            await context.bot.send_message(uid, msg)
            count += 1
        except: pass
    await update.message.reply_text(f"¡Difundido a {count} usuarios!")
    logger.info(f"Admin {admin_id} sent broadcast to {count} users.")

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    """
    Adds current group to broadcast targets.
    """
    if update.effective_user.id != admin_id: return
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        state['groups'].add(chat.id)
        save_json(config['GROUPS_FILE'], list(state['groups']))
        await update.message.reply_text(f"✅ Grupo '{chat.title}' añadido a la lista de difusión.")
        logger.info(f"Group added: {chat.title} ({chat.id})")

async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state):
    """
    Lists registered groups.
    """
    if update.effective_user.id != admin_id: return
    if not state['groups']:
        await update.message.reply_text("No hay grupos configurados.")
        return
    msg = "📢 <b>Grupos de difusión:</b>\n\n" + "\n".join([f"• <code>{gid}</code>" for gid in state['groups']])
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def remove_group(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    """
    Removes current group from targets.
    """
    if update.effective_user.id != admin_id: return
    cid = update.effective_chat.id
    if cid in state['groups']:
        state['groups'].discard(cid)
        save_json(config['GROUPS_FILE'], list(state['groups']))
        await update.message.reply_text("✅ Grupo removido de la lista de difusión.")
        logger.info(f"Group removed: {cid}")
    else:
        await update.message.reply_text("❌ Este grupo no está en la lista.")
