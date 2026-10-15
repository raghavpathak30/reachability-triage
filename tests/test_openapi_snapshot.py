import json
from pathlib import Path

from main import app

SNAPSHOT = Path(__file__).resolve().parents[1] / "web" / "openapi.json"


def test_openapi_snapshot_matches_app():
    expected = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    actual = SNAPSHOT.read_text(encoding="utf-8")
    assert actual == expected, (
        "web/openapi.json is stale. Run `.venv/bin/python scripts/dump_openapi.py` "
        "then `npm run gen:api` in web/."
    )
