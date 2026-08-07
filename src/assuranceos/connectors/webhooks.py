from __future__ import annotations

import hashlib
import hmac


class WebhookSignatureError(ValueError):
    pass


def verify_hmac_sha256(*, payload: bytes, secret: bytes, signature_header: str) -> None:
    """Verify the common ``sha256=<hex>`` webhook signature format in constant time."""

    prefix = "sha256="
    if not signature_header.startswith(prefix):
        raise WebhookSignatureError("webhook signature must use sha256")
    provided = signature_header[len(prefix) :].lower()
    if len(provided) != 64 or any(ch not in "0123456789abcdef" for ch in provided):
        raise WebhookSignatureError("webhook signature is malformed")
    expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided, expected):
        raise WebhookSignatureError("webhook signature verification failed")
