import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, func
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

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_triage_jobs_status",
        ),
    )
