"""Tests for membership exchange-rate retrieval and conversion."""

from datetime import datetime, timezone

import pytest

from core.membership_exchange import (
    ExchangeRateError,
    MxnBtcRateProvider,
    membership_price_mxn,
    mxn_to_msat,
    parse_mxn_per_btc_rate,
)


def test_parse_rate_supports_coingecko_shape():
    """The configured provider response is converted to MXN per BTC."""
    assert parse_mxn_per_btc_rate({"bitcoin": {"mxn": 1_600_000}}) == 1_600_000


def test_mxn_to_msat_rounds_up_to_avoid_undercharging():
    """Fractional millisatoshis are rounded upward to a whole unit."""
    assert mxn_to_msat("1100", "1600000") == 68_750_000
    assert mxn_to_msat("1", "3000000000") == 34


def test_membership_price_multiplies_fractional_quantity():
    """A one-and-a-half office membership produces the expected MXN total."""
    assert membership_price_mxn(
        {"membership_plan": "private_office", "membership_quantity": 1.5},
        {"private_office": {"price_mxn": 5500}},
    ) == 8250


def test_rate_provider_uses_fresh_cache_without_fetching(tmp_path):
    """Fresh persisted rates prevent unnecessary provider requests."""
    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    cache_file = tmp_path / "rate.json"
    cache_file.write_text(
        '{"mxn_per_btc": "1600000", "fetched_at": "2026-11-06T11:55:00+00:00"}',
        encoding="utf-8",
    )
    calls = []

    def fetcher(url, timeout):
        """Record unexpected fetches in the cache test."""
        calls.append((url, timeout))
        return 1

    provider = MxnBtcRateProvider(
        "https://example.test/rate",
        str(cache_file),
        fetcher=fetcher,
    )

    assert provider.get_rate(now) == 1_600_000
    assert calls == []


def test_rate_provider_refreshes_expired_cache(tmp_path):
    """An expired cache is replaced with a newly fetched rate."""
    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    cache_file = tmp_path / "rate.json"
    cache_file.write_text(
        '{"mxn_per_btc": "1600000", "fetched_at": "2026-11-06T11:00:00+00:00"}',
        encoding="utf-8",
    )
    provider = MxnBtcRateProvider(
        "https://example.test/rate",
        str(cache_file),
        max_age_seconds=900,
        fetcher=lambda url, timeout: 1_700_000,
    )

    assert provider.get_rate(now) == 1_700_000
    assert "1700000" in cache_file.read_text(encoding="utf-8")


def test_rate_provider_fails_when_refresh_is_unavailable(tmp_path):
    """Stale rates are never used when the configured provider is unavailable."""
    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    cache_file = tmp_path / "rate.json"
    cache_file.write_text(
        '{"mxn_per_btc": "1600000", "fetched_at": "2026-11-06T11:00:00+00:00"}',
        encoding="utf-8",
    )
    def failing_fetcher(url, timeout):
        """Simulate an unavailable exchange-rate service."""
        raise RuntimeError("offline")

    provider = MxnBtcRateProvider(
        "https://example.test/rate",
        str(cache_file),
        max_age_seconds=900,
        fetcher=failing_fetcher,
    )

    with pytest.raises(ExchangeRateError):
        provider.get_rate(now)
