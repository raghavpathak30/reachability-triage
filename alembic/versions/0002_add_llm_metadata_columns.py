"""add llm metadata columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-18

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("triage_jobs", sa.Column("token_count", sa.Integer, nullable=True))
    op.add_column("triage_jobs", sa.Column("total_cost", sa.Numeric(10, 6), nullable=True))
    op.add_column("triage_jobs", sa.Column("model_string", sa.String(100), nullable=True))
    op.add_column("triage_jobs", sa.Column("prompt_version", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("triage_jobs", "prompt_version")
    op.drop_column("triage_jobs", "model_string")
    op.drop_column("triage_jobs", "total_cost")
    op.drop_column("triage_jobs", "token_count")
