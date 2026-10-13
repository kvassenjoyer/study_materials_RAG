# Test-only credential literals are intentional.
# ruff: noqa: S106
"""Each test migrates its own PostgreSQL schema; no production tables are modified."""

import asyncio
import collections.abc
import dataclasses
import datetime
import os
import pathlib
import typing
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, MetaData, Table, column, func, insert, inspect, select, table, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from study_rag.adapters import database
from study_rag.adapters.repositories import (
    PostgreSQLAuthRepository,
    PostgreSQLConversationRepository,
    PostgreSQLDocumentRepository,
)
from study_rag.adapters.repositories.projects import PostgreSQLProjectRepository
from study_rag.api.schemas import DocumentResponse
from study_rag.domain import entities, errors


pytestmark = pytest.mark.integration
_MESSAGE_PAIR_SIZE = 2
_FIXTURE_RESPONSE_DURATION_MS = 1200


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class RepositoryHarness:
    database_engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    auth_repository: PostgreSQLAuthRepository
    document_repository: PostgreSQLDocumentRepository
    conversation_repository: PostgreSQLConversationRepository


@typing.final
class ChunkScanArguments(typing.TypedDict):
    tenant_identifier: uuid.UUID
    project_identifier: uuid.UUID
    embedding_profile: str
    limit_count: int
    offset_value: int


@pytest.fixture
async def repository_harness() -> collections.abc.AsyncIterator[RepositoryHarness]:
    database_url: typing.Final = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("Set TEST_DATABASE_URL for PostgreSQL tests; every test uses an isolated schema")
    schema_name: typing.Final = "rag_test_" + uuid.uuid4().hex
    administration_engine: typing.Final = create_async_engine(database_url)
    async with administration_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    database_engine: typing.Final = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema_name}}
    )
    try:
        async with database_engine.begin() as connection:
            await connection.run_sync(apply_test_migration)
        session_factory: typing.Final = async_sessionmaker(database_engine, expire_on_commit=False)
        yield RepositoryHarness(
            database_engine=database_engine,
            session_factory=session_factory,
            auth_repository=PostgreSQLAuthRepository(session_factory=session_factory),
            document_repository=PostgreSQLDocumentRepository(session_factory=session_factory),
            conversation_repository=PostgreSQLConversationRepository(session_factory=session_factory),
        )
    finally:
        await database_engine.dispose()
        async with administration_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        await administration_engine.dispose()


def apply_test_migration(connection: Connection, target_revision: str = "head") -> None:
    configuration = Config(str(pathlib.Path(__file__).resolve().parents[2] / "alembic.ini"))
    configuration.attributes["connection"] = connection
    command.upgrade(configuration, target_revision)


async def create_test_account(repository_harness: RepositoryHarness) -> entities.UserAccount:
    return await repository_harness.auth_repository.create_account(
        login_name=f"{uuid.uuid4()}@example.test", password_hash="fixture-hash"
    )


def document_registration(*, content_digest: str = "a" * 64) -> entities.DocumentRegistration:
    document_identifier = uuid.uuid4()
    return entities.DocumentRegistration(
        document_identifier=document_identifier,
        filename_text="lecture.txt",
        media_type="text/plain",
        storage_key=f"fixture/{document_identifier}/source.txt",
        content_digest=content_digest,
        byte_count=36,
        embedding_profile="fixture-embedding-profile",
    )


def chunk_fixture(document_record: entities.DocumentRecord) -> entities.ChunkRecord:
    return entities.ChunkRecord(
        chunk_identifier=uuid.uuid4(),
        document_identifier=document_record.document_identifier,
        tenant_identifier=document_record.tenant_identifier,
        ordinal_value=0,
        content_text="Photosynthesis converts light into chemical energy.",
        token_count=8,
        page_start=1,
        page_end=1,
    )


async def registered_fixture(repository_harness: RepositoryHarness) -> entities.DocumentRecord:
    user_account = await create_test_account(repository_harness)
    return await repository_harness.document_repository.register_document(
        tenant_identifier=user_account.user_identifier,
        document_registration=document_registration(),
    )


async def claim_fixture(repository_harness: RepositoryHarness) -> entities.DocumentLease:
    document_lease = await repository_harness.document_repository.claim_document(
        worker_identifier=uuid.uuid4(), lease_seconds=120
    )
    assert document_lease is not None
    return document_lease


async def expire_document_lease(repository_harness: RepositoryHarness, document_lease: entities.DocumentLease) -> None:
    async with repository_harness.session_factory.begin() as database_session:
        await database_session.execute(
            update(database.DocumentRow)
            .where(database.DocumentRow.document_identifier == document_lease.document_record.document_identifier)
            .values(lease_expires_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1))
        )


async def test_overview_scan_paginates_ready_chunks_and_excludes_deleted_or_foreign_data(
    repository_harness: RepositoryHarness,
) -> None:
    document_record = await registered_fixture(repository_harness)
    document_lease = await claim_fixture(repository_harness)
    chunk_records = tuple(
        dataclasses.replace(chunk_fixture(document_record), ordinal_value=ordinal_value) for ordinal_value in range(3)
    )
    await repository_harness.document_repository.replace_chunks(
        document_lease=document_lease, chunk_records=chunk_records, page_count=3
    )
    arguments: ChunkScanArguments = {
        "tenant_identifier": document_record.tenant_identifier,
        "project_identifier": document_record.project_identifier,
        "embedding_profile": document_record.embedding_profile,
        "limit_count": 2,
        "offset_value": 0,
    }
    assert not await repository_harness.document_repository.list_ready_chunks(**arguments)
    await repository_harness.document_repository.complete_indexing(document_lease=document_lease)
    first_sources = await repository_harness.document_repository.list_ready_chunks(**arguments)
    second_sources = await repository_harness.document_repository.list_ready_chunks(**(arguments | {"offset_value": 2}))
    assert tuple(retrieved_source.chunk_record for retrieved_source in first_sources + second_sources) == chunk_records
    assert not await repository_harness.document_repository.list_ready_chunks(
        **(arguments | {"tenant_identifier": uuid.uuid4()})
    )
    assert not await repository_harness.document_repository.list_ready_chunks(
        **(arguments | {"project_identifier": uuid.uuid4()})
    )
    await repository_harness.document_repository.request_deletion(
        tenant_identifier=document_record.tenant_identifier,
        document_identifier=document_record.document_identifier,
    )
    assert not await repository_harness.document_repository.list_ready_chunks(**arguments)


async def test_initial_migration_creates_all_tables(repository_harness: RepositoryHarness) -> None:
    async with repository_harness.database_engine.connect() as connection:
        table_names = await connection.run_sync(lambda connection: inspect(connection).get_table_names())

    assert set(table_names) == {
        "alembic_version",
        "user_accounts",
        "projects",
        "documents",
        "document_chunks",
        "chat_sessions",
        "chat_turns",
        "chat_messages",
    }


