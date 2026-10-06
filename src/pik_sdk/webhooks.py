"""HMAC-SHA256 webhook signatures with a timestamp and replay window.

Canonical signed string: ``{unix_timestamp}.{raw_body}``.
Header value: ``t=<unix>,v1=<hex digest>``.

The API signs with this module. Receivers should verify with this module or
the TypeScript port in ``sdk/typescript/src/webhooks.ts``. Both are tested
against the same fixture.
"""

from __future__ import annotations

import hashlib
import hmac
import time

TIMESTAMP_EXPIRED = "timestamp_expired"
MISMATCH = "signature_mismatch"
MALFORMED = "malformed_signature"


class SignatureError(Exception):
    """Base class for webhook signature failures."""

    code = "signature_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class TimestampExpired(SignatureError):
    code = TIMESTAMP_EXPIRED


class SignatureMismatch(SignatureError):
    code = MISMATCH


class MalformedSignature(SignatureError):
    code = MALFORMED


def sign(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Return a ``Pik-Signature`` header value for ``payload``."""
    if timestamp is None:
        timestamp = int(time.time())
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes")
    digest = _digest(payload, secret, int(timestamp))
    return f"t={int(timestamp)},v1={digest}"


def verify(
    payload: bytes,
    header: str,
    secret: str,
    *,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> None:
    """Raise ``SignatureError`` when ``header`` does not authenticate ``payload``.

    ``tolerance_seconds`` is the allowed absolute skew between ``now`` and the
    timestamp embedded in the header. The default is five minutes.
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes")
    if tolerance_seconds < 0:
        raise ValueError("tolerance_seconds must be >= 0")
    timestamp, presented = _parse(header)
    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > tolerance_seconds:
        raise TimestampExpired(
            f"Signature timestamp {timestamp} is outside the {tolerance_seconds}s replay window."
        )
    expected = _digest(bytes(payload), secret, timestamp)
    if not hmac.compare_digest(expected, presented):
        raise SignatureMismatch("Webhook signature did not match the payload and secret.")


def _digest(payload: bytes, secret: str, timestamp: int) -> str:
    message = f"{timestamp}.".encode("ascii") + payload
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _parse(header: str) -> tuple[int, str]:
    if not header or not isinstance(header, str):
        raise MalformedSignature("Missing Pik-Signature header.")
    timestamp: int | None = None
    signature: str | None = None
    for part in header.split(","):
        piece = part.strip()
        if "=" not in piece:
            raise MalformedSignature("Malformed Pik-Signature header.")
        key, value = piece.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "t":
            if not value.isdigit():
                raise MalformedSignature("Signature timestamp is not an integer.")
            timestamp = int(value)
        elif key == "v1":
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value.lower()):
                raise MalformedSignature("Signature v1 digest is not 64 hex characters.")
            signature = value.lower()
    if timestamp is None or signature is None:
        raise MalformedSignature("Pik-Signature must include t and v1.")
    return timestamp, signature
