"""Webhook signature helper, including the published test vector."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pik_sdk.webhooks import (
    MalformedSignature,
    SignatureMismatch,
    TimestampExpired,
    sign,
    verify,
)

PAYLOAD = b'{"id":"evt_test","type":"webhook.ping"}'
SECRET = "whsec_test_secret"
TIMESTAMP = 1_700_000_000
HEADER = "t=1700000000,v1=14f56858cb772a58ae166d4bb7989b72117a5b8cfcb1b2ee7ba3d58b23a77cfd"
ROOT = Path(__file__).resolve().parents[1]


def test_known_vector_and_replay_window():
    assert sign(PAYLOAD, SECRET, timestamp=TIMESTAMP) == HEADER
    digest = HEADER.split("v1=", 1)[1]
    verify(PAYLOAD, f"t={TIMESTAMP},v1={digest.upper()}", SECRET, now=TIMESTAMP)
    verify(PAYLOAD, f"t={TIMESTAMP},v1={digest}", SECRET, now=TIMESTAMP + 300)
    with pytest.raises(TimestampExpired) as expired:
        verify(PAYLOAD, HEADER, SECRET, now=TIMESTAMP + 301)
    assert expired.value.code == "timestamp_expired"
    with pytest.raises(SignatureMismatch):
        verify(PAYLOAD + b" ", HEADER, SECRET, now=TIMESTAMP)
    with pytest.raises(MalformedSignature):
        verify(PAYLOAD, "v1=abc", SECRET, now=TIMESTAMP)
    with pytest.raises(TypeError):
        sign("not-bytes", SECRET)  # type: ignore[arg-type]


def test_typescript_helper_accepts_python_signature():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    package = ROOT / "sdk" / "typescript"
    completed = subprocess.run(
        [node, "--experimental-strip-types", "--test", "test/webhooks.test.ts"],
        cwd=package,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    direct = ROOT / "sdk" / "typescript" / "test" / "_parity_check.ts"
    direct.write_text(
        "\n".join(
            [
                "import { verifyWebhook } from '../src/webhooks.ts';",
                "const [payload, header, secret, now] = process.argv.slice(2);",
                "verifyWebhook(Buffer.from(payload), header, secret, { now: Number(now) });",
                "console.log('ok');",
                "",
            ]
        ),
        encoding="utf-8",
    )
    try:
        checked = subprocess.run(
            [node, "--experimental-strip-types", str(direct), PAYLOAD.decode(), HEADER, SECRET, str(TIMESTAMP)],
            cwd=package,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        direct.unlink(missing_ok=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert checked.stdout.strip() == "ok"
