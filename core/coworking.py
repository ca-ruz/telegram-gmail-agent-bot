"""Persistence and timing helpers for weekly coworking invitations."""

from datetime import datetime
from zoneinfo import ZoneInfo

from tools.local.data_manager import load_json, save_json


COWORKING_TIMEZONE = ZoneInfo("America/Mexico_City")


def load_coworking_state(file_path):
    """Load weekly coworking notification state from JSON."""
    state = load_json(file_path, {})
    return state if isinstance(state, dict) else {}


def save_coworking_state(file_path, state):
    """Persist weekly coworking notification state atomically."""
    save_json(file_path, state)


def current_coworking_week(now=None):
    """Return the local ISO week key for a timestamp."""
    current_time = now or datetime.now(COWORKING_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=COWORKING_TIMEZONE)
    return current_time.astimezone(COWORKING_TIMEZONE).strftime("%G-W%V")


def get_coworking_notification_week(now=None, weekday=2, hour=10):
    """Return the due week key for the configured weekly notification slot."""
    current_time = now or datetime.now(COWORKING_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=COWORKING_TIMEZONE)
    local_time = current_time.astimezone(COWORKING_TIMEZONE)
    if local_time.weekday() != weekday or local_time.hour < hour:
        return None
    return current_coworking_week(local_time)
