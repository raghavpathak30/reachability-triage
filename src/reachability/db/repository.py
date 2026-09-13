import uuid

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from reachability.triage.agent_models import TriageFinding

from .models import TriageJob

_FINDING_ADAPTER = TypeAdapter(TriageFinding)


def _serialize_finding(finding: TriageFinding) -> dict:
    return _FINDING_ADAPTER.dump_python(finding, mode="json")


def _deserialize_finding(raw: dict) -> TriageFinding:
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
        "finding": _deserialize_finding(job.finding) if job.finding is not None else None,
        "error": job.error,
    }
