"""Persist ingestion diagnostics, content hashes and page extraction records."""

from alembic import op
import sqlalchemy as sa

revision = "0008_document_cleaning"
down_revision = "0007_m4_call_audits"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("knowledge_documents", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("knowledge_documents", sa.Column("processing_report", sa.JSON(), nullable=True))
    op.create_index("ix_knowledge_documents_content_hash", "knowledge_documents", ["content_hash"])
    op.create_table("knowledge_document_pages",
        sa.Column("document_id", sa.String(36), sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("page_number", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("extraction", sa.String(20), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("cleaned_text", sa.Text(), nullable=False),
        sa.Column("extraction_data", sa.JSON(), nullable=False))


def downgrade():
    op.drop_table("knowledge_document_pages")
    op.drop_index("ix_knowledge_documents_content_hash", table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "processing_report")
    op.drop_column("knowledge_documents", "content_hash")
