"""create triage_jobs

Revision ID: 0001
Revises:
Create Date: 2026-09-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "triage_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("target", JSONB, nullable=False),
        sa.Column("finding", JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column(
            "attempt_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "reaped_count", sa.Integer, nullable=False, server_default="0"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_triage_jobs_status",
        ),
    )
    op.create_index(
        "ix_triage_jobs_status_created_at",
        "triage_jobs",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("triage_jobs")
