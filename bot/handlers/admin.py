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
...
async def handle_admin_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    """
    Handles natural language messages from the Admin (no slash required).
    """
    if update.effective_user.id != admin_id or not update.message.text:
        return

    # If it's a reply, use the existing reply logic
    if update.message.reply_to_message:
        await handle_admin_reply(update, context, admin_id, ai_service, state, config)
        return

    text = update.message.text
    
    # 1. Detect Intent
    await update.message.reply_text("🤔 Entendiendo tu solicitud...")
    res = await ai_service.generate_event_promo(text, INTENT_DETECTOR_PROMPT)
    if not res:
        await update.message.reply_text("❌ No pude entender tu solicitud.")
        return

    try:
        intent_data = json.loads(res)
        intent = intent_data.get("intent", "UNKNOWN")
        
        if intent == "ADD_EVENT":
            # Re-use add_event logic but with the full text
            context.args = text.split() # Mock context args if needed, or just change add_event
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
            await update.message.reply_text("❓ No estoy seguro de qué quieres hacer. Prueba con un comando como /helpadmin.")
            
    except Exception as e:
        logger.error(f"Intent Error: {e}")
        await update.message.reply_text("❌ Error al procesar tu mensaje.")
from datetime import datetime, timezone

# Initialize logger for this module
logger = logging.getLogger(__name__)

def save_prompt_history(config, event_summary, image_prompt):
    """Stores a generated image prompt for later admin review."""
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
    """Formats one prompt history record for Telegram."""
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
    """Admin-only command that shows the most recent generated image prompt."""
    if update.effective_user.id != admin_id: return
    prompt_history = load_json(config['PROMPT_HISTORY_FILE'], [])
    if not prompt_history:
        await update.message.reply_text("No prompt history yet.")
        return
    await update.message.reply_text(format_prompt_history_entry(prompt_history[-1], prompt_limit=3200), parse_mode=ParseMode.HTML)

async def check_prompts(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    """Admin-only command that shows the most recent generated image prompts."""
    if update.effective_user.id != admin_id: return
    prompt_history = load_json(config['PROMPT_HISTORY_FILE'], [])
    if not prompt_history:
        await update.message.reply_text("No prompt history yet.")
        return
    recent_prompts = prompt_history[-5:]
    prompt_message = "\n\n---\n\n".join(format_prompt_history_entry(entry, prompt_limit=500) for entry in reversed(recent_prompts))
    await update.message.reply_text(prompt_message, parse_mode=ParseMode.HTML)

async def help_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id):
    """Lists available admin commands."""
    if update.effective_user.id != admin_id: return
    await update.message.reply_text(
        "<b>Admin commands</b>\n\n"
        "/status - Show bot health\n"
        "/groups - List broadcast groups\n"
        "/addgroup - Add this group\n"
        "/removegroup - Remove this group\n"
        "/addevent - Create event (natural language)\n"
        "/editevent - Edit an existing event\n"
        "/deleteevent - Delete an event\n"
        "/draft - Generate promo flyer\n"
        "/pendingpromo - Show staged promo\n"
        "/broadcast - Manual message to all\n\n"
        "💡 <i>Tip: Puedes responder a un borrador o a un evento seleccionado para editarlo con AI.</i>",
        parse_mode=ParseMode.HTML
    )

