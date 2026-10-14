"""Persistence and configuration helpers for weekly coffee host reminders."""

import logging
from zoneinfo import ZoneInfo

from tools.local.data_manager import load_json, save_json

logger = logging.getLogger(__name__)

COFFEE_TIMEZONE = ZoneInfo("America/Mexico_City")
VALID_REMINDER_RULES = ("7d", "1d")


def load_coffee_hosts(file_path):
    """Load the coffee host roster from a JSON mapping."""
    hosts = load_json(file_path, {})
    if isinstance(hosts, dict):
        return hosts

    logger.warning("Coffee host roster %s is not a JSON object; using an empty roster.", file_path)
    return {}


def load_coffee_schedule(file_path):
    """Load Saturday coffee host assignments from a JSON mapping."""
    schedule = load_json(file_path, {})
    if isinstance(schedule, dict):
        return schedule

    logger.warning("Coffee host schedule %s is not a JSON object; using an empty schedule.", file_path)
    return {}


def save_coffee_hosts(file_path, hosts):
    """Persist the coffee host roster atomically."""
    save_json(file_path, hosts)


def save_coffee_schedule(file_path, schedule):
    """Persist Saturday coffee host assignments atomically."""
    save_json(file_path, schedule)


def parse_reminder_rules(raw_rules):
    """Parse configured coffee reminder stages and discard unsupported values."""
    configured = [rule.strip() for rule in raw_rules.split(",") if rule.strip()]
    rules = tuple(rule for rule in configured if rule in VALID_REMINDER_RULES)

    if not rules:
        logger.warning(
            "No valid coffee host reminder rules configured; using defaults %s.",
            VALID_REMINDER_RULES,
        )
        return VALID_REMINDER_RULES

    return tuple(dict.fromkeys(rules))
