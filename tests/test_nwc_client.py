"""Tests for the isolated NWC invoice client adapter."""

import asyncio

import pytest

from core.nwc_client import (
    InvalidNwcUriError,
    NwcClientError,
    NwcInvoiceClient,
    NwcTransportError,
    redact_nwc_uri,
    is_valid_payment_preimage,
    validate_nwc_uri,
)


NWC_URI = (
    "nostr+walletconnect://wallet-key?"
    "relay=wss%3A%2F%2Frelay.example&secret=super-secret"
)


class FakeSession:
    """Minimal async NWC session used by adapter tests."""

    def __init__(self, invoice_response=None, lookup_response=None, error=None):
        """Store fake responses for one session."""
        self.invoice_response = invoice_response
        self.lookup_response = lookup_response
        self.error = error

    async def __aenter__(self):
        """Return the fake session."""
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Close the fake session."""
        return None

    async def make_invoice(self, **kwargs):
        """Return the configured invoice response."""
        if self.error:
            raise self.error
        assert kwargs == {
            "amount": 25_000,
            "description": "Membership 2026-11",
            "expiry": 600,
        }
        return self.invoice_response

    async def lookup_invoice(self, **kwargs):
        """Return the configured lookup response."""
        if self.error:
            raise self.error
        assert kwargs == {"payment_hash": "hash-001"}
        return self.lookup_response


def test_validate_nwc_uri_returns_public_connection_details_only():
    """URI validation returns relays without exposing the connection secret."""
    connection = validate_nwc_uri(NWC_URI)

    assert connection.wallet_pubkey == "wallet-key"
    assert connection.relay_urls == ("wss://relay.example",)
    assert "super-secret" not in repr(connection)


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "https://wallet-key?relay=wss%3A%2F%2Frelay.example&secret=secret",
        "nostr+walletconnect://wallet-key?relay=wss%3A%2F%2Frelay.example",
        "nostr+walletconnect://wallet-key?secret=secret",
    ],
)
def test_validate_nwc_uri_rejects_incomplete_values(uri):
    """Invalid or incomplete connection URIs fail before network access."""
    with pytest.raises(InvalidNwcUriError):
        validate_nwc_uri(uri)


def test_redact_nwc_uri_removes_secret():
    """Redacted URIs retain relay diagnostics without retaining credentials."""
    redacted = redact_nwc_uri(NWC_URI)

    assert "relay.example" in redacted
    assert "super-secret" not in redacted
    assert "redacted" in redacted


def test_make_invoice_normalizes_sdk_response():
    """Invoice creation returns a stable internal representation."""
    session = FakeSession(
        invoice_response={
            "invoice": "lnbc-test",
            "payment_hash": "hash-001",
            "amount": 25_000,
            "expires_at": 1_800_000_000,
        }
    )
    client = NwcInvoiceClient(NWC_URI, client_factory=lambda _: session)

    invoice = asyncio.run(
        client.make_invoice(
            amount_msat=25_000,
            description="Membership 2026-11",
            expiry_seconds=600,
        )
    )

    assert invoice.invoice == "lnbc-test"
    assert invoice.payment_hash == "hash-001"
    assert invoice.amount_msat == 25_000
    assert invoice.expires_at == 1_800_000_000


def test_make_invoice_accepts_amount_msat_sdk_field():
    """Invoice creation accepts SDK responses using the explicit amount name."""
    session = FakeSession(
        invoice_response={
            "invoice": "lnbc-test",
            "payment_hash": "hash-001",
            "amount_msat": 25_000,
            "expires_at": 1_800_000_000,
        }
    )
    client = NwcInvoiceClient(NWC_URI, client_factory=lambda _: session)

    invoice = asyncio.run(
        client.make_invoice(
            amount_msat=25_000,
            description="Membership 2026-11",
            expiry_seconds=600,
        )
    )

    assert invoice.amount_msat == 25_000


def test_make_invoice_uses_requested_amount_when_wallet_omits_amount():
    """Invoice creation accepts wallet responses that omit the amount field."""
    session = FakeSession(
        invoice_response={
            "invoice": "lnbc-test",
            "payment_hash": "hash-001",
            "expires_at": 1_800_000_000,
        }
    )
    client = NwcInvoiceClient(NWC_URI, client_factory=lambda _: session)

    invoice = asyncio.run(
        client.make_invoice(
            amount_msat=25_000,
            description="Membership 2026-11",
            expiry_seconds=600,
        )
    )

    assert invoice.amount_msat == 25_000


def test_lookup_invoice_normalizes_settled_response():
    """Invoice lookup exposes settlement state and timestamp."""
    session = FakeSession(
        lookup_response={
            "payment_hash": "hash-001",
            "state": "settled",
            "amount": "25000msat",
            "settled_at": 1_800_000_100,
            "preimage": "00" * 32,
        }
    )
    client = NwcInvoiceClient(NWC_URI, client_factory=lambda _: session)

    status = asyncio.run(client.lookup_invoice(payment_hash="hash-001"))

    assert status.payment_hash == "hash-001"
    assert status.state == "settled"
    assert status.amount_msat == 25_000
    assert status.settled_at == 1_800_000_100
    assert status.preimage == "00" * 32
    assert status.is_settled is True


def test_payment_preimage_proves_payment_hash():
    """A valid 32-byte preimage hashes to the reported payment hash."""
    assert is_valid_payment_preimage(
        "66687aadf862bd776c8fc18b8e9f8e20089714856ee233b3902a591d0d5f2925",
        "00" * 32,
    ) is True


@pytest.mark.parametrize(
    ("payment_hash", "preimage"),
    [
        ("66687aadf862bd776c8fc18b8e9f8e20089714856ee233b3902a591d0d5f2925", None),
        ("66687aadf862bd776c8fc18b8e9f8e20089714856ee233b3902a591d0d5f2925", "11" * 32),
        ("not-a-hash", "00" * 32),
        ("66687aadf862bd776c8fc18b8e9f8e20089714856ee233b3902a591d0d5f2925", "invalid"),
    ],
)
def test_invalid_payment_preimage_does_not_prove_payment(payment_hash, preimage):
    """Missing or mismatched preimages cannot prove invoice settlement."""
    assert is_valid_payment_preimage(payment_hash, preimage) is False


def test_lookup_invoice_rejects_response_without_payment_hash():
    """An incomplete lookup response cannot inherit the requested hash."""
    session = FakeSession(
        lookup_response={
            "state": "settled",
            "amount": 25_000,
            "settled_at": 1_800_000_100,
        }
    )
    client = NwcInvoiceClient(NWC_URI, client_factory=lambda _: session)

    with pytest.raises(NwcClientError, match="payment_hash"):
        asyncio.run(client.lookup_invoice(payment_hash="hash-001"))


def test_transport_errors_do_not_include_connection_uri():
    """Transport failures use generic messages that cannot leak NWC secrets."""
    session = FakeSession(error=RuntimeError(f"failed for {NWC_URI}"))
    client = NwcInvoiceClient(NWC_URI, client_factory=lambda _: session)

    with pytest.raises(NwcTransportError) as error:
        asyncio.run(client.lookup_invoice(payment_hash="hash-001"))

    assert str(error.value) == "NWC invoice lookup failed."
    assert "super-secret" not in str(error.value)
