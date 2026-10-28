"""Read-only aggregation helpers for the primary administrator status view."""

from core.group_notification_routing import (
    GROUP_NOTIFICATION_CATEGORIES,
    normalize_group_notification_categories,
)
from core.notification_preferences import (
    NOTIFICATION_CATEGORIES,
    is_notification_enabled,
)


def _normalize_user_id(value):
    """Return a positive Telegram user ID as a string, or an empty string."""
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return ""
    return str(normalized) if normalized > 0 else ""


def _sort_identifiers(values):
    """Sort numeric identifiers numerically and other values lexicographically."""
    return sorted(
        values,
        key=lambda value: (0, int(value)) if str(value).lstrip("-").isdigit() else (1, str(value)),
    )


def _user_metadata(metadata, user_id):
    """Return normalized metadata for one user ID when available."""
    users = metadata.get("users", {}) if isinstance(metadata, dict) else {}
    record = users.get(str(user_id), {}) if isinstance(users, dict) else {}
    return record if isinstance(record, dict) else {}


def _member_matches(members, user_id):
    """Return member records associated with one Telegram user ID."""
    matches = []
    for member_id, member in (members or {}).items():
        if not isinstance(member, dict):
            continue
        if _normalize_user_id(member.get("telegram_id")) == str(user_id):
            matches.append((str(member_id), member))
    return matches


def _coffee_host_matches(coffee_hosts, user_id):
    """Return coffee-host records associated with one Telegram user ID."""
    matches = []
    for host_id, host in (coffee_hosts or {}).items():
        if not isinstance(host, dict):
            continue
        if _normalize_user_id(host.get("telegram_id")) == str(user_id):
            matches.append((str(host_id), host))
    return matches


def _build_user_record(user_id, state, subscribers, notification_preferences):
    """Build one user record with identity, role, and notification details."""
    metadata = _user_metadata(state.get("telegram_metadata", {}), user_id)
    member_matches = _member_matches(state.get("members", {}), user_id)
    coffee_host_matches = _coffee_host_matches(state.get("coffee_hosts", {}), user_id)

    fallback_names = [
        member.get("name")
        for _, member in member_matches
        if member.get("name")
    ]
    fallback_names.extend(
        host.get("name")
        for _, host in coffee_host_matches
        if host.get("name")
    )
    display_name = metadata.get("display_name") or next(iter(fallback_names), str(user_id))
    categories = {
        category: is_notification_enabled(
            notification_preferences,
            user_id,
            category,
        )
        for category in NOTIFICATION_CATEGORIES
    }

    return {
        "telegram_id": int(user_id),
        "display_name": str(display_name),
        "username": str(metadata.get("username", "") or ""),
        "subscribed": int(user_id) in subscribers,
        "member_ids": [member_id for member_id, _ in member_matches],
        "active_member": any(
            member.get("active", True) for _, member in member_matches
        ),
        "coffee_host_ids": [host_id for host_id, _ in coffee_host_matches],
        "active_coffee_host": any(
            host.get("active", True) for _, host in coffee_host_matches
        ),
        "notification_preferences": categories,
    }


def _build_member_records(members):
    """Build safe member records without including payment history."""
    records = []
    for member_id, member in (members or {}).items():
        if not isinstance(member, dict):
            continue
        telegram_id = _normalize_user_id(member.get("telegram_id"))
        records.append(
            {
                "member_id": str(member_id),
                "name": str(member.get("name", "") or ""),
                "telegram_id": int(telegram_id) if telegram_id else None,
                "active": bool(member.get("active", True)),
            }
        )
    return sorted(records, key=lambda record: record["member_id"])


def _build_coffee_host_records(coffee_hosts):
    """Build safe coffee-host records for status details."""
    records = []
    for host_id, host in (coffee_hosts or {}).items():
        if not isinstance(host, dict):
            continue
        telegram_id = _normalize_user_id(host.get("telegram_id"))
        records.append(
            {
                "host_id": str(host_id),
                "name": str(host.get("name", "") or ""),
                "telegram_id": int(telegram_id) if telegram_id else None,
                "active": bool(host.get("active", True)),
            }
        )
    return sorted(records, key=lambda record: record["host_id"])