async def test_progress_migration_backfills_ready_documents_and_preserves_existing_fields(
    empty_database_engine: AsyncEngine,
) -> None:
    async with empty_database_engine.begin() as connection:
        await connection.run_sync(apply_test_migration, "c8f4b23e015a")
        previous_documents = await connection.run_sync(
            lambda database_connection: Table("documents", MetaData(), autoload_with=database_connection)
        )
    session_factory = async_sessionmaker(empty_database_engine, expire_on_commit=False)
    account_record = await PostgreSQLAuthRepository(session_factory=session_factory).create_account(
        login_name="progress-migration@example.test", password_hash="fixture-hash"
    )
    project_record = await PostgreSQLProjectRepository(session_factory=session_factory).create_project(
        tenant_identifier=account_record.user_identifier, title_text="Existing materials"
    )
    timestamp_value = datetime.datetime.now(datetime.UTC)
    document_identifier = uuid.uuid4()
    async with empty_database_engine.begin() as connection:
        await connection.execute(
            insert(previous_documents).values(
                document_identifier=document_identifier,
                tenant_identifier=account_record.user_identifier,
                project_identifier=project_record.project_identifier,
                filename_text="existing-book.pdf",
                media_type="application/pdf",
                storage_key="fixture/book",
                content_digest="a" * 64,
                byte_count=8000000,
                embedding_profile="fixture-profile",
                state_name="ready",
                chunk_count=123,
                page_count=535,
                next_attempt_at=timestamp_value,
                attempts=1,
                created_at=timestamp_value,
                updated_at=timestamp_value,
            )
        )
        previous_record = (await connection.execute(select(previous_documents))).mappings().one()
        await connection.run_sync(apply_test_migration)
        current_record = (await connection.execute(select(database.DocumentRow))).mappings().one()
        assert current_record["indexed_chunk_count"] == previous_record["chunk_count"]
        assert {column_name: current_record[column_name] for column_name in previous_record} == dict(previous_record)

        def downgrade_progress(database_connection: Connection) -> None:
            configuration = Config("alembic.ini")
            configuration.attributes["connection"] = database_connection
            command.downgrade(configuration, "c8f4b23e015a")

        await connection.run_sync(downgrade_progress)
        downgraded_record = (await connection.execute(select(previous_documents))).mappings().one()
        assert dict(downgraded_record) == dict(previous_record)


async def test_automatic_chat_title_is_set_once_and_manual_name_wins(repository_harness: RepositoryHarness) -> None:
    account_record = await create_test_account(repository_harness)
    repository = repository_harness.conversation_repository
    chat_record = await repository.create_chat(tenant_identifier=account_record.user_identifier)
    first_request = uuid.uuid4()
    first_turn = await repository.begin_turn(
        tenant_identifier=account_record.user_identifier,
        chat_identifier=chat_record.chat_identifier,
        client_request_identifier=first_request,
        question_text="  Объясни\nградиентный спуск  ",  # noqa: RUF001 - Russian whitespace fixture
        lease_seconds=120,
    )
    titled_chat = await repository.fetch_chat(
        tenant_identifier=account_record.user_identifier, chat_identifier=chat_record.chat_identifier
    )
    assert titled_chat.title_text == "Объясни градиентный спуск"
    await repository.fail_turn(
        tenant_identifier=account_record.user_identifier, turn_record=first_turn, error_code="fixture_error"
    )
    await repository.rename_chat(
        tenant_identifier=account_record.user_identifier,
        chat_identifier=chat_record.chat_identifier,
        title_text="Оптимизация",
    )
    await repository.begin_turn(
        tenant_identifier=account_record.user_identifier,
        chat_identifier=chat_record.chat_identifier,
        client_request_identifier=first_request,
        question_text="  Объясни\nградиентный спуск  ",  # noqa: RUF001 - Russian whitespace fixture
        lease_seconds=120,
    )
    assert (
        await repository.fetch_chat(
            tenant_identifier=account_record.user_identifier, chat_identifier=chat_record.chat_identifier
        )
    ).title_text == "Оптимизация"
    manual_chat = await repository.create_chat(
        tenant_identifier=account_record.user_identifier, title_text="Мой семинар"
    )
    await repository.begin_turn(
        tenant_identifier=account_record.user_identifier,
        chat_identifier=manual_chat.chat_identifier,
        client_request_identifier=uuid.uuid4(),
        question_text="Другой вопрос",
        lease_seconds=120,
    )
    assert (
        await repository.fetch_chat(
            tenant_identifier=account_record.user_identifier, chat_identifier=manual_chat.chat_identifier
        )
    ).title_text == "Мой семинар"


async def test_chat_deletion_removes_history_and_prevents_late_completion(
    repository_harness: RepositoryHarness,
) -> None:
    document_record = await registered_fixture(repository_harness)
    repository = repository_harness.conversation_repository
    chat_record = await repository.create_chat(tenant_identifier=document_record.tenant_identifier)
    pending_turn = await repository.begin_turn(
        tenant_identifier=document_record.tenant_identifier,
        chat_identifier=chat_record.chat_identifier,
        client_request_identifier=uuid.uuid4(),
        question_text="Привет",
        lease_seconds=120,
    )
    await repository.delete_chat(
        tenant_identifier=document_record.tenant_identifier, chat_identifier=chat_record.chat_identifier
    )
    with pytest.raises(errors.ResourceNotFoundError):
        await repository.complete_turn(
            tenant_identifier=document_record.tenant_identifier,
            turn_record=pending_turn,
            generated_answer=entities.GeneratedAnswer(
                answer_text="Late answer", sufficient_context=False, citation_identifiers=()
            ),
            citation_records=(),
        )
    await repository.fail_turn(
        tenant_identifier=document_record.tenant_identifier, turn_record=pending_turn, error_code="late_failure"
    )
    assert (
        await repository.list_chats(
            tenant_identifier=document_record.tenant_identifier, limit_count=100, offset_value=0
        )
    ).total_count == 0
    async with repository_harness.session_factory() as database_session:
        assert await database_session.scalar(select(func.count()).select_from(database.ChatMessageRow)) == 0
        assert await database_session.scalar(select(func.count()).select_from(database.ChatTurnRow)) == 0
    assert (
        await repository_harness.document_repository.fetch_document(
            tenant_identifier=document_record.tenant_identifier, document_identifier=document_record.document_identifier
        )
        == document_record
    )


async def test_project_deletion_revokes_index_lease_and_does_not_reappear(
    repository_harness: RepositoryHarness,
) -> None:
    document_record = await registered_fixture(repository_harness)
    old_lease = await claim_fixture(repository_harness)
    project_repository = PostgreSQLProjectRepository(session_factory=repository_harness.session_factory)
    renamed_project = await project_repository.rename_project(
        tenant_identifier=document_record.tenant_identifier,
        project_identifier=document_record.project_identifier,
        title_text="Лекции",
    )
    assert renamed_project.title_text == "Лекции"
    chat_record = await repository_harness.conversation_repository.create_chat(
        tenant_identifier=document_record.tenant_identifier,
        project_identifier=document_record.project_identifier,
    )
    await project_repository.delete_project(
        tenant_identifier=document_record.tenant_identifier,
        project_identifier=document_record.project_identifier,
    )
    with pytest.raises(errors.ResourceNotFoundError):
        await repository_harness.conversation_repository.fetch_chat(
            tenant_identifier=document_record.tenant_identifier,
            chat_identifier=chat_record.chat_identifier,
        )
    with pytest.raises(errors.ResourceNotFoundError):
        await repository_harness.document_repository.register_document(
            tenant_identifier=document_record.tenant_identifier,
            document_registration=dataclasses.replace(
                document_registration(content_digest="b" * 64), project_identifier=document_record.project_identifier
            ),
        )
    with pytest.raises(errors.LeaseLostError):
        await repository_harness.document_repository.complete_indexing(document_lease=old_lease)
    assert (
        await project_repository.list_projects(
            tenant_identifier=document_record.tenant_identifier, limit_count=100, offset_value=0
        )
    ).total_count == 0
    deletion_lease = await claim_fixture(repository_harness)
    assert deletion_lease.action_name == "delete"
    await repository_harness.document_repository.complete_deletion(document_lease=deletion_lease)
    replacement = await project_repository.create_project(
        tenant_identifier=document_record.tenant_identifier, title_text="Новый курс"
    )
    default_chat = await repository_harness.conversation_repository.create_chat(
        tenant_identifier=document_record.tenant_identifier
    )
    assert default_chat.project_identifier == replacement.project_identifier


