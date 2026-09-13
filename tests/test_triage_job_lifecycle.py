"""FastAPI-level lifecycle tests for U5's job wiring (`POST`/`GET
/v1/triage`), now driven end-to-end through U3's polling worker
(`run_worker_once`) instead of the removed in-memory job dict /
`BackgroundTasks` dispatch.

`test_full_lifecycle_completed_via_monkeypatched_chain` is the one test in
this whole plan that pushes a real, populated `path: list[CallEdge]`
(carrying a real `CallEdge.file: Path` value) through `TriageOut`'s JSON
serialization -- see `.agent/plan.md`'s step 7 and Critique #3 for why a
`path is not None or reason is not None` OR-assertion would not have been
enough.

The monkeypatched-chain tests call `run_worker_once` explicitly and
synchronously right after `POST /v1/triage` (it only returns once the job
is finalized, so no polling loop is needed there). The `@pytest.mark.network`
tests keep their poll loop, but run `run_worker_once` in a background
thread for the poll's duration -- simulating the independent worker
process a real deployment has. That thread's lifecycle is explicit, not
fire-and-forget: a `threading.Event` stop flag checked at the top of each
loop iteration, started before the poll begins, and `thread.join(timeout=...)`
in a `try/finally` around the poll (including the path where
`_poll_until_terminal` itself raises via `pytest.fail` on timeout) so the
thread is always confirmed stopped before the test function returns.
Without this, an unstopped thread could keep calling `claim_next_queued_job`
past this test's own teardown, racing the next test's `db_session`
`TRUNCATE triage_jobs` fixture and claiming rows the next test just
inserted -- a source of flaky, hard-to-reproduce failures elsewhere in the
suite if left unbounded.
"""

import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from main import app
from reachability.triage import job_runner
from reachability.triage.acquisition import AcquisitionError
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.job_runner import DEFAULT_TOOL_CALL_BUDGET
from reachability.triage.worker import run_worker_once

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import measure_l5  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "l5" / "02_transitive_three_hop"
FIXTURE_REPO = FIXTURE_DIR / "repo"

client = TestClient(app)


@pytest.fixture(autouse=True)
def _postgres(db_session):
    yield


def _resolve_fixture_target() -> tuple[str, str]:
    repo_index = build_repo_index(FIXTURE_REPO)
    label = measure_l5.load_label(FIXTURE_DIR)
    target_module = measure_l5._module_dotted_name(label["sink"]["file"])
    target_symbol = measure_l5.resolve_target(
        repo_index.symbol_index, target_module, label["sink"]["line"], FIXTURE_DIR.name
    )
    return target_module, target_symbol


def _session_factory_for(db_session) -> sessionmaker:
    return sessionmaker(bind=db_session.get_bind())


def _poll_until_terminal(triage_id: str, timeout_seconds: float = 30.0, interval_seconds: float = 0.2) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        body = client.get(f"/v1/triage/{triage_id}").json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(interval_seconds)
    pytest.fail(f"triage job {triage_id} did not reach a terminal status within {timeout_seconds}s")


def _background_worker_loop(
    session_factory: sessionmaker, worker_id: str, budget: int, stop_event: threading.Event
) -> None:
    """Runs `run_worker_once` in a loop, checked against `stop_event` at
    the top of every iteration, standing in for a real deployment's
    independent worker process for the duration of one test's poll.
    """
    while not stop_event.is_set():
        claimed = run_worker_once(session_factory, worker_id, budget)
        if not claimed:
            stop_event.wait(0.2)


def test_missing_target_module_returns_422():
    response = client.post(
        "/v1/triage", json={"package": "requests", "version": "2.31.0"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_full_lifecycle_completed_via_monkeypatched_chain(monkeypatch, db_session):
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

    session_factory = _session_factory_for(db_session)
    claimed = run_worker_once(session_factory, "test-worker", DEFAULT_TOOL_CALL_BUDGET)
    assert claimed is True

    body = client.get(f"/v1/triage/{triage_id}").json()

    assert body["status"] == "completed"
    assert body["finding"]["result"]["verdict"] == "reachable"
    assert body["finding"]["result"]["path"] is not None
    assert len(body["finding"]["result"]["path"]) > 0
    # This is the field that actually exercises CallEdge.file: Path through
    # TriageOut's JSON serialization -- assert it round-tripped as a string.
    for edge in body["finding"]["result"]["path"]:
        assert isinstance(edge["file"], str)


def test_full_lifecycle_failed_via_monkeypatched_acquisition_error(monkeypatch, db_session):
    def _boom(request, workdir):
        raise AcquisitionError("simulated acquisition failure")

    monkeypatch.setattr(job_runner, "acquire_source", _boom)

    response = client.post(
        "/v1/triage",
        json={"package": "somepkg", "version": "1.0.0", "target_module": "somepkg"},
    )
    assert response.status_code == 202
    triage_id = response.json()["id"]

    session_factory = _session_factory_for(db_session)
    claimed = run_worker_once(session_factory, "test-worker", DEFAULT_TOOL_CALL_BUDGET)
    assert claimed is True

    body = client.get(f"/v1/triage/{triage_id}").json()

    assert body["status"] == "failed"
    assert body["error"]
    assert isinstance(body["error"], str)


@pytest.mark.network
def test_resolvable_package_reaches_completed(db_session):
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

    session_factory = _session_factory_for(db_session)
    stop_event = threading.Event()
    worker_thread = threading.Thread(
        target=_background_worker_loop,
        args=(session_factory, "network-test-worker", DEFAULT_TOOL_CALL_BUDGET, stop_event),
        daemon=True,
    )
    worker_thread.start()
    try:
        body = _poll_until_terminal(triage_id, timeout_seconds=180.0)
    finally:
        stop_event.set()
        worker_thread.join(timeout=10.0)

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
def test_unresolvable_package_reaches_failed(db_session):
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

    session_factory = _session_factory_for(db_session)
    stop_event = threading.Event()
    worker_thread = threading.Thread(
        target=_background_worker_loop,
        args=(session_factory, "network-test-worker", DEFAULT_TOOL_CALL_BUDGET, stop_event),
        daemon=True,
    )
    worker_thread.start()
    try:
        body = _poll_until_terminal(triage_id, timeout_seconds=180.0)
    finally:
        stop_event.set()
        worker_thread.join(timeout=10.0)

    assert body["status"] == "failed"
    assert body["error"]
