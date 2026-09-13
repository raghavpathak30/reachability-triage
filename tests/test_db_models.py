import uuid

from sqlalchemy import inspect, text

from conftest import _alembic_config
from alembic import command
from reachability.db.models import TriageJob


def test_triage_job_round_trips_jsonb_columns(db_session):
    job_id = uuid.uuid4()
    target = {"package": "six", "version": "1.16.0", "target_module": "six"}
    finding = {
        "verdict": "reachable",
        "path": [{"caller_id": "a", "callee_id": "b", "confidence": 1.0}],
    }
    job = TriageJob(
        id=job_id,
        status="completed",
        target=target,
        finding=finding,
        error=None,
    )
    db_session.add(job)
    db_session.commit()

    fetched = db_session.get(TriageJob, job_id)
    assert fetched is not None
    assert fetched.id == job_id
    assert fetched.status == "completed"
    assert fetched.target == target
    assert fetched.finding == finding
    assert fetched.error is None
    assert fetched.created_at is not None
    assert fetched.updated_at is not None
    assert fetched.claimed_at is None
    assert fetched.worker_id is None
    assert fetched.attempt_count == 0
    assert fetched.reaped_count == 0


def test_downgrade_then_upgrade_round_trip(postgres_cluster):
    cfg = _alembic_config(postgres_cluster)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    from sqlalchemy import create_engine

    engine = create_engine(postgres_cluster)
    try:
        inspector = inspect(engine)
        assert "triage_jobs" in inspector.get_table_names()
        columns = {col["name"] for col in inspector.get_columns("triage_jobs")}
        assert columns == {
            "id",
            "status",
            "target",
            "finding",
            "error",
            "created_at",
            "updated_at",
            "claimed_at",
            "worker_id",
            "attempt_count",
            "reaped_count",
        }
    finally:
        with engine.connect() as conn:
            conn.execute(text("TRUNCATE triage_jobs"))
            conn.commit()
        engine.dispose()


def test_fixture_is_reusable_second_test(db_session):
    result = db_session.execute(text("SELECT 1")).scalar()
    assert result == 1