async def test_management_rejects_foreign_resources(repository_harness: RepositoryHarness) -> None:
    owner_account = await create_test_account(repository_harness)
    stranger = await create_test_account(repository_harness)
    repository = repository_harness.conversation_repository
    chat_record = await repository.create_chat(tenant_identifier=owner_account.user_identifier)
    project_repository = PostgreSQLProjectRepository(session_factory=repository_harness.session_factory)
    with pytest.raises(errors.ResourceNotFoundError):
        await repository.rename_chat(
            tenant_identifier=stranger.user_identifier,
            chat_identifier=chat_record.chat_identifier,
            title_text="Чужой чат",
        )
    with pytest.raises(errors.ResourceNotFoundError):
        await repository.delete_chat(
            tenant_identifier=stranger.user_identifier, chat_identifier=chat_record.chat_identifier
        )
    with pytest.raises(errors.ResourceNotFoundError):
        await project_repository.rename_project(
            tenant_identifier=stranger.user_identifier,
            project_identifier=chat_record.project_identifier,
            title_text="Чужой проект",
        )
    with pytest.raises(errors.ResourceNotFoundError):
        await project_repository.delete_project(
            tenant_identifier=stranger.user_identifier, project_identifier=chat_record.project_identifier
        )
    assert (
        await repository.fetch_chat(
            tenant_identifier=owner_account.user_identifier, chat_identifier=chat_record.chat_identifier
        )
        == chat_record
    )


async def test_project_deletion_keeps_other_project_history_and_files(repository_harness: RepositoryHarness) -> None:
    document_record = await registered_fixture(repository_harness)
    project_repository = PostgreSQLProjectRepository(session_factory=repository_harness.session_factory)
    other_project = await project_repository.create_project(
        tenant_identifier=document_record.tenant_identifier, title_text="Другой курс"
    )
    other_document = await repository_harness.document_repository.register_document(
        tenant_identifier=document_record.tenant_identifier,
        document_registration=dataclasses.replace(
            document_registration(), project_identifier=other_project.project_identifier
        ),
    )
    repository = repository_harness.conversation_repository
    for project_identifier in (document_record.project_identifier, other_project.project_identifier):
        chat_record = await repository.create_chat(
            tenant_identifier=document_record.tenant_identifier, project_identifier=project_identifier
        )
        await repository.begin_turn(
            tenant_identifier=document_record.tenant_identifier,
            chat_identifier=chat_record.chat_identifier,
            client_request_identifier=uuid.uuid4(),
            question_text="Вопрос",
            lease_seconds=120,
        )
    await project_repository.delete_project(
        tenant_identifier=document_record.tenant_identifier, project_identifier=document_record.project_identifier
    )
    async with repository_harness.session_factory() as database_session:
        assert await database_session.scalar(select(func.count()).select_from(database.ChatMessageRow)) == 1
        assert await database_session.scalar(select(func.count()).select_from(database.ChatTurnRow)) == 1
    assert (
        await repository_harness.document_repository.fetch_document(
            tenant_identifier=document_record.tenant_identifier, document_identifier=other_document.document_identifier
        )
        == other_document
    )
    assert (
        await repository.list_chats(
            tenant_identifier=document_record.tenant_identifier,
            project_identifier=other_project.project_identifier,
            limit_count=100,
            offset_value=0,
        )
    ).total_count == 1


async def test_account_duplicate_and_lookup(repository_harness: RepositoryHarness) -> None:
    user_account = await create_test_account(repository_harness)

    with pytest.raises(errors.ResourceConflictError, match="already registered"):
        await repository_harness.auth_repository.create_account(
            login_name=user_account.login_name, password_hash="other-hash"
        )

    assert (
        await repository_harness.auth_repository.fetch_by_identifier(user_identifier=user_account.user_identifier)
        == user_account
    )
    assert await repository_harness.auth_repository.fetch_by_login(login_name=user_account.login_name) == user_account


async def test_login_accounts_reuse_legacy_column_and_preserve_existing_identifiers(
    repository_harness: RepositoryHarness,
) -> None:
    legacy_identifier = uuid.uuid4()
    async with repository_harness.session_factory.begin() as database_session:
        await database_session.execute(
            insert(
                table(
                    "user_accounts",
                    column("user_identifier"),
                    column("email_address"),
                    column("password_hash"),
                    column("created_at"),
                )
            ).values(
                user_identifier=legacy_identifier,
                email_address="legacy@example.test",
                password_hash="legacy-hash",
                created_at=datetime.datetime.now(datetime.UTC),
            )
        )
    repository = repository_harness.auth_repository
    legacy_account = await repository.fetch_by_login(login_name="legacy@example.test")
    assert legacy_account is not None
    assert legacy_account.user_identifier == legacy_identifier
    assert legacy_account.login_name == "legacy@example.test"
    assert legacy_account.password_hash == "legacy-hash"  # noqa: S105 - Test-only hash.
    new_account = await repository.create_account(login_name="student_123", password_hash="new-hash")
    assert await repository.fetch_by_login(login_name="student_123") == new_account
    with pytest.raises(errors.ResourceConflictError) as raised_error:
        await repository.create_account(login_name="student_123", password_hash="another-hash")
    assert raised_error.value.error_code == "login_already_registered"


async def test_projects_isolate_chats_documents_deduplication_and_ready_chunks(
    repository_harness: RepositoryHarness,
) -> None:
    user_account = await create_test_account(repository_harness)
    project_repository = PostgreSQLProjectRepository(session_factory=repository_harness.session_factory)
    first_project = await project_repository.create_project(
        tenant_identifier=user_account.user_identifier, title_text="Algebra"
    )
    second_project = await project_repository.create_project(
        tenant_identifier=user_account.user_identifier, title_text="Physics"
    )
    first_registration = dataclasses.replace(
        document_registration(), project_identifier=first_project.project_identifier
    )
    second_registration = dataclasses.replace(
        document_registration(), project_identifier=second_project.project_identifier
    )
    first_document = await repository_harness.document_repository.register_document(
        tenant_identifier=user_account.user_identifier,
        document_registration=first_registration,
    )
    second_document = await repository_harness.document_repository.register_document(
        tenant_identifier=user_account.user_identifier,
        document_registration=second_registration,
    )
    duplicate_document = await repository_harness.document_repository.register_document(
        tenant_identifier=user_account.user_identifier,
        document_registration=dataclasses.replace(first_registration, document_identifier=uuid.uuid4()),
    )
    assert duplicate_document.document_identifier == first_document.document_identifier
    assert second_document.document_identifier != first_document.document_identifier
    first_chunk = chunk_fixture(first_document)
    second_chunk = chunk_fixture(second_document)
    for document_record, chunk_record in ((first_document, first_chunk), (second_document, second_chunk)):
        document_lease = await claim_fixture(repository_harness)
        assert document_lease.document_record.document_identifier == document_record.document_identifier
        await repository_harness.document_repository.replace_chunks(
            document_lease=document_lease, chunk_records=(chunk_record,), page_count=1
        )
        await repository_harness.document_repository.complete_indexing(document_lease=document_lease)
    document_page = await repository_harness.document_repository.list_documents(
        tenant_identifier=user_account.user_identifier,
        project_identifier=first_project.project_identifier,
        limit_count=50,
        offset_value=0,
    )
    assert document_page.items == (
        await repository_harness.document_repository.fetch_document(
            tenant_identifier=user_account.user_identifier,
            document_identifier=first_document.document_identifier,
        ),
    )
    assert await repository_harness.document_repository.fetch_ready_identifiers(
        tenant_identifier=user_account.user_identifier,
        project_identifier=first_project.project_identifier,
        embedding_profile=first_document.embedding_profile,
    ) == (first_document.document_identifier,)
    assert await repository_harness.document_repository.fetch_ready_chunks(
        tenant_identifier=user_account.user_identifier,
        project_identifier=first_project.project_identifier,
        embedding_profile=first_document.embedding_profile,
        chunk_identifiers=(first_chunk.chunk_identifier, second_chunk.chunk_identifier),
    ) == (first_chunk,)
    overview_sources = await repository_harness.document_repository.list_ready_chunks(
        tenant_identifier=user_account.user_identifier,
        project_identifier=first_project.project_identifier,
        embedding_profile=first_document.embedding_profile,
        limit_count=256,
        offset_value=0,
    )
    assert tuple(retrieved_source.chunk_record for retrieved_source in overview_sources) == (first_chunk,)
    assert not await repository_harness.document_repository.list_ready_chunks(
        tenant_identifier=user_account.user_identifier,
        project_identifier=first_project.project_identifier,
        embedding_profile="different-profile",
        limit_count=256,
        offset_value=0,
    )
    first_chat = await repository_harness.conversation_repository.create_chat(
        tenant_identifier=user_account.user_identifier,
        project_identifier=first_project.project_identifier,
        title_text="First chat",
    )
    await repository_harness.conversation_repository.create_chat(
        tenant_identifier=user_account.user_identifier,
        project_identifier=second_project.project_identifier,
        title_text="Second chat",
    )
    chat_page = await repository_harness.conversation_repository.list_chats(
        tenant_identifier=user_account.user_identifier,
        project_identifier=first_project.project_identifier,
        limit_count=50,
        offset_value=0,
    )
    assert chat_page.items == (first_chat,)
    foreign_account = await create_test_account(repository_harness)
    with pytest.raises(errors.ResourceNotFoundError):
        await project_repository.fetch_project(
            tenant_identifier=foreign_account.user_identifier,
            project_identifier=first_project.project_identifier,
        )
    with pytest.raises(errors.ResourceNotFoundError):
        await repository_harness.conversation_repository.create_chat(
            tenant_identifier=foreign_account.user_identifier,
            project_identifier=first_project.project_identifier,
            title_text="Forbidden",
        )
    with pytest.raises(errors.ResourceNotFoundError):
        await repository_harness.document_repository.register_document(
            tenant_identifier=foreign_account.user_identifier,
            document_registration=first_registration,
        )


