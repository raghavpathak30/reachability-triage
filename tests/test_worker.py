"""U3 unit tests for `src/reachability/triage/worker.py`: the claim
query, the guarded finalize, and `run_worker_once` end-to-end.

`test_finalize_job_guard_rejects_stale_attempt_count` is the one
success-criteria-mandated test that must assert the guarded `UPDATE`
affects zero rows explicitly -- not just that `finalize_job` "doesn't
crash." See `agent_docs/PHASE3_PERSISTENCE.md` U3 step 3 and this
project's own `worker.py` docstring for why that guard is the actual
anti-corruption mechanism this unit exists to build.
"""

import sys
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from reachability.db.models import TriageJob
from reachability.triage import job_runner
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.worker import (
    claim_next_queued_job,
    finalize_job,
    run_worker_once,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import measure_l5  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "l5" / "02_transitive_three_hop"
FIXTURE_REPO = FIXTURE_DIR / "repo"


def _resolve_fixture_target() -> tuple[str, str]:
    repo_index = build_repo_index(FIXTURE_REPO)
    label = measure_l5.load_label(FIXTURE_DIR)
    target_module = measure_l5._module_dotted_name(label["sink"]["file"])
    target_symbol = measure_l5.resolve_target(
        repo_index.symbol_index, target_module, label["sink"]["line"], FIXTURE_DIR.name
    )
    return target_module, target_symbol


def _insert_queued_job(db_session, target: dict) -> uuid.UUID:
    job_id = uuid.uuid4()
    job = TriageJob(id=job_id, status="queued", target=target, finding=None, error=None)
    db_session.add(job)
    db_session.commit()
    return job_id


def _session_factory_for(db_session) -> sessionmaker:
    return sessionmaker(bind=db_session.get_bind())


def test_claim_next_queued_job_claims_oldest_queued_row(db_session):
    target = {"package": "requests", "version": "2.31.0", "target_module": "placeholder", "target_symbol": None}
    job_id = _insert_queued_job(db_session, target)

    claimed = claim_next_queued_job(db_session, "worker-1")

    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["target"] == target
    assert claimed["attempt_count"] == 1

    row = db_session.get(TriageJob, job_id)
    assert row.status == "running"
    assert row.worker_id == "worker-1"
    assert row.claimed_at is not None


def test_claim_next_queued_job_empty_queue_returns_none(db_session):
    claimed = claim_next_queued_job(db_session, "worker-1")
    assert claimed is None


def test_finalize_job_guard_rejects_stale_attempt_count(db_session):
    target = {"package": "requests", "version": "2.31.0", "target_module": "placeholder", "target_symbol": None}
    job_id = _insert_queued_job(db_session, target)

    # worker-1 claims the job (attempt_count -> 1).
    claimed = claim_next_queued_job(db_session, "worker-1")
    assert claimed["attempt_count"] == 1

    # Simulate the reaper resetting it and worker-2 reclaiming it
    # (attempt_count -> 2) while worker-1 is still (slowly) executing.
    db_session.execute(
        text(
            "UPDATE triage_jobs SET status = 'queued', worker_id = NULL, "
            "claimed_at = NULL WHERE id = :id"
        ),
        {"id": job_id},
    )
    db_session.commit()
    reclaimed = claim_next_queued_job(db_session, "worker-2")
    assert reclaimed["attempt_count"] == 2

    # worker-1's stale finalize call, still carrying claimed_attempt_count=1.
    stale_record = {"status": "completed", "finding": None, "error": None}
    affected = finalize_job(db_session, job_id, "worker-1", 1, stale_record)

    assert affected is False

    row = db_session.get(TriageJob, job_id)
    # worker-2's claimed state (running, worker_id="worker-2",
    # attempt_count=2) must be untouched by worker-1's stale finalize --
    # never overwritten with worker-1's ("completed") stale_record.
    assert row.status == "running"
    assert row.worker_id == "worker-2"
    assert row.attempt_count == 2


def test_run_worker_once_end_to_end(db_session, monkeypatch):
    target_module, target_symbol = _resolve_fixture_target()
    target = {
        "package": "unused",
        "version": "0.0.0",
        "target_module": target_module,
        "target_symbol": target_symbol,
    }
    job_id = _insert_queued_job(db_session, target)

    monkeypatch.setattr(job_runner, "acquire_source", lambda request, workdir: FIXTURE_REPO)

    session_factory = _session_factory_for(db_session)
    claimed_something = run_worker_once(session_factory, "test-worker", budget=30)

    assert claimed_something is True

    row = db_session.get(TriageJob, job_id)
    db_session.refresh(row)
    assert row.status == "completed"
    assert row.finding is not None
    assert row.finding["result"]["verdict"] == "reachable"
    assert row.worker_id == "test-worker"
    assert row.attempt_count == 1


def test_run_worker_once_empty_queue_returns_false(db_session):
    session_factory = _session_factory_for(db_session)
    assert run_worker_once(session_factory, "test-worker", budget=30) is False


def test_run_worker_once_malformed_target_finalizes_as_failed_not_crash(db_session):
    """A stored `target` that no longer matches `TriageRequest`'s schema
    (here: missing the required `target_module`) must finalize the row as
    `failed`, not raise out of `run_worker_once` and crash the caller --
    see `worker.py`'s `run_worker_once` docstring for why this guard
    exists (a reviewer-flagged gap: `_target_to_triage_request` sits
    outside `run_triage_job`'s own "never raises" guarantee).
    """
    malformed_target = {"package": "requests", "version": "2.31.0"}  # missing target_module
    job_id = _insert_queued_job(db_session, malformed_target)

    session_factory = _session_factory_for(db_session)
    claimed_something = run_worker_once(session_factory, "test-worker", budget=30)

    assert claimed_something is True

    row = db_session.get(TriageJob, job_id)
    db_session.refresh(row)
    assert row.status == "failed"
    assert row.error is not None
    assert row.finding is None
    assert row.worker_id == "test-worker"
    assert row.attempt_count == 1
