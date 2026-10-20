"""Associate GitHub's immutable user ID with a local account."""

from alembic import op
import sqlalchemy as sa

revision = "0009_github_oauth"
down_revision = "0008_document_cleaning"
branch_labels = None
depends_on = None


def upgrade():
    # 0001 creates users from current ORM metadata on a fresh installation.
    inspector = sa.inspect(op.get_bind())
    if "github_id" not in {column["name"] for column in inspector.get_columns("users")}:
        op.add_column("users", sa.Column("github_id", sa.String(32), nullable=True))
    if "uq_users_github_id" not in {index["name"] for index in inspector.get_indexes("users")}:
        op.create_index("uq_users_github_id", "users", ["github_id"], unique=True)


def downgrade():
    op.drop_index("uq_users_github_id", table_name="users")
    op.drop_column("users", "github_id")
