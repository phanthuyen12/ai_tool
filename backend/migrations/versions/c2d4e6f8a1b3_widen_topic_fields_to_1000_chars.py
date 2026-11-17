"""widen_topic_fields_to_1000_chars

Revision ID: c2d4e6f8a1b3
Revises: 8f3c1a2b9d4e
Create Date: 2026-11-16 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2d4e6f8a1b3'
down_revision: Union[str, Sequence[str], None] = '8f3c1a2b9d4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('projects', 'topic', type_=sa.String(length=1000), existing_type=sa.String(length=500))
    op.alter_column(
        'batch_items', 'topic_variant', type_=sa.String(length=1000), existing_type=sa.String(length=500)
    )


def downgrade() -> None:
    op.alter_column('projects', 'topic', type_=sa.String(length=500), existing_type=sa.String(length=1000))
    op.alter_column(
        'batch_items', 'topic_variant', type_=sa.String(length=500), existing_type=sa.String(length=1000)
    )
