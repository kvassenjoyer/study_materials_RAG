"""Group existing documents and chats into isolated project spaces."""

import uuid

import sqlalchemy as sa
from alembic import op


revision = "b7e3a12d904f"
down_revision = "92c2c394c585"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("project_identifier", sa.Uuid(), primary_key=True),
        sa.Column("tenant_identifier", sa.Uuid(), sa.ForeignKey("user_accounts.user_identifier"), nullable=False),
        sa.Column("title_text", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_identifier", "tenant_identifier", name="uq_projects_project_identifier"),
    )
    op.create_index("ix_projects_tenant_created", "projects", ["tenant_identifier", "created_at"])
    op.add_column("documents", sa.Column("project_identifier", sa.Uuid(), nullable=True))
    op.add_column("chat_sessions", sa.Column("project_identifier", sa.Uuid(), nullable=True))
    connection = op.get_bind()
    accounts = connection.execute(sa.text("SELECT user_identifier, created_at FROM user_accounts")).all()
    for tenant_identifier, created_at in accounts:
        project_identifier = uuid.uuid5(uuid.NAMESPACE_URL, f"study-rag:default:{tenant_identifier}")
        connection.execute(
            sa.text(
                "INSERT INTO projects (project_identifier, tenant_identifier, title_text, created_at, updated_at) "
                "VALUES (:project, :tenant, :title, :created, :created)"
            ),
            {
                "project": project_identifier,
                "tenant": tenant_identifier,
                "title": "Мои материалы",
                "created": created_at,
            },
        )
    for table_name in ("documents", "chat_sessions"):
        owned_table = sa.table(table_name, sa.column("project_identifier"), sa.column("tenant_identifier"))
        project_table = sa.table("projects", sa.column("project_identifier"), sa.column("tenant_identifier"))
        connection.execute(
            owned_table.update()
            .where(owned_table.c.tenant_identifier == project_table.c.tenant_identifier)
            .values(project_identifier=project_table.c.project_identifier)
        )
        op.alter_column(table_name, "project_identifier", nullable=False)
        op.create_foreign_key(
            f"fk_{table_name}_project_identifier_projects",
            table_name,
            "projects",
            ["project_identifier", "tenant_identifier"],
            ["project_identifier", "tenant_identifier"],
        )
    op.drop_index("uq_documents_tenant_digest_profile_live", table_name="documents")
    op.create_index(
        "uq_documents_project_digest_profile_live",
        "documents",
        ["tenant_identifier", "project_identifier", "content_digest", "embedding_profile"],
        unique=True,
        postgresql_where=sa.text("state_name <> 'deleted'"),
    )


def downgrade() -> None:
    # The old uniqueness contract cannot represent identical uploads in different projects.
    # PostgreSQL rejects this downgrade atomically if those duplicates exist.
    op.create_index(
        "uq_documents_tenant_digest_profile_live",
        "documents",
        ["tenant_identifier", "content_digest", "embedding_profile"],
        unique=True,
        postgresql_where=sa.text("state_name <> 'deleted'"),
    )
    op.drop_index("uq_documents_project_digest_profile_live", table_name="documents")
    for table_name in ("documents", "chat_sessions"):
        op.drop_constraint(f"fk_{table_name}_project_identifier_projects", table_name, type_="foreignkey")
        op.drop_column(table_name, "project_identifier")
    op.drop_table("projects")
