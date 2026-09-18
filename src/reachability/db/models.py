import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TriageJob(Base):
    __tablename__ = "triage_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # App-generated (uuid.uuid4()) before insert, per DECISIONS.md §2 -- the
    # pre-allocation rationale (202 + Location header before any DB round
    # trip) still holds; Postgres never generates this column's value.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    target: Mapped[dict] = mapped_column(JSONB, nullable=False)
    finding: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # No `onupdate=func.now()` and no DB-level trigger: every mutation path
    # in this phase (claim, finalize, reap) must set `updated_at = now()`
    # explicitly in hand-written SQL, or it silently defeats the reaper's
    # staleness detection with nothing to catch the omission.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reaped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Phase 5 U3: a job-scoped cost *record*, not the project-wide "cost
    # accounting" capability CLAUDE.md's NOT BUILT list names as absent
    # (no budgets/alerts/aggregation here) -- see `llm_config.py`'s
    # docstring for the same caveat on `total_cost`'s price-table source.
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    model_string: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_triage_jobs_status",
        ),
    )


class LLMResponseCache(Base):
    """Postgres-backed cache of real Groq responses, keyed by
    `compute_cache_key` (`llm_cache.py`). Postgres-backed, not an
    in-process dict, per Phase 3's own rule (`DECISIONS.md` §8 decision
    1): a second, non-persistent source of truth is exactly the
    corruption risk that phase exists to eliminate, and the same
    reasoning applies to this cache surviving a worker restart.
    """

    __tablename__ = "llm_response_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)
    model_string: Mapped[str] = mapped_column(String(100), nullable=False)
    response_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
