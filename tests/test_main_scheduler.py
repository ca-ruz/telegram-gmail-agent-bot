from unittest.mock import patch

import main


class FakeJobQueue:
    """Record recurring jobs registered by the application bootstrap."""

    def __init__(self):
        self.jobs = []

    def run_repeating(self, callback, interval, first):
        """Record a repeating job without starting a scheduler."""
        self.jobs.append({"name": callback.__name__, "interval": interval, "first": first})


class FakeApplication:
    """Minimal application double for main bootstrap testing."""

    def __init__(self):
        self.job_queue = FakeJobQueue()
        self.handlers = []

    def add_handler(self, handler):
        """Record a Telegram handler registration."""
        self.handlers.append(handler)

    def add_error_handler(self, handler):
        """Accept the application error handler registration."""
        self.error_handler = handler

    def run_polling(self):
        """Avoid starting a real Telegram polling loop in tests."""


class FakeApplicationBuilder:
    """Minimal builder double for application bootstrap testing."""

    def __init__(self, application):
        self.application = application
        self.received_token = None

    def token(self, token):
        """Record the configured bot token and continue the builder chain."""
        self.received_token = token
        return self

    def build(self):
        """Return the fake application."""
        return self.application


def test_main_registers_all_notification_jobs():
    """Application startup registers calendar, coffee-host, and manual-promo jobs."""
    application = FakeApplication()
    builder = FakeApplicationBuilder(application)

    with patch.object(main, "ApplicationBuilder", return_value=builder), patch.dict(
        main.CONFIG,
        {"BOT_TOKEN": "test-token", "CHECK_INTERVAL_MINUTES": 30},
    ):
        main.main()

    assert builder.received_token == "test-token"
    assert application.job_queue.jobs == [
        {"name": "calendar_job", "interval": 1800, "first": 10},
        {"name": "coffee_host_job", "interval": 1800, "first": 20},
        {"name": "manual_promo_job", "interval": 1800, "first": 30},
    ]


def test_main_registers_only_enabled_notification_jobs():
    """Disabled scheduler services are not registered at application startup."""
    application = FakeApplication()
    builder = FakeApplicationBuilder(application)

    with patch.object(main, "ApplicationBuilder", return_value=builder), patch.dict(
        main.CONFIG,
        {
            "BOT_TOKEN": "test-token",
            "CHECK_INTERVAL_MINUTES": 15,
            "ENABLE_CALENDAR_POLLING": False,
            "ENABLE_COFFEE_HOST_REMINDERS": True,
            "ENABLE_MANUAL_PROMO_REMINDERS": False,
        },
    ):
        main.main()

    assert application.job_queue.jobs == [
        {"name": "coffee_host_job", "interval": 900, "first": 20},
    ]


def test_parse_env_bool_accepts_common_values(monkeypatch):
    """Environment boolean parsing accepts true and false spellings."""
    monkeypatch.setenv("TEST_FLAG", "yes")
    assert main.parse_env_bool("TEST_FLAG", False) is True

    monkeypatch.setenv("TEST_FLAG", "off")
    assert main.parse_env_bool("TEST_FLAG", True) is False


def test_parse_env_bool_uses_default_for_invalid_values(monkeypatch):
    """Invalid environment boolean values fall back to the configured default."""
    monkeypatch.setenv("TEST_FLAG", "sometimes")
    assert main.parse_env_bool("TEST_FLAG", True) is True
