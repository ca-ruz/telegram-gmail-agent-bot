import pytest
from datetime import timedelta
from tools.local.calendar_api import friendly_delta, extract_link

def test_friendly_delta_days():
    # 2 days and a bit should round to 2 days
    assert friendly_delta(timedelta(days=2, hours=1)) == "2 días"
    # 1.6 days should round up to 2 days
    assert friendly_delta(timedelta(days=1, hours=15)) == "2 días"
    # Exactly 1 day
    assert friendly_delta(timedelta(days=1)) == "1 día"

def test_friendly_delta_hours():
    # 5 hours and 10 mins should round to 5 hours
    assert friendly_delta(timedelta(hours=5, minutes=10)) == "5 horas"
    # 55 minutes should be treated as minutes, but let's check current logic
    # In current logic, if >= 3600s it shows hours
    assert friendly_delta(timedelta(minutes=61)) == "1 hora"
    # Exactly 1 hour
    assert friendly_delta(timedelta(hours=1)) == "1 hora"

def test_friendly_delta_minutes():
    # 45 minutes
    assert friendly_delta(timedelta(minutes=45)) == "45 minutos"
    # 1 minute
    assert friendly_delta(timedelta(minutes=1)) == "1 minuto"
    # Less than 1 minute
    assert friendly_delta(timedelta(seconds=30)) == "menos de 1 minuto"

def test_extract_link_basic():
    description = "Check out this link: https://example.com and join us!"
    assert extract_link(description) == "https://example.com"

def test_extract_link_multiple():
    description = "First: https://a.com, Second: https://b.com"
    # Should pick the first one
    assert extract_link(description) == "https://a.com"

def test_extract_link_none():
    assert extract_link("No links here") is None
    assert extract_link("") is None
    assert extract_link(None) is None