async def test_default_project_creation_is_idempotent_under_concurrent_requests(
    repository_harness: RepositoryHarness,
) -> None:
    user_account = await create_test_account(repository_harness)
    project_repository = PostgreSQLProjectRepository(session_factory=repository_harness.session_factory)
    first_page, second_page = await asyncio.gather(
        *(
            project_repository.list_projects(
                tenant_identifier=user_account.user_identifier, limit_count=50, offset_value=0
            )
            for _request_index in range(2)
        )
    )
    assert first_page.items == second_page.items
    assert len(first_page.items) == 1


async def test_document_dedup_race_is_tenant_scoped(repository_harness: RepositoryHarness) -> None:
    first_account = await create_test_account(repository_harness)
    second_account = await create_test_account(repository_harness)

    first_document, duplicate_document = await asyncio.gather(
        repository_harness.document_repository.register_document(
            tenant_identifier=first_account.user_identifier, document_registration=document_registration()
        ),
        repository_harness.document_repository.register_document(
            tenant_identifier=first_account.user_identifier, document_registration=document_registration()
        ),
    )

    assert first_document.document_identifier == duplicate_document.document_identifier
    second_document = await repository_harness.document_repository.register_document(
        tenant_identifier=second_account.user_identifier, document_registration=document_registration()
    )
    assert second_document.document_identifier != first_document.document_identifier
    with pytest.raises(errors.ResourceNotFoundError):
        await repository_harness.document_repository.fetch_document(
            tenant_identifier=second_account.user_identifier,
            document_identifier=first_document.document_identifier,
        )
    assert (
        await repository_harness.document_repository.list_documents(
            tenant_identifier=first_account.user_identifier, limit_count=50, offset_value=0
        )
    ).total_count == 1


async def test_partial_index_not_visible_and_ready_chunks_are_tenant_scoped(
    repository_harness: RepositoryHarness,
) -> None:
    document_record = await registered_fixture(repository_harness)
    document_lease = await claim_fixture(repository_harness)
    stored_chunk = chunk_fixture(document_record)
    await repository_harness.document_repository.replace_chunks(
        document_lease=document_lease, chunk_records=(stored_chunk,), page_count=1
    )

    assert (
        await repository_harness.document_repository.fetch_ready_identifiers(
            project_identifier=document_record.project_identifier,
            tenant_identifier=document_record.tenant_identifier,
            embedding_profile=document_record.embedding_profile,
        )
        == ()
    )
    assert (
        await repository_harness.document_repository.fetch_ready_chunks(
            project_identifier=document_record.project_identifier,
            tenant_identifier=document_record.tenant_identifier,
            embedding_profile=document_record.embedding_profile,
            chunk_identifiers=(stored_chunk.chunk_identifier,),
        )
        == ()
    )
    with pytest.raises(errors.ResourceNotFoundError):
        await repository_harness.document_repository.fetch_chunk(
            tenant_identifier=document_record.tenant_identifier, chunk_identifier=stored_chunk.chunk_identifier
        )
    await repository_harness.document_repository.complete_indexing(document_lease=document_lease)
    assert await repository_harness.document_repository.fetch_ready_chunks(
        project_identifier=document_record.project_identifier,
        tenant_identifier=document_record.tenant_identifier,
        embedding_profile=document_record.embedding_profile,
        chunk_identifiers=(stored_chunk.chunk_identifier,),
    ) == (stored_chunk,)
    assert (
        await repository_harness.document_repository.fetch_ready_chunks(
            project_identifier=document_record.project_identifier,
            tenant_identifier=uuid.uuid4(),
            embedding_profile=document_record.embedding_profile,
            chunk_identifiers=(stored_chunk.chunk_identifier,),
        )
        == ()
    )
    assert (
        await repository_harness.document_repository.fetch_ready_chunks(
            project_identifier=document_record.project_identifier,
            tenant_identifier=document_record.tenant_identifier,
            embedding_profile="another-profile",
            chunk_identifiers=(stored_chunk.chunk_identifier,),
        )
        == ()
    )
    retrieved_chunk = await repository_harness.document_repository.fetch_chunk(
        tenant_identifier=document_record.tenant_identifier, chunk_identifier=stored_chunk.chunk_identifier
    )
    assert retrieved_chunk.chunk_record == stored_chunk


async def test_expired_lease_reclaimed_after_repository_restart(repository_harness: RepositoryHarness) -> None:
    document_record = await registered_fixture(repository_harness)
    original_lease = await claim_fixture(repository_harness)
    await repository_harness.document_repository.replace_chunks(
        document_lease=original_lease, chunk_records=(chunk_fixture(document_record),), page_count=1
    )
    await repository_harness.document_repository.update_indexing_progress(
        document_lease=original_lease, indexed_chunk_count=1
    )
    await expire_document_lease(repository_harness, original_lease)
    reopened_repository = PostgreSQLDocumentRepository(session_factory=repository_harness.session_factory)

    recovered_lease = await reopened_repository.claim_document(worker_identifier=uuid.uuid4(), lease_seconds=120)

    assert recovered_lease is not None
    assert recovered_lease.document_record.document_identifier == document_record.document_identifier
    assert recovered_lease.lease_identifier != original_lease.lease_identifier
    assert recovered_lease.document_record.indexed_chunk_count == 0
    assert recovered_lease.document_record.indexing_progress_percent == 0
    with pytest.raises(errors.LeaseLostError):
        await reopened_repository.update_indexing_progress(document_lease=original_lease, indexed_chunk_count=1)
    assert not await reopened_repository.renew_lease(document_lease=original_lease, lease_seconds=120)
    with pytest.raises(errors.LeaseLostError):
        await reopened_repository.replace_chunks(
            document_lease=original_lease, chunk_records=(chunk_fixture(document_record),), page_count=1
        )
    with pytest.raises(errors.LeaseLostError):
        await reopened_repository.complete_indexing(document_lease=original_lease)
    with pytest.raises(errors.LeaseLostError):
        await reopened_repository.fail_indexing(
            document_lease=original_lease, error_code="late_failure", error_message="stale"
        )


