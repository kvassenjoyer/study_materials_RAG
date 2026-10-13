"""PostgreSQL schema and explicitly owned async database resources."""

import collections.abc
import contextlib
import datetime
import typing
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from study_rag.domain.errors import DependencyUnavailableError


_REQUIRED_SCHEMA_REVISION: typing.Final = "d9a5c34f126b"


@typing.final
class CitationPayload(typing.TypedDict):
    chunk_identifier: str
    document_identifier: str
    filename_text: str
    page_start: int | None
    page_end: int | None
    excerpt_text: str
    similarity_score: float
    source_url: str
    source_label: str


class DatabaseBase(DeclarativeBase):  # noqa: COP012 - ORM rows inherit this framework base
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        },
    )


@typing.final
class UserAccountRow(DatabaseBase):
    __tablename__ = "user_accounts"

    user_identifier: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    # Keep the original column so existing accounts and running installations retain their data.
    login_name: Mapped[str] = mapped_column("email_address", String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


@typing.final
class ProjectRow(DatabaseBase):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("project_identifier", "tenant_identifier"),
        Index("ix_projects_tenant_created", "tenant_identifier", "created_at"),
    )

    project_identifier: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_identifier: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_identifier"))
    title_text: Mapped[str] = mapped_column(String(200))
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


@typing.final
class DocumentRow(DatabaseBase):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("document_identifier", "tenant_identifier"),
        ForeignKeyConstraint(
            ["project_identifier", "tenant_identifier"],
            ["projects.project_identifier", "projects.tenant_identifier"],
        ),
        CheckConstraint("state_name IN ('queued','indexing','ready','failed','deleting','deleted')", name="state_name"),
        CheckConstraint("chunk_count >= 0 AND byte_count >= 0 AND attempts >= 0", name="positive_counts"),
        CheckConstraint("indexed_chunk_count >= 0 AND indexed_chunk_count <= chunk_count", name="indexing_counts"),
        Index("ix_documents_tenant_created", "tenant_identifier", "created_at"),
        Index("ix_documents_claim", "state_name", "next_attempt_at", "lease_expires_at"),
        Index(
            "uq_documents_project_digest_profile_live",
            "tenant_identifier",
            "project_identifier",
            "content_digest",
            "embedding_profile",
            unique=True,
            postgresql_where=text("state_name <> 'deleted'"),
        ),
    )

    document_identifier: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_identifier: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_identifier"))
    project_identifier: Mapped[uuid.UUID]
    filename_text: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(100))
    storage_key: Mapped[str] = mapped_column(Text)
    content_digest: Mapped[str] = mapped_column(String(64))
    byte_count: Mapped[int] = mapped_column(BigInteger)
    embedding_profile: Mapped[str] = mapped_column(Text)
    state_name: Mapped[str] = mapped_column(String(16))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_chunk_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    page_count: Mapped[int | None] = mapped_column(Integer)
    lease_identifier: Mapped[uuid.UUID | None]
    lease_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    worker_identifier: Mapped[uuid.UUID | None]
    next_attempt_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


@typing.final
class ChunkRow(DatabaseBase):
    __tablename__ = "document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_identifier", "tenant_identifier"],
            ["documents.document_identifier", "documents.tenant_identifier"],
        ),
        UniqueConstraint("document_identifier", "ordinal_value"),
        CheckConstraint("ordinal_value >= 0 AND token_count > 0", name="positive_counts"),
        Index("ix_chunks_tenant_document", "tenant_identifier", "document_identifier"),
    )

    chunk_identifier: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    document_identifier: Mapped[uuid.UUID]
    tenant_identifier: Mapped[uuid.UUID]
    ordinal_value: Mapped[int] = mapped_column(Integer)
    content_text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)


@typing.final
class ChatSessionRow(DatabaseBase):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        UniqueConstraint("chat_identifier", "tenant_identifier"),
        ForeignKeyConstraint(
            ["project_identifier", "tenant_identifier"],
            ["projects.project_identifier", "projects.tenant_identifier"],
        ),
        Index("ix_chats_tenant_created", "tenant_identifier", "created_at"),
    )

    chat_identifier: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_identifier: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_identifier"))
    project_identifier: Mapped[uuid.UUID]
    title_text: Mapped[str] = mapped_column(String(200))
    automatic_title: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


