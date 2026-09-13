"""U3: the polling worker -- claim, execute, finalize.

Owns exactly three transaction boundaries, per
`agent_docs/PHASE3_PERSISTENCE.md`'s U3 section:

1. **Claim** (`claim_next_queued_job`) -- one committed transaction. If the
   worker crashes before this commits, the row was never claimed; Postgres
   rolls the transaction back and the row is still `queued`.
2. **Execute** (`run_triage_job`, U5/job_runner.py) -- no open transaction,
   no lock held, so the (potentially minutes-long, over-the-network)
   acquire/index/agent-loop chain never blocks the claim query for any
   other worker. If the worker crashes here, the row is stuck `running`
   with a stale `updated_at` -- exactly what U4's reaper exists to detect.
3. **Finalize** (`finalize_job`) -- one committed transaction, guarded by
   `worker_id`/`attempt_count` so a late (reaped-but-not-actually-dead)
   worker's finalize can never clobber a result a reclaiming worker has
   already recorded for the same job.

The claim and finalize SQL below are copied verbatim from
`agent_docs/PHASE3_PERSISTENCE.md` U3 steps 1 and 3 -- do not simplify or
"improve" either statement; both were traced by an adversarial critic
through every interleaving (two workers racing, crash-before-commit,
crash-after-commit, reaper-vs-finalize) and confirmed correct as written.
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker

from ..db.repository import serialize_finding
from ..db.session import get_sessionmaker
from .job_runner import DEFAULT_TOOL_CALL_BUDGET, run_triage_job
from .reaper import reap_stale_jobs

if TYPE_CHECKING:
    from main import TriageRequest

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 1.0

# See agent_docs/PHASE3_PERSISTENCE.md U4: derived from this repo's own
# slowest currently-observed real job path (the 180s network poll in
# tests/test_triage_job_lifecycle.py::test_resolvable_package_reaches_completed),
# ~1.7x headroom above that. Overridable via TRIAGE_STALE_JOB_TIMEOUT_SECONDS,
# not hardcoded so ops can retune without a code change.
DEFAULT_STALE_JOB_TIMEOUT_SECONDS = 300

# Copied verbatim from agent_docs/PHASE3_PERSISTENCE.md U3 step 1.
_CLAIM_SQL = text(
    """
    WITH next_job AS (
      SELECT id FROM triage_jobs
      WHERE status = 'queued'
      ORDER BY created_at
      FOR UPDATE SKIP LOCKED
      LIMIT 1
    )
    UPDATE triage_jobs
    SET status = 'running', worker_id = :worker_id,
        claimed_at = now(), updated_at = now(),
        attempt_count = attempt_count + 1
    FROM next_job
    WHERE triage_jobs.id = next_job.id
    RETURNING triage_jobs.id, triage_jobs.target, triage_jobs.attempt_count
    """
)

# Copied verbatim from agent_docs/PHASE3_PERSISTENCE.md U3 step 3. The
# worker_id/attempt_count guard in the WHERE clause is the concrete
# anti-corruption mechanism -- see this module's docstring, point 3.
_FINALIZE_SQL = text(
    """
    UPDATE triage_jobs
    SET status = :final_status, finding = :finding_json, error = :error,
        updated_at = now()
    WHERE id = :id AND worker_id = :worker_id AND attempt_count = :claimed_attempt_count
    RETURNING id
    """
).bindparams(bindparam("finding_json", type_=JSONB))


def _generate_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def claim_next_queued_job(session: Session, worker_id: str) -> dict | None:
    """Claim the oldest queued job for `worker_id`. One committed
    transaction (see this module's docstring, point 1). Returns
    `{"id": ..., "target": ..., "attempt_count": ...}` or `None` if the
    queue is empty.
    """
    row = session.execute(_CLAIM_SQL, {"worker_id": worker_id}).fetchone()
    session.commit()
    if row is None:
        return None
    return {"id": row.id, "target": row.target, "attempt_count": row.attempt_count}


def _record_from_claimed(claimed: dict) -> dict:
    return {
        "id": claimed["id"],
        "status": "running",
        "target": claimed["target"],
        "finding": None,
        "error": None,
    }


def _target_to_triage_request(target: dict) -> "TriageRequest":
    """Reconstruct a `TriageRequest` from the stored JSONB `target` dict.

    Both shapes `main.py` ever stores (`package`+`version` or `repo_url`,
    always with `target_module`/`target_symbol`) map directly onto
    `TriageRequest`'s own field names, so this is a direct
    `TriageRequest(**target)` -- no shape-specific branching needed.
    """
    from main import TriageRequest

    return TriageRequest(**target)


def finalize_job(
    session: Session,
    job_id: uuid.UUID,
    worker_id: str,
    claimed_attempt_count: int,
    record: dict,
) -> bool:
    """Guarded finalize -- see this module's docstring, point 3, and
    `agent_docs/PHASE3_PERSISTENCE.md` U3 step 3. Returns whether the
    guarded `UPDATE` affected a row; logs a warning if it did not (this
    worker was reaped and reclaimed while still executing, and its result
    is correctly discarded rather than clobbering the reclaiming worker's).
    """
    finding = record.get("finding")
    finding_json = serialize_finding(finding) if finding is not None else None
    result = session.execute(
        _FINALIZE_SQL,
        {
            "final_status": record["status"],
            "finding_json": finding_json,
            "error": record.get("error"),
            "id": job_id,
            "worker_id": worker_id,
            "claimed_attempt_count": claimed_attempt_count,
        },
    )
    session.commit()
    affected = result.rowcount > 0
    if not affected:
        logger.warning(
            "finalize_job affected 0 rows for job %s (worker_id=%s, "
            "claimed_attempt_count=%s) -- this job was reclaimed by another "
            "worker while this one was still executing; this worker's "
            "result is discarded, not applied",
            job_id,
            worker_id,
            claimed_attempt_count,
        )
    return affected


def run_worker_once(session_factory: sessionmaker, worker_id: str, budget: int) -> bool:
    """Drive exactly one unit of work: claim, execute, finalize. Returns
    `False` on an empty queue (nothing claimed), `True` otherwise.
    """
    session = session_factory()
    try:
        claimed = claim_next_queued_job(session, worker_id)
        if claimed is None:
            return False

        record = _record_from_claimed(claimed)
        request = _target_to_triage_request(claimed["target"])
        run_triage_job(record, request, budget)
        finalize_job(session, claimed["id"], worker_id, claimed["attempt_count"], record)
        return True
    finally:
        session.close()


def main() -> None:
    timeout_seconds = int(
        os.environ.get("TRIAGE_STALE_JOB_TIMEOUT_SECONDS", DEFAULT_STALE_JOB_TIMEOUT_SECONDS)
    )
    poll_interval = float(
        os.environ.get("TRIAGE_WORKER_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
    )
    worker_id = _generate_worker_id()
    session_factory = get_sessionmaker()

    while True:
        reaper_session = session_factory()
        try:
            reap_stale_jobs(reaper_session, timeout_seconds)
        finally:
            reaper_session.close()
        if not run_worker_once(session_factory, worker_id, DEFAULT_TOOL_CALL_BUDGET):
            time.sleep(poll_interval)


if __name__ == "__main__":
    main()
