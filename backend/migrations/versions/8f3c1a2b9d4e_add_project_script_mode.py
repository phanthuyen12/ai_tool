"""add_project_script_mode

Revision ID: 8f3c1a2b9d4e
Revises: 4a0bcf0b3ae4
Create Date: 2026-11-15 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8f3c1a2b9d4e'
down_revision: Union[str, Sequence[str], None] = '4a0bcf0b3ae4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'projects',
        sa.Column('script_mode', sa.String(length=32), nullable=False, server_default='ai_presenter'),
    )
    op.alter_column('projects', 'script_mode', server_default=None)


def downgrade() -> None:
    op.drop_column('projects', 'script_mode')