async def test_indexing_progress_persists_and_reaches_100_only_when_ready(
    repository_harness: RepositoryHarness,
) -> None:
    document_record = await registered_fixture(repository_harness)
    document_lease = await claim_fixture(repository_harness)
    chunk_records = tuple(
        dataclasses.replace(chunk_fixture(document_record), ordinal_value=ordinal_value) for ordinal_value in range(3)
    )
    await repository_harness.document_repository.replace_chunks(
        document_lease=document_lease, chunk_records=chunk_records, page_count=3
    )
    await repository_harness.document_repository.update_indexing_progress(
        document_lease=document_lease, indexed_chunk_count=1
    )
    reopened_repository = PostgreSQLDocumentRepository(session_factory=repository_harness.session_factory)
    progress_record = await reopened_repository.fetch_document(
        tenant_identifier=document_record.tenant_identifier,
        document_identifier=document_record.document_identifier,
    )
    expected_percent = 33
    assert progress_record.indexed_chunk_count == 1
    assert progress_record.indexing_progress_percent == expected_percent
    assert (
        DocumentResponse.model_validate(progress_record).model_dump()["indexing_progress_percent"] == expected_percent
    )
    await reopened_repository.update_indexing_progress(document_lease=document_lease, indexed_chunk_count=0)
    for invalid_count in (-1, 4):
        with pytest.raises(errors.InvalidInputError):
            await reopened_repository.update_indexing_progress(
                document_lease=document_lease, indexed_chunk_count=invalid_count
            )
    unchanged_record = await reopened_repository.fetch_document(
        tenant_identifier=document_record.tenant_identifier,
        document_identifier=document_record.document_identifier,
    )
    assert unchanged_record.indexed_chunk_count == 1
    await reopened_repository.update_indexing_progress(
        document_lease=document_lease, indexed_chunk_count=len(chunk_records)
    )
    finalizing_record = await reopened_repository.fetch_document(
        tenant_identifier=document_record.tenant_identifier,
        document_identifier=document_record.document_identifier,
    )
    finalizing_percent = 99
    assert finalizing_record.indexing_progress_percent == finalizing_percent
    assert (
        await reopened_repository.fetch_ready_identifiers(
            tenant_identifier=document_record.tenant_identifier,
            project_identifier=document_record.project_identifier,
            embedding_profile=document_record.embedding_profile,
        )
        == ()
    )
    await reopened_repository.complete_indexing(document_lease=document_lease)
    ready_record = await reopened_repository.fetch_document(
        tenant_identifier=document_record.tenant_identifier,
        document_identifier=document_record.document_identifier,
    )
    complete_percent = 100
    assert ready_record.indexing_progress_percent == complete_percent
    assert ready_record.indexed_chunk_count == len(chunk_records)


async def test_ready_rebuild_requires_explicit_flag_and_keeps_ownership(repository_harness: RepositoryHarness) -> None:
    document_record = await registered_fixture(repository_harness)
    document_lease = await claim_fixture(repository_harness)
    await repository_harness.document_repository.replace_chunks(
        document_lease=document_lease, chunk_records=(chunk_fixture(document_record),), page_count=2
    )
    await repository_harness.document_repository.complete_indexing(document_lease=document_lease)
    with pytest.raises(errors.ResourceConflictError):
        await repository_harness.document_repository.retry_indexing(
            tenant_identifier=document_record.tenant_identifier,
            document_identifier=document_record.document_identifier,
        )
    with pytest.raises(errors.ResourceNotFoundError):
        await repository_harness.document_repository.retry_indexing(
            tenant_identifier=uuid.uuid4(),
            document_identifier=document_record.document_identifier,
            rebuild_requested=True,
        )
    rebuilt_record = await repository_harness.document_repository.retry_indexing(
        tenant_identifier=document_record.tenant_identifier,
        document_identifier=document_record.document_identifier,
        rebuild_requested=True,
    )
    assert rebuilt_record.state_name == entities.DocumentState.QUEUED
    assert rebuilt_record.indexed_chunk_count == 0
    assert rebuilt_record.document_identifier == document_record.document_identifier
    repeated_record = await repository_harness.document_repository.retry_indexing(
        tenant_identifier=document_record.tenant_identifier,
        document_identifier=document_record.document_identifier,
        rebuild_requested=True,
    )
    assert repeated_record == rebuilt_record
    await repository_harness.document_repository.request_deletion(
        tenant_identifier=document_record.tenant_identifier,
        document_identifier=document_record.document_identifier,
    )
    with pytest.raises(errors.ResourceConflictError):
        await repository_harness.document_repository.retry_indexing(
            tenant_identifier=document_record.tenant_identifier,
            document_identifier=document_record.document_identifier,
            rebuild_requested=True,
        )


async def test_retry_clears_failed_progress_and_deletion_revokes_updates(repository_harness: RepositoryHarness) -> None:
    document_record = await registered_fixture(repository_harness)
    indexing_lease = await claim_fixture(repository_harness)
    chunk_records = tuple(
        dataclasses.replace(chunk_fixture(document_record), ordinal_value=ordinal_value) for ordinal_value in range(2)
    )
    await repository_harness.document_repository.replace_chunks(
        document_lease=indexing_lease, chunk_records=chunk_records, page_count=2
    )
    await repository_harness.document_repository.update_indexing_progress(
        document_lease=indexing_lease, indexed_chunk_count=1
    )
    await repository_harness.document_repository.fail_indexing(
        document_lease=indexing_lease, error_code="fixture", error_message="fixture"
    )
    failed_record = await repository_harness.document_repository.fetch_document(
        tenant_identifier=document_record.tenant_identifier,
        document_identifier=document_record.document_identifier,
    )
    assert failed_record.indexed_chunk_count == 1
    retried_record = await repository_harness.document_repository.retry_indexing(
        tenant_identifier=document_record.tenant_identifier,
        document_identifier=document_record.document_identifier,
    )
    assert retried_record.indexed_chunk_count == 0
    retry_lease = await claim_fixture(repository_harness)
    await repository_harness.document_repository.request_deletion(
        tenant_identifier=document_record.tenant_identifier,
        document_identifier=document_record.document_identifier,
    )
    with pytest.raises(errors.LeaseLostError):
        await repository_harness.document_repository.update_indexing_progress(
            document_lease=retry_lease, indexed_chunk_count=0
        )
    deletion_lease = await claim_fixture(repository_harness)
    with pytest.raises(errors.LeaseLostError):
        await repository_harness.document_repository.update_indexing_progress(
            document_lease=deletion_lease, indexed_chunk_count=0
        )


async def test_claim_is_exclusive_and_empty_document_never_ready(repository_harness: RepositoryHarness) -> None:
    await registered_fixture(repository_harness)

    claimed_leases = await asyncio.gather(
        *[
            repository_harness.document_repository.claim_document(worker_identifier=uuid.uuid4(), lease_seconds=120)
            for one_iteration in range(2)
        ]
    )

    present_leases = tuple(
        one_document_lease for one_document_lease in claimed_leases if one_document_lease is not None
    )
    assert len(present_leases) == 1
    with pytest.raises(errors.InvalidInputError, match="empty document"):
        await repository_harness.document_repository.complete_indexing(document_lease=present_leases[0])


