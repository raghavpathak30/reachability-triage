"""add llm_mode column

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-29

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("triage_jobs", sa.Column("llm_mode", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("triage_jobs", "llm_mode")
