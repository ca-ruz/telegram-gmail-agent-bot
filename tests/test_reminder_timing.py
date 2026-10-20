from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.reminder_timing import get_event_reminder_stage


GDL_TZ = ZoneInfo("America/Mexico_City")
REMINDER_RULES = {
    "1w": timedelta(days=7),
    "3d": timedelta(days=3),
    "24h": timedelta(days=1),
    "1h": timedelta(hours=1),
}


def test_same_day_reminder_defaults_to_one_pm():
    """Evening events use the configured same-day hour."""
    event_time = datetime(2026, 10, 20, 18, 0, tzinfo=GDL_TZ)
    now = datetime(2026, 10, 20, 13, 0, tzinfo=GDL_TZ)

    assert get_event_reminder_stage(event_time, now, REMINDER_RULES) == "same_day"


def test_morning_event_uses_two_hour_fallback():
    """Morning events use the lead-time fallback instead of a one-hour stage."""
    event_time = datetime(2026, 10, 20, 11, 0, tzinfo=GDL_TZ)
    now = datetime(2026, 10, 20, 9, 0, tzinfo=GDL_TZ)

    assert get_event_reminder_stage(event_time, now, REMINDER_RULES) == "same_day"


def test_same_day_reminder_hour_is_configurable():
    """The same-day reminder can use a configured local hour."""
    event_time = datetime(2026, 10, 20, 18, 0, tzinfo=GDL_TZ)
    before_reminder = datetime(2026, 10, 20, 14, 59, tzinfo=GDL_TZ)
    at_reminder = datetime(2026, 10, 20, 15, 0, tzinfo=GDL_TZ)

    assert get_event_reminder_stage(
        event_time,
        before_reminder,
        REMINDER_RULES,
        same_day_hour=15,
    ) == "24h"
    assert get_event_reminder_stage(
        event_time,
        at_reminder,
        REMINDER_RULES,
        same_day_hour=15,
    ) == "same_day"


def test_one_hour_stage_is_not_used():
    """Events within one hour still use the same-day stage."""
    event_time = datetime(2026, 10, 20, 18, 0, tzinfo=GDL_TZ)
    now = datetime(2026, 10, 20, 17, 0, tzinfo=GDL_TZ)

    assert get_event_reminder_stage(event_time, now, REMINDER_RULES) == "same_day"
