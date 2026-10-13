"""Persist queued answers independently of HTTP connections."""

import sqlalchemy as sa
from alembic import op


revision = "f1c7e56b348d"
down_revision = "e0b6d45a237c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_turns", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE chat_turns SET queued_at = created_at")
    op.alter_column("chat_turns", "queued_at", nullable=False)
    op.drop_constraint(op.f("ck_chat_turns_status_name"), "chat_turns", type_="check")
    op.create_check_constraint(
        op.f("ck_chat_turns_status_name"), "chat_turns", "status_name IN ('queued','pending','completed','failed')"
    )
    op.create_index("ix_turns_queue", "chat_turns", ["status_name", "queued_at"])


def downgrade() -> None:
    op.execute(
        "UPDATE chat_turns SET status_name = 'failed', error_code = 'answer_cancelled' WHERE status_name = 'queued'"
    )
    op.drop_index("ix_turns_queue", "chat_turns")
    op.drop_constraint(op.f("ck_chat_turns_status_name"), "chat_turns", type_="check")
    op.create_check_constraint(
        op.f("ck_chat_turns_status_name"), "chat_turns", "status_name IN ('pending','completed','failed')"
    )
    op.drop_column("chat_turns", "queued_at")
