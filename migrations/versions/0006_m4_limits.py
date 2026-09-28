"""Add document sizes for per-user storage quotas.

Revision ID: 0006_m4_limits
Revises: 0005_m3_api_keys
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_m4_limits"
down_revision = "0005_m3_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("knowledge_documents", sa.Column("replacement_size_bytes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_documents", "replacement_size_bytes")
    op.drop_column("knowledge_documents", "size_bytes")
