"""Account-owned project spaces and a stable default for existing clients."""

import datetime
import typing
import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from study_rag.adapters import database
from study_rag.domain import entities, errors


def project_record(project_row: database.ProjectRow) -> entities.ProjectRecord:
    return entities.ProjectRecord(
        project_identifier=project_row.project_identifier,
        tenant_identifier=project_row.tenant_identifier,
        title_text=project_row.title_text,
        created_at=project_row.created_at,
        updated_at=project_row.updated_at,
    )


async def resolve_project(
    *, database_session: AsyncSession, tenant_identifier: uuid.UUID, project_identifier: uuid.UUID | None
) -> database.ProjectRow:
    if project_identifier is None:
        existing_project = await database_session.scalar(
            select(database.ProjectRow)
            .where(database.ProjectRow.tenant_identifier == tenant_identifier, database.ProjectRow.deleted_at.is_(None))
            .order_by(database.ProjectRow.created_at, database.ProjectRow.project_identifier)
            .limit(1)
            .with_for_update()
        )
        if existing_project is not None:
            return existing_project
        project_identifier = uuid.uuid5(uuid.NAMESPACE_URL, f"study-rag:default:{tenant_identifier}")
        timestamp_value = datetime.datetime.now(datetime.UTC)
        await database_session.execute(
            insert(database.ProjectRow)
            .values(
                project_identifier=project_identifier,
                tenant_identifier=tenant_identifier,
                title_text="Мои материалы",
                created_at=timestamp_value,
                updated_at=timestamp_value,
            )
            .on_conflict_do_nothing(index_elements=["project_identifier"])
        )
    project_row = await database_session.scalar(
        select(database.ProjectRow)
        .where(
            database.ProjectRow.project_identifier == project_identifier,
            database.ProjectRow.tenant_identifier == tenant_identifier,
            database.ProjectRow.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if project_row is None:
        raise errors.ResourceNotFoundError(error_code="project_not_found", message_text="Project not found")
    return project_row


@typing.final
class PostgreSQLProjectRepository:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_scope = database.PostgreSQLSessionScope(session_factory=session_factory)

    async def rename_project(
        self, *, tenant_identifier: uuid.UUID, project_identifier: uuid.UUID, title_text: str
    ) -> entities.ProjectRecord:
        async with self._session_scope.transaction_session() as database_session:
            project_row = await resolve_project(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                project_identifier=project_identifier,
            )
            project_row.title_text = title_text
            project_row.updated_at = datetime.datetime.now(datetime.UTC)
            await database_session.flush()
            return project_record(project_row)

    async def delete_project(self, *, tenant_identifier: uuid.UUID, project_identifier: uuid.UUID) -> None:
        timestamp_value = datetime.datetime.now(datetime.UTC)
        async with self._session_scope.transaction_session() as database_session:
            project_row = await resolve_project(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                project_identifier=project_identifier,
            )
            project_row.deleted_at = timestamp_value
            project_row.updated_at = timestamp_value
            chat_identifiers = select(database.ChatSessionRow.chat_identifier).where(
                database.ChatSessionRow.project_identifier == project_identifier,
                database.ChatSessionRow.tenant_identifier == tenant_identifier,
            )
            await database_session.execute(
                update(database.ChatSessionRow)
                .where(database.ChatSessionRow.chat_identifier.in_(chat_identifiers))
                .values(deleted_at=timestamp_value, updated_at=timestamp_value)
            )
            await database_session.execute(
                delete(database.ChatMessageRow).where(database.ChatMessageRow.chat_identifier.in_(chat_identifiers))
            )
            await database_session.execute(
                delete(database.ChatTurnRow).where(database.ChatTurnRow.chat_identifier.in_(chat_identifiers))
            )
            await database_session.execute(
                update(database.DocumentRow)
                .where(
                    database.DocumentRow.project_identifier == project_identifier,
                    database.DocumentRow.tenant_identifier == tenant_identifier,
                    database.DocumentRow.state_name != "deleted",
                )
                .values(
                    state_name="deleting",
                    lease_identifier=None,
                    lease_expires_at=None,
                    worker_identifier=None,
                    next_attempt_at=timestamp_value,
                    updated_at=timestamp_value,
                    error_code=None,
                    error_message=None,
                )
            )

    async def create_project(self, *, tenant_identifier: uuid.UUID, title_text: str) -> entities.ProjectRecord:
        timestamp_value = datetime.datetime.now(datetime.UTC)
        project_row = database.ProjectRow(
            project_identifier=uuid.uuid4(),
            tenant_identifier=tenant_identifier,
            title_text=title_text,
            created_at=timestamp_value,
            updated_at=timestamp_value,
        )
        async with self._session_scope.transaction_session() as database_session:
            database_session.add(project_row)
            await database_session.flush()
        return project_record(project_row)

    async def fetch_project(
        self, *, tenant_identifier: uuid.UUID, project_identifier: uuid.UUID
    ) -> entities.ProjectRecord:
        async with self._session_scope.reading_session() as database_session:
            return project_record(
                await resolve_project(
                    database_session=database_session,
                    tenant_identifier=tenant_identifier,
                    project_identifier=project_identifier,
                )
            )

    async def list_projects(
        self, *, tenant_identifier: uuid.UUID, limit_count: int, offset_value: int
    ) -> entities.PageResult[entities.ProjectRecord]:
        async with self._session_scope.transaction_session() as database_session:
            any_project = await database_session.scalar(
                select(database.ProjectRow.project_identifier)
                .where(database.ProjectRow.tenant_identifier == tenant_identifier)
                .limit(1)
            )
            if any_project is None:
                await resolve_project(
                    database_session=database_session, tenant_identifier=tenant_identifier, project_identifier=None
                )
            owned_filter = (
                database.ProjectRow.tenant_identifier == tenant_identifier
            ) & database.ProjectRow.deleted_at.is_(None)
            total_count = await database_session.scalar(
                select(func.count()).select_from(database.ProjectRow).where(owned_filter)
            )
            project_rows = await database_session.scalars(
                select(database.ProjectRow)
                .where(owned_filter)
                .order_by(database.ProjectRow.created_at, database.ProjectRow.project_identifier)
                .offset(offset_value)
                .limit(limit_count)
            )
            return entities.PageResult(
                items=tuple(project_record(project_row) for project_row in project_rows),
                total_count=total_count or 0,
                offset_value=offset_value,
                limit_count=limit_count,
            )
