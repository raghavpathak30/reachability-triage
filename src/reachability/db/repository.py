import uuid

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from reachability.triage.agent_models import TriageFinding

from .models import TriageJob

_FINDING_ADAPTER = TypeAdapter(TriageFinding)


def serialize_finding(finding: TriageFinding) -> dict:
    """Shared by `get_job`/`create_job` here and `worker.py`'s
    `finalize_job` -- any write path that stores a non-null `finding`
    JSONB value goes through this, not a duplicated inline call to
    `_FINDING_ADAPTER`.
    """
    return _FINDING_ADAPTER.dump_python(finding, mode="json")


def deserialize_finding(raw: dict) -> TriageFinding:
    return _FINDING_ADAPTER.validate_python(raw)


def create_job(session: Session, target: dict) -> uuid.UUID:
    job_id = uuid.uuid4()
    job = TriageJob(
        id=job_id,
        status="queued",
        target=target,
        finding=None,
        error=None,
    )
    session.add(job)
    session.commit()
    return job_id


def get_job(session: Session, job_id: uuid.UUID) -> dict | None:
    job = session.get(TriageJob, job_id)
    if job is None:
        return None
    return {
        "id": job.id,
        "status": job.status,
        "finding": deserialize_finding(job.finding) if job.finding is not None else None,
        "error": job.error,
    }