def _build_group_records(groups, metadata, categories):
    """Build group records with display metadata and notification categories."""
    metadata_groups = metadata.get("groups", {}) if isinstance(metadata, dict) else {}
    records = []
    category_memberships = {
        category: set(categories.get(category, []))
        for category in GROUP_NOTIFICATION_CATEGORIES
    }
    for raw_group_id in groups or []:
        group_id = str(raw_group_id)
        group_metadata = metadata_groups.get(group_id, {})
        if not isinstance(group_metadata, dict):
            group_metadata = {}
        numeric_id = int(group_id) if group_id.lstrip("-").isdigit() else group_id
        records.append(
            {
                "group_id": numeric_id,
                "display_name": str(
                    group_metadata.get("display_name") or group_id
                ),
                "username": str(group_metadata.get("username", "") or ""),
                "categories": [
                    category
                    for category in GROUP_NOTIFICATION_CATEGORIES
                    if numeric_id in category_memberships[category]
                    or group_id in category_memberships[category]
                ],
            }
        )
    return sorted(records, key=lambda record: str(record["group_id"]))


def build_admin_status_snapshot(state):
    """Return a read-only aggregate of users, roles, groups, and preferences."""
    state = state if isinstance(state, dict) else {}
    subscribers = {
        int(user_id)
        for user_id in state.get("subscribers", set())
        if _normalize_user_id(user_id)
    }
    notification_preferences = state.get("notification_preferences", {})
    if not isinstance(notification_preferences, dict):
        notification_preferences = {}

    members = state.get("members", {})
    if not isinstance(members, dict):
        members = {}
    coffee_hosts = state.get("coffee_hosts", {})
    if not isinstance(coffee_hosts, dict):
        coffee_hosts = {}
    metadata = state.get("telegram_metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    known_user_ids = set(subscribers)
    known_user_ids.update(
        int(user_id)
        for user_id in (metadata.get("users", {}) if isinstance(metadata.get("users", {}), dict) else {})
        if _normalize_user_id(user_id)
    )
    known_user_ids.update(
        int(user_id)
        for member in members.values()
        if isinstance(member, dict)
        for user_id in [_normalize_user_id(member.get("telegram_id"))]
        if user_id
    )
    known_user_ids.update(
        int(user_id)
        for host in coffee_hosts.values()
        if isinstance(host, dict)
        for user_id in [_normalize_user_id(host.get("telegram_id"))]
        if user_id
    )
    known_user_ids.update(
        int(user_id)
        for user_id in notification_preferences
        if _normalize_user_id(user_id)
    )

    groups = state.get("groups", set())
    if not isinstance(groups, (set, list, tuple)):
        groups = []
    categories = normalize_group_notification_categories(
        state.get("group_notification_categories", {})
    )
    member_records = _build_member_records(members)
    coffee_host_records = _build_coffee_host_records(coffee_hosts)
    group_records = _build_group_records(groups, metadata, categories)
    user_records = [
        _build_user_record(user_id, state, subscribers, notification_preferences)
        for user_id in _sort_identifiers(known_user_ids)
    ]

    return {
        "counts": {
            "known_users": len(user_records),
            "subscribers": len(subscribers),
            "members": len(member_records),
            "active_members": sum(record["active"] for record in member_records),
            "coffee_hosts": len(coffee_host_records),
            "active_coffee_hosts": sum(
                record["active"] for record in coffee_host_records
            ),
            "broadcast_groups": len(group_records),
        },
        "users": user_records,
        "members": member_records,
        "coffee_hosts": coffee_host_records,
        "groups": group_records,
        "notification_categories": list(NOTIFICATION_CATEGORIES),
        "group_notification_categories": categories,
    }
