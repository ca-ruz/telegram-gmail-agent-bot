import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import user
from bot.interface import get_main_keyboard, get_main_menu_text
from tools.local.data_manager import load_json


def make_update(user_id=42, callback_data=None, photo=None):
    """Build the minimal Telegram update shape used by the user handlers."""
    message = SimpleNamespace(
        photo=photo or [],
        chat_id=123,
        reply_text=AsyncMock(),
        delete=AsyncMock(),
    )
    query = SimpleNamespace(
        data=callback_data,
        from_user=SimpleNamespace(id=user_id, username="test-user"),
        message=message,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_caption=AsyncMock(),
    )
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, username="test-user"),
        effective_chat=SimpleNamespace(id=123),
        message=message,
        callback_query=query,
    )


def test_main_menu_explains_public_actions():
    """The public menu explains what each button does."""
    menu_text = get_main_menu_text()
    button_texts = {
        button.text
        for row in get_main_keyboard().inline_keyboard
        for button in row
    }

    assert "Suscripciones" in menu_text
    assert "Bitcoin & Lightning GDL" in menu_text
    assert "Meetup" in menu_text
    assert "Eventos" in menu_text
    assert "Bitdevs" in menu_text
    assert "Calendario del mes" in button_texts
    assert "Próximos eventos" in button_texts
    assert "Web" in button_texts


def test_main_menu_uses_localized_callback_names():
    """Public menu buttons use the Spanish command callback names."""
    buttons = {
        button.text: button
        for row in get_main_keyboard().inline_keyboard
        for button in row
    }

    assert buttons["Eventos"].callback_data == "eventos"
    assert buttons["Web"].callback_data == "web"


def test_main_menu_shows_private_coffee_action_only_to_eligible_users():
    """The coffee action is omitted for regular users and shown to hosts."""
    regular_buttons = {
        button.text
        for row in get_main_keyboard().inline_keyboard
        for button in row
    }
    host_buttons = {
        button.text
        for row in get_main_keyboard(show_coffee=True).inline_keyboard
        for button in row
    }

    assert "☕ Café" not in regular_buttons
    assert "☕ Café" in host_buttons


def test_coffee_schedule_handler_rejects_unassigned_user():
    """Users who are not hosts cannot access the private coffee schedule."""
    update = make_update(user_id=789)

    asyncio.run(
        user.coffee_schedule(
            update,
            SimpleNamespace(),
            admin_id=42,
            coffee_hosts={
                "host-001": {"telegram_id": 123, "active": True},
            },
            coffee_host_schedule={},
        )
    )

    assert "solo para" in update.message.reply_text.await_args.args[0]


def test_start_sends_welcome_once_and_persists_user(tmp_path):
    """Only the first /start response is recorded as a welcome."""
    welcomed_file = tmp_path / "welcomed_users.json"
    update = make_update()

    with patch.object(user, "WELCOMED_USERS_FILE", str(welcomed_file)):
        with patch.object(user.os.path, "exists", return_value=False):
            asyncio.run(user.start(update, SimpleNamespace()))
            asyncio.run(user.start(update, SimpleNamespace()))

    assert update.message.reply_text.await_count == 2
    assert load_json(str(welcomed_file), []) == [42]
    first_text = update.message.reply_text.await_args_list[0].args[0]
    assert "Bitcoin & Lightning GDL" in first_text
    assert "Suscripciones" in first_text


def test_subscribe_reports_explicit_notification_scope(tmp_path):
    """New subscriptions report every enabled notification category."""
    subscribers_file = tmp_path / "subscribers.json"
    subscribers = set()
    update = make_update(callback_data="subscribe")

    with patch.object(user, "SUBSCRIBERS_FILE", str(subscribers_file)):
        asyncio.run(user.button_click(update, SimpleNamespace(), subscribers))

    message = update.callback_query.edit_message_text.await_args.kwargs["text"]
    assert "Te suscribiste" in message
    assert "Eventos" in message
    assert "Recordatorios de café" in message
    assert "Avisos de membresía" in message
    assert subscribers == {42}


def test_subscribe_preserves_existing_specialized_opt_outs(tmp_path):
    """Subscribing again does not silently re-enable specialized categories."""
    subscribers_file = tmp_path / "subscribers.json"
    preferences_file = tmp_path / "notification_preferences.json"
    subscribers = {42}
    preferences = {
        "42": {"events": True, "coffee": False, "payments": False},
    }
    update = make_update(callback_data="subscribe")

    with patch.object(user, "SUBSCRIBERS_FILE", str(subscribers_file)):
        asyncio.run(
            user.button_click(
                update,
                SimpleNamespace(),
                subscribers,
                preferences,
                str(preferences_file),
            )
        )

    assert preferences["42"] == {
        "events": True,
        "coffee": False,
        "payments": False,
    }
    message = update.callback_query.edit_message_text.await_args.kwargs["text"]
    assert "Eventos" in message
    assert "Recordatorios de café" not in message
    assert "Avisos de membresía" not in message


def test_unsubscribe_reports_explicit_notification_scope(tmp_path):
    """Unsubscribe feedback names the event notification service."""
    subscribers_file = tmp_path / "subscribers.json"
    subscribers = {42}
    update = make_update(callback_data="unsubscribe")

    with patch.object(user, "SUBSCRIBERS_FILE", str(subscribers_file)):
        asyncio.run(user.button_click(update, SimpleNamespace(), subscribers))

    message = update.callback_query.edit_message_text.await_args.kwargs["text"]
    assert "Te diste de baja" in message
    assert "Bitcoin & Lightning GDL" in message
    assert subscribers == set()


def test_photo_link_button_deletes_image_and_sends_only_link():
    """Link buttons replace the welcome image with a clean link message."""
    update = make_update(callback_data="meetup", photo=[object()])
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    asyncio.run(user.button_click(update, context, set()))

    update.callback_query.message.delete.assert_awaited_once()
    context.bot.send_message.assert_awaited_once_with(
        chat_id=123,
        text="https://www.meetup.com/guadalajara-bitcoin-and-lightning/",
    )
    update.callback_query.edit_message_caption.assert_not_awaited()


def test_subscription_controls_toggle_categories_and_disable_all(tmp_path):
    """Users can toggle categories and disable every notification."""
    subscribers_file = tmp_path / "subscribers.json"
    preferences_file = tmp_path / "notification_preferences.json"
    subscribers = {42}
    preferences = {
        "42": {"events": True, "coffee": False, "payments": False},
    }
    context = SimpleNamespace()

    with patch.object(user, "SUBSCRIBERS_FILE", str(subscribers_file)):
        update = make_update(callback_data="subscription_toggle_coffee")
        asyncio.run(
            user.button_click(
                update,
                context,
                subscribers,
                preferences,
                str(preferences_file),
            )
        )

        assert preferences["42"]["coffee"] is True
        assert "Recordatorios de café" in update.callback_query.edit_message_text.await_args.kwargs["text"]

        update = make_update(callback_data="subscriptions_disable_all")
        asyncio.run(
            user.button_click(
                update,
                context,
                subscribers,
                preferences,
                str(preferences_file),
            )
        )

    assert preferences["42"] == {"events": False, "coffee": False, "payments": False}
    assert subscribers == set()
    assert "Desactivaste todas" in update.callback_query.edit_message_text.await_args.kwargs["text"]