@typing.final
class ChatTurnRow(DatabaseBase):
    __tablename__ = "chat_turns"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chat_identifier", "tenant_identifier"],
            ["chat_sessions.chat_identifier", "chat_sessions.tenant_identifier"],
        ),
        UniqueConstraint("chat_identifier", "client_request_identifier"),
        UniqueConstraint("turn_identifier", "chat_identifier", "tenant_identifier"),
        CheckConstraint("status_name IN ('pending','completed','failed')", name="status_name"),
        Index("ix_turns_chat_status", "chat_identifier", "status_name"),
    )

    turn_identifier: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    chat_identifier: Mapped[uuid.UUID]
    tenant_identifier: Mapped[uuid.UUID]
    client_request_identifier: Mapped[uuid.UUID]
    status_name: Mapped[str] = mapped_column(String(16))
    lease_identifier: Mapped[uuid.UUID | None]
    lease_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


@typing.final
class ChatMessageRow(DatabaseBase):
    __tablename__ = "chat_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["turn_identifier", "chat_identifier", "tenant_identifier"],
            ["chat_turns.turn_identifier", "chat_turns.chat_identifier", "chat_turns.tenant_identifier"],
        ),
        UniqueConstraint("turn_identifier", "role_name"),
        CheckConstraint("role_name IN ('user','assistant')", name="role_name"),
        Index("ix_messages_chat_created", "chat_identifier", "created_at"),
    )

    message_identifier: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    chat_identifier: Mapped[uuid.UUID]
    tenant_identifier: Mapped[uuid.UUID]
    turn_identifier: Mapped[uuid.UUID]
    role_name: Mapped[str] = mapped_column(String(16))
    content_text: Mapped[str] = mapped_column(Text)
    citation_payload: Mapped[list[CitationPayload]] = mapped_column(JSONB, default=list)
    sufficient_context: Mapped[bool | None]
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


@typing.final
class DatabaseResources:
    def __init__(self, *, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize_database(self) -> None:
        await self._verify_database_readiness()

    async def check_readiness(self) -> bool:
        try:
            await self._verify_database_readiness()
        except DependencyUnavailableError:
            return False
        return True

    async def close_database(self) -> None:
        await self.engine.dispose()

    async def _verify_database_readiness(self) -> None:
        try:
            async with self.engine.connect() as connection:
                await self._verify_schema(connection=connection)
        except ProgrammingError as schema_error:
            raise DependencyUnavailableError(
                error_code="database_schema_unavailable",
                message_text="PostgreSQL schema is missing or incompatible; run alembic upgrade head",
            ) from schema_error
        except (SQLAlchemyError, OSError) as database_error:
            raise DependencyUnavailableError(
                error_code="database_unavailable",
                message_text="PostgreSQL is unavailable",
            ) from database_error

    async def _verify_schema(self, *, connection: AsyncConnection) -> None:
        migration_revisions: typing.Final = await connection.scalars(text("SELECT version_num FROM alembic_version"))
        recorded_revisions: tuple[object, ...] = tuple(migration_revisions)
        if recorded_revisions != (_REQUIRED_SCHEMA_REVISION,):
            raise DependencyUnavailableError(
                error_code="database_schema_unavailable",
                message_text="PostgreSQL schema revision is incompatible; run alembic upgrade head",
            )
        # LIMIT 0 checks every required table and column without reading users' stored content.
        for one_table in DatabaseBase.metadata.sorted_tables:
            await connection.execute(one_table.select().limit(0))


@typing.final
class PostgreSQLSessionScope:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @contextlib.asynccontextmanager
    async def transaction_session(self) -> collections.abc.AsyncIterator[AsyncSession]:
        try:
            async with self._session_factory.begin() as database_session:
                yield database_session
        except (DBAPIError, OSError) as database_error:
            if isinstance(database_error, IntegrityError):
                raise
            raise DependencyUnavailableError(
                error_code="database_unavailable",
                message_text="PostgreSQL operation failed",
            ) from database_error

    @contextlib.asynccontextmanager
    async def reading_session(self) -> collections.abc.AsyncIterator[AsyncSession]:
        try:
            async with self._session_factory() as database_session:
                yield database_session
        except (DBAPIError, OSError) as database_error:
            raise DependencyUnavailableError(
                error_code="database_unavailable",
                message_text="PostgreSQL operation failed",
            ) from database_error
