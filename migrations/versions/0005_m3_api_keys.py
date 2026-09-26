"""Add personal API keys for remote MCP access.

Revision ID: 0005_m3_api_keys
Revises: 0004_m2_scope
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_m3_api_keys"
down_revision = "0004_m2_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personal_api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("token_suffix", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_personal_api_keys_owner_id", "personal_api_keys", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_personal_api_keys_owner_id", table_name="personal_api_keys")
    op.drop_table("personal_api_keys")
