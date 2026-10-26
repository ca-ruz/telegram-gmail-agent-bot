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
from datetime import date, datetime, timezone
from core.coffee_hosts import (
    COFFEE_TIMEZONE,
    build_coffee_assignment,
    build_coffee_host,
    next_coffee_host_id,
    save_coffee_hosts,
    send_immediate_coffee_host_reminder,
    upcoming_saturdays,
    send_manual_coffee_host_reminder,
)
from core.manual_promos import (
    MANUAL_PROMO_TIMEZONE,
    build_manual_promo,
    deliver_manual_promo,
    deliver_manual_promo_test,
    format_manual_promo_caption,
    get_manual_promo_stage,
    save_manual_promos,
    set_first_notification_schedule,
)
from core.monthly_calendar import (
    build_monthly_calendar,
    parse_calendar_month,
    save_monthly_calendar,
)
from core.notification_preferences import (
    filter_users_by_category,
    is_notification_enabled,
)
from core.membership_payments import (
    build_member,
    can_manage_payments,
    next_member_id,
    save_members,
    save_payment_state,
)
from core.imported_events import (
    assign_imported_event_flyer,
    get_events_missing_flyers,
    save_imported_events,
)
from core.linked_events import build_linked_event, validate_linked_events
from core.promoter import send_manual_event_reminder, send_manual_event_reminder_test
from core.telegram_metadata import record_group_metadata, save_telegram_metadata

# Initialize logger for this module
logger = logging.getLogger(__name__)

def format_manual_promo_preview(promo):
    """Format manual promo metadata and publication text for Telegram preview."""
    return (
        f"<b>{escape(str(promo.get('summary', 'Sin título')))}</b>\n\n"
        f"{format_manual_promo_caption(promo)}\n\n"
        "¿Cómo deseas enviar este promo?"
    )

def save_prompt_history(config, event, image_prompt):
    """
    Stores a generated image prompt for later admin review.
    
    Args:
        config (dict): Bot configuration dictionary.
        event (dict or str): The event object, or a legacy event title.
        image_prompt (str): The prompt used for DALL-E generation.
    """
    prompt_history = load_json(config['PROMPT_HISTORY_FILE'], [])
    if isinstance(event, dict):
        event_summary = event.get("summary", "Sin título")
        event_start = event.get("start")
    else:
        event_summary = event
        event_start = None

    entry = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "event": event_summary,
        "image_model": config.get("OPENAI_IMAGE_MODEL"),
        "image_quality": config.get("OPENAI_IMAGE_QUALITY"),
        "image_prompt": image_prompt,
    }
    if event_start is not None:
        entry["event_start"] = event_start
    prompt_history.append(entry)
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


def parse_manual_reminder_request(raw_args):
    """Parse and validate an admin manual reminder request."""
    parts = [part.strip() for part in raw_args.split("|")]
    if len(parts) == 3:
        if (
            parts[0].lower() == "test"
            and parts[1].lower() == "event"
            and parts[2]
        ):
            return {"type": "event_test", "event_id": parts[2]}
        return None

    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None

    reminder_type, target = parts[0].lower(), parts[1]
    if reminder_type == "coffee":
        try:
            reminder_date = date.fromisoformat(target)
        except ValueError:
            return None
        if reminder_date.weekday() != 5:
            return None
        return {"type": "coffee", "date": target}

    if reminder_type == "event":
        return {"type": "event", "event_id": target}

    return None


async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    """Validate and send an administrator manual reminder request."""
    if update.effective_user.id != admin_id:
        return

    raw_args = update.message.text.partition(" ")[2].strip()
    request = parse_manual_reminder_request(raw_args)
    if not request:
        await update.message.reply_text(
            "Uso: /remind coffee | AAAA-MM-DD\n"
            "o: /remind event | event-id\n"
            "o: /remind test | event | event-id"
        )
        return

    if request["type"] == "coffee":
        result = await send_manual_coffee_host_reminder(
            context,
            config,
            state,
            request["date"],
        )
        if not result["found"]:
            await update.message.reply_text("❌ No hay asignación de café para esa fecha.")
            return
        await update.message.reply_text(
            f"☕ Recordatorio manual: {result['sent']} enviados, "
            f"{result['failed']} fallos, {result['skipped']} omitidos."
        )
        return

    if request["type"] == "event_test":
        result = await send_manual_event_reminder_test(
            context,
            config,
            admin_id,
            request["event_id"],
        )
        if not result["found"]:
            await update.message.reply_text("❌ Evento no encontrado en el calendario.")
            return
        if result["failures"]:
            await update.message.reply_text("❌ No se pudo enviar la prueba a tu DM.")
            return
        await update.message.reply_text(
            "🧪 MODO PRUEBA: recordatorio enviado solo a tu DM."
        )
        return

    result = await send_manual_event_reminder(
        context,
        config,
        state,
        request["event_id"],
    )
    if not result["found"]:
        await update.message.reply_text("❌ Evento no encontrado en el calendario.")
        return
    await update.message.reply_text(
        f"📅 Recordatorio manual: {result['subscribers_sent']} DMs, "
        f"{result['groups_sent']} grupos, {result['failures']} fallos."
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
        "/users - List known Telegram users\n"
        "/addgroup - Add this group to broadcast targets\n"
        "/removegroup - Remove this group from broadcast targets\n"
        "/addevent - Create a Google Calendar event (natural language)\n"
        "/editevent - Interactively edit an existing event\n"
        "/deleteevent - Interactively delete an event\n"
        "/draft - Manually generate a promo for an upcoming event\n"
        "/manualpromo - Stage an existing flyer and publication text\n"
        "/calendario [AAAA-MM] - Upload or replace the monthly calendar image\n"
        "/pendingpromo - Show currently staged promo with publish/delete buttons\n"
        "/checkprompt - Show the most recent image prompt\n"
        "/checkprompts - Show the last 5 image prompts\n"
        "/coffeehost - View or manage Saturday coffee hosts\n"
        "/coffeehost add | Name | Telegram ID\n"
        "/coffeehost remove | host-id\n"
        "/payments - Revisar el estado de pagos de membresía\n"
        "/broadcast - Send a manual message to all subscribers\n\n"
        "💡 <i>Tip: Puedes responder a un borrador o a un evento seleccionado para editarlo con AI.</i>",
        parse_mode=ParseMode.HTML
    )


def format_payment_review(payment_state, members):
    """Format the current payment period for an authorized administrator."""
    period = escape(str(payment_state.get("period", "Sin periodo")))
    records = payment_state.get("members", {})
    if not isinstance(records, dict):
        records = {}

    lines = [f"<b>Pagos de membresía: {period}</b>"]
    paid_lines = []
    unpaid_lines = []
    for member_id, record in sorted(records.items()):
        member = members.get(member_id, {})
        name = escape(str(member.get("name", member_id)))
        line = f"{name} (<code>{escape(str(member_id))}</code>)"
        if isinstance(record, dict) and record.get("status") == "paid":
            paid_lines.append(line)
        else:
            unpaid_lines.append(line)

    lines.append(f"\n<b>Pagados ({len(paid_lines)})</b>")
    lines.extend(f"\n✅ {line}" for line in paid_lines or ["Ninguno"])
    lines.append(f"\n\n<b>Pendientes ({len(unpaid_lines)})</b>")
    lines.extend(f"\n⏳ {line}" for line in unpaid_lines or ["Ninguno"])
    return "".join(lines)


