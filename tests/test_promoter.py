from datetime import datetime, timedelta, timezone

from core.promoter import cleanup_pending_promos
from tools.local.data_manager import load_json


def test_cleanup_pending_promos_removes_expired(tmp_path):
    """Expired staged promos are removed and persisted."""
    file_path = tmp_path / "pending_promos.json"
    pending = {
        "admin": {
            "event_start": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "summary": "Expired event",
        }
    }

    cleaned = cleanup_pending_promos(pending, str(file_path))

    assert cleaned == {}
    assert load_json(str(file_path), None) == {}


def test_cleanup_pending_promos_keeps_future(tmp_path):
    """Future staged promos are kept in memory and do not force a write."""
    file_path = tmp_path / "pending_promos.json"
    pending = {
        "admin": {
            "event_start": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "summary": "Future event",
        }
    }

    cleaned = cleanup_pending_promos(pending, str(file_path))

    assert cleaned == pending
    assert not file_path.exists()


def test_cleanup_pending_promos_keeps_missing_event_start(tmp_path):
    """Legacy staged promos without event_start are kept."""
    file_path = tmp_path / "pending_promos.json"
    pending = {
        "admin": {
            "summary": "Legacy pending promo",
        }
    }

    cleaned = cleanup_pending_promos(pending, str(file_path))

    assert cleaned == pending
    assert not file_path.exists()


def test_cleanup_pending_promos_keeps_invalid_event_start(tmp_path):
    """Invalid event_start values do not delete the staged promo."""
    file_path = tmp_path / "pending_promos.json"
    pending = {
        "admin": {
            "event_start": "not-a-date",
            "summary": "Bad date",
        }
    }

    cleaned = cleanup_pending_promos(pending, str(file_path))

    assert cleaned == pending
    assert not file_path.exists()
