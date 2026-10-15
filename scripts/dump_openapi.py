#!/usr/bin/env python
"""Write web/openapi.json from the FastAPI app's generated OpenAPI schema.

Run with the project venv (system Python lacks fastapi):
    .venv/bin/python scripts/dump_openapi.py

Needs no DATABASE_URL. After a backend schema change, rerun this, then
`npm run gen:api` in web/. tests/test_openapi_snapshot.py fails on drift.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import main  # noqa: E402

OUT = REPO_ROOT / "web" / "openapi.json"


def render() -> str:
    return json.dumps(main.app.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.write_text(render(), encoding="utf-8")