async def test_tombstone_revokes_lease_and_cleanup_backoff_is_durable(repository_harness: RepositoryHarness) -> None:
    document_record = await registered_fixture(repository_harness)
    indexing_lease = await claim_fixture(repository_harness)
    await repository_harness.document_repository.replace_chunks(
        document_lease=indexing_lease, chunk_records=(chunk_fixture(document_record),), page_count=1
    )
    await repository_harness.document_repository.complete_indexing(document_lease=indexing_lease)

    deleting_record = await repository_harness.document_repository.request_deletion(
        tenant_identifier=document_record.tenant_identifier, document_identifier=document_record.document_identifier
    )

    assert deleting_record.state_name is entities.DocumentState.DELETING
    assert (
        await repository_harness.document_repository.fetch_ready_identifiers(
            project_identifier=document_record.project_identifier,
            tenant_identifier=document_record.tenant_identifier,
            embedding_profile=document_record.embedding_profile,
        )
        == ()
    )
    assert not await repository_harness.document_repository.renew_lease(
        document_lease=indexing_lease, lease_seconds=120
    )
    deletion_lease = await claim_fixture(repository_harness)
    assert deletion_lease.action_name == "delete"
    await repository_harness.document_repository.fail_deletion(
        document_lease=deletion_lease, error_code="vector_unavailable", error_message="dependency unavailable"
    )
    assert (
        await repository_harness.document_repository.claim_document(worker_identifier=uuid.uuid4(), lease_seconds=120)
        is None
    )
    failed_cleanup = await repository_harness.document_repository.fetch_document(
        tenant_identifier=document_record.tenant_identifier, document_identifier=document_record.document_identifier
    )
    assert failed_cleanup.state_name is entities.DocumentState.DELETING
    assert failed_cleanup.error_code == "vector_unavailable"
    async with repository_harness.session_factory.begin() as database_session:
        await database_session.execute(
            update(database.DocumentRow)
            .where(database.DocumentRow.document_identifier == document_record.document_identifier)
            .values(next_attempt_at=datetime.datetime.now(datetime.UTC))
        )
    retry_lease = await claim_fixture(repository_harness)
    await repository_harness.document_repository.complete_deletion(document_lease=retry_lease)
    with pytest.raises(errors.ResourceNotFoundError):
        await repository_harness.document_repository.fetch_document(
            tenant_identifier=document_record.tenant_identifier, document_identifier=document_record.document_identifier
        )
    replacement_document = await repository_harness.document_repository.register_document(
        tenant_identifier=document_record.tenant_identifier, document_registration=document_registration()
    )
    assert replacement_document.document_identifier != document_record.document_identifier


async def test_failed_index_retry_preserves_no_false_ready(repository_harness: RepositoryHarness) -> None:
    document_record = await registered_fixture(repository_harness)
    indexing_lease = await claim_fixture(repository_harness)
    await repository_harness.document_repository.replace_chunks(
        document_lease=indexing_lease, chunk_records=(chunk_fixture(document_record),), page_count=1
    )
    await repository_harness.document_repository.fail_indexing(
        document_lease=indexing_lease, error_code="partial_upsert", error_message="dependency unavailable"
    )

    retried_document = await repository_harness.document_repository.retry_indexing(
        tenant_identifier=document_record.tenant_identifier, document_identifier=document_record.document_identifier
    )

    assert retried_document.state_name is entities.DocumentState.QUEUED
    assert retried_document.error_code is None
    assert (
        await repository_harness.document_repository.fetch_ready_identifiers(
            project_identifier=document_record.project_identifier,
            tenant_identifier=document_record.tenant_identifier,
            embedding_profile=document_record.embedding_profile,
        )
        == ()
    )


async def test_turn_concurrency_retry_and_replay(repository_harness: RepositoryHarness) -> None:
    user_account = await create_test_account(repository_harness)
    chat_record = await repository_harness.conversation_repository.create_chat(
        tenant_identifier=user_account.user_identifier, title_text="Biology"
    )
    first_turn = await repository_harness.conversation_repository.begin_turn(
        tenant_identifier=user_account.user_identifier,
        chat_identifier=chat_record.chat_identifier,
        client_request_identifier=uuid.uuid4(),
        question_text="What is photosynthesis?",
        lease_seconds=300,
    )
    with pytest.raises(errors.ResourceConflictError, match="already being generated"):
        await repository_harness.conversation_repository.begin_turn(
            tenant_identifier=user_account.user_identifier,
            chat_identifier=chat_record.chat_identifier,
            client_request_identifier=uuid.uuid4(),
            question_text="Another question",
            lease_seconds=300,
        )
    assert (
        await repository_harness.conversation_repository.fetch_history(
            tenant_identifier=user_account.user_identifier, chat_identifier=chat_record.chat_identifier, message_limit=8
        )
        == ()
    )
    await repository_harness.conversation_repository.fail_turn(
        tenant_identifier=user_account.user_identifier, turn_record=first_turn, error_code="model_unavailable"
    )
    retried_turn = await repository_harness.conversation_repository.begin_turn(
        tenant_identifier=user_account.user_identifier,
        chat_identifier=chat_record.chat_identifier,
        client_request_identifier=first_turn.client_request_identifier,
        question_text=first_turn.user_message.content_text,
        lease_seconds=300,
    )
    assert retried_turn.user_message.message_identifier == first_turn.user_message.message_identifier
    with pytest.raises(errors.LeaseLostError):
        await repository_harness.conversation_repository.complete_turn(
            tenant_identifier=user_account.user_identifier,
            turn_record=first_turn,
            generated_answer=entities.GeneratedAnswer(
                answer_text="stale", sufficient_context=False, citation_identifiers=()
            ),
            citation_records=(),
        )
    await repository_harness.conversation_repository.fail_turn(
        tenant_identifier=user_account.user_identifier, turn_record=first_turn, error_code="stale_error"
    )
    completed_answer = await repository_harness.conversation_repository.complete_turn(
        tenant_identifier=user_account.user_identifier,
        turn_record=retried_turn,
        generated_answer=entities.GeneratedAnswer(
            answer_text="Insufficient context", sufficient_context=False, citation_identifiers=()
        ),
        citation_records=(),
    )

    replayed_turn = await repository_harness.conversation_repository.begin_turn(
        tenant_identifier=user_account.user_identifier,
        chat_identifier=chat_record.chat_identifier,
        client_request_identifier=first_turn.client_request_identifier,
        question_text=first_turn.user_message.content_text,
        lease_seconds=300,
    )

    assert replayed_turn.status_name == "completed"
    assert replayed_turn.assistant_message == completed_answer.assistant_message
    message_page = await repository_harness.conversation_repository.list_messages(
        tenant_identifier=user_account.user_identifier,
        chat_identifier=chat_record.chat_identifier,
        limit_count=50,
        offset_value=0,
    )
    assert message_page.total_count == _MESSAGE_PAIR_SIZE
    assert [one_message_record.role_name for one_message_record in message_page.items] == ["user", "assistant"]
    assert (
        len(
            await repository_harness.conversation_repository.fetch_history(
                tenant_identifier=user_account.user_identifier,
                chat_identifier=chat_record.chat_identifier,
                message_limit=8,
            )
        )
        == _MESSAGE_PAIR_SIZE
    )
    with pytest.raises(errors.ResourceConflictError, match="another question"):
        await repository_harness.conversation_repository.begin_turn(
            tenant_identifier=user_account.user_identifier,
            chat_identifier=chat_record.chat_identifier,
            client_request_identifier=first_turn.client_request_identifier,
            question_text="Different text",
            lease_seconds=300,
        )
    with pytest.raises(errors.ResourceNotFoundError):
        await repository_harness.conversation_repository.list_messages(
            tenant_identifier=uuid.uuid4(), chat_identifier=chat_record.chat_identifier, limit_count=50, offset_value=0
        )


