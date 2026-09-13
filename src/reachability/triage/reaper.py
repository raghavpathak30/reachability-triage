"""U4: the stale-job reaper.

`reap_stale_jobs` resets any `running` job whose `updated_at` is older
than `timeout_seconds` back to `queued`, clearing `worker_id`/`claimed_at`
and incrementing `reaped_count` -- recovering a job whose worker crashed
mid-execution (see `worker.py`'s docstring, point 2: the execute step
holds no transaction/lock, so a crash there just leaves a stale
`updated_at` for this to detect).

The SQL below is copied verbatim from `agent_docs/PHASE3_PERSISTENCE.md`
U4 -- `FOR UPDATE SKIP LOCKED` in this CTE (not just the worker's claim
query) stops the reaper from blocking on, or racing, a row a live
worker's finalize `UPDATE` happens to be touching at that exact instant.
The only addition over the spec's own SQL is selecting `updated_at`/
`attempt_count` in the `stale` CTE and returning them alongside `id`, so
the sign-off-required warning log below can carry each row's *pre-reap*
values -- a follow-up `SELECT` after the `UPDATE` commits would only see
the already-reset row, since the `UPDATE` itself overwrites `updated_at`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_REAP_SQL = text(
    """
    WITH stale AS (
      SELECT id, updated_at, attempt_count FROM triage_jobs
      WHERE status = 'running'
        AND updated_at < now() - make_interval(secs => :timeout_seconds)
      FOR UPDATE SKIP LOCKED
    )
    UPDATE triage_jobs
    SET status = 'queued', worker_id = NULL, claimed_at = NULL,
        reaped_count = reaped_count + 1, updated_at = now()
    FROM stale
    WHERE triage_jobs.id = stale.id
    RETURNING triage_jobs.id,
              stale.updated_at AS pre_reap_updated_at,
              stale.attempt_count AS pre_reap_attempt_count
    """
)


def reap_stale_jobs(session: Session, timeout_seconds: int) -> int:
    """Reset every `running` job stale past `timeout_seconds` back to
    `queued`. One committed transaction. Emits one `logging.warning` per
    reaped row (`job_id`, pre-reap `attempt_count`, `elapsed_seconds` --
    this is the sign-off-required observability so a future retune of the
    300s default has real data behind it, not the single 180s data point
    the default itself was derived from). Returns the count of rows reaped.
    """
    rows = session.execute(_REAP_SQL, {"timeout_seconds": timeout_seconds}).fetchall()
    session.commit()

    now = datetime.now(timezone.utc)
    for row in rows:
        elapsed_seconds = (now - row.pre_reap_updated_at).total_seconds()
        logger.warning(
            "reaped stale job %s: attempt_count=%s, elapsed_seconds=%.1f "
            "since last update (timeout_seconds=%s)",
            row.id,
            row.pre_reap_attempt_count,
            elapsed_seconds,
            timeout_seconds,
        )
    return len(rows)
