#!/usr/bin/env python
"""Capture real error responses from the app into web/src/api/__fixtures__/.

Run with the project venv:
    .venv/bin/python scripts/dump_error_envelopes.py           # write
    .venv/bin/python scripts/dump_error_envelopes.py --check   # diff only

Needs no DATABASE_URL: the session factory and get_job are replaced on the
`main` module (no main.py edit). Ids are hard-coded because the 404 message
embeds the id. The fixtures pin pydantic's 422 message strings, so a
pydantic upgrade requires regenerating them (rerun without --check).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

OUT_DIR = REPO_ROOT / "web" / "src" / "api" / "__fixtures__"
ZERO_UUID = "00000000-0000-0000-0000-000000000000"


class _DummySession:
    def close(self) -> None:
        return None


def _capture() -> dict[str, dict]:
    main.get_sessionmaker = lambda: (lambda: _DummySession())
    main.get_job = lambda session, triage_id: None
    client = TestClient(main.app)
    cases = {
        "422-empty-body": lambda: client.post("/v1/triage", json={}),
        "422-missing-version": lambda: client.post(
            "/v1/triage",
            json={"package": "six", "target_module": "six"},
        ),
        "404-valid-uuid": lambda: client.get(f"/v1/triage/{ZERO_UUID}"),
        "404-unknown-route": lambda: client.get("/nope"),
        "404-not-a-uuid": lambda: client.get("/v1/triage/not-a-uuid"),
    }
    out = {}
    for name, call in cases.items():
        resp = call()
        out[name] = {"status": resp.status_code, "body": resp.json()}
    return out


def _render(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main_cli(argv: list[str]) -> int:
    captured = _capture()
    check = "--check" in argv
    stale = []
    for name, payload in captured.items():
        path = OUT_DIR / f"{name}.json"
        text = _render(payload)
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    if stale:
        sys.stderr.write(
            "stale fixtures: " + ", ".join(stale)
            + "; rerun scripts/dump_error_envelopes.py\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main_cli(sys.argv[1:]))
