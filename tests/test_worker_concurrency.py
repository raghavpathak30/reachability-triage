"""U5: the concurrent-claim stress test -- dangerous error #4 in
`agent_docs/PHASE3_PERSISTENCE.md`'s §0. This is the one gate in this
phase that cannot be satisfied by a single-threaded unit test with mocked
locking; U1-U4's own tests all exercise `claim_next_queued_job` from one
session at a time. This test exercises the real claim SQL
(`FOR UPDATE SKIP LOCKED`) under real concurrent load against a real
Postgres cluster.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from reachability.db.models import TriageJob
from reachability.triage.worker import claim_next_queued_job

M = 20  # pre-inserted queued rows
N = 8  # concurrent worker threads
ITERATIONS = 5


def _insert_queued_jobs(engine, count: int) -> list[uuid.UUID]:
    ids = [uuid.uuid4() for _ in range(count)]
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        for job_id in ids:
            session.add(
                TriageJob(id=job_id, status="queued", target={}, finding=None, error=None)
            )
        session.commit()
    finally:
        session.close()
    return ids


def _claim_until_empty(session_factory: sessionmaker, worker_id: str) -> list[uuid.UUID]:
    """Runs in its own thread: opens its own `Session` (never shared across
    threads -- only the `Engine` is thread-safe) and loops
    `claim_next_queued_job` until the queue is empty.
    """
    claimed_ids: list[uuid.UUID] = []
    session = session_factory()
    try:
        while True:
            claimed = claim_next_queued_job(session, worker_id)
            if claimed is None:
                break
            claimed_ids.append(claimed["id"])
    finally:
        session.close()
    return claimed_ids


def test_concurrent_claims_never_double_claim(postgres_cluster):
    engine = create_engine(postgres_cluster, pool_size=10, max_overflow=0)
    session_factory = sessionmaker(bind=engine)

    try:
        for iteration in range(ITERATIONS):
            with engine.begin() as conn:
                conn.execute(text("TRUNCATE triage_jobs"))

            inserted_ids = _insert_queued_jobs(engine, M)

            with ThreadPoolExecutor(max_workers=N) as executor:
                futures = [
                    executor.submit(_claim_until_empty, session_factory, f"worker-{i}")
                    for i in range(N)
                ]
                per_thread_claims = [future.result() for future in futures]

            all_claimed_ids = [
                job_id for thread_claims in per_thread_claims for job_id in thread_claims
            ]

            assert sorted(all_claimed_ids) == sorted(inserted_ids), (
                f"iteration {iteration}: claimed ids don't match the {M} inserted ids "
                "-- some job was never claimed"
            )
            assert len(all_claimed_ids) == len(set(all_claimed_ids)) == M, (
                f"iteration {iteration}: duplicate id present across threads' claim "
                "lists -- a job was double-claimed"
            )
    finally:
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE triage_jobs"))
        engine.dispose()