def payment_review_keyboard(payment_state, members):
    """Build status-toggle buttons for the current payment records."""
    records = payment_state.get("members", {})
    if not isinstance(records, dict):
        return None

    buttons = []
    for member_id, record in sorted(records.items()):
        member = members.get(member_id, {})
        name = str(member.get("name", member_id))
        is_paid = isinstance(record, dict) and record.get("status") == "paid"
        label = f"↩ {name}: pendiente" if is_paid else f"✅ {name}: pagado"
        buttons.append([
            InlineKeyboardButton(
                label,
                callback_data=f"payment_toggle_{member_id}",
            )
        ])
    return InlineKeyboardMarkup(buttons) if buttons else None


async def payments(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    primary_admin_id,
    payment_admin_id,
    state,
):
    """Show the current membership payment state to authorized administrators."""
    if not can_manage_payments(
        update.effective_user.id,
        primary_admin_id,
        payment_admin_id,
    ):
        await update.message.reply_text(
            "❌ No tienes permisos para revisar pagos de membresía."
        )
        return

    await update.message.reply_text(
        format_payment_review(
            state.get("payment_state", {}),
            state.get("members", {}),
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=payment_review_keyboard(
            state.get("payment_state", {}),
            state.get("members", {}),
        ),
    )


async def handle_payment_review(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    primary_admin_id,
    payment_admin_id,
    state,
):
    """Open the payment review from an authorized follow-up button."""
    query = update.callback_query
    if not can_manage_payments(
        query.from_user.id,
        primary_admin_id,
        payment_admin_id,
    ):
        await query.answer("No tienes permisos para revisar pagos.", show_alert=True)
        return

    await query.answer()
    await query.edit_message_text(
        format_payment_review(
            state.get("payment_state", {}),
            state.get("members", {}),
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=payment_review_keyboard(
            state.get("payment_state", {}),
            state.get("members", {}),
        ),
    )


async def handle_payment_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    primary_admin_id,
    payment_admin_id,
    state,
    config,
):
    """Toggle a member's paid status from the administrator review."""
    query = update.callback_query
    if not can_manage_payments(
        query.from_user.id,
        primary_admin_id,
        payment_admin_id,
    ):
        await query.answer("No tienes permisos para modificar pagos.", show_alert=True)
        return

    member_id = query.data.removeprefix("payment_toggle_")
    payment_state = state.setdefault("payment_state", {})
    records = payment_state.setdefault("members", {})
    record = records.get(member_id)
    if not isinstance(record, dict):
        await query.answer("Ese miembro no está en el periodo actual.", show_alert=True)
        return

    member = state.setdefault("members", {}).get(member_id, {})
    name = str(member.get("name", member_id))
    if record.get("status") == "paid":
        record["status"] = "unpaid"
        record["paid_at"] = None
        message = f"⏳ {escape(name)} quedó pendiente."
    else:
        record["status"] = "paid"
        record["paid_at"] = datetime.now(timezone.utc).isoformat()
        message = f"✅ {escape(name)} quedó marcado como pagado."

    save_payment_state(config["PAYMENT_STATE_FILE"], payment_state)
    await query.answer(message)
    await query.edit_message_text(
        format_payment_review(payment_state, state.get("members", {})),
        parse_mode=ParseMode.HTML,
        reply_markup=payment_review_keyboard(
            payment_state,
            state.get("members", {}),
        ),
    )


def format_members(members):
    """Format active and inactive members for an authorized administrator."""
    lines = ["<b>Miembros</b>"]
    for status, active in (("Activos", True), ("Inactivos", False)):
        lines.append(f"\n<b>{status}</b>")
        matching_members = [
            (member_id, member)
            for member_id, member in members.items()
            if isinstance(member, dict) and member.get("active", True) is active
        ]
        if not matching_members:
            lines.append("\nNinguno")
            continue
        for member_id, member in sorted(matching_members):
            name = escape(str(member.get("name", "Sin nombre")))
            telegram_id = escape(str(member.get("telegram_id", "sin ID")))
            lines.append(f"\n<code>{escape(member_id)}</code> - {name} ({telegram_id})")
    return "".join(lines)


async def manage_members(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    primary_admin_id,
    payment_admin_id,
    state,
    config,
):
    """Manage the persistent member roster for authorized administrators."""
    if not can_manage_payments(
        update.effective_user.id,
        primary_admin_id,
        payment_admin_id,
    ):
        await update.message.reply_text(
            "❌ No tienes permisos para administrar miembros."
        )
        return

    members = state.setdefault("members", {})
    raw_args = update.message.text.partition(" ")[2].strip()
    parts = [part.strip() for part in raw_args.split("|")] if raw_args else []
    action = parts[0].lower() if parts else "list"

    if action == "list":
        await update.message.reply_text(format_members(members), parse_mode=ParseMode.HTML)
        return

    if action == "add":
        if len(parts) != 3 or not parts[1] or not parts[2]:
            await update.message.reply_text(
                "Uso: /members add | Nombre | Telegram ID"
            )
            return
        name = parts[1]
        try:
            telegram_id = int(parts[2])
        except ValueError:
            await update.message.reply_text("❌ El Telegram ID debe ser numérico.")
            return
        if telegram_id <= 0:
            await update.message.reply_text("❌ El Telegram ID debe ser positivo.")
            return
        if any(
            isinstance(member, dict) and member.get("telegram_id") == telegram_id
            for member in members.values()
        ):
            await update.message.reply_text("❌ Ese Telegram ID ya está registrado.")
            return

        member_id = next_member_id(members)
        members[member_id] = build_member(name, telegram_id)
        save_members(config["MEMBERS_FILE"], members)
        await update.message.reply_text(
            f"✅ Miembro agregado: <code>{escape(member_id)}</code> - {escape(name)}",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "update":
        if len(parts) != 4 or not parts[1] or not parts[2] or not parts[3]:
            await update.message.reply_text(
                "Uso: /members update | member-id | Nombre | Telegram ID"
            )
            return
        member_id, name = parts[1], parts[2]
        if member_id not in members or not isinstance(members[member_id], dict):
            await update.message.reply_text("❌ Ese miembro no existe.")
            return
        try:
            telegram_id = int(parts[3])
        except ValueError:
            await update.message.reply_text("❌ El Telegram ID debe ser numérico.")
            return
        if telegram_id <= 0:
            await update.message.reply_text("❌ El Telegram ID debe ser positivo.")
            return
        if any(
            other_id != member_id
            and isinstance(member, dict)
            and member.get("telegram_id") == telegram_id
            for other_id, member in members.items()
        ):
            await update.message.reply_text("❌ Ese Telegram ID ya está registrado.")
            return

        members[member_id].update({"name": name, "telegram_id": telegram_id})
        save_members(config["MEMBERS_FILE"], members)
        await update.message.reply_text(
            f"✅ Miembro actualizado: <code>{escape(member_id)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "deactivate":
        if len(parts) != 2 or not parts[1]:
            await update.message.reply_text("Uso: /members deactivate | member-id")
            return
        member_id = parts[1]
        member = members.get(member_id)
        if not isinstance(member, dict):
            await update.message.reply_text("❌ Ese miembro no existe.")
            return
        member["active"] = False
        save_members(config["MEMBERS_FILE"], members)
        await update.message.reply_text(
            f"✅ Miembro desactivado: <code>{escape(member_id)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_text(
        "Uso:\n"
        "/members list\n"
        "/members add | Nombre | Telegram ID\n"
        "/members update | member-id | Nombre | Telegram ID\n"
        "/members deactivate | member-id"
    )


async def monthly_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id):
    """Start an administrator-only monthly calendar image upload."""
    if update.effective_user.id != admin_id:
        return

    raw_args = update.message.text.partition(" ")[2].strip()
    parts = [part.strip() for part in raw_args.split("|", 1)] if raw_args else []
    try:
        month = parse_calendar_month(parts[0] if parts else "")
    except ValueError:
        await update.message.reply_text(
            "❌ El mes debe tener formato AAAA-MM."
        )
        return

    caption = parts[1] if len(parts) == 2 else ""
    context.user_data["monthly_calendar_upload"] = {
        "month": month,
        "caption": caption,
    }
    await update.message.reply_text(
        f"✅ Calendario de {month} listo. Ahora envía la imagen para subirla o reemplazarla."
    )


async def handle_monthly_calendar_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id,
    state,
    config,
):
    """Persist an administrator's uploaded monthly calendar image."""
    if update.effective_user.id != admin_id:
        return

    upload = context.user_data.get("monthly_calendar_upload")
    if not upload:
        return

    image_file_id = update.message.photo[-1].file_id
    calendar = build_monthly_calendar(
        upload["month"],
        image_file_id,
        caption=upload["caption"],
    )
    stored_calendar = state.setdefault("monthly_calendar", {})
    stored_calendar.clear()
    stored_calendar.update(calendar)
    save_monthly_calendar(config["MONTHLY_CALENDAR_FILE"], calendar)
    context.user_data.pop("monthly_calendar_upload", None)
    await update.message.reply_text(
        f"✅ Calendario de {upload['month']} guardado correctamente."
    )


async def handle_admin_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id,
    state,
    config,
):
    """Route an administrator photo to its active upload workflow."""
    if context.user_data.get("monthly_calendar_upload"):
        await handle_monthly_calendar_photo(update, context, admin_id, state, config)
        return
    if context.user_data.get("imported_flyer_event_key"):
        await handle_imported_flyer_photo(update, context, admin_id, state, config)
        return
    await handle_manual_promo_photo(update, context, admin_id, state, config)


