"""URL checks for partner webhook endpoints."""

from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import urlparse

from pik_api.errors import APIError

_EVENT = re.compile(r"^[a-z][a-z0-9_.]{1,63}$|^\*$")
_BLOCKED_HOSTS = {"metadata.google.internal", "metadata.internal"}


def validate_webhook_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise APIError(
            400,
            "invalid_request_error",
            "invalid_webhook_url",
            "Webhook URL must be an absolute http or https URL.",
        )
    if parsed.username or parsed.password:
        raise APIError(
            400,
            "invalid_request_error",
            "invalid_webhook_url",
            "Webhook URL must not include username or password.",
        )
    host = parsed.hostname.lower().rstrip(".")
    if host in _BLOCKED_HOSTS:
        raise APIError(
            400,
            "invalid_request_error",
            "webhook_url_blocked",
            "That webhook host is not allowed.",
        )
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    # Link-local is blocked (cloud metadata). Loopback and private nets stay
    # allowed so a partner can point webhooks at a local receiver.
    if ip is not None and (ip.is_link_local or ip.is_multicast or ip.is_unspecified):
        raise APIError(
            400,
            "invalid_request_error",
            "webhook_url_blocked",
            "That webhook host is not allowed.",
        )
    return url.strip()


def validate_event_names(events: list[str]) -> list[str]:
    cleaned: list[str] = []
    for name in events:
        if not _EVENT.match(name):
            raise APIError(
                400,
                "invalid_request_error",
                "invalid_event_type",
                f"Unsupported event name {name!r}. Use lowercase names such as order.created, or *.",
            )
        if name not in cleaned:
            cleaned.append(name)
    return cleaned


def canonical_json(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def loads(raw: str):
    return json.loads(raw)
