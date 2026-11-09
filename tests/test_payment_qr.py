"""Tests for rendering Lightning invoices as QR codes."""

from io import BytesIO

import pytest

from core.payment_qr import build_invoice_qr


def test_build_invoice_qr_returns_rewound_png_buffer():
    """A valid invoice becomes a Telegram-compatible PNG buffer."""
    buffer = build_invoice_qr("lnbc100n1test-invoice")

    assert isinstance(buffer, BytesIO)
    assert buffer.name == "lightning-invoice.png"
    assert buffer.tell() == 0
    assert buffer.read(8) == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("invoice", ["", "   ", None])
def test_build_invoice_qr_rejects_empty_invoice(invoice):
    """Empty invoice data cannot produce a misleading QR code."""
    with pytest.raises(ValueError, match="non-empty invoice"):
        build_invoice_qr(invoice)