async def flyers(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state):
    """List imported events that still need a manually assigned flyer."""
    if update.effective_user.id != admin_id:
        return

    missing_events = get_events_missing_flyers(state.get("imported_events", {}))
    if not missing_events:
        await update.message.reply_text("✅ No hay eventos importados pendientes de flyer.")
        return

    keyboard = []
    for event in missing_events[:10]:
        label = format_imported_event_label(event)
        keyboard.append([
            InlineKeyboardButton(
                label[:60],
                callback_data=f"imported_flyer_{event['key']}",
            )
        ])
    await update.message.reply_text(
        "Selecciona el evento importado al que quieres asignar un flyer:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def format_imported_event_label(event):
    """Return an imported-event button label with its day and month."""
    summary = str(event.get("summary", "Evento"))
    start = event.get("start", {})
    start_value = start.get("dateTime", start.get("date"))
    if start_value:
        try:
            event_date = datetime.fromisoformat(
                str(start_value).replace("Z", "+00:00")
            )
            return f"📸 {summary} · {event_date.day:02d}/{event_date.month:02d}"
        except ValueError:
            pass
    return f"📸 {summary}"


async def handle_imported_flyer_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id,
    state,
):
    """Start the photo upload workflow for one imported event."""
    query = update.callback_query
    if query.from_user.id != admin_id:
        return

    await query.answer()
    event_key = query.data.removeprefix("imported_flyer_")
    event = state.get("imported_events", {}).get(event_key)
    if not event or event.get("flyer_file_id"):
        await query.edit_message_text("❌ Ese evento ya no necesita un flyer.")
        return

    context.user_data["imported_flyer_event_key"] = event_key
    await query.edit_message_text(
        f"📸 Envía ahora el flyer para: {escape(event.get('summary', 'Evento'))}",
        parse_mode=ParseMode.HTML,
    )


async def handle_imported_flyer_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id,
    state,
    config,
):
    """Persist an administrator's flyer assignment for an imported event."""
    if update.effective_user.id != admin_id:
        return

    event_key = context.user_data.get("imported_flyer_event_key")
    if not event_key:
        return

    image_file_id = update.message.photo[-1].file_id
    event = assign_imported_event_flyer(
        state.setdefault("imported_events", {}),
        event_key,
        image_file_id,
    )
    if not event:
        context.user_data.pop("imported_flyer_event_key", None)
        await update.message.reply_text("❌ No se encontró el evento importado.")
        return

    save_imported_events(config["IMPORTED_EVENTS_FILE"], state["imported_events"])
    context.user_data.pop("imported_flyer_event_key", None)
    await update.message.reply_text(
        f"✅ Flyer asignado a: {escape(event.get('summary', 'Evento'))}",
        parse_mode=ParseMode.HTML,
    )


async def manual_promo(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id):
    """Start staging a manually supplied event flyer and publication text."""
    if update.effective_user.id != admin_id:
        return

    raw_args = update.message.text.partition(" ")[2].strip()
    parts = [part.strip() for part in raw_args.split("|", 3)]
    if len(parts) != 4 or any(not part for part in parts):
        await update.message.reply_text(
            "Uso: /manualpromo AAAA-MM-DD HH:MM | Título | Lugar | Texto de publicación"
        )
        return

    try:
        event_start = datetime.fromisoformat(parts[0])
    except ValueError:
        await update.message.reply_text(
            "❌ La fecha debe tener formato AAAA-MM-DD HH:MM."
        )
        return

    if event_start.tzinfo is None:
        event_start = event_start.replace(tzinfo=COFFEE_TIMEZONE)

    if event_start <= datetime.now(event_start.tzinfo):
        await update.message.reply_text("❌ La fecha del evento debe estar en el futuro.")
        return

    context.user_data["manual_promo_draft"] = {
        "event_id": f"manual-{event_start.strftime('%Y%m%dT%H%M%S')}",
        "summary": parts[1],
        "location": parts[2],
        "event_start": event_start.isoformat(),
        "text": parts[3],
    }
    await update.message.reply_text(
        "✅ Datos guardados. Ahora envía el flyer como una foto para continuar."
    )


def parse_linked_event_input(raw_text, display_order, event_group_id):
    """Parse one guided linked-event input line into a validated segment."""
    parts = [part.strip() for part in raw_text.split("|")]
    if len(parts) != 6:
        raise ValueError(
            "Usa: Título | Inicio | Fin (opcional) | Lugar | "
            "URL (opcional) | Relación (opcional)"
        )

    summary, start_text, end_text, location, url, relationship = parts
    if not summary or not start_text or not location:
        raise ValueError("Título, inicio y lugar son obligatorios.")

    try:
        event_start = datetime.fromisoformat(start_text)
    except ValueError as exc:
        raise ValueError("El inicio debe tener formato AAAA-MM-DD HH:MM.") from exc
    if event_start.tzinfo is None:
        event_start = event_start.replace(tzinfo=COFFEE_TIMEZONE)
    if event_start <= datetime.now(event_start.tzinfo):
        raise ValueError("La fecha del evento debe estar en el futuro.")

    event_end = None
    if end_text:
        try:
            event_end = datetime.fromisoformat(end_text)
        except ValueError as exc:
            raise ValueError("El fin debe tener formato AAAA-MM-DD HH:MM.") from exc
        if event_end.tzinfo is None:
            event_end = event_end.replace(tzinfo=event_start.tzinfo)

    return build_linked_event(
        event_id=f"{event_group_id}-event-{display_order + 1}",
        summary=summary,
        event_start=event_start.isoformat(),
        event_end=event_end.isoformat() if event_end else None,
        location=location,
        url=url,
        relationship=relationship,
        display_order=display_order,
    )


