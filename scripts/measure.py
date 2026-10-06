"""Measure coverage, webhook recovery, API latency, and help-bot retrieval.

Writes measurements/results.json. Every number in the README comes from this script.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import platform
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pik_api.config import Settings  # noqa: E402
from pik_api.main import create_app  # noqa: E402
from pik_api.models import WebhookDelivery  # noqa: E402
from pik_api.seed import create_partner_with_key  # noqa: E402
from pik_api.webhooks.delivery import HttpxTransport, process_due  # noqa: E402
from pik_bot.__main__ import evaluate  # noqa: E402
from pik_sdk.webhooks import SignatureError, verify  # noqa: E402

LOAD_PORT = 8411
DEMO_KEY = "pk_test_acme_7f3a9c2e1b84d0"
GET_SAMPLES = 200
POST_SAMPLES = 100
CONCURRENCY = 8
WARMUP = 20


def percentile(values: list[float], pct: float) -> float:
    """Linear interpolation between closest ranks. ``pct`` is 0-100."""
    ordered = sorted(values)
    if not ordered:
        raise RuntimeError("no samples")
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def run_pytest() -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--cov=src", "--cov-report=term", "-o", "addopts="],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    output = completed.stdout + "\n" + completed.stderr
    if completed.returncode != 0:
        raise RuntimeError(output[-4000:])
    passed = re.search(r"(\d+) passed", output)
    total = re.search(r"^TOTAL\s+.*?\s+(\d+(?:\.\d+)?)%\s*$", output, re.M)
    if not passed or not total:
        raise RuntimeError("could not parse pytest output\n" + output[-2000:])
    return {
        "command": "python -m pytest --cov=src --cov-report=term -o addopts=",
        "passed": int(passed.group(1)),
        "coverage_percent": float(total.group(1)),
        "coverage_includes_branches": True,
    }


def run_node_tests() -> dict:
    completed = subprocess.run(
        ["npm", "test"],
        cwd=ROOT / "sdk" / "typescript",
        check=False,
        capture_output=True,
        text=True,
    )
    output = completed.stdout + "\n" + completed.stderr
    if completed.returncode != 0:
        raise RuntimeError(output[-4000:])
    tests = re.search(r"^# tests (\d+)$", output, re.M)
    passed = re.search(r"^# pass (\d+)$", output, re.M)
    if not tests or not passed:
        raise RuntimeError("could not parse node:test output\n" + output[-2000:])
    return {
        "command": "npm test",
        "working_directory": "sdk/typescript",
        "tests": int(tests.group(1)),
        "passed": int(passed.group(1)),
    }


def webhook_drill(tmp: Path) -> dict:
    settings = Settings(
        database_url=f"sqlite:///{tmp}/webhooks-{time.time_ns()}.db",
        seed_demo=False,
        rate_limit=100_000,
        webhook_max_attempts=5,
        webhook_backoff_base_seconds=0.05,
        webhook_backoff_cap_seconds=0.4,
        webhook_timeout_seconds=2.0,
    )
    state = {
        "secret": "",
        "always_fail": set(),
        "attempts": {},
        "signature_ok": 0,
        "signature_failures": 0,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            delivery_id = self.headers.get("Pik-Delivery", "")
            try:
                verify(body, self.headers.get("Pik-Signature", ""), state["secret"])
            except SignatureError:
                state["signature_failures"] += 1
                self.send_response(401)
                self.end_headers()
                return
            state["signature_ok"] += 1
            count = state["attempts"].get(delivery_id, 0) + 1
            state["attempts"][delivery_id] = count
            if count == 1 or delivery_id in state["always_fail"]:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"injected failure")
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/hooks"
    app = create_app(settings)
    from fastapi.testclient import TestClient

    key = "pk_test_measure_webhook_key"
    db = app.state.session_factory()
    try:
        _partner, _api_key = create_partner_with_key(db, "Measure Partner", key)
        db.commit()
    finally:
        db.close()
    started = time.perf_counter()
    try:
        with TestClient(app) as client:
            headers = {"X-API-Key": key}
            created = client.post(
                "/v1/webhook_endpoints",
                headers=headers,
                json={"url": url, "events": ["order.created"]},
            )
            assert created.status_code == 201, created.text
            state["secret"] = created.json()["secret"]
            for index in range(30):
                response = client.post(
                    "/v1/orders",
                    headers=headers,
                    json={
                        "external_id": f"measure-{index}",
                        "currency": "usd",
                        "items": [{"sku": "WIDGET-1", "quantity": 1, "unit_amount": 100}],
                    },
                )
                assert response.status_code == 201, response.text
            db = app.state.session_factory()
            try:
                rows = list(
                    db.scalars(select(WebhookDelivery).order_by(WebhookDelivery.created_at, WebhookDelivery.id))
                )
                if len(rows) != 30:
                    raise RuntimeError(f"expected 30 deliveries, found {len(rows)}")
                state["always_fail"] = {rows[index].id for index in range(30) if index % 5 == 0}
                deadline = time.perf_counter() + 20
                ticks = 0
                while time.perf_counter() < deadline:
                    ticks += 1
                    process_due(db, HttpxTransport(), settings, limit=50)
                    db.expire_all()
                    rows = list(db.scalars(select(WebhookDelivery)))
                    if rows and all(row.status in {"succeeded", "dead_letter"} for row in rows):
                        break
                    time.sleep(0.02)
                else:
                    statuses = {row.id: row.status for row in rows}
                    raise RuntimeError(f"drill timed out: {statuses}")
                succeeded = [row for row in rows if row.status == "succeeded"]
                dead = [row for row in rows if row.status == "dead_letter"]
                elapsed = time.perf_counter() - started
                return {
                    "command": "python scripts/measure.py",
                    "deliveries": len(rows),
                    "injection": "HTTP 500 on attempt 1 for every delivery; 6 of 30 (indexes 0,5,10,15,20,25) return HTTP 500 on every attempt",
                    "max_attempts": settings.webhook_max_attempts,
                    "backoff_base_seconds": settings.webhook_backoff_base_seconds,
                    "succeeded": len(succeeded),
                    "dead_letter": len(dead),
                    "success_rate": len(succeeded) / len(rows),
                    "signature_checks_passed": state["signature_ok"],
                    "signature_checks_failed": state["signature_failures"],
                    "median_attempts_when_succeeded": percentile([float(row.attempt_count) for row in succeeded], 50),
                    "worker_ticks": ticks,
                    "elapsed_seconds": round(elapsed, 3),
                }
            finally:
                db.close()
    finally:
        server.shutdown()
        app.state.engine.dispose()


def _wait_health(base: str) -> None:
    deadline = time.time() + 15
    last = ""
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base}/v1/health", timeout=1)
            if response.status_code == 200:
                return
            last = response.text
        except httpx.HTTPError as exc:
            last = str(exc)
        time.sleep(0.1)
    raise RuntimeError(f"load-test server did not become healthy: {last}")


async def _time_requests(method: str, url: str, headers: dict, payloads: list[dict | None]) -> dict:
    samples: list[float] = []
    errors = 0
    semaphore = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=10) as client:

        async def one(payload: dict | None) -> None:
            nonlocal errors
            async with semaphore:
                started = time.perf_counter()
                try:
                    response = await client.request(method, url, headers=headers, json=payload)
                except httpx.HTTPError:
                    errors += 1
                    return
                elapsed_ms = (time.perf_counter() - started) * 1000
            if response.status_code >= 400:
                errors += 1
                return
            samples.append(elapsed_ms)

        await asyncio.gather(*[one(None) for _ in range(WARMUP)])
        samples.clear()
        errors = 0
        await asyncio.gather(*[one(payload) for payload in payloads])
    if not samples:
        raise RuntimeError(f"no successful {method} samples; errors={errors}")
    return {
        "samples": len(samples),
        "errors": errors,
        "concurrency": CONCURRENCY,
        "warmup_excluded": WARMUP,
        "p50_ms": round(percentile(samples, 50), 3),
        "p95_ms": round(percentile(samples, 95), 3),
    }


def load_test(tmp: Path) -> dict:
    database = tmp / f"load-{time.time_ns()}.db"
    env = os.environ.copy()
    env["PIK_DATABASE_URL"] = f"sqlite:///{database}"
    env["PIK_SEED_DEMO"] = "1"
    env["PIK_RATE_LIMIT"] = "100000"
    env["PIK_LOG_LEVEL"] = "WARNING"
    log_path = tmp / "uvicorn.log"
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "pik_api.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(LOAD_PORT),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{LOAD_PORT}"
    try:
        _wait_health(base)
        headers = {"X-API-Key": DEMO_KEY}
        gets = asyncio.run(_time_requests("GET", f"{base}/v1/orders", headers, [None] * GET_SAMPLES))
        posts = asyncio.run(
            _time_requests(
                "POST",
                f"{base}/v1/orders",
                headers,
                [
                    {
                        "external_id": f"load-{index}",
                        "currency": "usd",
                        "items": [{"sku": "WIDGET-1", "quantity": 1, "unit_amount": 2500}],
                    }
                    for index in range(POST_SAMPLES)
                ],
            )
        )
        return {
            "command": f"uvicorn on 127.0.0.1:{LOAD_PORT}, one process, SQLite",
            "get_orders": gets,
            "post_orders": posts,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pik").setLevel(logging.WARNING)
    tmp = ROOT / ".pik" / "measure"
    tmp.mkdir(parents=True, exist_ok=True)
    pytest_result = run_pytest()
    node_result = run_node_tests()
    retrieval = evaluate()
    webhooks = webhook_drill(tmp)
    latency = load_test(tmp)
    results = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pytest": pytest_result,
        "typescript_webhook_tests": node_result,
        "retrieval": {
            "command": "python -m pik_bot",
            "cases": retrieval["cases"],
            "top1_hits": retrieval["top1_hits"],
            "top3_hits": retrieval["top3_hits"],
            "top1_accuracy": retrieval["top1_accuracy"],
            "top3_accuracy": retrieval["top3_accuracy"],
            "labeled_set": "src/pik_bot/eval_set.json",
        },
        "webhooks": webhooks,
        "latency": latency,
    }
    destination = ROOT / "measurements" / "results.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
