"""Create durable tenant-owned RAG schema.

Revision ID: 4f6c7fea6768
Revises:
"""

import typing

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: typing.Final = "4f6c7fea6768"
down_revision: typing.Final = None
branch_labels: typing.Final = None
depends_on: typing.Final = None


def upgrade() -> None:  # noqa: COP007 - Alembic entry point
    op.create_table(
        "user_accounts",
        sa.Column("user_identifier", sa.Uuid(), nullable=False),
        sa.Column("email_address", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_identifier", name=op.f("pk_user_accounts")),
        sa.UniqueConstraint("email_address", name=op.f("uq_user_accounts_email_address")),
    )
    op.create_table(
        "chat_sessions",
        sa.Column("chat_identifier", sa.Uuid(), nullable=False),
        sa.Column("tenant_identifier", sa.Uuid(), nullable=False),
        sa.Column("title_text", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_identifier"],
            ["user_accounts.user_identifier"],
            name=op.f("fk_chat_sessions_tenant_identifier_user_accounts"),
        ),
        sa.PrimaryKeyConstraint("chat_identifier", name=op.f("pk_chat_sessions")),
        sa.UniqueConstraint("chat_identifier", "tenant_identifier", name=op.f("uq_chat_sessions_chat_identifier")),
    )
    op.create_index("ix_chats_tenant_created", "chat_sessions", ["tenant_identifier", "created_at"], unique=False)
    op.create_table(
        "documents",
        sa.Column("document_identifier", sa.Uuid(), nullable=False),
        sa.Column("tenant_identifier", sa.Uuid(), nullable=False),
        sa.Column("filename_text", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("byte_count", sa.BigInteger(), nullable=False),
        sa.Column("embedding_profile", sa.Text(), nullable=False),
        sa.Column("state_name", sa.String(length=16), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("lease_identifier", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_identifier", sa.Uuid(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state_name IN ('queued','indexing','ready','failed','deleting','deleted')",
            name=op.f("ck_documents_state_name"),
        ),
        sa.CheckConstraint(
            "chunk_count >= 0 AND byte_count >= 0 AND attempts >= 0", name=op.f("ck_documents_positive_counts")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_identifier"],
            ["user_accounts.user_identifier"],
            name=op.f("fk_documents_tenant_identifier_user_accounts"),
        ),
        sa.PrimaryKeyConstraint("document_identifier", name=op.f("pk_documents")),
        sa.UniqueConstraint("document_identifier", "tenant_identifier", name=op.f("uq_documents_document_identifier")),
    )
    op.create_index(
        "ix_documents_claim", "documents", ["state_name", "next_attempt_at", "lease_expires_at"], unique=False
    )
    op.create_index("ix_documents_tenant_created", "documents", ["tenant_identifier", "created_at"], unique=False)
    op.create_index(
        "uq_documents_tenant_digest_live",
        "documents",
        ["tenant_identifier", "content_digest"],
        unique=True,
        postgresql_where=sa.text("state_name <> 'deleted'"),
    )
    op.create_table(
        "chat_turns",
        sa.Column("turn_identifier", sa.Uuid(), nullable=False),
        sa.Column("chat_identifier", sa.Uuid(), nullable=False),
        sa.Column("tenant_identifier", sa.Uuid(), nullable=False),
        sa.Column("client_request_identifier", sa.Uuid(), nullable=False),
        sa.Column("status_name", sa.String(length=16), nullable=False),
        sa.Column("lease_identifier", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status_name IN ('pending','completed','failed')", name=op.f("ck_chat_turns_status_name")),
        sa.ForeignKeyConstraint(
            ["chat_identifier", "tenant_identifier"],
            ["chat_sessions.chat_identifier", "chat_sessions.tenant_identifier"],
            name=op.f("fk_chat_turns_chat_identifier_chat_sessions"),
        ),
        sa.PrimaryKeyConstraint("turn_identifier", name=op.f("pk_chat_turns")),
        sa.UniqueConstraint("chat_identifier", "client_request_identifier", name=op.f("uq_chat_turns_chat_identifier")),
        sa.UniqueConstraint(
            "turn_identifier", "chat_identifier", "tenant_identifier", name=op.f("uq_chat_turns_turn_identifier")
        ),
    )
    op.create_index("ix_turns_chat_status", "chat_turns", ["chat_identifier", "status_name"], unique=False)
    op.create_table(
        "document_chunks",
        sa.Column("chunk_identifier", sa.Uuid(), nullable=False),
        sa.Column("document_identifier", sa.Uuid(), nullable=False),
        sa.Column("tenant_identifier", sa.Uuid(), nullable=False),
        sa.Column("ordinal_value", sa.Integer(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.CheckConstraint("ordinal_value >= 0 AND token_count > 0", name=op.f("ck_document_chunks_positive_counts")),
        sa.ForeignKeyConstraint(
            ["document_identifier", "tenant_identifier"],
            ["documents.document_identifier", "documents.tenant_identifier"],
            name=op.f("fk_document_chunks_document_identifier_documents"),
        ),
        sa.PrimaryKeyConstraint("chunk_identifier", name=op.f("pk_document_chunks")),
        sa.UniqueConstraint(
            "document_identifier", "ordinal_value", name=op.f("uq_document_chunks_document_identifier")
        ),
    )
    op.create_index(
        "ix_chunks_tenant_document", "document_chunks", ["tenant_identifier", "document_identifier"], unique=False
    )
    op.create_table(
        "chat_messages",
        sa.Column("message_identifier", sa.Uuid(), nullable=False),
        sa.Column("chat_identifier", sa.Uuid(), nullable=False),
        sa.Column("tenant_identifier", sa.Uuid(), nullable=False),
        sa.Column("turn_identifier", sa.Uuid(), nullable=False),
        sa.Column("role_name", sa.String(length=16), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("citation_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sufficient_context", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role_name IN ('user','assistant')", name=op.f("ck_chat_messages_role_name")),
        sa.ForeignKeyConstraint(
            ["turn_identifier", "chat_identifier", "tenant_identifier"],
            ["chat_turns.turn_identifier", "chat_turns.chat_identifier", "chat_turns.tenant_identifier"],
            name=op.f("fk_chat_messages_turn_identifier_chat_turns"),
        ),
        sa.PrimaryKeyConstraint("message_identifier", name=op.f("pk_chat_messages")),
        sa.UniqueConstraint("turn_identifier", "role_name", name=op.f("uq_chat_messages_turn_identifier")),
    )
    op.create_index("ix_messages_chat_created", "chat_messages", ["chat_identifier", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_messages_chat_created", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chunks_tenant_document", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_turns_chat_status", table_name="chat_turns")
    op.drop_table("chat_turns")
    op.drop_index(
        "uq_documents_tenant_digest_live", table_name="documents", postgresql_where=sa.text("state_name <> 'deleted'")
    )
    op.drop_index("ix_documents_tenant_created", table_name="documents")
    op.drop_index("ix_documents_claim", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_chats_tenant_created", table_name="chat_sessions")
    op.drop_table("chat_sessions")
    op.drop_table("user_accounts")
