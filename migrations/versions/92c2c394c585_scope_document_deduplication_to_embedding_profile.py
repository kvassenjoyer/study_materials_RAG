"""Scope live document deduplication to the embedding profile.

Revision ID: 92c2c394c585
Revises: 4f6c7fea6768
"""

import typing

import sqlalchemy as sa
from alembic import op


revision: typing.Final = "92c2c394c585"
down_revision: typing.Final = "4f6c7fea6768"
branch_labels: typing.Final = None
depends_on: typing.Final = None


def upgrade() -> None:  # noqa: COP007 - Alembic entry point
    op.create_index(
        "uq_documents_tenant_digest_profile_live",
        "documents",
        ["tenant_identifier", "content_digest", "embedding_profile"],
        unique=True,
        postgresql_where=sa.text("state_name <> 'deleted'"),
    )
    op.drop_index("uq_documents_tenant_digest_live", table_name="documents")


def downgrade() -> None:
    # Validate the stricter historical uniqueness before removing the current index.
    # If multiple live profiles now exist, PostgreSQL rejects this downgrade without deleting data.
    op.create_index(
        "uq_documents_tenant_digest_live",
        "documents",
        ["tenant_identifier", "content_digest"],
        unique=True,
        postgresql_where=sa.text("state_name <> 'deleted'"),
    )
    op.drop_index("uq_documents_tenant_digest_profile_live", table_name="documents")