async def test_expired_turn_cannot_complete_after_new_request(repository_harness: RepositoryHarness) -> None:
    user_account = await create_test_account(repository_harness)
    chat_record = await repository_harness.conversation_repository.create_chat(
        tenant_identifier=user_account.user_identifier, title_text="Algorithms"
    )
    expired_turn = await repository_harness.conversation_repository.begin_turn(
        tenant_identifier=user_account.user_identifier,
        chat_identifier=chat_record.chat_identifier,
        client_request_identifier=uuid.uuid4(),
        question_text="First question",
        lease_seconds=300,
    )
    async with repository_harness.session_factory.begin() as database_session:
        await database_session.execute(
            update(database.ChatTurnRow)
            .where(database.ChatTurnRow.turn_identifier == expired_turn.turn_identifier)
            .values(lease_expires_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1))
        )

    replacement_turn = await repository_harness.conversation_repository.begin_turn(
        tenant_identifier=user_account.user_identifier,
        chat_identifier=chat_record.chat_identifier,
        client_request_identifier=uuid.uuid4(),
        question_text="New question",
        lease_seconds=300,
    )

    assert replacement_turn.turn_identifier != expired_turn.turn_identifier
    with pytest.raises(errors.LeaseLostError):
        await repository_harness.conversation_repository.complete_turn(
            tenant_identifier=user_account.user_identifier,
            turn_record=expired_turn,
            generated_answer=entities.GeneratedAnswer(
                answer_text="stale", sufficient_context=False, citation_identifiers=()
            ),
            citation_records=(),
        )
    message_page = await repository_harness.conversation_repository.list_messages(
        tenant_identifier=user_account.user_identifier,
        chat_identifier=chat_record.chat_identifier,
        limit_count=50,
        offset_value=0,
    )
    assert [one_message_record.turn_status for one_message_record in message_page.items] == ["failed", "pending"]


async def test_citation_snapshot_survives_document_deletion(repository_harness: RepositoryHarness) -> None:
    document_record = await registered_fixture(repository_harness)
    document_lease = await claim_fixture(repository_harness)
    stored_chunk = chunk_fixture(document_record)
    await repository_harness.document_repository.replace_chunks(
        document_lease=document_lease, chunk_records=(stored_chunk,), page_count=1
    )
    await repository_harness.document_repository.complete_indexing(document_lease=document_lease)
    chat_record = await repository_harness.conversation_repository.create_chat(
        tenant_identifier=document_record.tenant_identifier, title_text="Lecture"
    )
    turn_record = await repository_harness.conversation_repository.begin_turn(
        tenant_identifier=document_record.tenant_identifier,
        chat_identifier=chat_record.chat_identifier,
        client_request_identifier=uuid.uuid4(),
        question_text="Explain",
        lease_seconds=300,
    )
    citation_record = entities.CitationRecord(
        source_label="S3",
        chunk_identifier=stored_chunk.chunk_identifier,
        document_identifier=document_record.document_identifier,
        filename_text=document_record.filename_text,
        page_start=1,
        page_end=1,
        excerpt_text=stored_chunk.content_text,
        similarity_score=0.91,
        source_url=f"/rest/study-rag/chunks/{stored_chunk.chunk_identifier}/",
    )
    await repository_harness.conversation_repository.complete_turn(
        tenant_identifier=document_record.tenant_identifier,
        turn_record=turn_record,
        generated_answer=entities.GeneratedAnswer(
            answer_text="Photosynthesis [S3]",
            sufficient_context=True,
            citation_identifiers=(stored_chunk.chunk_identifier,),
        ),
        citation_records=(citation_record,),
    )
    await repository_harness.document_repository.request_deletion(
        tenant_identifier=document_record.tenant_identifier, document_identifier=document_record.document_identifier
    )
    deletion_lease = await claim_fixture(repository_harness)
    await repository_harness.document_repository.complete_deletion(document_lease=deletion_lease)

    history_records = await repository_harness.conversation_repository.fetch_history(
        tenant_identifier=document_record.tenant_identifier,
        chat_identifier=chat_record.chat_identifier,
        message_limit=8,
    )

    assert history_records[-1].citation_records == (citation_record,)
    with pytest.raises(errors.ResourceNotFoundError):
        await repository_harness.document_repository.fetch_chunk(
            tenant_identifier=document_record.tenant_identifier, chunk_identifier=stored_chunk.chunk_identifier
        )


async def test_active_indexing_tombstone_rejects_late_completion(repository_harness: RepositoryHarness) -> None:
    document_record = await registered_fixture(repository_harness)
    indexing_lease = await claim_fixture(repository_harness)
    await repository_harness.document_repository.replace_chunks(
        document_lease=indexing_lease, chunk_records=(chunk_fixture(document_record),), page_count=1
    )

    await repository_harness.document_repository.request_deletion(
        tenant_identifier=document_record.tenant_identifier, document_identifier=document_record.document_identifier
    )

    with pytest.raises(errors.LeaseLostError):
        await repository_harness.document_repository.complete_indexing(document_lease=indexing_lease)
    deletion_lease = await claim_fixture(repository_harness)
    assert deletion_lease.action_name == "delete"
    assert (
        await repository_harness.document_repository.fetch_ready_identifiers(
            project_identifier=document_record.project_identifier,
            tenant_identifier=document_record.tenant_identifier,
            embedding_profile=document_record.embedding_profile,
        )
        == ()
    )


async def test_concurrent_chat_requests_reserve_only_one_turn(repository_harness: RepositoryHarness) -> None:
    user_account = await create_test_account(repository_harness)
    chat_record = await repository_harness.conversation_repository.create_chat(
        tenant_identifier=user_account.user_identifier, title_text="Concurrent chat"
    )

    turn_results = await asyncio.gather(
        *[
            repository_harness.conversation_repository.begin_turn(
                tenant_identifier=user_account.user_identifier,
                chat_identifier=chat_record.chat_identifier,
                client_request_identifier=uuid.uuid4(),
                question_text="Concurrent question",
                lease_seconds=300,
            )
            for one_iteration in range(2)
        ],
        return_exceptions=True,
    )

    successful_turns = tuple(one_result for one_result in turn_results if isinstance(one_result, entities.TurnRecord))
    conflicting_turns = tuple(
        one_result for one_result in turn_results if isinstance(one_result, errors.ResourceConflictError)
    )
    assert len(successful_turns) == 1
    assert len(conflicting_turns) == 1
    message_page = await repository_harness.conversation_repository.list_messages(
        tenant_identifier=user_account.user_identifier,
        chat_identifier=chat_record.chat_identifier,
        limit_count=50,
        offset_value=0,
    )
    assert message_page.total_count == 1


@pytest.fixture
async def empty_database_engine(repository_harness: RepositoryHarness) -> collections.abc.AsyncIterator[AsyncEngine]:
    schema_name: typing.Final = "rag_empty_" + uuid.uuid4().hex
    async with repository_harness.database_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    database_engine: typing.Final = create_async_engine(
        repository_harness.database_engine.url,
        connect_args={"server_settings": {"search_path": schema_name}},
    )
    try:
        yield database_engine
    finally:
        await database_engine.dispose()
        async with repository_harness.database_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))


