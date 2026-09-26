"""Persist pending replacement files until the old index is retired.

Revision ID: 0003_m2_replace
Revises: 0002_m1_title
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_m2_replace"
down_revision = "0002_m1_title"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column("replacement_storage_key", sa.String(100), nullable=True))
    op.add_column("knowledge_documents", sa.Column("replacement_filename", sa.String(255), nullable=True))
    op.add_column("knowledge_documents", sa.Column("replacement_file_type", sa.String(20), nullable=True))
    op.add_column("knowledge_documents", sa.Column(
        "replacement_old_ready", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("knowledge_documents", "replacement_old_ready")
    op.drop_column("knowledge_documents", "replacement_file_type")
    op.drop_column("knowledge_documents", "replacement_filename")
    op.drop_column("knowledge_documents", "replacement_storage_key")
