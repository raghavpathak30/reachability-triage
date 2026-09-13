"""U4 tests for `src/reachability/triage/reaper.py`.

`test_reap_emits_structured_warning_log` is the sign-off-required
observability test: `reap_stale_jobs` must emit exactly one
`logging.warning` per reaped row carrying the job's id, its pre-reap
`attempt_count`, and an `elapsed_seconds` value `>= timeout_seconds` --
see `agent_docs/PHASE3_PERSISTENCE.md` U4 and this project's own
`reaper.py` docstring for why.
"""

import logging
import re
import uuid

from sqlalchemy import text

from reachability.db.models import TriageJob
from reachability.triage.reaper import reap_stale_jobs


def _insert_running_job(
    db_session,
    *,
    updated_at_offset_seconds: float,
    attempt_count: int = 1,
    worker_id: str = "worker-1",
) -> uuid.UUID:
    """Insert a job and directly backdate it into a `running` state via a
    raw `UPDATE ... SET updated_at = now() - interval` -- not through the
    ORM's `server_default`, which only fires on insert and cannot be used
    to backdate an existing row.
    """
    job_id = uuid.uuid4()
    job = TriageJob(id=job_id, status="queued", target={}, finding=None, error=None)
    db_session.add(job)
    db_session.commit()

    db_session.execute(
        text(
            "UPDATE triage_jobs SET status = 'running', worker_id = :worker_id, "
            "claimed_at = now(), attempt_count = :attempt_count, "
            "updated_at = now() - make_interval(secs => :offset) "
            "WHERE id = :id"
        ),
        {
            "worker_id": worker_id,
            "attempt_count": attempt_count,
            "offset": updated_at_offset_seconds,
            "id": job_id,
        },
    )
    db_session.commit()
    return job_id


def test_reap_resets_stale_running_job_to_queued(db_session):
    job_id = _insert_running_job(
        db_session, updated_at_offset_seconds=61, attempt_count=3, worker_id="worker-1"
    )

    reaped_count = reap_stale_jobs(db_session, timeout_seconds=60)

    assert reaped_count == 1
    row = db_session.get(TriageJob, job_id)
    db_session.refresh(row)
    assert row.status == "queued"
    assert row.worker_id is None
    assert row.claimed_at is None
    assert row.reaped_count == 1


def test_reap_leaves_running_job_within_threshold_untouched(db_session):
    job_id = _insert_running_job(db_session, updated_at_offset_seconds=10, attempt_count=1)

    reaped_count = reap_stale_jobs(db_session, timeout_seconds=60)

    assert reaped_count == 0
    row = db_session.get(TriageJob, job_id)
    db_session.refresh(row)
    assert row.status == "running"
    assert row.worker_id is not None
    assert row.claimed_at is not None
    assert row.reaped_count == 0


def test_reap_twice_without_intervening_claim_does_not_double_increment(db_session):
    job_id = _insert_running_job(db_session, updated_at_offset_seconds=61, attempt_count=1)

    first = reap_stale_jobs(db_session, timeout_seconds=60)
    assert first == 1

    # The job is now `queued`; the second call's `WHERE status = 'running'`
    # filter excludes it, since nothing re-claimed it in between.
    second = reap_stale_jobs(db_session, timeout_seconds=60)
    assert second == 0

    row = db_session.get(TriageJob, job_id)
    db_session.refresh(row)
    assert row.status == "queued"
    assert row.reaped_count == 1


def test_reap_emits_structured_warning_log(db_session, caplog):
    job_id = _insert_running_job(db_session, updated_at_offset_seconds=90, attempt_count=5)

    with caplog.at_level(logging.WARNING, logger="reachability.triage.reaper"):
        reaped_count = reap_stale_jobs(db_session, timeout_seconds=60)

    assert reaped_count == 1
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1

    message = warning_records[0].getMessage()
    assert str(job_id) in message
    assert "attempt_count=5" in message

    match = re.search(r"elapsed_seconds=([\d.]+)", message)
    assert match is not None
    assert float(match.group(1)) >= 60
