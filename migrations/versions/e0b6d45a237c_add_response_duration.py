"""Store the measured response preparation duration for assistant messages."""

import sqlalchemy as sa
from alembic import op


revision = "e0b6d45a237c"
down_revision = "d9a5c34f126b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("response_duration_ms", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_chat_messages_response_duration_ms"), "chat_messages", "response_duration_ms >= 0"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_chat_messages_response_duration_ms"), "chat_messages", type_="check")
    op.drop_column("chat_messages", "response_duration_ms")
