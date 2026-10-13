"""Support automatic chat titles and safe deletion of workspace resources."""

import sqlalchemy as sa
from alembic import op


revision = "c8f4b23e015a"
down_revision = "b7e3a12d904f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chat_sessions", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "chat_sessions", sa.Column("automatic_title", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade() -> None:
    op.drop_column("chat_sessions", "automatic_title")
    op.drop_column("chat_sessions", "deleted_at")
    op.drop_column("projects", "deleted_at")
