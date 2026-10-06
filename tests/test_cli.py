"""Onboarding CLI checks."""

from __future__ import annotations

import socket
import ssl
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from pik_cli.checks import check_tls, run_checks
from pik_cli.main import main


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(204)
        self.end_headers()

    def log_message(self, _format, *_args):
        return


@pytest.fixture
def webhook_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/hooks"
    finally:
        server.shutdown()


def test_check_reports_pass_fail_and_skip(client, partner, webhook_url):
    report = run_checks(client, api_key=partner["key"], webhook_url=webhook_url)
    by_name = {check.name: check for check in report.checks}
    assert by_name["API key validity"].status == "PASS", by_name["API key validity"].detail
    assert by_name["Webhook endpoint reachability"].status == "PASS", by_name["Webhook endpoint reachability"].detail
    assert by_name["Signature verification round-trip"].status == "PASS", by_name["Signature verification round-trip"].detail
    assert "v1 signature matched" in by_name["Signature verification round-trip"].detail
    assert by_name["TLS certificate"].status == "SKIP"
    assert report.failed == 0

    closed = run_checks(client, api_key="pk_test_missing", webhook_url="http://127.0.0.1:9/hooks")
    closed_by_name = {check.name: check for check in closed.checks}
    assert closed_by_name["API key validity"].status == "FAIL"
    assert closed_by_name["Webhook endpoint reachability"].status == "FAIL"
    assert closed_by_name["Signature verification round-trip"].status == "SKIP"
    assert closed.failed == 2


def test_cli_version_help_and_check(capsys, app, partner, webhook_url, monkeypatch):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0"

    help_code = main(["help", "429 rate_limit_exceeded Too many requests. Retry-After 12"])
    assert help_code == 0
    output = capsys.readouterr().out
    assert "Mode: retrieval" in output
    assert "Retry-After" in output or "rate" in output.lower()

    def fake_run_checks(*_args, **_kwargs):
        from pik_cli.checks import Check, Report

        return Report([Check("API key validity", "FAIL", "nope")])

    monkeypatch.setattr("pik_cli.main.run_checks", fake_run_checks)
    monkeypatch.setattr("pik_cli.main.httpx.Client", lambda **_kwargs: _NullClient())
    assert main(["check", "--api-key", partner["key"], "--json"]) == 1
    assert '"failed": 1' in capsys.readouterr().out


def test_http_skips_tls_and_self_signed_fails(tmp_path):
    skipped = check_tls("http://127.0.0.1:9/hooks", timeout=1)
    assert skipped.status == "SKIP"

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(5)
    sock.settimeout(3)

    def accept_one():
        try:
            conn, _addr = sock.accept()
        except OSError:
            return
        try:
            with context.wrap_socket(conn, server_side=True):
                pass
        except ssl.SSLError:
            pass

    thread = Thread(target=accept_one, daemon=True)
    thread.start()
    try:
        result = check_tls(f"https://127.0.0.1:{port}/hooks", timeout=2)
    finally:
        sock.close()
    assert result.status == "FAIL"
    assert "certificate" in result.detail.lower() or "ssl" in result.detail.lower()


class _NullClient:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False
