"""FastAPI-level lifecycle tests for U5's job wiring (`POST`/`GET
/v1/triage`). Follows `tests/test_main.py`'s `TestClient` +
autouse-`TRIAGE_DB.clear()` pattern.

`test_full_lifecycle_completed_via_monkeypatched_chain` is the one test in
this whole plan that pushes a real, populated `path: list[CallEdge]`
(carrying a real `CallEdge.file: Path` value) through `TriageOut`'s JSON
serialization -- see `.agent/plan.md`'s step 7 and Critique #3 for why a
`path is not None or reason is not None` OR-assertion would not have been
enough.
"""

import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import TRIAGE_DB, app
from reachability.triage import job_runner
from reachability.triage.acquisition import AcquisitionError
from reachability.triage.index_adapter import build_repo_index

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import measure_l5  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "l5" / "02_transitive_three_hop"
FIXTURE_REPO = FIXTURE_DIR / "repo"

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_triage_db():
    TRIAGE_DB.clear()
    yield
    TRIAGE_DB.clear()


def _resolve_fixture_target() -> tuple[str, str]:
    repo_index = build_repo_index(FIXTURE_REPO)
    label = measure_l5.load_label(FIXTURE_DIR)
    target_module = measure_l5._module_dotted_name(label["sink"]["file"])
    target_symbol = measure_l5.resolve_target(
        repo_index.symbol_index, target_module, label["sink"]["line"], FIXTURE_DIR.name
    )
    return target_module, target_symbol


def _poll_until_terminal(triage_id: str, timeout_seconds: float = 30.0, interval_seconds: float = 0.2) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        body = client.get(f"/v1/triage/{triage_id}").json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(interval_seconds)
    pytest.fail(f"triage job {triage_id} did not reach a terminal status within {timeout_seconds}s")


def test_missing_target_module_returns_422():
    response = client.post(
        "/v1/triage", json={"package": "requests", "version": "2.31.0"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_full_lifecycle_completed_via_monkeypatched_chain(monkeypatch):
    """Must exercise a REAL populated `path`, not just "path or reason" --
    monkeypatches `acquire_source` to return the real, on-disk L5 fixture
    `02_transitive_three_hop`, known to produce a `REACHABLE` verdict with
    a 3-edge path (`label.json`'s `allowed_verdicts: ["reachable"]`).
    `build_repo_index` is left un-mocked, so it runs for real against that
    fixture directory; `run_triage_loop` also runs for real via the real
    `DeterministicPolicyStubLLMClient`.
    """
    target_module, target_symbol = _resolve_fixture_target()

    monkeypatch.setattr(
        job_runner, "acquire_source", lambda request, workdir: FIXTURE_REPO
    )

    response = client.post(
        "/v1/triage",
        json={
            "package": "unused",
            "version": "0.0.0",
            "target_module": target_module,
            "target_symbol": target_symbol,
        },
    )
    assert response.status_code == 202
    triage_id = response.json()["id"]

    body = _poll_until_terminal(triage_id)

    assert body["status"] == "completed"
    assert body["finding"]["result"]["verdict"] == "reachable"
    assert body["finding"]["result"]["path"] is not None
    assert len(body["finding"]["result"]["path"]) > 0
    # This is the field that actually exercises CallEdge.file: Path through
    # TriageOut's JSON serialization -- assert it round-tripped as a string.
    for edge in body["finding"]["result"]["path"]:
        assert isinstance(edge["file"], str)


def test_full_lifecycle_failed_via_monkeypatched_acquisition_error(monkeypatch):
    def _boom(request, workdir):
        raise AcquisitionError("simulated acquisition failure")

    monkeypatch.setattr(job_runner, "acquire_source", _boom)

    response = client.post(
        "/v1/triage",
        json={"package": "somepkg", "version": "1.0.0", "target_module": "somepkg"},
    )
    assert response.status_code == 202
    triage_id = response.json()["id"]

    body = _poll_until_terminal(triage_id)

    assert body["status"] == "failed"
    assert body["error"]
    assert isinstance(body["error"], str)


# Test-methodology note: BackgroundTasks runs inside the same ASGI coroutine
# the TestClient's ASGITransport awaits to completion before returning
# control to the caller (Starlette's Response.__call__ sends the body, then
# awaits self.background(), all within the one `await app(...)` call
# ASGITransport blocks on) -- so the POST call itself will typically already
# have completed the whole job by the time it returns under TestClient. The
# poll loop below is defensive/forward-looking (matches how a real client
# against a live uvicorn server must behave, where the response is flushed
# to the socket before the background task runs), not a sign that async
# dispatch is broken if it succeeds on the first GET.


@pytest.mark.network
def test_resolvable_package_reaches_completed():
    response = client.post(
        "/v1/triage",
        json={
            "package": "six",
            "version": "1.16.0",
            "target_module": "six",
            "target_symbol": "this_symbol_does_not_exist_in_six",
        },
    )
    assert response.status_code == 202
    triage_id = response.json()["id"]

    body = _poll_until_terminal(triage_id, timeout_seconds=180.0)

    assert body["status"] == "completed"
    assert body["finding"]["result"]["verdict"] in (
        "reachable",
        "reachable_only_from_tests",
        "not_reachable",
        "unknown",
    )
    result = body["finding"]["result"]
    assert result["path"] is not None or result["reason"] is not None


@pytest.mark.network
def test_unresolvable_package_reaches_failed():
    response = client.post(
        "/v1/triage",
        json={
            "package": "this-package-definitely-does-not-exist-reachability-triage",
            "version": "0.0.0",
            "target_module": "x",
            "target_symbol": None,
        },
    )
    assert response.status_code == 202
    triage_id = response.json()["id"]

    body = _poll_until_terminal(triage_id, timeout_seconds=180.0)

    assert body["status"] == "failed"
    assert body["error"]