async def linked_promo(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id):
    """Start the guided administrator workflow for a linked-event promo."""
    if update.effective_user.id != admin_id:
        return

    draft_id = f"linked-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
    context.user_data["linked_promo_draft"] = {
        "event_group_id": draft_id,
        "events": [],
        "phase": "events",
    }
    await update.message.reply_text(
        "✅ Promo de eventos relacionados iniciado.\n\n"
        "Envía cada evento en una línea con este formato:\n"
        "Título | AAAA-MM-DD HH:MM | AAAA-MM-DD HH:MM (opcional) | "
        "Lugar | URL (opcional) | Relación (opcional)\n\n"
        "Ejemplo:\n"
        "Forks Meetup | 2026-10-20 18:00 | 2026-10-20 20:00 | "
        "Community Space | https://ejemplo.com |\n\n"
        "Agrega al menos dos eventos y después escribe /linkedpromodone.\n"
        "Para cancelar: /linkedpromocancel"
    )


async def linked_promo_done(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id):
    """Move a linked-event draft from event entry to publication text."""
    if update.effective_user.id != admin_id:
        return

    draft = context.user_data.get("linked_promo_draft")
    if not draft:
        await update.message.reply_text("❌ No hay un promo de eventos relacionados activo.")
        return
    if len(draft["events"]) < 2:
        await update.message.reply_text("❌ Agrega al menos dos eventos antes de continuar.")
        return

    try:
        draft["events"] = validate_linked_events(draft["events"])
    except ValueError as exc:
        await update.message.reply_text(f"❌ {exc}")
        return

    draft["phase"] = "text"
    await update.message.reply_text(
        "✅ Eventos guardados. Ahora envía el texto de publicación.\n"
        "Puedes explicar la relación entre los eventos y añadir hashtags."
    )


async def linked_promo_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id):
    """Cancel the active linked-event administrator workflow."""
    if update.effective_user.id != admin_id:
        return

    removed = context.user_data.pop("linked_promo_draft", None)
    await update.message.reply_text(
        "🗑 Promo de eventos relacionados cancelado."
        if removed
        else "No hay un promo de eventos relacionados activo."
    )


async def handle_linked_promo_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id,
):
    """Handle one event line or the publication text in a linked-event draft."""
    if update.effective_user.id != admin_id:
        return

    draft = context.user_data.get("linked_promo_draft")
    if not draft or not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if draft["phase"] == "events":
        try:
            event = parse_linked_event_input(
                text,
                len(draft["events"]),
                draft["event_group_id"],
            )
        except ValueError as exc:
            await update.message.reply_text(f"❌ {exc}")
            return
        draft["events"].append(event)
        await update.message.reply_text(
            f"✅ Evento {len(draft['events'])} guardado: {escape(event['summary'])}.\n"
            "Agrega otro evento o escribe /linkedpromodone.",
            parse_mode=ParseMode.HTML,
        )
        return

    if draft["phase"] != "text":
        return
    if not text:
        await update.message.reply_text("❌ El texto de publicación no puede estar vacío.")
        return

    events = validate_linked_events(draft["events"])
    first_event = events[0]
    promo = build_manual_promo(
        draft["event_group_id"],
        " + ".join(event["summary"] for event in events),
        first_event["event_start"],
        text,
        "",
        location=first_event["location"],
        linked_events=events,
    )
    draft.clear()
    draft.update(promo)
    draft["phase"] = "photo"
    context.user_data["manual_promo_draft"] = draft
    context.user_data.pop("linked_promo_draft", None)
    await update.message.reply_text(
        "✅ Texto guardado. Ahora envía el flyer como una foto para continuar."
    )


