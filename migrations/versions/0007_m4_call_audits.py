"""Add metadata-only call audit records.

Revision ID: 0007_m4_call_audits
Revises: 0006_m4_limits
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_m4_call_audits"
down_revision = "0006_m4_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("key_id", sa.String(36), nullable=True),
        sa.Column("operation", sa.String(60), nullable=False),
        sa.Column("result_category", sa.String(30), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for field in ("owner_id", "key_id", "request_id", "created_at"):
        op.create_index(f"ix_call_audits_{field}", "call_audits", [field])


def downgrade() -> None:
    for field in ("created_at", "request_id", "key_id", "owner_id"):
        op.drop_index(f"ix_call_audits_{field}", table_name="call_audits")
    op.drop_table("call_audits")
