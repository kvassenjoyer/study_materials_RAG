import datetime
import typing
import uuid

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from study_rag.adapters import database
from study_rag.adapters.repositories import mapping
from study_rag.adapters.repositories.projects import resolve_project
from study_rag.domain import entities, errors


maximum_chunk_page_size: typing.Final = 512


@typing.final
class PostgreSQLDocumentRepository:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_scope = database.PostgreSQLSessionScope(session_factory=session_factory)

    async def list_ready_chunks(
        self,
        *,
        tenant_identifier: uuid.UUID,
        project_identifier: uuid.UUID,
        embedding_profile: str,
        limit_count: int,
        offset_value: int,
    ) -> tuple[entities.RetrievedChunk, ...]:
        if not 1 <= limit_count <= maximum_chunk_page_size or offset_value < 0:
            raise errors.InvalidInputError(error_code="invalid_chunk_page", message_text="Invalid chunk page")
        async with self._session_scope.reading_session() as database_session:
            chunk_rows = await database_session.execute(
                select(database.ChunkRow, database.DocumentRow.filename_text)
                .join(
                    database.DocumentRow,
                    and_(
                        database.ChunkRow.document_identifier == database.DocumentRow.document_identifier,
                        database.ChunkRow.tenant_identifier == database.DocumentRow.tenant_identifier,
                    ),
                )
                .join(
                    database.ProjectRow,
                    and_(
                        database.ProjectRow.project_identifier == database.DocumentRow.project_identifier,
                        database.ProjectRow.tenant_identifier == database.DocumentRow.tenant_identifier,
                    ),
                )
                .where(
                    database.ChunkRow.tenant_identifier == tenant_identifier,
                    database.DocumentRow.tenant_identifier == tenant_identifier,
                    database.DocumentRow.project_identifier == project_identifier,
                    database.DocumentRow.embedding_profile == embedding_profile,
                    database.DocumentRow.state_name == "ready",
                    database.ProjectRow.deleted_at.is_(None),
                )
                .order_by(database.ChunkRow.document_identifier, database.ChunkRow.ordinal_value)
                .offset(offset_value)
                .limit(limit_count)
            )
            return tuple(
                entities.RetrievedChunk(
                    chunk_record=mapping.chunk_record(chunk_row),
                    filename_text=filename_text,
                    similarity_score=0.0,
                )
                for chunk_row, filename_text in chunk_rows
            )

    async def register_document(
        self,
        *,
        tenant_identifier: uuid.UUID,
        document_registration: entities.DocumentRegistration,
    ) -> entities.DocumentRecord:
        async with self._session_scope.transaction_session() as database_session:
            project_row = await resolve_project(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                project_identifier=document_registration.project_identifier,
            )
        timestamp_value: typing.Final = datetime.datetime.now(datetime.UTC)
        document_row: typing.Final = database.DocumentRow(
            document_identifier=document_registration.document_identifier,
            tenant_identifier=tenant_identifier,
            project_identifier=project_row.project_identifier,
            filename_text=document_registration.filename_text,
            media_type=document_registration.media_type,
            storage_key=document_registration.storage_key,
            content_digest=document_registration.content_digest,
            byte_count=document_registration.byte_count,
            embedding_profile=document_registration.embedding_profile,
            state_name="queued",
            chunk_count=0,
            indexed_chunk_count=0,
            page_count=None,
            next_attempt_at=timestamp_value,
            attempts=0,
            created_at=timestamp_value,
            updated_at=timestamp_value,
        )
        try:
            async with self._session_scope.transaction_session() as database_session:
                await resolve_project(
                    database_session=database_session,
                    tenant_identifier=tenant_identifier,
                    project_identifier=document_row.project_identifier,
                )
                database_session.add(document_row)
                await database_session.flush()
        except IntegrityError:
            async with self._session_scope.reading_session() as database_session:
                existing_row: typing.Final = await database_session.scalar(
                    select(database.DocumentRow).where(
                        database.DocumentRow.tenant_identifier == tenant_identifier,
                        database.DocumentRow.project_identifier == project_row.project_identifier,
                        database.DocumentRow.content_digest == document_registration.content_digest,
                        database.DocumentRow.embedding_profile == document_registration.embedding_profile,
                        database.DocumentRow.state_name != "deleted",
                    ),
                )
            if existing_row is None:
                raise
            return mapping.document_record(existing_row)
        return mapping.document_record(document_row)

    async def fetch_document(
        self,
        *,
        tenant_identifier: uuid.UUID,
        document_identifier: uuid.UUID,
    ) -> entities.DocumentRecord:
        async with self._session_scope.reading_session() as database_session:
            document_row: typing.Final = await self._owned_document(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                document_identifier=document_identifier,
            )
            return mapping.document_record(document_row)

    async def list_documents(
        self,
        *,
        tenant_identifier: uuid.UUID,
        limit_count: int,
        offset_value: int,
        project_identifier: uuid.UUID | None = None,
    ) -> entities.PageResult[entities.DocumentRecord]:
        async with self._session_scope.transaction_session() as database_session:
            project_row = await resolve_project(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                project_identifier=project_identifier,
            )
        owned_filter: typing.Final = and_(
            database.DocumentRow.tenant_identifier == tenant_identifier,
            database.DocumentRow.project_identifier == project_row.project_identifier,
            database.DocumentRow.state_name != "deleted",
        )
        async with self._session_scope.reading_session() as database_session:
            total_count: typing.Final = await database_session.scalar(
                select(func.count()).select_from(database.DocumentRow).where(owned_filter),
            )
            document_rows: typing.Final = await database_session.scalars(
                select(database.DocumentRow)
                .where(owned_filter)
                .order_by(database.DocumentRow.created_at, database.DocumentRow.document_identifier)
                .offset(offset_value)
                .limit(limit_count),
            )
            return entities.PageResult(
                items=tuple(mapping.document_record(one_document_row) for one_document_row in document_rows),
                total_count=total_count or 0,
                offset_value=offset_value,
                limit_count=limit_count,
            )

    async def fetch_ready_identifiers(
        self,
        *,
        tenant_identifier: uuid.UUID,
        embedding_profile: str,
        project_identifier: uuid.UUID,
    ) -> tuple[uuid.UUID, ...]:
        async with self._session_scope.reading_session() as database_session:
            document_identifiers: typing.Final = await database_session.scalars(
                select(database.DocumentRow.document_identifier).where(
                    database.DocumentRow.tenant_identifier == tenant_identifier,
                    database.DocumentRow.project_identifier == project_identifier,
                    database.DocumentRow.embedding_profile == embedding_profile,
                    database.DocumentRow.state_name == "ready",
                ),
            )
            return tuple(document_identifiers)

    async def fetch_ready_chunks(
        self,
        *,
        tenant_identifier: uuid.UUID,
        embedding_profile: str,
        chunk_identifiers: tuple[uuid.UUID, ...],
        project_identifier: uuid.UUID,
    ) -> tuple[entities.ChunkRecord, ...]:
        if not chunk_identifiers:
            return ()
        async with self._session_scope.reading_session() as database_session:
            chunk_rows: typing.Final = await database_session.scalars(
                select(database.ChunkRow)
                .join(
                    database.DocumentRow,
                    and_(
                        database.ChunkRow.document_identifier == database.DocumentRow.document_identifier,
                        database.ChunkRow.tenant_identifier == database.DocumentRow.tenant_identifier,
                    ),
                )
                .where(
                    database.ChunkRow.tenant_identifier == tenant_identifier,
                    database.DocumentRow.tenant_identifier == tenant_identifier,
                    database.DocumentRow.project_identifier == project_identifier,
                    database.DocumentRow.embedding_profile == embedding_profile,
                    database.DocumentRow.state_name == "ready",
                    database.ChunkRow.chunk_identifier.in_(chunk_identifiers),
                ),
            )
            return tuple(mapping.chunk_record(one_chunk_row) for one_chunk_row in chunk_rows)

    async def fetch_chunk(
        self,
        *,
        tenant_identifier: uuid.UUID,
        chunk_identifier: uuid.UUID,
    ) -> entities.RetrievedChunk:
        async with self._session_scope.reading_session() as database_session:
            result_rows: typing.Final = await database_session.execute(
                select(database.ChunkRow, database.DocumentRow.filename_text)
                .join(
                    database.DocumentRow,
                    and_(
                        database.ChunkRow.document_identifier == database.DocumentRow.document_identifier,
                        database.ChunkRow.tenant_identifier == database.DocumentRow.tenant_identifier,
                    ),
                )
                .where(
                    database.ChunkRow.tenant_identifier == tenant_identifier,
                    database.DocumentRow.tenant_identifier == tenant_identifier,
                    database.DocumentRow.state_name == "ready",
                    database.ChunkRow.chunk_identifier == chunk_identifier,
                ),
            )
            result_value: typing.Final = result_rows.one_or_none()
            if result_value is None:
                raise errors.ResourceNotFoundError(error_code="chunk_not_found", message_text="Chunk not found")
            return entities.RetrievedChunk(
                chunk_record=mapping.chunk_record(result_value[0]),
                filename_text=result_value[1],
                similarity_score=0.0,
            )

    async def request_deletion(
        self,
        *,
        tenant_identifier: uuid.UUID,
        document_identifier: uuid.UUID,
    ) -> entities.DocumentRecord:
        async with self._session_scope.transaction_session() as database_session:
            document_row: typing.Final = await self._owned_document(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                document_identifier=document_identifier,
                lock_required=True,
            )
            if document_row.state_name != "deleting":
                document_row.state_name = "deleting"
                self._release_lease(document_row=document_row)
                document_row.next_attempt_at = datetime.datetime.now(datetime.UTC)
                document_row.updated_at = document_row.next_attempt_at
                document_row.error_code = None
                document_row.error_message = None
            result_value: typing.Final = mapping.document_record(document_row)
        return result_value

    async def retry_indexing(
        self,
        *,
        tenant_identifier: uuid.UUID,
        document_identifier: uuid.UUID,
        rebuild_requested: bool = False,
    ) -> entities.DocumentRecord:
        async with self._session_scope.transaction_session() as database_session:
            document_row: typing.Final = await self._owned_document(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                document_identifier=document_identifier,
                lock_required=True,
            )
            if document_row.state_name in ("queued", "indexing"):
                return mapping.document_record(document_row)
            if document_row.state_name != "failed" and not (rebuild_requested and document_row.state_name == "ready"):
                raise errors.ResourceConflictError(
                    error_code="document_not_retryable", message_text="Document cannot be retried"
                )
            document_row.state_name = "queued"
            document_row.indexed_chunk_count = 0
            document_row.error_code = None
            document_row.error_message = None
            document_row.next_attempt_at = datetime.datetime.now(datetime.UTC)
            document_row.updated_at = document_row.next_attempt_at
            result_value: typing.Final = mapping.document_record(document_row)
        return result_value

    async def claim_document(
        self,
        *,
        worker_identifier: uuid.UUID,
        lease_seconds: int,
    ) -> entities.DocumentLease | None:
        timestamp_value: typing.Final = datetime.datetime.now(datetime.UTC)
        async with self._session_scope.transaction_session() as database_session:
            document_row: typing.Final = await database_session.scalar(
                select(database.DocumentRow)
                .where(
                    database.DocumentRow.next_attempt_at <= timestamp_value,
                    or_(
                        database.DocumentRow.state_name == "queued",
                        and_(
                            database.DocumentRow.state_name.in_(("indexing", "deleting")),
                            or_(
                                database.DocumentRow.lease_identifier.is_(None),
                                database.DocumentRow.lease_expires_at <= timestamp_value,
                            ),
                        ),
                    ),
                )
                .order_by(database.DocumentRow.next_attempt_at, database.DocumentRow.created_at)
                .with_for_update(skip_locked=True)
                .limit(1),
            )
            if document_row is None:
                return None
            action_name: typing.Literal["index", "delete"] = (
                "delete" if document_row.state_name == "deleting" else "index"
            )
            if action_name == "index":
                document_row.state_name = "indexing"
                document_row.indexed_chunk_count = 0
                document_row.chunk_count = 0
            document_row.lease_identifier = uuid.uuid4()
            document_row.worker_identifier = worker_identifier
            document_row.lease_expires_at = timestamp_value + datetime.timedelta(seconds=lease_seconds)
            document_row.updated_at = timestamp_value
            document_row.attempts += 1
            result_value: typing.Final = entities.DocumentLease(
                document_record=mapping.document_record(document_row),
                lease_identifier=document_row.lease_identifier,
                action_name=action_name,
            )
        return result_value

    async def renew_lease(self, *, document_lease: entities.DocumentLease, lease_seconds: int) -> bool:
        try:
            async with self._session_scope.transaction_session() as database_session:
                document_row: typing.Final = await self._leased_document(
                    database_session=database_session,
                    document_lease=document_lease,
                )
                document_row.lease_expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
                    seconds=lease_seconds
                )
        except errors.LeaseLostError:
            return False
        return True

    async def replace_chunks(
        self,
        *,
        document_lease: entities.DocumentLease,
        chunk_records: tuple[entities.ChunkRecord, ...],
        page_count: int | None,
    ) -> None:
        if document_lease.action_name != "index":
            raise errors.LeaseLostError(error_code="lease_lost", message_text="Indexing lease is no longer valid")
        if any(
            one_chunk_record.tenant_identifier != document_lease.document_record.tenant_identifier
            or one_chunk_record.document_identifier != document_lease.document_record.document_identifier
            for one_chunk_record in chunk_records
        ):
            raise errors.InvalidInputError(error_code="chunk_owner_mismatch", message_text="Chunk ownership mismatch")
        async with self._session_scope.transaction_session() as database_session:
            document_row: typing.Final = await self._leased_document(
                database_session=database_session, document_lease=document_lease
            )
            await database_session.execute(
                delete(database.ChunkRow).where(
                    database.ChunkRow.document_identifier == document_row.document_identifier,
                    database.ChunkRow.tenant_identifier == document_row.tenant_identifier,
                )
            )
            database_session.add_all(mapping.chunk_database_row(one_chunk_record) for one_chunk_record in chunk_records)
            document_row.chunk_count = len(chunk_records)
            document_row.indexed_chunk_count = 0
            document_row.page_count = page_count
            document_row.updated_at = datetime.datetime.now(datetime.UTC)

    async def update_indexing_progress(
        self, *, document_lease: entities.DocumentLease, indexed_chunk_count: int
    ) -> None:
        if document_lease.action_name != "index":
            raise errors.LeaseLostError(error_code="lease_lost", message_text="Indexing lease is no longer valid")
        async with self._session_scope.transaction_session() as database_session:
            document_row = await self._leased_document(database_session=database_session, document_lease=document_lease)
            if not 0 <= indexed_chunk_count <= document_row.chunk_count:
                raise errors.InvalidInputError(
                    error_code="invalid_indexing_progress",
                    message_text="Indexed count must fit the document chunk count",
                )
            document_row.indexed_chunk_count = max(document_row.indexed_chunk_count, indexed_chunk_count)
            document_row.updated_at = datetime.datetime.now(datetime.UTC)

    async def complete_indexing(self, *, document_lease: entities.DocumentLease) -> None:
        if document_lease.action_name != "index":
            raise errors.LeaseLostError(error_code="lease_lost", message_text="Indexing lease is no longer valid")
        async with self._session_scope.transaction_session() as database_session:
            document_row: typing.Final = await self._leased_document(
                database_session=database_session, document_lease=document_lease
            )
            if document_row.chunk_count == 0:
                raise errors.InvalidInputError(
                    error_code="no_chunks", message_text="An empty document cannot become ready"
                )
            document_row.state_name = "ready"
            document_row.indexed_chunk_count = document_row.chunk_count
            document_row.error_code = None
            document_row.error_message = None
            self._release_lease(document_row=document_row)
            document_row.updated_at = datetime.datetime.now(datetime.UTC)

    async def fail_indexing(
        self, *, document_lease: entities.DocumentLease, error_code: str, error_message: str
    ) -> None:
        if document_lease.action_name != "index":
            raise errors.LeaseLostError(error_code="lease_lost", message_text="Indexing lease is no longer valid")
        async with self._session_scope.transaction_session() as database_session:
            document_row: typing.Final = await self._leased_document(
                database_session=database_session, document_lease=document_lease
            )
            document_row.state_name = "failed"
            document_row.error_code = error_code
            document_row.error_message = error_message
            document_row.updated_at = datetime.datetime.now(datetime.UTC)
            self._release_lease(document_row=document_row)

    async def complete_deletion(self, *, document_lease: entities.DocumentLease) -> None:
        if document_lease.action_name != "delete":
            raise errors.LeaseLostError(error_code="lease_lost", message_text="Deletion lease is no longer valid")
        async with self._session_scope.transaction_session() as database_session:
            document_row: typing.Final = await self._leased_document(
                database_session=database_session, document_lease=document_lease
            )
            await database_session.execute(
                delete(database.ChunkRow).where(
                    database.ChunkRow.document_identifier == document_row.document_identifier,
                    database.ChunkRow.tenant_identifier == document_row.tenant_identifier,
                )
            )
            document_row.state_name = "deleted"
            document_row.chunk_count = 0
            document_row.indexed_chunk_count = 0
            document_row.error_code = None
            document_row.error_message = None
            document_row.updated_at = datetime.datetime.now(datetime.UTC)
            self._release_lease(document_row=document_row)

    async def fail_deletion(
        self, *, document_lease: entities.DocumentLease, error_code: str, error_message: str
    ) -> None:
        if document_lease.action_name != "delete":
            raise errors.LeaseLostError(error_code="lease_lost", message_text="Deletion lease is no longer valid")
        async with self._session_scope.transaction_session() as database_session:
            document_row: typing.Final = await self._leased_document(
                database_session=database_session, document_lease=document_lease
            )
            document_row.error_code = error_code
            document_row.error_message = error_message
            document_row.updated_at = datetime.datetime.now(datetime.UTC)
            document_row.next_attempt_at = document_row.updated_at + datetime.timedelta(
                seconds=min(300, 2 ** min(document_row.attempts, 9))
            )
            self._release_lease(document_row=document_row)

    async def _owned_document(
        self,
        *,
        database_session: AsyncSession,
        tenant_identifier: uuid.UUID,
        document_identifier: uuid.UUID,
        lock_required: bool = False,
    ) -> database.DocumentRow:
        select_statement = select(database.DocumentRow).where(
            database.DocumentRow.tenant_identifier == tenant_identifier,
            database.DocumentRow.document_identifier == document_identifier,
            database.DocumentRow.state_name != "deleted",
        )
        document_row: typing.Final = await database_session.scalar(
            select_statement.with_for_update() if lock_required else select_statement,
        )
        if document_row is None:
            raise errors.ResourceNotFoundError(error_code="document_not_found", message_text="Document not found")
        return document_row

    async def _leased_document(
        self,
        *,
        database_session: AsyncSession,
        document_lease: entities.DocumentLease,
    ) -> database.DocumentRow:
        document_row: typing.Final = await database_session.scalar(
            select(database.DocumentRow)
            .where(
                database.DocumentRow.document_identifier == document_lease.document_record.document_identifier,
                database.DocumentRow.tenant_identifier == document_lease.document_record.tenant_identifier,
                database.DocumentRow.lease_identifier == document_lease.lease_identifier,
                database.DocumentRow.lease_expires_at > datetime.datetime.now(datetime.UTC),
                database.DocumentRow.state_name
                == ("indexing" if document_lease.action_name == "index" else "deleting"),
            )
            .with_for_update()
        )
        if document_row is None:
            raise errors.LeaseLostError(error_code="lease_lost", message_text="Document lease is no longer valid")
        return document_row

    def _release_lease(self, *, document_row: database.DocumentRow) -> None:
        document_row.lease_identifier = None
        document_row.lease_expires_at = None
        document_row.worker_identifier = None
