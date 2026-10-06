"""Write the FastAPI OpenAPI document to openapi/openapi.json."""

from __future__ import annotations

import json
from pathlib import Path

from pik_api.config import Settings
from pik_api.main import create_app


def main() -> int:
    app = create_app(Settings(database_url="sqlite:///:memory:", seed_demo=False))
    spec = app.openapi()
    destination = Path(__file__).resolve().parents[1] / "openapi" / "openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    app.state.engine.dispose()
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
