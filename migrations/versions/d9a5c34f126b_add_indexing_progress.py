"""Persist successfully indexed chunk counts for document progress."""

import sqlalchemy as sa
from alembic import op


revision = "d9a5c34f126b"
down_revision = "c8f4b23e015a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("indexed_chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0"))
    )
    op.execute("UPDATE documents SET indexed_chunk_count = chunk_count WHERE state_name = 'ready'")
    op.create_check_constraint(
        op.f("ck_documents_indexing_counts"),
        "documents",
        "indexed_chunk_count >= 0 AND indexed_chunk_count <= chunk_count",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_documents_indexing_counts"), "documents", type_="check")
    op.drop_column("documents", "indexed_chunk_count")
