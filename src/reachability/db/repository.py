import uuid

from pydantic import TypeAdapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reachability.triage.agent_models import TriageFinding

from .models import LLMResponseCache, TriageJob

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
        "llm_mode": job.llm_mode,
    }


def get_cached_response(session: Session, cache_key: str) -> dict | None:
    """Look up a cached real-LLM response by its content-derived key
    (`llm_cache.compute_cache_key`). Returns `None` on a cache miss --
    never raises for a missing key."""
    row = session.query(LLMResponseCache).filter_by(cache_key=cache_key).one_or_none()
    if row is None:
        return None
    return row.response_json


def store_cached_response(
    session: Session,
    cache_key: str,
    prompt_version: str,
    model_string: str,
    response_json: dict,
) -> None:
    """Insert a cached response row. Called only on a cache miss, after a
    real call succeeds -- see `groq_llm.py::GroqLLMClient.next_action`.

    Two callers can legitimately race to the same `cache_key` -- e.g. two
    jobs targeting the same repo/prompt, or a reaper-reclaimed job whose
    original and reclaiming workers are both still mid-flight
    (`DECISIONS.md` Sec.8 decision 6(a): "bounded duplicate work, not
    corruption"). The loser's insert hits the unique index on `cache_key`;
    that's a benign lost race, not a failure -- the loser already has its
    own, equally-valid real response in hand and is about to return it
    regardless of whether its own row gets persisted, so this rolls back
    and returns rather than propagating `IntegrityError` and failing that
    job."""
    row = LLMResponseCache(
        id=uuid.uuid4(),
        cache_key=cache_key,
        prompt_version=prompt_version,
        model_string=model_string,
        response_json=response_json,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