async def handle_manual_promo_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id,
    state,
    config,
):
    """Store the admin's uploaded flyer and show a confirmation preview."""
    if update.effective_user.id != admin_id:
        return

    draft = context.user_data.get("manual_promo_draft")
    if not draft:
        return

    image_file_id = update.message.photo[-1].file_id
    promo = build_manual_promo(
        draft["event_id"],
        draft["summary"],
        draft["event_start"],
        draft["text"],
        image_file_id,
        location=draft["location"],
        linked_events=draft.get("linked_events"),
    )
    promo["status"] = "pending"
    state.setdefault("manual_promos", {})[draft["event_id"]] = promo
    save_manual_promos(config["MANUAL_PROMOS_FILE"], state["manual_promos"])
    context.user_data.pop("manual_promo_draft", None)

    caption = format_manual_promo_preview(promo)
    keyboard = [[
        InlineKeyboardButton(
            "🧪 Enviar prueba",
            callback_data=f"manual_test_{draft['event_id']}",
        ),
    ], [
        InlineKeyboardButton(
            "✅ Enviar ahora",
            callback_data=f"manual_send_now_{draft['event_id']}",
        ),
        InlineKeyboardButton(
            "🕒 Programar primer aviso",
            callback_data=f"manual_schedule_{draft['event_id']}",
        ),
    ], [
        InlineKeyboardButton(
            "🗑 Cancelar",
            callback_data=f"manual_cancel_{draft['event_id']}",
        ),
    ]]
    await update.message.reply_photo(
        photo=image_file_id,
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def format_manual_promo_delivery_result(result):
    """Format DM and group delivery counts for an admin confirmation."""
    return (
        "DM enviados: {subscribers_sent}\n"
        "Grupos enviados: {groups_sent}\n"
        "Fallos en DMs: {subscriber_failures}\n"
        "Fallos en grupos: {group_failures}"
    ).format(**result)


async def handle_manual_promo_send_now(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id,
    state,
    config,
):
    """Send a staged manual promo immediately to DMs and groups."""
    query = update.callback_query
    if query.from_user.id != admin_id:
        return

    await query.answer()
    promo_id = query.data.removeprefix("manual_send_now_")
    promo = state.setdefault("manual_promos", {}).get(promo_id)
    if not promo:
        await query.edit_message_caption("❌ El promo manual ya no existe.")
        return

    now = datetime.now(MANUAL_PROMO_TIMEZONE)
    promo["status"] = "active"
    set_first_notification_schedule(promo, now)
    stage = get_manual_promo_stage(
        promo,
        now,
        config.get("REMINDER_RULES", {}),
        config.get("EVENT_SAME_DAY_REMINDER_HOUR", 13),
        config.get("EVENT_MORNING_REMINDER_LEAD_HOURS", 2),
    ) or "initial"
    result = await deliver_manual_promo(
        context,
        promo,
        state.setdefault("subscribers", set()),
        state.setdefault("groups", set()),
        stage,
        is_first_notification=True,
        community_group_url=config.get("COMMUNITY_GROUP_URL", ""),
        group_notification_categories=state.get("group_notification_categories"),
    )
    save_manual_promos(config["MANUAL_PROMOS_FILE"], state["manual_promos"])
    await query.edit_message_caption(
        f"✅ Primera notificación enviada: {escape(promo['summary'])}\n\n"
        f"{format_manual_promo_delivery_result(result)}",
        parse_mode=ParseMode.HTML,
    )


async def handle_manual_promo_test(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id,
    state,
    config=None,
):
    """Send a staged manual promo test only to the administrator's DM."""
    query = update.callback_query
    if query.from_user.id != admin_id:
        return

    await query.answer()
    promo_id = query.data.removeprefix("manual_test_")
    promo = state.setdefault("manual_promos", {}).get(promo_id)
    if not promo:
        await query.edit_message_caption("❌ El promo manual ya no existe.")
        return

    result = await deliver_manual_promo_test(
        context,
        promo,
        admin_id,
        (config or {}).get("COMMUNITY_GROUP_URL", ""),
    )
    if result["failures"]:
        message = "❌ No se pudo enviar la prueba a tu DM."
    else:
        message = (
            f"🧪 <b>MODO PRUEBA</b>\n\n"
            f"✅ Prueba enviada solo a tu DM: {escape(promo['summary'])}"
        )
    await query.edit_message_caption(message, parse_mode=ParseMode.HTML)


async def handle_manual_promo_schedule(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id,
    state,
):
    """Ask the admin for the first-notification date and time."""
    query = update.callback_query
    if query.from_user.id != admin_id:
        return

    await query.answer()
    promo_id = query.data.removeprefix("manual_schedule_")
    if promo_id not in state.setdefault("manual_promos", {}):
        await query.edit_message_caption("❌ El promo manual ya no existe.")
        return

    context.user_data["manual_promo_schedule_id"] = promo_id
    await query.edit_message_caption(
        "🕒 Escribe la fecha y hora del primer aviso en formato "
        "AAAA-MM-DD HH:MM (hora de Guadalajara).",
    )


async def handle_manual_promo_schedule_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id,
    state,
    config,
):
    """Validate and persist a scheduled first notification from admin text."""
    if update.effective_user.id != admin_id:
        return

    promo_id = context.user_data.get("manual_promo_schedule_id")
    if not promo_id:
        return

    promo = state.setdefault("manual_promos", {}).get(promo_id)
    if not promo:
        context.user_data.pop("manual_promo_schedule_id", None)
        await update.message.reply_text("❌ El promo manual ya no existe.")
        return

    try:
        scheduled_for = datetime.fromisoformat(update.message.text.strip())
    except ValueError:
        await update.message.reply_text(
            "❌ Usa el formato AAAA-MM-DD HH:MM para programar el primer aviso."
        )
        return

    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=MANUAL_PROMO_TIMEZONE)
    now = datetime.now(MANUAL_PROMO_TIMEZONE)
    event_start = datetime.fromisoformat(
        str(promo["event_start"]).replace("Z", "+00:00")
    )
    if event_start.tzinfo is None:
        event_start = event_start.replace(tzinfo=MANUAL_PROMO_TIMEZONE)

    if scheduled_for <= now:
        await update.message.reply_text("❌ El primer aviso debe estar en el futuro.")
        return
    if scheduled_for >= event_start:
        await update.message.reply_text(
            "❌ El primer aviso debe ser antes del inicio del evento."
        )
        return

    promo["status"] = "active"
    set_first_notification_schedule(promo, scheduled_for)
    save_manual_promos(config["MANUAL_PROMOS_FILE"], state["manual_promos"])
    context.user_data.pop("manual_promo_schedule_id", None)
    await update.message.reply_text(
        f"✅ Primer aviso programado para {scheduled_for.strftime('%d/%m/%Y %H:%M')} "
        "(hora de Guadalajara).\n"
        "Se enviará a los suscriptores y grupos configurados."
    )


async def handle_manual_promo_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id,
    state,
    config,
):
    """Activate a legacy staged promo without sending an immediate notification."""
    query = update.callback_query
    if query.from_user.id != admin_id:
        return

    await query.answer()
    promo_id = query.data.removeprefix("manual_confirm_")
    promo = state.setdefault("manual_promos", {}).get(promo_id)
    if not promo:
        await query.edit_message_caption("❌ El promo manual ya no existe.")
        return

    promo["status"] = "active"
    save_manual_promos(config["MANUAL_PROMOS_FILE"], state["manual_promos"])
    await query.edit_message_caption(
        f"✅ Promo activado para recordatorios: {escape(promo['summary'])}",
        parse_mode=ParseMode.HTML,
    )


async def handle_manual_promo_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    admin_id,
    state,
    config,
):
    """Delete a manually staged promo after admin cancellation."""
    query = update.callback_query
    if query.from_user.id != admin_id:
        return

    await query.answer()
    promo_id = query.data.removeprefix("manual_cancel_")
    promo = state.setdefault("manual_promos", {}).pop(promo_id, None)
    if promo:
        save_manual_promos(config["MANUAL_PROMOS_FILE"], state["manual_promos"])
        await query.edit_message_caption("🗑 Promo manual eliminado.")
    else:
        await query.edit_message_caption("❌ El promo manual ya no existe.")


def format_coffee_host_schedule(schedule, hosts, reference_date):
    """Format upcoming Saturday assignments for the admin command."""
    lines = ["☕ <b>Próximos anfitriones del café</b>"]
    for saturday in upcoming_saturdays(reference_date):
        date_key = saturday.isoformat()
        assignment = schedule.get(date_key)
        if not assignment:
            lines.append(f"\n<b>{saturday.strftime('%d/%m/%Y')}</b>: Sin asignar")
            continue

        host_names = [
            hosts.get(host_id, {}).get("name", host_id)
            for host_id in assignment.get("hosts", [])
        ]
        host_text = ", ".join(escape(name) for name in host_names) or "Sin anfitriones"
        venue = escape(assignment.get("venue") or "Lugar pendiente")
        start_time = escape(assignment.get("start_time") or "Hora pendiente")
        lines.append(
            f"\n<b>{saturday.strftime('%d/%m/%Y')}</b>: {host_text}"
            f"\n📍 {venue} · ⏰ {start_time}"
        )
        if assignment.get("notes"):
            lines.append(f"\n📝 {escape(assignment['notes'])}")

    active_hosts = [
        f"<code>{escape(host_id)}</code> - {escape(data.get('name', host_id))}"
        for host_id, data in hosts.items()
        if isinstance(data, dict) and data.get("active", True)
    ]
    inactive_hosts = [
        f"<code>{escape(host_id)}</code> - {escape(data.get('name', host_id))}"
        for host_id, data in hosts.items()
        if isinstance(data, dict) and not data.get("active", True)
    ]
    lines.append("\n<b>Anfitriones activos:</b>")
    lines.extend(active_hosts or ["Ninguno configurado"])
    lines.append("\n<b>Anfitriones inactivos:</b>")
    lines.extend(inactive_hosts or ["Ninguno"])
    lines.append(
        "\nUso: <code>/coffeehost add | Nombre | Telegram ID</code>"
        "\n<code>/coffeehost remove | host-id</code>"
        "\n<code>/coffeehost AAAA-MM-DD host-001,host-002 | Lugar | 11:00 | Notas</code>"
    )
    lines.append("Para borrar una asignación: <code>/coffeehost AAAA-MM-DD clear</code>")
    return "".join(lines)


