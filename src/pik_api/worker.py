"""Delivery worker process."""

from __future__ import annotations

import logging
import time

from pik_api.config import Settings, load_settings
from pik_api.db import init_database
from pik_api.webhooks.delivery import HttpxTransport, Transport, process_due, recover_stuck

log = logging.getLogger("pik.worker")


def tick(session_factory, transport: Transport, settings: Settings, *, now_iso: str | None = None) -> int:
    db = session_factory()
    try:
        recover_stuck(db, settings, now_iso=now_iso)
        return process_due(db, transport, settings, now_iso=now_iso)
    finally:
        db.close()


def serve(settings: Settings, session_factory, transport: Transport, *, sleep=time.sleep, max_ticks: int | None = None) -> int:
    completed = 0
    while True:
        count = tick(session_factory, transport, settings)
        completed += 1
        log.info("worker tick deliveries=%s", count)
        if max_ticks is not None and completed >= max_ticks:
            return 0
        sleep(settings.worker_poll_seconds)


def main() -> int:
    settings = load_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    _engine, session_factory = init_database(settings)
    log.info("webhook worker started database=%s", settings.database_url)
    try:
        return serve(settings, session_factory, HttpxTransport())
    except KeyboardInterrupt:
        log.info("webhook worker stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
