"""Exchange-rate retrieval and MXN-to-millisatoshi conversion helpers."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_UP
import json
import logging
from urllib.request import Request, urlopen

from tools.local.data_manager import load_json, save_json

logger = logging.getLogger(__name__)

MSATOSHIS_PER_BTC = Decimal("100000000000")


class ExchangeRateError(ValueError):
    """Raised when a usable current exchange rate is unavailable."""


def _timestamp(now=None):
    """Return a timezone-aware UTC datetime for cache comparisons."""
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        return current_time.replace(tzinfo=timezone.utc)
    return current_time.astimezone(timezone.utc)


def _timestamp_text(value):
    """Return an ISO timestamp for a cache record."""
    return _timestamp(value).isoformat()


def _parse_timestamp(value):
    """Parse a cache timestamp or return ``None`` for invalid input."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _timestamp(parsed)


def _normalize_rate(value):
    """Return a positive finite MXN-per-BTC rate as a Decimal."""
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ExchangeRateError("Exchange rate is not numeric.") from None
    if not rate.is_finite() or rate <= 0:
        raise ExchangeRateError("Exchange rate must be positive and finite.")
    return rate


def parse_mxn_per_btc_rate(payload):
    """Extract an MXN-per-BTC rate from a supported provider response."""
    value = None
    if isinstance(payload, dict):
        bitcoin = payload.get("bitcoin")
        if isinstance(bitcoin, dict):
            value = bitcoin.get("mxn")
        if value is None:
            value = payload.get("mxn_per_btc", payload.get("rate"))
    return _normalize_rate(value)


def fetch_mxn_per_btc_rate(url, timeout_seconds):
    """Fetch and parse one MXN-per-BTC exchange rate from an HTTP endpoint."""
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "membership-payment-bot"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.load(response)
    except Exception as error:
        raise ExchangeRateError("Could not fetch the exchange rate.") from error
    return parse_mxn_per_btc_rate(payload)


class MxnBtcRateProvider:
    """Load a bounded exchange-rate cache and refresh it when it expires."""

    def __init__(
        self,
        url,
        cache_file,
        max_age_seconds=900,
        timeout_seconds=10,
        fetcher=None,
    ):
        """Initialize a provider with a URL, cache path, and freshness limit."""
        self.url = str(url).strip()
        self.cache_file = cache_file
        self.max_age_seconds = int(max_age_seconds)
        self.timeout_seconds = int(timeout_seconds)
        self.fetcher = fetcher or fetch_mxn_per_btc_rate

    def _cached_rate(self, now):
        """Return a fresh cached rate, or ``None`` when the cache is unusable."""
        cached = load_json(self.cache_file, {})
        if not isinstance(cached, dict):
            return None
        fetched_at = _parse_timestamp(cached.get("fetched_at"))
        if fetched_at is None:
            return None
        age_seconds = (now - fetched_at).total_seconds()
        if age_seconds < 0 or age_seconds > self.max_age_seconds:
            return None
        try:
            return _normalize_rate(cached.get("mxn_per_btc"))
        except ExchangeRateError:
            return None

    def get_rate(self, now=None):
        """Return a fresh rate, refreshing the cache or raising on failure."""
        current_time = _timestamp(now)
        cached_rate = self._cached_rate(current_time)
        if cached_rate is not None:
            return cached_rate
        if not self.url:
            raise ExchangeRateError("Exchange-rate URL is not configured.")
        if self.max_age_seconds <= 0 or self.timeout_seconds <= 0:
            raise ExchangeRateError("Exchange-rate cache and timeout settings are invalid.")

        try:
            rate = _normalize_rate(self.fetcher(self.url, self.timeout_seconds))
        except ExchangeRateError:
            raise
        except Exception as error:
            raise ExchangeRateError("Could not obtain a valid exchange rate.") from error

        save_json(
            self.cache_file,
            {
                "mxn_per_btc": str(rate),
                "fetched_at": _timestamp_text(current_time),
                "source_url": self.url,
            },
        )
        return rate


def membership_price_mxn(member, plans):
    """Return a member's total MXN price using plan and quantity metadata."""
    if not isinstance(member, dict) or not isinstance(plans, dict):
        raise ValueError("Member pricing data is not configured.")
    plan_key = str(member.get("membership_plan", "")).strip().lower()
    plan = plans.get(plan_key)
    if not isinstance(plan, dict):
        raise ValueError("Member does not have a configured membership plan.")
    try:
        price_mxn = Decimal(str(plan["price_mxn"]))
        quantity = Decimal(str(member.get("membership_quantity", 1)))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        raise ValueError("Member pricing data is invalid.") from None
    if not price_mxn.is_finite() or price_mxn <= 0 or not quantity.is_finite() or quantity <= 0:
        raise ValueError("Member pricing values must be positive and finite.")
    return price_mxn * quantity


def mxn_to_msat(amount_mxn, mxn_per_btc):
    """Convert an MXN amount to whole millisatoshis, rounding up safely."""
    try:
        amount = Decimal(str(amount_mxn))
        rate = _normalize_rate(mxn_per_btc)
    except (ExchangeRateError, InvalidOperation, TypeError, ValueError):
        raise ValueError("MXN amount and exchange rate must be valid.") from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError("MXN amount must be positive and finite.")
    millisatoshis = (amount / rate * MSATOSHIS_PER_BTC).quantize(
        Decimal("1"),
        rounding=ROUND_UP,
    )
    return int(millisatoshis)