async def coffee_host(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config):
    """View or update Saturday coffee host assignments."""
    if update.effective_user.id != admin_id:
        return

    raw_args = update.message.text.partition(" ")[2].strip()
    schedule = state.setdefault("coffee_host_schedule", {})
    hosts = state.setdefault("coffee_hosts", {})

    if not raw_args:
        today = datetime.now(COFFEE_TIMEZONE).date()
        await update.message.reply_text(
            format_coffee_host_schedule(schedule, hosts, today),
            parse_mode=ParseMode.HTML,
        )
        return

    command_parts = [part.strip() for part in raw_args.split("|")]
    action = command_parts[0].lower()

    if action == "add":
        if len(command_parts) != 3 or not command_parts[1] or not command_parts[2]:
            await update.message.reply_text(
                "Uso: /coffeehost add | Nombre | Telegram ID"
            )
            return

        try:
            telegram_id = int(command_parts[2])
        except ValueError:
            await update.message.reply_text("❌ El Telegram ID debe ser numérico.")
            return

        if telegram_id <= 0:
            await update.message.reply_text("❌ El Telegram ID debe ser positivo.")
            return

        if any(
            isinstance(host, dict) and host.get("telegram_id") == telegram_id
            for host in hosts.values()
        ):
            await update.message.reply_text("❌ Ese Telegram ID ya está registrado.")
            return

        host_id = next_coffee_host_id(hosts)
        hosts[host_id] = build_coffee_host(command_parts[1], telegram_id)
        save_coffee_hosts(config["COFFEE_HOSTS_FILE"], hosts)
        await update.message.reply_text(
            f"✅ Anfitrión agregado: {host_id} - {escape(command_parts[1])}",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "remove":
        if len(command_parts) != 2 or not command_parts[1]:
            await update.message.reply_text("Uso: /coffeehost remove | host-id")
            return

        host_id = command_parts[1]
        host = hosts.get(host_id)
        if not isinstance(host, dict):
            await update.message.reply_text(f"❌ Anfitrión no encontrado: {host_id}")
            return

        if not host.get("active", True):
            await update.message.reply_text(f"ℹ️ El anfitrión ya estaba inactivo: {host_id}")
            return

        host["active"] = False
        save_coffee_hosts(config["COFFEE_HOSTS_FILE"], hosts)
        await update.message.reply_text(f"✅ Anfitrión desactivado: {host_id}")
        return

    assignment_parts = command_parts[0].split()
    if len(assignment_parts) < 2:
        await update.message.reply_text(
            "Uso: /coffeehost AAAA-MM-DD host-001,host-002 | Lugar | 11:00 | Notas"
        )
        return

    date_key = assignment_parts[0]
    try:
        saturday = date.fromisoformat(date_key)
    except ValueError:
        await update.message.reply_text("❌ La fecha debe tener formato AAAA-MM-DD.")
        return

    if saturday.weekday() != 5:
        await update.message.reply_text("❌ La fecha debe ser un sábado.")
        return

    host_ids = [host_id.strip() for host_id in assignment_parts[1].split(",") if host_id.strip()]
    if len(host_ids) == 1 and host_ids[0].lower() == "clear":
        if date_key in schedule:
            del schedule[date_key]
            save_json(config["COFFEE_HOST_SCHEDULE_FILE"], schedule)
        await update.message.reply_text(f"✅ Asignación eliminada para {date_key}.")
        return

    missing_hosts = [
        host_id for host_id in host_ids
        if host_id not in hosts or not hosts[host_id].get("active", True)
    ]
    if missing_hosts:
        missing = ", ".join(missing_hosts)
        await update.message.reply_text(f"❌ Anfitriones no disponibles: {missing}")
        return

    venue = command_parts[1] if len(command_parts) > 1 and command_parts[1] else config["COFFEE_DEFAULT_VENUE"]
    start_time = command_parts[2] if len(command_parts) > 2 and command_parts[2] else config["COFFEE_DEFAULT_START_TIME"]
    notes = command_parts[3] if len(command_parts) > 3 else ""
    schedule[date_key] = build_coffee_assignment(host_ids, venue, start_time, notes)
    save_json(config["COFFEE_HOST_SCHEDULE_FILE"], schedule)
    immediate_result = await send_immediate_coffee_host_reminder(
        context,
        config,
        state,
        date_key,
    )
    disabled_hosts = []
    notification_preferences = state.get("notification_preferences")
    if notification_preferences is not None:
        for host_id in host_ids:
            telegram_id = hosts[host_id].get("telegram_id")
            if not is_notification_enabled(
                notification_preferences,
                telegram_id,
                "coffee",
            ):
                disabled_hosts.append(hosts[host_id].get("name", host_id))

    warning = ""
    if disabled_hosts:
        warning = (
            "\n⚠️ Notificaciones de café desactivadas para: "
            f"{', '.join(disabled_hosts)}. Deben activarlas en /suscripciones."
        )
    delivery_note = ""
    if immediate_result["sent"]:
        delivery_note = "\n📨 Recordatorio inmediato enviado."
    elif immediate_result["failed"]:
        delivery_note = "\n⚠️ No se pudo enviar el recordatorio inmediato."
    elif immediate_result["skipped"]:
        delivery_note = "\nℹ️ El recordatorio inmediato no se envió a los anfitriones omitidos."
    await update.message.reply_text(
        f"✅ Anfitriones asignados para {date_key}: {', '.join(host_ids)}\n"
        f"📍 {escape(venue)} · ⏰ {escape(start_time)}{warning}{delivery_note}",
        parse_mode=ParseMode.HTML,
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


def _has_configured_openai_key(value):
    """Return whether an OpenAI key value is non-empty and not a template placeholder."""
    normalized = str(value or "").strip().lower()
    return bool(normalized) and not (
        normalized.startswith("your_")
        or normalized in {"changeme", "replace_me", "placeholder"}
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state, config, status_msg=None):
    """
    Admin-only command to show the bot's current status and configuration.
    """
    if update.effective_user.id != admin_id: return
    calendar_enabled = config.get("ENABLE_CALENDAR_POLLING", True)
    coffee_enabled = config.get("ENABLE_COFFEE_HOST_REMINDERS", True)
    manual_promos_enabled = config.get("ENABLE_MANUAL_PROMO_REMINDERS", True)
    payment_enabled = config.get("ENABLE_PAYMENT_REMINDERS", True)
    payment_group_configured = bool(config.get("MEMBERS_GROUP_ID", 0))
    same_day_hour = config.get("EVENT_SAME_DAY_REMINDER_HOUR", 13)
    morning_lead_hours = config.get("EVENT_MORNING_REMINDER_LEAD_HOURS", 2)
    payment_hour = config.get("PAYMENT_REMINDER_HOUR", 9)
    payment_state = state.get("payment_state", {})
    payment_period = payment_state.get("period", "Sin periodo")
    coworking_enabled = config.get("ENABLE_COWORKING_NOTIFICATIONS", True)
    coworking_weekday = config.get("COWORKING_WEEKDAY", 2)
    coworking_hour = config.get("COWORKING_HOUR", 10)
    coworking_quiet_hours = config.get("COWORKING_QUIET_WINDOW_HOURS", 24)
    coworking_state = state.get("coworking", {})
    coworking_last_sent = coworking_state.get("last_sent_week", "Nunca")
    msg = (
        f"📊 <b>BTC GDL Bot Status</b>\n\n"
        f"👥 <b>Subscribers:</b> {len(state['subscribers'])}\n"
        f"📢 <b>Broadcast Groups:</b> {len(state['groups'])}\n"
        f"📝 <b>Pending Promos:</b> {len(state['pending_promos'])}\n\n"
        f"🤖 <b>OpenAI:</b> {'✅ Configured' if _has_configured_openai_key(config.get('OPENAI_API_KEY')) else '❌ Missing or placeholder key'}\n"
        f"📅 <b>Calendar polling:</b> {'✅ Enabled' if calendar_enabled else '⛔ Disabled'}\n"
        f"☕ <b>Coffee reminders:</b> {'✅ Enabled' if coffee_enabled else '⛔ Disabled'}\n"
        f"📰 <b>Manual promo reminders:</b> {'✅ Enabled' if manual_promos_enabled else '⛔ Disabled'}\n"
        f"💳 <b>Payment reminders:</b> {'✅ Enabled' if payment_enabled else '⛔ Disabled'}\n"
        f"🏠 <b>Payment group:</b> {'✅ Configured' if payment_group_configured else '❌ Missing'}\n"
        f"👤 <b>Payment members:</b> {len(state.get('members', {}))}\n"
        f"📆 <b>Payment period:</b> {escape(str(payment_period))}\n"
        f"🕘 <b>Payment reminder hour:</b> {payment_hour:02d}:00\n"
        f"🏠 <b>Coworking invitations:</b> {'✅ Enabled' if coworking_enabled else '⛔ Disabled'}\n"
        f"📅 <b>Coworking slot:</b> weekday {coworking_weekday}, {coworking_hour:02d}:00\n"
        f"🔕 <b>Coworking quiet window:</b> {coworking_quiet_hours} hours\n"
        f"📤 <b>Last coworking send:</b> {escape(str(coworking_last_sent))}\n"
        f"🔔 <b>Same-day event reminder:</b> {same_day_hour:02d}:00\n"
        f"🌅 <b>Morning-event fallback:</b> {morning_lead_hours} hours before\n"
        f"⏱ <b>Check Interval:</b> {config.get('CHECK_INTERVAL_MINUTES')} min"
    )
    if status_msg:
        await status_msg.edit_text(msg, parse_mode=ParseMode.HTML)
    else:
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

    groups_sent = 0
    subscribers_sent = 0

    # Groups
    for group_id in state['groups']:
        try:
            if isinstance(image, BytesIO): image.seek(0)
            await context.bot.send_photo(group_id, photo=image, caption=text, parse_mode=ParseMode.HTML)
            groups_sent += 1
        except Exception as e:
            logger.error(f"Failed to publish to group {group_id}: {e}")
    
    # Individual Subscribers
    event_subscribers = filter_users_by_category(
        state['subscribers'],
        state.get('notification_preferences'),
        "events",
    )
    for user_id in event_subscribers:
        try:
            if isinstance(image, BytesIO): image.seek(0)
            await context.bot.send_photo(user_id, photo=image, caption=text, parse_mode=ParseMode.HTML)
            subscribers_sent += 1
        except Exception as e:
            logger.error(f"Failed to publish to subscriber {user_id}: {e}")

    await query.edit_message_caption(
        f"✅ Sent to {groups_sent} groups and {subscribers_sent} subscribers."
    )
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

async def delete_event(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config, status_msg=None):
    """
    Admin-only command to list events and choose one for deletion.
    """
    if update.effective_user.id != admin_id: return
    
    if status_msg:
        await status_msg.edit_text("🔍 Buscando eventos próximos para eliminar...")
    else:
        status_msg = await update.message.reply_text("🔍 Buscando eventos próximos para eliminar...")

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
        await status_msg.edit_text("¿Qué evento quieres ELIMINAR del Google Calendar?", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Delete List Error: {e}")
        await status_msg.edit_text(f"❌ Error: {e}")

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

async def edit_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config, status_msg=None):
    """
    Initiates the interactive editing workflow for a calendar event.
    """
    if update.effective_user.id != admin_id: return
    
    if status_msg:
        await status_msg.edit_text("🔍 Selecciona un evento para editar sus detalles:")
    else:
        status_msg = await update.message.reply_text("🔍 Selecciona un evento para editar sus detalles:")

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
        await status_msg.edit_text("Elige el evento que quieres cambiar:", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e: await status_msg.edit_text(f"❌ Error: {e}")

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

async def add_event(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, forced_text=None, status_msg=None):
    """
    Admin-only command to create a new calendar event using AI parsing.
    """
    if update.effective_user.id != admin_id: return
    if not forced_text and not context.args:
        await update.message.reply_text("Uso: /addevent [descripción del evento]\nEjemplo: /addevent Meetup este viernes a las 6pm en Starbucks")
        return
    desc = forced_text if forced_text else " ".join(context.args)
    
    if status_msg:
        await status_msg.edit_text("⏳ Procesando detalles y buscando dirección... Por favor espera.")
    else:
        status_msg = await update.message.reply_text("⏳ Procesando detalles y buscando dirección... Por favor espera.")
    
    current_date = datetime.now().strftime("%Y-%m-%d")
    prompt = EVENT_PARSER_SYSTEM_PROMPT.format(current_date=current_date)
    res = await ai_service.generate_event_promo(desc, prompt)
    if not res: 
        await status_msg.edit_text("❌ Falló la generación del evento.")
        return
    try:
        data = json.loads(res)
        context.user_data['pending_event'] = data
        summary = (
            f"📅 <b>CONFIRMAR NUEVO EVENTO</b>\n\n"
            f"<b>Título:</b> {escape(data['summary'])}\n"
            f"<b>Inicio:</b> {escape(data['start']['dateTime'])}\n"
            f"<b>📍 Ubicación confirmada:</b> {escape(data.get('location', 'N/A'))}\n\n"
            f"¿Confirmar creación en Google Calendar?"
        )
        keyboard = [[InlineKeyboardButton("✅ Confirmar", callback_data="confirm_add"), InlineKeyboardButton("❌ Cancelar", callback_data="cancel_add")]]
        await status_msg.edit_text(summary, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Add Event Parse Error: {e}")
        await status_msg.edit_text("❌ Error al procesar los detalles del evento.")

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
    try:
        parsed_start = datetime.fromisoformat(start_raw)
        storage_start = parsed_start.strftime("%Y%m%dT%H%M%SZ")
    except (TypeError, ValueError):
        storage_start = start_raw[:10].replace("-", "")
    storage_key = f"{eid}_{storage_start}"
    
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
            save_prompt_history(config, event, prmpt)
            
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
                    "storage_key": storage_key,
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
        if not staged.get("event_info"):
            await update.message.reply_text(
                "❌ This older draft cannot be edited because its event data is missing."
            )
            return
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

async def draft(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, config, status_msg=None):
    """
    Manual /draft command to pick an event and generate marketing content.
    """
    if update.effective_user.id != admin_id: return
    logger.info(f"Admin {admin_id} requested manual draft list.")
    
    if status_msg:
        await status_msg.edit_text("🔍 Buscando eventos próximos en el calendario...")
    else:
        status_msg = await update.message.reply_text("🔍 Buscando eventos próximos en el calendario...")

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
        await status_msg.edit_text("¿Para qué evento quieres generar un borrador?", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e: await status_msg.edit_text(f"❌ Error: {e}")

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

async def handle_admin_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config, forced_text=None, status_msg=None):
    """
    Handles natural language messages from the Admin (no slash required).
    Determines intent (Add/Edit/Delete/Draft) using AI.
    """
    text = forced_text or (update.message.text if update.message else None)
    
    if update.effective_user.id != admin_id or not text:
        return

    if context.user_data.get("manual_promo_schedule_id"):
        await handle_manual_promo_schedule_input(
            update,
            context,
            admin_id,
            state,
            config,
        )
        return

    if context.user_data.get("linked_promo_draft"):
        await handle_linked_promo_input(update, context, admin_id)
        return

    # Replies go to refinement logic (only for text messages, not forced voice text)
    if not forced_text and update.message and update.message.reply_to_message:
        await handle_admin_reply(update, context, admin_id, ai_service, state, config)
        return

    if not status_msg:
        status_msg = await update.message.reply_text("🤔 Entendiendo tu solicitud... Por favor espera.")
    else:
        await status_msg.edit_text("🤔 Entendiendo tu solicitud... Por favor espera.")
    
    res = await ai_service.generate_event_promo(text, INTENT_DETECTOR_PROMPT)
    if not res:
        await status_msg.edit_text("❌ No pude entender tu solicitud. Intenta con un comando /helpadmin.")
        return

    try:
        intent_data = json.loads(res)
        intent = intent_data.get("intent", "UNKNOWN")
        logger.info(f"AI detected admin intent: {intent}")
        
        # TTS logic: If input was voice, read out the status
        if forced_text:
            voice_response = f"Entendido. Detecté que quieres {intent.lower().replace('_', ' ')}. Procesando..."
            audio = await ai_service.generate_speech(voice_response)
            if audio:
                await update.message.reply_voice(voice=audio)

        if intent == "ADD_EVENT":
            await add_event(update, context, admin_id, ai_service, forced_text=text, status_msg=status_msg)
        elif intent == "DELETE_EVENT":
            await delete_event(update, context, admin_id, config, status_msg=status_msg)
        elif intent == "EDIT_EVENT":
            await edit_event_start(update, context, admin_id, config, status_msg=status_msg)
        elif intent == "DRAFT_PROMO":
            await draft(update, context, admin_id, config, status_msg=status_msg)
        elif intent == "STATUS":
            await status(update, context, admin_id, state, config, status_msg=status_msg)
        else:
            await status_msg.edit_text("❓ No estoy seguro de qué quieres hacer. ¿Quieres agregar, editar o borrar un evento?")
            
    except Exception as e:
        logger.error(f"Intent Error: {e}")
        await status_msg.edit_text("❌ Error al procesar tu mensaje de chat.")

async def handle_voice_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, ai_service, state, config):
    """
    Downloads and transcribes voice notes from the admin, then routes them to the chat handler.
    """
    if update.effective_user.id != admin_id or not update.message.voice:
        return

    # Inform the user we're listening
    await update.message.reply_chat_action("record_voice")
    
    # Status message to be edited later
    status_msg = await update.message.reply_text("🎤 Escuchando tu audio...")
    
    try:
        # Download the voice file
        voice_file = await update.message.voice.get_file()
        audio_buffer = BytesIO()
        await voice_file.download_to_memory(out=audio_buffer)
        audio_buffer.seek(0)
        audio_buffer.name = "voice.ogg" # Whisper needs an extension to infer format

        # Transcribe
        text = await ai_service.transcribe_voice(audio_buffer)
        
        if not text or len(text.strip()) < 2:
            await status_msg.edit_text("❌ No pude entender el audio. ¿Podrías repetirlo o escribirlo?")
            return

        # Update status and process
        await status_msg.edit_text(f"📝 <i>Transripción:</i> \"{text}\"", parse_mode=ParseMode.HTML)
        
        # Process via handle_admin_chat using the forced_text parameter
        await handle_admin_chat(update, context, admin_id, ai_service, state, config, forced_text=text, status_msg=status_msg)

    except Exception as e:
        logger.error(f"Voice Processing Error: {e}")
        await status_msg.edit_text("❌ Hubo un problema al procesar tu mensaje de voz.")

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
        metadata = state.setdefault(
            "telegram_metadata",
            {"users": {}, "groups": {}},
        )
        record_group_metadata(metadata, chat)
        if config.get("TELEGRAM_METADATA_FILE"):
            save_telegram_metadata(config["TELEGRAM_METADATA_FILE"], metadata)
        await update.message.reply_text(f"✅ Grupo '{chat.title}' añadido a la lista de difusión.")
        logger.info(f"Group added: {chat.title} ({chat.id})")

async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state):
    """List registered groups with readable metadata and canonical IDs."""
    if update.effective_user.id != admin_id:
        return
    groups = state.get("groups", set())
    if not groups:
        await update.message.reply_text("No hay grupos configurados.")
        return

    metadata = state.get("telegram_metadata", {})
    group_metadata = metadata.get("groups", {}) if isinstance(metadata, dict) else {}
    if not isinstance(group_metadata, dict):
        group_metadata = {}
    lines = []
    for group_id in sorted(groups, key=str):
        record = group_metadata.get(str(group_id), {})
        if not isinstance(record, dict):
            record = {}
        display_name = record.get("display_name", "Grupo sin nombre")
        username = record.get("username", "")
        line = f"• <b>{escape(str(display_name))}</b>"
        if username:
            line += f" (@{escape(str(username))})"
        line += f"\n  ID: <code>{escape(str(group_id))}</code>"
        lines.append(line)

    msg = "📢 <b>Grupos de difusión:</b>\n\n" + "\n".join(lines)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE, admin_id, state):
    """List known Telegram users with subscription status and canonical IDs."""
    if update.effective_user.id != admin_id:
        return

    metadata = state.get("telegram_metadata", {})
    user_metadata = metadata.get("users", {}) if isinstance(metadata, dict) else {}
    if not isinstance(user_metadata, dict):
        user_metadata = {}
    user_ids = set(user_metadata)
    user_ids.update(str(user_id) for user_id in state.get("subscribers", set()))
    if not user_ids:
        await update.message.reply_text("No hay usuarios registrados.")
        return

    subscribed_ids = {str(user_id) for user_id in state.get("subscribers", set())}
    lines = []
    for user_id in sorted(user_ids, key=str):
        record = user_metadata.get(user_id, {})
        if not isinstance(record, dict):
            record = {}
        display_name = record.get("display_name", "Usuario sin nombre")
        username = record.get("username", "")
        status = "suscrito" if user_id in subscribed_ids else "no suscrito"
        line = f"• <b>{escape(str(display_name))}</b>"
        if username:
            line += f" (@{escape(str(username))})"
        line += (
            f"\n  ID: <code>{escape(str(user_id))}</code>"
            f" · {status}"
        )
        lines.append(line)

    msg = "👤 <b>Usuarios conocidos:</b>\n\n" + "\n".join(lines)
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
