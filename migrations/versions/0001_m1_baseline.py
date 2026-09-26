"""M1 private knowledge baseline; legacy rows stay quarantined.

Revision ID: 0001_m1
Revises:
"""

from alembic import op
import sqlalchemy as sa

from backend.database import Base
from backend import models  # noqa: F401

revision = "0001_m1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # Existing installations used create_all. Preserve their four legacy tables;
    # on a fresh installation, create the same baseline before M1 tables.
    for name in ("users", "chat_sessions", "chat_messages", "parent_chunks"):
        Base.metadata.tables[name].create(bind, checkfirst=True)
    inspector = sa.inspect(bind)
    if "reasoning_content" not in {col["name"] for col in inspector.get_columns("chat_messages")}:
        op.add_column("chat_messages", sa.Column("reasoning_content", sa.Text(), nullable=True))

    # Some pre-Alembic deployments created the M1 tables via create_all. Do
    # not overwrite them or stamp an incompatible partial schema as current.
    expected = {
        name: {column.name for column in Base.metadata.tables[name].columns}
        for name in ("invitations", "knowledge_bases", "knowledge_documents", "knowledge_parent_chunks")
    }
    for name, columns in expected.items():
        if inspector.has_table(name):
            existing = {column["name"] for column in inspector.get_columns(name)}
            if existing != columns:
                raise RuntimeError(f"现有 {name} 表结构与 M1 基线不一致：请人工核对后迁移")

    op.create_table(
        "invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("used_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        if_not_exists=True,
    )
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        if_not_exists=True,
    )
    op.create_index("ix_knowledge_bases_owner_id", "knowledge_bases", ["owner_id"], if_not_exists=True)
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("knowledge_id", sa.String(36), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(100), nullable=False, unique=True),
        sa.Column("file_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_summary", sa.String(500), nullable=True),
        sa.Column("legacy_filename", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        if_not_exists=True,
    )
    op.create_index("ix_knowledge_documents_knowledge_id", "knowledge_documents", ["knowledge_id"], if_not_exists=True)
    op.create_table(
        "knowledge_parent_chunks",
        sa.Column("chunk_id", sa.String(512), primary_key=True),
        sa.Column("knowledge_id", sa.String(36), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_type", sa.String(50), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("parent_chunk_id", sa.String(512), nullable=False),
        sa.Column("root_chunk_id", sa.String(512), nullable=False),
        sa.Column("chunk_level", sa.Integer(), nullable=False),
        sa.Column("chunk_idx", sa.Integer(), nullable=False),
        if_not_exists=True,
    )
    op.create_index("ix_knowledge_parent_chunks_knowledge_id", "knowledge_parent_chunks", ["knowledge_id"], if_not_exists=True)
    op.create_index("ix_knowledge_parent_chunks_document_id", "knowledge_parent_chunks", ["document_id"], if_not_exists=True)


def downgrade() -> None:
    op.drop_table("knowledge_parent_chunks")
    op.drop_table("knowledge_documents")
    op.drop_table("knowledge_bases")
    op.drop_table("invitations")
    # Legacy tables and their data are deliberately retained.
