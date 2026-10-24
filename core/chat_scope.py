"""Helpers for distinguishing private chats from Telegram groups."""


PRIVATE_CHAT_TYPE = "private"
GROUP_CHAT_TYPES = frozenset({"group", "supergroup"})


def get_chat_type(update_or_chat):
    """Return a normalized Telegram chat type from an update or chat object."""
    chat = getattr(update_or_chat, "effective_chat", update_or_chat)
    return str(getattr(chat, "type", "") or "").strip().lower()


def is_private_chat(update_or_chat):
    """Return whether an update or chat represents a private conversation."""
    return get_chat_type(update_or_chat) == PRIVATE_CHAT_TYPE


def is_group_chat(update_or_chat):
    """Return whether an update or chat represents a group conversation."""
    return get_chat_type(update_or_chat) in GROUP_CHAT_TYPES
