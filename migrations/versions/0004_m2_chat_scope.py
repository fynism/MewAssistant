"""Store the knowledge selection on each chat message.

Revision ID: 0004_m2_scope
Revises: 0003_m2_replace
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_m2_scope"
down_revision = "0003_m2_replace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("chat_messages")}
    if "knowledge_scope_json" not in columns:
        op.add_column("chat_messages", sa.Column("knowledge_scope_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    # Fresh baseline databases already have this column. Keep recorded scopes.
    pass
