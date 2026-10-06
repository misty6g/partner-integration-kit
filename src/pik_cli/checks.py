"""Setup checks used by the ``pik check`` command."""

from __future__ import annotations

import json
import socket
import ssl
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

import httpx

from pik_sdk.webhooks import SignatureError, verify


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class Report:
    checks: list[Check]

    @property
    def failed(self) -> int:
        return sum(1 for check in self.checks if check.status == "FAIL")

    def render(self) -> str:
        lines = ["Partner Integration Kit — setup check", ""]
        width = max(len(check.name) for check in self.checks)
        for check in self.checks:
            lines.append(f"  {check.name.ljust(width)}  {check.status:<4}  {check.detail}")
        passed = sum(1 for check in self.checks if check.status == "PASS")
        skipped = sum(1 for check in self.checks if check.status == "SKIP")
        lines.append("")
        lines.append(f"{passed} passed, {self.failed} failed, {skipped} skipped")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "passed": sum(1 for check in self.checks if check.status == "PASS"),
            "failed": self.failed,
            "skipped": sum(1 for check in self.checks if check.status == "SKIP"),
            "checks": [asdict(check) for check in self.checks],
        }


def run_checks(
    api: httpx.Client,
    *,
    api_key: str,
    webhook_url: str | None,
    insecure: bool = False,
    timeout: float = 5.0,
) -> Report:
    checks: list[Check] = [check_api_key(api, api_key)]
    authed = checks[0].status == "PASS"
    if not webhook_url:
        skip = "pass --webhook-url to run this check"
        checks.append(Check("Webhook endpoint reachability", "SKIP", skip))
        checks.append(Check("Signature verification round-trip", "SKIP", skip))
        checks.append(Check("TLS certificate", "SKIP", skip))
        return Report(checks)
    checks.append(check_reachability(webhook_url, insecure=insecure, timeout=timeout))
    if authed:
        checks.append(check_signature(api, webhook_url, api_key))
    else:
        checks.append(Check("Signature verification round-trip", "SKIP", "skipped because the API key was rejected"))
    checks.append(check_tls(webhook_url, timeout=timeout))
    return Report(checks)


def check_api_key(api: httpx.Client, api_key: str) -> Check:
    name = "API key validity"
    try:
        response = api.get("/v1/account", headers={"X-API-Key": api_key})
    except httpx.HTTPError as exc:
        return Check(name, "FAIL", f"could not reach the API: {exc.__class__.__name__}")
    if response.status_code != 200:
        detail = _error_detail(response)
        return Check(name, "FAIL", f"HTTP {response.status_code} {detail}")
    body = response.json()
    return Check(name, "PASS", f"partner {body.get('name')} ({body.get('id')})")


def check_reachability(webhook_url: str, *, insecure: bool, timeout: float) -> Check:
    name = "Webhook endpoint reachability"
    try:
        response = httpx.post(
            webhook_url,
            json={"type": "pik.probe"},
            headers={"User-Agent": "PIK-Doctor/0.1"},
            timeout=timeout,
            follow_redirects=False,
            verify=not insecure,
        )
    except httpx.HTTPError as exc:
        return Check(name, "FAIL", f"{exc.__class__.__name__}: {exc}")
    return Check(name, "PASS", f"endpoint responded HTTP {response.status_code}")


def _api_headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key}


def check_signature(api: httpx.Client, webhook_url: str, api_key: str) -> Check:
    name = "Signature verification round-trip"
    created = api.post(
        "/v1/webhook_endpoints",
        headers=_api_headers(api_key),
        json={"url": webhook_url, "events": ["order.created", "order.cancelled", "order.fulfilled"]},
    )
    if created.status_code != 201:
        return Check(name, "FAIL", f"could not register endpoint: HTTP {created.status_code} {_error_detail(created)}")
    payload = created.json()
    endpoint_id = payload["id"]
    secret = payload["secret"]
    try:
        ping = api.post(f"/v1/webhook_endpoints/{endpoint_id}/ping", headers=_api_headers(api_key))
        if ping.status_code != 200:
            return Check(name, "FAIL", f"ping failed: HTTP {ping.status_code} {_error_detail(ping)}")
        body = ping.json()
        try:
            verify(body["body"].encode("utf-8"), body["signature"], secret)
        except SignatureError as exc:
            return Check(name, "FAIL", f"{exc.code}: {exc.message}")
        return Check(
            name,
            "PASS",
            f"v1 signature matched for {body['delivery_id']} (delivery {body['delivery_status']})",
        )
    finally:
        api.delete(f"/v1/webhook_endpoints/{endpoint_id}", headers=_api_headers(api_key))


def check_tls(webhook_url: str, *, timeout: float) -> Check:
    name = "TLS certificate"
    parsed = urlparse(webhook_url)
    if parsed.scheme == "http":
        return Check(name, "SKIP", "URL is not HTTPS")
    if parsed.scheme != "https" or not parsed.hostname:
        return Check(name, "FAIL", "URL is not HTTPS")
    host = parsed.hostname
    port = parsed.port or 443
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as secure:
                cert = secure.getpeercert()
        not_after = ""
        if isinstance(cert, dict):
            not_after = str(cert.get("notAfter", ""))
        detail = f"verified certificate for {host}"
        if not_after:
            detail = f"{detail}, not after {not_after}"
        return Check(name, "PASS", detail)
    except ssl.SSLCertVerificationError as exc:
        return Check(name, "FAIL", f"certificate verification failed: {exc.verify_message}")
    except (ssl.SSLError, OSError, TimeoutError) as exc:
        return Check(name, "FAIL", f"{exc.__class__.__name__}: {exc}")


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return response.text[:180]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return response.text[:180]