def resources_for_test_engine(
    *,
    database_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> database.DatabaseResources:
    def build_isolated_engine(database_url: str, *, pool_pre_ping: bool) -> AsyncEngine:
        assert database_url == "postgresql+asyncpg://isolated-test-schema"
        assert pool_pre_ping
        return database_engine

    # Inject only connection construction; readiness queries use the actual PostgreSQL schema.
    monkeypatch.setattr(database, "create_async_engine", build_isolated_engine)
    return database.DatabaseResources(database_url="postgresql+asyncpg://isolated-test-schema")


async def test_empty_schema_is_unavailable_without_auto_migration(
    empty_database_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_resources = resources_for_test_engine(database_engine=empty_database_engine, monkeypatch=monkeypatch)

    assert not await database_resources.check_readiness()
    with pytest.raises(errors.DependencyUnavailableError) as captured_error:
        await database_resources.initialize_database()

    assert captured_error.value.error_code == "database_schema_unavailable"
    async with empty_database_engine.connect() as connection:
        table_names = await connection.run_sync(lambda connection: inspect(connection).get_table_names())
    assert table_names == []


async def test_migrated_schema_is_ready(
    repository_harness: RepositoryHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_resources = resources_for_test_engine(
        database_engine=repository_harness.database_engine, monkeypatch=monkeypatch
    )

    await database_resources.initialize_database()

    assert await database_resources.check_readiness()


async def test_missing_required_column_is_unavailable(
    repository_harness: RepositoryHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with repository_harness.database_engine.begin() as connection:
        await connection.execute(text("ALTER TABLE chat_messages DROP COLUMN citation_payload"))
    database_resources = resources_for_test_engine(
        database_engine=repository_harness.database_engine, monkeypatch=monkeypatch
    )

    assert not await database_resources.check_readiness()

    with pytest.raises(errors.DependencyUnavailableError) as captured_error:
        await database_resources.initialize_database()
    assert captured_error.value.error_code == "database_schema_unavailable"


async def test_profile_change_allows_new_job_with_same_content(repository_harness: RepositoryHarness) -> None:
    old_document = await registered_fixture(repository_harness)
    old_lease = await claim_fixture(repository_harness)
    await repository_harness.document_repository.replace_chunks(
        document_lease=old_lease, chunk_records=(chunk_fixture(old_document),), page_count=1
    )
    await repository_harness.document_repository.complete_indexing(document_lease=old_lease)
    new_profile = "second-embedding-profile"
    new_registration = dataclasses.replace(document_registration(), embedding_profile=new_profile)
    duplicate_registration = dataclasses.replace(new_registration, document_identifier=uuid.uuid4())

    new_document, duplicate_document = await asyncio.gather(
        repository_harness.document_repository.register_document(
            tenant_identifier=old_document.tenant_identifier, document_registration=new_registration
        ),
        repository_harness.document_repository.register_document(
            tenant_identifier=old_document.tenant_identifier, document_registration=duplicate_registration
        ),
    )

    assert new_document.document_identifier == duplicate_document.document_identifier
    assert new_document.document_identifier != old_document.document_identifier
    assert new_document.state_name is entities.DocumentState.QUEUED
    assert (
        await repository_harness.document_repository.fetch_ready_identifiers(
            project_identifier=old_document.project_identifier,
            tenant_identifier=old_document.tenant_identifier,
            embedding_profile=new_profile,
        )
        == ()
    )
    new_lease = await claim_fixture(repository_harness)
    await repository_harness.document_repository.replace_chunks(
        document_lease=new_lease, chunk_records=(chunk_fixture(new_document),), page_count=1
    )
    await repository_harness.document_repository.complete_indexing(document_lease=new_lease)
    assert await repository_harness.document_repository.fetch_ready_identifiers(
        project_identifier=old_document.project_identifier,
        tenant_identifier=old_document.tenant_identifier,
        embedding_profile=new_profile,
    ) == (new_document.document_identifier,)
    assert await repository_harness.document_repository.fetch_ready_identifiers(
        project_identifier=old_document.project_identifier,
        tenant_identifier=old_document.tenant_identifier,
        embedding_profile=old_document.embedding_profile,
    ) == (old_document.document_identifier,)
    foreign_account = await create_test_account(repository_harness)
    foreign_document = await repository_harness.document_repository.register_document(
        tenant_identifier=foreign_account.user_identifier,
        document_registration=dataclasses.replace(new_registration, document_identifier=uuid.uuid4()),
    )
    assert foreign_document.document_identifier != new_document.document_identifier
    with pytest.raises(errors.ResourceNotFoundError):
        await repository_harness.document_repository.fetch_document(
            tenant_identifier=foreign_account.user_identifier,
            document_identifier=new_document.document_identifier,
        )


async def test_profile_dedup_migration_preserves_existing_data(
    empty_database_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with empty_database_engine.begin() as connection:
        await connection.run_sync(apply_test_migration, "4f6c7fea6768")
    session_factory = async_sessionmaker(empty_database_engine, expire_on_commit=False)
    auth_repository = PostgreSQLAuthRepository(session_factory=session_factory)
    document_repository = PostgreSQLDocumentRepository(session_factory=session_factory)
    old_account = await auth_repository.create_account(
        login_name="migration@example.test", password_hash="fixture-hash"
    )
    old_registration = document_registration()
    timestamp_value = datetime.datetime.now(datetime.UTC)
    async with empty_database_engine.begin() as connection:
        await connection.execute(
            insert(database.DocumentRow).values(
                document_identifier=old_registration.document_identifier,
                tenant_identifier=old_account.user_identifier,
                filename_text=old_registration.filename_text,
                media_type=old_registration.media_type,
                storage_key=old_registration.storage_key,
                content_digest=old_registration.content_digest,
                byte_count=old_registration.byte_count,
                embedding_profile=old_registration.embedding_profile,
                state_name="queued",
                chunk_count=0,
                next_attempt_at=timestamp_value,
                attempts=0,
                created_at=timestamp_value,
                updated_at=timestamp_value,
            )
        )
        await connection.execute(
            insert(
                table(
                    "chat_sessions",
                    *[
                        column(column_name)
                        for column_name in (
                            "chat_identifier",
                            "tenant_identifier",
                            "title_text",
                            "created_at",
                            "updated_at",
                        )
                    ],
                )
            ).values(
                chat_identifier=uuid.uuid4(),
                tenant_identifier=old_account.user_identifier,
                title_text="Existing chat",
                created_at=timestamp_value,
                updated_at=timestamp_value,
            )
        )
    database_resources = resources_for_test_engine(database_engine=empty_database_engine, monkeypatch=monkeypatch)
    assert not await database_resources.check_readiness()

    async with empty_database_engine.begin() as connection:
        await connection.run_sync(apply_test_migration)

    assert await database_resources.check_readiness()
    assert await auth_repository.fetch_by_identifier(user_identifier=old_account.user_identifier) == old_account
    old_document = await document_repository.fetch_document(
        tenant_identifier=old_account.user_identifier,
        document_identifier=old_registration.document_identifier,
    )
    assert old_document.filename_text == old_registration.filename_text
    assert old_document.content_digest == old_registration.content_digest
    assert old_document.storage_key == old_registration.storage_key
    assert old_document.state_name == entities.DocumentState.QUEUED
    old_chats = await PostgreSQLConversationRepository(session_factory=session_factory).list_chats(
        tenant_identifier=old_account.user_identifier,
        limit_count=50,
        offset_value=0,
    )
    assert len(old_chats.items) == 1
    assert old_chats.items[0].project_identifier == old_document.project_identifier
    new_document = await document_repository.register_document(
        tenant_identifier=old_account.user_identifier,
        document_registration=dataclasses.replace(document_registration(), embedding_profile="migration-new-profile"),
    )
    assert new_document.document_identifier != old_document.document_identifier
    assert (
        await document_repository.list_documents(
            tenant_identifier=old_account.user_identifier, limit_count=50, offset_value=0
        )
    ).total_count == _MESSAGE_PAIR_SIZE
