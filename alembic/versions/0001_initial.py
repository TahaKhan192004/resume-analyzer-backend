"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-13
"""
from alembic import op

from app.models.entities import SQLModel

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    SQLModel.metadata.create_all(op.get_bind())


def downgrade() -> None:
    SQLModel.metadata.drop_all(op.get_bind())
