"""Small, safe adapter around the NIP-47 Nostr Wallet Connect client."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
from typing import Any, Callable, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


NWC_SCHEME = "nostr+walletconnect"
REQUIRED_NWC_QUERY_PARAMETERS = ("relay", "secret")
NWC_STATUS_STATES = frozenset({
    "pending",
    "settled",
    "expired",
    "failed",
    "unmatched",
})


class NwcClientError(RuntimeError):
    """Base error raised by the NWC adapter."""


class InvalidNwcUriError(NwcClientError, ValueError):
    """Raised when an NWC connection URI is incomplete or malformed."""


class NwcTransportError(NwcClientError):
    """Raised when the NWC dependency cannot complete a request."""


@dataclass(frozen=True)
class NwcConnection:
    """Validated, non-secret information extracted from an NWC URI."""

    wallet_pubkey: str
    relay_urls: tuple[str, ...]


@dataclass(frozen=True)
class NwcInvoice:
    """Normalized invoice data returned by an NWC wallet service."""

    invoice: str
    payment_hash: str
    amount_msat: int
    expires_at: int | None


@dataclass(frozen=True)
class NwcInvoiceStatus:
    """Normalized invoice status returned by an NWC wallet service."""

    payment_hash: str
    state: str
    amount_msat: int | None
    settled_at: int | None
    preimage: str | None = None

    @property
    def is_settled(self) -> bool:
        """Return whether the wallet reports a settled invoice."""
        return self.state == "settled"


class NwcSession(Protocol):
    """Minimal async interface required from the NWC dependency."""

    async def __aenter__(self) -> "NwcSession":
        """Open the session."""

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        """Close the session."""

    async def make_invoice(
        self, *, amount: int, description: str, expiry: int
    ) -> Any:
        """Create an invoice through the wallet service."""

    async def lookup_invoice(self, *, payment_hash: str) -> Any:
        """Look up an invoice through the wallet service."""


NwcClientFactory = Callable[[str], NwcSession]


def validate_nwc_uri(connection_uri: str) -> NwcConnection:
    """Validate an NWC URI without exposing its secret."""
    if not isinstance(connection_uri, str) or not connection_uri.strip():
        raise InvalidNwcUriError("NWC connection URI is required.")

    parsed = urlsplit(connection_uri.strip())
    if parsed.scheme != NWC_SCHEME or not parsed.netloc:
        raise InvalidNwcUriError("NWC connection URI has an invalid scheme or wallet key.")
    if parsed.fragment or parsed.username or parsed.password:
        raise InvalidNwcUriError("NWC connection URI contains unsupported URL components.")

    query = parse_qs(parsed.query, keep_blank_values=True)
    missing = [name for name in REQUIRED_NWC_QUERY_PARAMETERS if not query.get(name)]
    if missing:
        missing_names = ", ".join(missing)
        raise InvalidNwcUriError(f"NWC connection URI is missing: {missing_names}.")

    relay_urls = tuple(value for value in query["relay"] if value)
    return NwcConnection(wallet_pubkey=parsed.netloc, relay_urls=relay_urls)


def redact_nwc_uri(connection_uri: str) -> str:
    """Return an NWC URI with its connection secret removed."""
    parsed = urlsplit(connection_uri)
    query = parse_qs(parsed.query, keep_blank_values=True)
    safe_query = []
    for relay in query.get("relay", []):
        safe_query.append(("relay", relay))
    safe_query.append(("secret", "<redacted>"))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(safe_query), "")
    )


def _default_client_factory(connection_uri: str) -> NwcSession:
    """Load the optional NWC dependency without importing it at module load."""
    try:
        module = importlib.import_module("nostrwalletconnect")
    except ModuleNotFoundError as exc:
        raise NwcClientError(
            "The nostrwalletconnect dependency is not installed."
        ) from exc
    return module.NWCClient(connection_uri)


def _field(payload: Any, name: str, default: Any = None) -> Any:
    """Read a field from either a mapping response or an SDK object."""
    if isinstance(payload, dict):
        return payload.get(name, default)
    return getattr(payload, name, default)


def _integer_field(payload: Any, name: str, *, required: bool = False) -> int | None:
    """Normalize an integer response field from common NWC representations."""
    value = _field(payload, name)
    if value is None:
        if required:
            raise NwcClientError(f"NWC response is missing {name}.")
        return None
    if isinstance(value, bool):
        raise NwcClientError(f"NWC response contains an invalid {name}.")
    if isinstance(value, str):
        value = value.removesuffix("msat")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise NwcClientError(f"NWC response contains an invalid {name}.") from exc


def _integer_field_alias(
    payload: Any,
    names: tuple[str, ...],
    *,
    required: bool = False,
) -> int | None:
    """Read an integer field using compatible SDK field names."""
    for name in names:
        value = _field(payload, name)
        if value is not None:
            return _integer_field(payload, name, required=True)
    if required:
        raise NwcClientError(f"NWC response is missing {names[0]}.")
    return None


def _string_field_alias(payload: Any, names: tuple[str, ...]) -> str | None:
    """Read the first non-empty string response field from compatible names."""
    for name in names:
        value = _field(payload, name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def is_valid_payment_preimage(payment_hash: str, preimage: str | None) -> bool:
    """Return whether a hex preimage cryptographically proves the payment hash."""
    if not isinstance(payment_hash, str) or not isinstance(preimage, str):
        return False

    normalized_hash = payment_hash.strip().lower()
    normalized_preimage = preimage.strip()
    if len(normalized_hash) != 64 or len(normalized_preimage) != 64:
        return False

    try:
        hash_bytes = bytes.fromhex(normalized_hash)
        preimage_bytes = bytes.fromhex(normalized_preimage)
    except ValueError:
        return False

    return hashlib.sha256(preimage_bytes).digest() == hash_bytes


def _normalize_invoice(
    payload: Any,
    *,
    fallback_amount_msat: int | None = None,
) -> NwcInvoice:
    """Normalize an invoice response and require its payment identity."""
    invoice = _field(payload, "invoice")
    payment_hash = _field(payload, "payment_hash")
    if not invoice or not payment_hash:
        raise NwcClientError("NWC invoice response is missing its payment identity.")
    amount_msat = _integer_field_alias(
        payload,
        ("amount", "amount_msat"),
        required=fallback_amount_msat is None,
    )
    if amount_msat is None:
        amount_msat = fallback_amount_msat
    return NwcInvoice(
        invoice=str(invoice),
        payment_hash=str(payment_hash),
        amount_msat=amount_msat,
        expires_at=_integer_field(payload, "expires_at"),
    )


def _normalize_status(payload: Any) -> NwcInvoiceStatus:
    """Normalize an invoice lookup response."""
    payment_hash = _field(payload, "payment_hash")
    if not payment_hash:
        raise NwcClientError("NWC lookup response is missing payment_hash.")

    raw_state = _field(payload, "state")
    if isinstance(raw_state, str) and raw_state.strip():
        state = raw_state.strip().lower()
        if state == "paid":
            state = "settled"
        if state not in NWC_STATUS_STATES:
            state = "unmatched"
    else:
        paid = _field(payload, "paid")
        if isinstance(paid, bool):
            state = "settled" if paid else "pending"
        else:
            state = "unmatched"
    return NwcInvoiceStatus(
        payment_hash=str(payment_hash),
        state=state,
        amount_msat=_integer_field_alias(payload, ("amount", "amount_msat")),
        settled_at=_integer_field(payload, "settled_at"),
        preimage=_string_field_alias(payload, ("preimage", "payment_preimage")),
    )


class NwcInvoiceClient:
    """Create and inspect invoices through one validated NWC connection."""

    def __init__(
        self,
        connection_uri: str,
        client_factory: NwcClientFactory | None = None,
    ) -> None:
        """Initialize the client without storing a parsed secret separately."""
        validate_nwc_uri(connection_uri)
        self._connection_uri = connection_uri.strip()
        self._client_factory = client_factory or _default_client_factory

    async def make_invoice(
        self, *, amount_msat: int, description: str, expiry_seconds: int
    ) -> NwcInvoice:
        """Create and normalize an incoming invoice."""
        if amount_msat <= 0:
            raise ValueError("Invoice amount must be greater than zero.")
        if expiry_seconds <= 0:
            raise ValueError("Invoice expiry must be greater than zero.")
        try:
            async with self._client_factory(self._connection_uri) as client:
                response = await client.make_invoice(
                    amount=amount_msat,
                    description=description,
                    expiry=expiry_seconds,
                )
            return _normalize_invoice(
                response,
                fallback_amount_msat=amount_msat,
            )
        except NwcClientError:
            raise
        except Exception as exc:
            raise NwcTransportError("NWC invoice creation failed.") from exc

    async def lookup_invoice(self, *, payment_hash: str) -> NwcInvoiceStatus:
        """Look up and normalize an invoice by payment hash."""
        if not payment_hash or not payment_hash.strip():
            raise ValueError("Payment hash is required.")
        try:
            async with self._client_factory(self._connection_uri) as client:
                response = await client.lookup_invoice(
                    payment_hash=payment_hash.strip()
                )
            return _normalize_status(response)
        except NwcClientError:
            raise
        except Exception as exc:
            raise NwcTransportError("NWC invoice lookup failed.") from exc
