"""Persistence and normalization helpers for group notification categories."""

import logging

from tools.local.data_manager import load_json, save_json


GROUP_NOTIFICATION_CATEGORIES = ("events", "payments", "coworking")
logger = logging.getLogger(__name__)


def _normalize_group_id(group_id):
    """Return a non-zero Telegram group identifier or ``None``."""
    try:
        normalized_id = int(group_id)
    except (TypeError, ValueError):
        return None

    return normalized_id if normalized_id else None


def _normalize_group_ids(group_ids):
    """Return unique, sorted Telegram group identifiers from an iterable."""
    if isinstance(group_ids, (str, bytes)) or not group_ids:
        return []

    normalized_ids = {
        normalized_id
        for group_id in group_ids
        if (normalized_id := _normalize_group_id(group_id)) is not None
    }
    return sorted(normalized_ids)


def normalize_group_notification_categories(raw_categories):
    """Normalize category assignments while dropping unknown categories and IDs."""
    if not isinstance(raw_categories, dict):
        raw_categories = {}

    return {
        category: _normalize_group_ids(raw_categories.get(category, []))
        for category in GROUP_NOTIFICATION_CATEGORIES
    }


def build_default_group_notification_categories(groups, members_group_id=0):
    """Build the initial category assignments without changing delivery behavior."""
    broadcast_groups = _normalize_group_ids(groups)
    categories = {
        "events": broadcast_groups,
        "payments": [],
        "coworking": broadcast_groups,
    }
    normalized_members_group_id = _normalize_group_id(members_group_id)
    if normalized_members_group_id is not None:
        categories["payments"] = [normalized_members_group_id]
    return categories


def load_group_notification_categories(file_path, groups, members_group_id=0):
    """Load category assignments and migrate existing group configuration once."""
    raw_categories = load_json(file_path, None)
    if isinstance(raw_categories, dict):
        return normalize_group_notification_categories(raw_categories)

    categories = build_default_group_notification_categories(
        groups,
        members_group_id,
    )
    save_group_notification_categories(file_path, categories)
    logger.info("Initialized group notification categories in %s.", file_path)
    return categories


def save_group_notification_categories(file_path, categories):
    """Persist normalized group notification category assignments atomically."""
    save_json(
        file_path,
        normalize_group_notification_categories(categories),
    )


def get_group_ids_for_category(categories, category):
    """Return the configured group IDs for one known notification category."""
    if category not in GROUP_NOTIFICATION_CATEGORIES:
        return set()
    normalized_categories = normalize_group_notification_categories(categories)
    return set(normalized_categories[category])


def get_group_targets(state, category):
    """Return category targets, falling back to legacy broadcast groups when needed."""
    if not isinstance(state, dict):
        return set()

    categories = state.get("group_notification_categories")
    if categories is None:
        raw_groups = state.get("groups", set())
        return set(_normalize_group_ids(raw_groups))

    return get_group_ids_for_category(categories, category)
