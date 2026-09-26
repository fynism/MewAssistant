"""Add the chat session title missing from pre-Alembic databases.

Revision ID: 0002_m1_title
Revises: 0001_m1
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_m1_title"
down_revision = "0001_m1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("chat_sessions")}
    if "title" not in columns:
        op.add_column("chat_sessions", sa.Column("title", sa.String(200), nullable=True))


def downgrade() -> None:
    # Fresh M1 databases already had this column in their baseline schema.
    # Keep it when moving the version marker back to avoid losing titles.
    pass
