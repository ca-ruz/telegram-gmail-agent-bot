"""Persistence and normalization helpers for Telegram metadata."""

import logging

from tools.local.data_manager import load_json, save_json


logger = logging.getLogger(__name__)


def build_telegram_metadata():
    """Build an empty metadata store for Telegram users and groups."""
    return {"users": {}, "groups": {}}


def _normalize_identifier(value, allow_negative=False):
    """Return a non-zero Telegram identifier as an integer."""
    try:
        identifier = int(value)
    except (TypeError, ValueError):
        return None

    if identifier == 0 or (not allow_negative and identifier < 0):
        return None
    return identifier


def _clean_text(value):
    """Return a trimmed text value or an empty string."""
    return str(value).strip() if value is not None else ""


def build_user_metadata(user_id, first_name="", last_name="", username=""):
    """Build normalized metadata for one Telegram user."""
    normalized_id = _normalize_identifier(user_id)
    if normalized_id is None:
        return None

    first_name = _clean_text(first_name)
    last_name = _clean_text(last_name)
    username = _clean_text(username).lstrip("@")
    display_name = " ".join(part for part in (first_name, last_name) if part)
    if not display_name:
        display_name = f"@{username}" if username else str(normalized_id)

    return {
        "id": normalized_id,
        "display_name": display_name,
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
    }


def build_group_metadata(chat_id, title="", username="", chat_type=""):
    """Build normalized metadata for one Telegram group or supergroup."""
    normalized_id = _normalize_identifier(chat_id, allow_negative=True)
    if normalized_id is None:
        return None

    title = _clean_text(title)
    username = _clean_text(username).lstrip("@")
    chat_type = _clean_text(chat_type)
    display_name = title or (f"@{username}" if username else str(normalized_id))

    return {
        "id": normalized_id,
        "display_name": display_name,
        "title": title,
        "username": username,
        "type": chat_type,
    }


def normalize_user_metadata(user):
    """Build normalized metadata from a Telegram user-like object."""
    return build_user_metadata(
        getattr(user, "id", None),
        getattr(user, "first_name", ""),
        getattr(user, "last_name", ""),
        getattr(user, "username", ""),
    )


def normalize_group_metadata(chat):
    """Build normalized metadata from a Telegram chat-like object."""
    return build_group_metadata(
        getattr(chat, "id", None),
        getattr(chat, "title", ""),
        getattr(chat, "username", ""),
        getattr(chat, "type", ""),
    )


def record_user_metadata(metadata, user):
    """Insert normalized user metadata into the in-memory store."""
    record = normalize_user_metadata(user)
    if record is None:
        return None

    users = metadata.setdefault("users", {})
    users[str(record["id"])] = record
    return record


def record_group_metadata(metadata, chat):
    """Insert normalized group metadata into the in-memory store."""
    record = normalize_group_metadata(chat)
    if record is None:
        return None

    groups = metadata.setdefault("groups", {})
    groups[str(record["id"])] = record
    return record


def load_telegram_metadata(file_path):
    """Load and normalize Telegram metadata from a JSON file."""
    raw_metadata = load_json(file_path, {})
    if not isinstance(raw_metadata, dict):
        logger.warning(
            "Telegram metadata %s is not a JSON object; using an empty store.",
            file_path,
        )
        return build_telegram_metadata()

    metadata = build_telegram_metadata()
    raw_users = raw_metadata.get("users", {})
    if isinstance(raw_users, dict):
        for raw_key, raw_user in raw_users.items():
            if not isinstance(raw_user, dict):
                continue
            record = build_user_metadata(
                raw_user.get("id", raw_key),
                raw_user.get("first_name", ""),
                raw_user.get("last_name", ""),
                raw_user.get("username", ""),
            )
            if record is not None:
                metadata["users"][str(record["id"])] = record

    raw_groups = raw_metadata.get("groups", {})
    if isinstance(raw_groups, dict):
        for raw_key, raw_group in raw_groups.items():
            if not isinstance(raw_group, dict):
                continue
            record = build_group_metadata(
                raw_group.get("id", raw_key),
                raw_group.get("title", raw_group.get("display_name", "")),
                raw_group.get("username", ""),
                raw_group.get("type", ""),
            )
            if record is not None:
                metadata["groups"][str(record["id"])] = record

    return metadata


def save_telegram_metadata(file_path, metadata):
    """Persist Telegram metadata atomically."""
    save_json(file_path, metadata)
