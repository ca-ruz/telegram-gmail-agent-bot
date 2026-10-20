"""Shared timing rules for event reminders."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

GDL_TZ = ZoneInfo("America/Mexico_City")


def get_event_reminder_stage(
    event_time,
    now,
    reminder_rules,
    same_day_hour=13,
    morning_lead_hours=2,
):
    """Return the most urgent reminder stage currently due for an event."""
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=GDL_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=GDL_TZ)

    event_time_gdl = event_time.astimezone(GDL_TZ)
    now_gdl = now.astimezone(GDL_TZ)
    if event_time_gdl <= now_gdl:
        return None

    if event_time_gdl.date() == now_gdl.date():
        same_day_time = event_time_gdl.replace(
            hour=same_day_hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        if event_time_gdl.hour < same_day_hour:
            same_day_time = event_time_gdl - timedelta(hours=morning_lead_hours)
        if now_gdl >= same_day_time:
            return "same_day"

    eligible = [
        (threshold, name)
        for name, threshold in reminder_rules.items()
        if name not in {"1h", "same_day"}
        and event_time - now <= threshold
    ]
    return min(eligible)[1] if eligible else None