def save_pending_promos(config, state):
    """Saves only JSON-serializable data to disk."""
    clean_pending = {}
    for uid, data in state['pending_promos'].items():
        clean_data = data.copy()
        if isinstance(clean_data.get('image'), BytesIO):
            continue 
        clean_pending[uid] = clean_data
    save_json(config['PENDING_PROMOS_FILE'], clean_pending)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    if update.effective_user.id != admin_id: return
    msg = (
        f"📊 <b>Bot Status</b>\n\n"
        f"Subs: {len(state['subscribers'])}\n"
        f"Groups: {len(state['groups'])}\n"
        f"Pending: {len(state['pending_promos'])}\n"
        f"OpenAI: {'✅ Configured' if config.get('OPENAI_API_KEY') else '❌ Missing'}"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def pending_promo(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state):
    if update.effective_user.id != admin_id: return
    staged = state['pending_promos'].get(str(admin_id))
    if not staged:
        await update.message.reply_text("No pending promo.")
        return
    keyboard = [[InlineKeyboardButton("✅ Publicar", callback_data="publish_draft"), InlineKeyboardButton("🗑 Eliminar", callback_data="clear_pending_promo")]]
    
    image = staged['image']
    if isinstance(image, BytesIO):
        image.seek(0)

    await update.message.reply_photo(photo=image, caption=f"Borrador: {staged.get('summary')}\n\n{staged.get('text')}\n\n¿Publicar?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

# --- BROADCAST & PUBLISH ---

async def handle_publish(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    query = update.callback_query
    if query.from_user.id != admin_id: return
    await query.answer()
    staged = state['pending_promos'].get(str(admin_id))
    if not staged:
        await query.edit_message_caption("❌ No staged draft found.")
        return
    await query.edit_message_caption("🚀 Publishing...")
    
    image = staged['image']
    text = staged['text']

    for group_id in state['groups']:
        try:
            if isinstance(image, BytesIO): image.seek(0)
            await context.bot.send_photo(group_id, photo=image, caption=text, parse_mode=ParseMode.HTML)
        except: pass
    
    for user_id in state['subscribers']:
        try:
            if isinstance(image, BytesIO): image.seek(0)
            await context.bot.send_photo(user_id, photo=image, caption=text, parse_mode=ParseMode.HTML)
        except: pass

    await query.edit_message_caption(f"✅ Published successfully!")
    del state['pending_promos'][str(admin_id)]
    save_pending_promos(config, state)

async def handle_clear_pending_promo(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    query = update.callback_query
    await query.answer()
    if str(admin_id) in state['pending_promos']:
        del state['pending_promos'][str(admin_id)]
        save_pending_promos(config, state)
        await query.edit_message_caption("🗑 Borrador eliminado.")

# --- CALENDAR ACTIONS (DELETE & EDIT) ---

async def delete_event(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    if update.effective_user.id != admin_id: return
    await update.message.reply_text("🔍 Buscando eventos para eliminar...")
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
        await update.message.reply_text("¿Qué evento quieres ELIMINAR?", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e: await update.message.reply_text(f"❌ Error: {e}")

async def handle_delete_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
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
        await query.edit_message_text(f"✅ Evento eliminado: {event.get('summary')}")
    except Exception as e: await query.edit_message_text(f"❌ Error: {e}")

async def edit_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    if update.effective_user.id != admin_id: return
    await update.message.reply_text("🔍 Selecciona un evento para editar:")
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
        await update.message.reply_text("Evento a editar:", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e: await update.message.reply_text(f"❌ Error: {e}")

async def handle_edit_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id):
    query = update.callback_query
    if query.from_user.id != admin_id: return
    await query.answer()
    idx = int(query.data.split('_')[-1])
    events = context.user_data.get('edit_list', [])
    if idx >= len(events): return
    context.user_data['editing_event'] = events[idx]
    await query.edit_message_text(f"Has seleccionado: <b>{events[idx].get('summary')}</b>\n\nResponde a este mensaje con los cambios que quieras hacer.\n<i>Ejemplo: Cambia la hora a las 7pm y la ubicación a Zoom.</i>", parse_mode=ParseMode.HTML)

async def handle_edit_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    query = update.callback_query
    if query.from_user.id != admin_id: return
    await query.answer()
    if query.data == "cancel_edit":
        await query.edit_message_text("Cancelado.")
        return
    data = context.user_data.get('pending_edit')
    event = context.user_data.get('editing_event')
    try:
        service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])
        service.events().update(calendarId=config['CALENDAR_ID'], eventId=event['id'], body=data).execute()
        await query.edit_message_text("✅ Evento actualizado.")
        context.user_data.pop('editing_event', None)
    except Exception as e: await query.edit_message_text(f"❌ Error: {e}")

# --- ADD LOGIC ---

async def add_event(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, forced_text=None):
    if update.effective_user.id != admin_id: return
    if not forced_text and not context.args:
        await update.message.reply_text("Uso: /addevent [descripción del evento]")
        return
    desc = forced_text if forced_text else " ".join(context.args)
    await update.message.reply_text("⏳ Procesando detalles... Por favor espera.")
    prompt = EVENT_PARSER_SYSTEM_PROMPT.format(current_date=datetime.now().strftime("%Y-%m-%d"))
    res = await ai_service.generate_event_promo(desc, prompt)
    if not res: return
    try:
        data = json.loads(res)
        context.user_data['pending_event'] = data
        summary = f"📅 <b>CONFIRMAR NUEVO EVENTO</b>\n\n<b>Título:</b> {data['summary']}\n<b>Inicio:</b> {data['start']['dateTime']}\n\n¿Confirmar creación?"
        keyboard = [[InlineKeyboardButton("✅ Confirmar", callback_data="confirm_add"), InlineKeyboardButton("❌ Cancelar", callback_data="cancel_add")]]
        await update.message.reply_text(summary, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    except: await update.message.reply_text("❌ Error al procesar los detalles.")

async def handle_add_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    query = update.callback_query
    if query.from_user.id != admin_id: return
    await query.answer()
    if query.data == "cancel_add":
        await query.edit_message_text("Cancelado.")
        return
    data = context.user_data.get('pending_event')
    try:
        service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])
        create_calendar_event(service, config['CALENDAR_ID'], data)
        await query.edit_message_text("✅ Evento creado exitosamente.")
    except Exception as e: await query.edit_message_text(f"❌ Error al crear el evento: {e}")

# --- DRAFT & PROMO LOGIC ---

async def generate_promo_for_event(event, context, admin_id, ai_service, state, config, instructions=None):
    event_summary = event.get('summary', 'Sin título')
    event_info = {"summary": event_summary, "description": event.get("description"), "start": event.get("start"), "location": event.get("location")}
    
    eid = event.get("id", "unk")
    start_raw = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", "unk"))
    storage_key = f"{eid}_{start_raw[:10]}"
    
    notified = state['notified_promos']
    promo_state = notified.get(storage_key, {"notified_thresholds": [], "flyer_created": False})
    promo_state["flyer_created"] = True
    notified[storage_key] = promo_state
    save_json(config['PROMOTED_FILE'], notified)

    if instructions:
        await context.bot.send_message(admin_id, "🔄 Refinando promoción... Por favor espera. (Costo de créditos aplicado).")
        current = state['pending_promos'].get(str(admin_id), {})
        raw = await ai_service.refine_event_promo(json.dumps(event_info), json.dumps(current), instructions, PROMOTER_SYSTEM_PROMPT)
    else:
        raw = await ai_service.generate_event_promo(json.dumps(event_info), PROMOTER_SYSTEM_PROMPT)

    if not raw: return
    try:
        data = json.loads(raw)
        txt = data.get("telegram_copy", "")
        prmpt = data.get("image_prompt")
        
        if prmpt:
            save_prompt_history(config, event_summary, prmpt)
            
        await context.bot.send_message(admin_id, f"<b>📝 BORRADOR GENERADO:</b>\n\n{txt}", parse_mode=ParseMode.HTML)
        
        if prmpt:
            await context.bot.send_message(admin_id, "🎨 Generando flyer... espera unos segundos.")
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
                
                stored_image = msg.photo[-1].file_id if msg.photo else img
                state['pending_promos'][str(admin_id)] = {"text": txt, "image": stored_image, "summary": event_summary, "event_info": event}
                save_pending_promos(config, state)
    except Exception as e: logger.error(e)

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    if update.effective_user.id != admin_id or not update.message.reply_to_message: return
    
    staged = state['pending_promos'].get(str(admin_id))
    if staged:
        await generate_promo_for_event(staged['event_info'], context, admin_id, ai_service, state, config, instructions=update.message.text)
        return

    editing_event = context.user_data.get('editing_event')
    if editing_event:
        await update.message.reply_text("⏳ Procesando cambios al evento... Por favor espera.")
        prompt = f"You are editing an existing event. Details: {json.dumps(editing_event)}\n\nApply these changes: {update.message.text}\n\nOutput only the updated JSON."
        res = await ai_service.generate_event_promo(update.message.text, prompt)
        if res:
            try:
                updated_data = json.loads(res)
                context.user_data['pending_edit'] = updated_data
                summary = f"📅 <b>CONFIRMAR CAMBIOS</b>\n\n<b>Título:</b> {updated_data['summary']}\n<b>Inicio:</b> {updated_data['start']['dateTime']}\n\n¿Confirmar cambios?"
                keyboard = [[InlineKeyboardButton("✅ Confirmar", callback_data="confirm_edit"), InlineKeyboardButton("❌ Cancelar", callback_data="cancel_edit")]]
                await update.message.reply_text(summary, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
            except: await update.message.reply_text("❌ Error al procesar los cambios.")
        return

async def draft(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    if update.effective_user.id != admin_id: return
    await update.message.reply_text("🔍 Buscando eventos próximos...")
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
        await update.message.reply_text("Elige un evento para el borrador:", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e: await update.message.reply_text(f"❌ Error: {e}")

async def handle_draft_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split('_')[-1])
    events = context.user_data.get('draft_events', [])
    if idx < len(events):
        await query.edit_message_text(f"🚀 Generando promoción para: {events[idx].get('summary')}\nPor favor espera...")
        await generate_promo_for_event(events[idx], context, admin_id, ai_service, state, config)

async def handle_auto_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split('_')[-1])
    events = context.bot_data.get('current_events', [])
    if idx < len(events):
        await query.edit_message_text(f"🚀 Generando promoción para: {events[idx].get('summary')}\nPor favor espera...")
        await generate_promo_for_event(events[idx], context, admin_id, ai_service, state, config)

# --- SHARED UTILS ---

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, subscribers):
    if update.effective_user.id != admin_id: return
    if not context.args: 
        await update.message.reply_text("Uso: /broadcast [mensaje]")
        return
    msg = " ".join(context.args)
    for uid in subscribers:
        try: await context.bot.send_message(uid, msg)
        except: pass
    await update.message.reply_text("¡Mensaje difundido exitosamente!")

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    if update.effective_user.id != admin_id: return
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        state['groups'].add(chat.id)
        save_json(config['GROUPS_FILE'], list(state['groups']))
        await update.message.reply_text(f"✅ Grupo '{chat.title}' añadido a la lista de difusión.")

async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state):
    if update.effective_user.id != admin_id: return
    if not state['groups']:
        await update.message.reply_text("No hay grupos configurados.")
        return
    msg = "📢 <b>Grupos de difusión:</b>\n\n" + "\n".join([f"• <code>{gid}</code>" for gid in state['groups']])
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def remove_group(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    if update.effective_user.id != admin_id: return
    cid = update.effective_chat.id
    if cid in state['groups']:
        state['groups'].discard(cid)
        save_json(config['GROUPS_FILE'], list(state['groups']))
        await update.message.reply_text("✅ Grupo removido de la lista de difusión.")
    else:
        await update.message.reply_text("❌ Este grupo no está en la lista.")

async def handle_edit_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config):
    query = update.callback_query
    if query.from_user.id != admin_id: return
    await query.answer()
    if query.data == "cancel_edit":
        await query.edit_message_text("Cancelado.")
        return
    data = context.user_data.get('pending_edit')
    event = context.user_data.get('editing_event')
    try:
        service = get_calendar_service_with_creds(config['SCOPES'], config['SERVICE_ACCOUNT_FILE'])
        service.events().update(calendarId=config['CALENDAR_ID'], eventId=event['id'], body=data).execute()
        await query.edit_message_text("✅ Evento actualizado.")
        context.user_data.pop('editing_event', None)
    except Exception as e: await query.edit_message_text(f"❌ Error: {e}")
