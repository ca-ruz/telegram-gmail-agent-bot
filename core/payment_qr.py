"""QR-code helpers for Lightning payment invoices."""

from io import BytesIO

import qrcode


def build_invoice_qr(invoice: str) -> BytesIO:
    """Render a BOLT11 invoice as an in-memory PNG image."""
    if not isinstance(invoice, str) or not invoice.strip():
        raise ValueError("A non-empty invoice is required.")

    qr_code = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr_code.add_data(invoice.strip())
    qr_code.make(fit=True)
    image = qr_code.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    buffer.name = "lightning-invoice.png"
    return buffer
