import datetime
import typing
import uuid

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from study_rag.adapters import database
from study_rag.adapters.repositories import mapping
from study_rag.adapters.repositories.projects import resolve_project
from study_rag.domain import entities, errors


_AUTOMATIC_TITLE_CHARACTERS: typing.Final = 76


@typing.final
class PostgreSQLConversationRepository:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_scope = database.PostgreSQLSessionScope(session_factory=session_factory)

    async def create_chat(
        self,
        *,
        tenant_identifier: uuid.UUID,
        title_text: str | None = None,
        project_identifier: uuid.UUID | None = None,
    ) -> entities.ChatRecord:
        async with self._session_scope.transaction_session() as database_session:
            project_row = await resolve_project(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                project_identifier=project_identifier,
            )
        timestamp_value: typing.Final = datetime.datetime.now(datetime.UTC)
        session_row: typing.Final = database.ChatSessionRow(
            chat_identifier=uuid.uuid4(),
            tenant_identifier=tenant_identifier,
            project_identifier=project_row.project_identifier,
            title_text=title_text or "Новый чат",
            automatic_title=title_text is None,
            created_at=timestamp_value,
            updated_at=timestamp_value,
        )
        async with self._session_scope.transaction_session() as database_session:
            await resolve_project(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                project_identifier=session_row.project_identifier,
            )
            database_session.add(session_row)
            await database_session.flush()
        return mapping.chat_record(session_row)

    async def rename_chat(
        self, *, tenant_identifier: uuid.UUID, chat_identifier: uuid.UUID, title_text: str
    ) -> entities.ChatRecord:
        async with self._session_scope.transaction_session() as database_session:
            session_row = await self._owned_chat(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                chat_identifier=chat_identifier,
                lock_required=True,
            )
            session_row.title_text = title_text
            session_row.automatic_title = False
            session_row.updated_at = datetime.datetime.now(datetime.UTC)
            await database_session.flush()
            return mapping.chat_record(session_row)

    async def delete_chat(self, *, tenant_identifier: uuid.UUID, chat_identifier: uuid.UUID) -> None:
        async with self._session_scope.transaction_session() as database_session:
            session_row = await self._owned_chat(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                chat_identifier=chat_identifier,
                lock_required=True,
            )
            session_row.deleted_at = datetime.datetime.now(datetime.UTC)
            session_row.updated_at = session_row.deleted_at
            await database_session.execute(
                delete(database.ChatMessageRow).where(
                    database.ChatMessageRow.chat_identifier == chat_identifier,
                    database.ChatMessageRow.tenant_identifier == tenant_identifier,
                )
            )
            await database_session.execute(
                delete(database.ChatTurnRow).where(
                    database.ChatTurnRow.chat_identifier == chat_identifier,
                    database.ChatTurnRow.tenant_identifier == tenant_identifier,
                )
            )

    async def fetch_chat(self, *, tenant_identifier: uuid.UUID, chat_identifier: uuid.UUID) -> entities.ChatRecord:
        async with self._session_scope.reading_session() as database_session:
            session_row: typing.Final = await self._owned_chat(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                chat_identifier=chat_identifier,
            )
            return mapping.chat_record(session_row)

    async def list_chats(
        self,
        *,
        tenant_identifier: uuid.UUID,
        limit_count: int,
        offset_value: int,
        project_identifier: uuid.UUID | None = None,
    ) -> entities.PageResult[entities.ChatRecord]:
        async with self._session_scope.transaction_session() as database_session:
            project_row = await resolve_project(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                project_identifier=project_identifier,
            )
        async with self._session_scope.reading_session() as database_session:
            total_count: typing.Final = await database_session.scalar(
                select(func.count())
                .select_from(database.ChatSessionRow)
                .where(
                    database.ChatSessionRow.tenant_identifier == tenant_identifier,
                    database.ChatSessionRow.project_identifier == project_row.project_identifier,
                    database.ChatSessionRow.deleted_at.is_(None),
                ),
            )
            session_rows: typing.Final = await database_session.scalars(
                select(database.ChatSessionRow)
                .where(
                    database.ChatSessionRow.tenant_identifier == tenant_identifier,
                    database.ChatSessionRow.project_identifier == project_row.project_identifier,
                    database.ChatSessionRow.deleted_at.is_(None),
                )
                .order_by(database.ChatSessionRow.created_at, database.ChatSessionRow.chat_identifier)
                .offset(offset_value)
                .limit(limit_count),
            )
            return entities.PageResult(
                items=tuple(mapping.chat_record(one_session_row) for one_session_row in session_rows),
                total_count=total_count or 0,
                limit_count=limit_count,
                offset_value=offset_value,
            )

    async def list_messages(
        self,
        *,
        tenant_identifier: uuid.UUID,
        chat_identifier: uuid.UUID,
        limit_count: int,
        offset_value: int,
    ) -> entities.PageResult[entities.MessageRecord]:
        async with self._session_scope.reading_session() as database_session:
            await self._owned_chat(
                database_session=database_session, tenant_identifier=tenant_identifier, chat_identifier=chat_identifier
            )
            total_count: typing.Final = await database_session.scalar(
                select(func.count())
                .select_from(database.ChatMessageRow)
                .where(
                    database.ChatMessageRow.tenant_identifier == tenant_identifier,
                    database.ChatMessageRow.chat_identifier == chat_identifier,
                ),
            )
            result_rows: typing.Final = await database_session.execute(
                select(database.ChatMessageRow, database.ChatTurnRow.status_name, database.ChatTurnRow.error_code)
                .join(
                    database.ChatTurnRow,
                    database.ChatMessageRow.turn_identifier == database.ChatTurnRow.turn_identifier,
                )
                .where(
                    database.ChatMessageRow.tenant_identifier == tenant_identifier,
                    database.ChatMessageRow.chat_identifier == chat_identifier,
                )
                .order_by(database.ChatMessageRow.created_at, database.ChatMessageRow.message_identifier)
                .offset(offset_value)
                .limit(limit_count),
            )
            return entities.PageResult(
                items=tuple(
                    mapping.message_record(one_result_value[0], one_result_value[1], error_code=one_result_value[2])
                    for one_result_value in result_rows
                ),
                total_count=total_count or 0,
                limit_count=limit_count,
                offset_value=offset_value,
            )

    async def fetch_history(
        self,
        *,
        tenant_identifier: uuid.UUID,
        chat_identifier: uuid.UUID,
        message_limit: int,
    ) -> tuple[entities.MessageRecord, ...]:
        async with self._session_scope.reading_session() as database_session:
            await self._owned_chat(
                database_session=database_session, tenant_identifier=tenant_identifier, chat_identifier=chat_identifier
            )
            message_rows: typing.Final = await database_session.scalars(
                select(database.ChatMessageRow)
                .join(
                    database.ChatTurnRow,
                    database.ChatMessageRow.turn_identifier == database.ChatTurnRow.turn_identifier,
                )
                .where(
                    database.ChatMessageRow.tenant_identifier == tenant_identifier,
                    database.ChatMessageRow.chat_identifier == chat_identifier,
                    database.ChatTurnRow.status_name == "completed",
                )
                .order_by(database.ChatMessageRow.created_at.desc(), database.ChatMessageRow.message_identifier.desc())
                .limit(message_limit),
            )
            return tuple(
                reversed(
                    tuple(mapping.message_record(one_message_row, "completed") for one_message_row in message_rows)
                )
            )

    async def begin_turn(
        self,
        *,
        tenant_identifier: uuid.UUID,
        chat_identifier: uuid.UUID,
        client_request_identifier: uuid.UUID,
        question_text: str,
        lease_seconds: int,
        queue_request: bool = False,
    ) -> entities.TurnRecord:
        timestamp_value: typing.Final = datetime.datetime.now(datetime.UTC)
        async with self._session_scope.transaction_session() as database_session:
            session_row = await self._owned_chat(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                chat_identifier=chat_identifier,
                lock_required=True,
            )
            previous_turn: typing.Final = await database_session.scalar(
                select(database.ChatTurnRow).where(
                    database.ChatTurnRow.tenant_identifier == tenant_identifier,
                    database.ChatTurnRow.chat_identifier == chat_identifier,
                    database.ChatTurnRow.client_request_identifier == client_request_identifier,
                )
            )
            if previous_turn is not None:
                previous_record: typing.Final = await self._turn_record(
                    database_session=database_session, turn_row=previous_turn
                )
                if previous_record.user_message.content_text != question_text:
                    raise errors.ResourceConflictError(
                        error_code="request_question_mismatch",
                        message_text="Request identifier was used for another question",
                    )
                if previous_turn.status_name == "completed":
                    return previous_record
                if (
                    queue_request
                    and previous_turn.status_name in {"queued", "pending"}
                    and (
                        previous_turn.status_name == "queued"
                        or (
                            previous_turn.lease_expires_at is not None
                            and previous_turn.lease_expires_at > timestamp_value
                        )
                    )
                ):
                    return previous_record
            active_turns: typing.Final = await database_session.scalars(
                select(database.ChatTurnRow).where(
                    database.ChatTurnRow.tenant_identifier == tenant_identifier,
                    database.ChatTurnRow.chat_identifier == chat_identifier,
                    database.ChatTurnRow.status_name.in_(("queued", "pending")),
                )
            )
            for one_active_turn in active_turns:
                if one_active_turn.status_name == "queued" or (
                    one_active_turn.lease_expires_at is not None and one_active_turn.lease_expires_at > timestamp_value
                ):
                    raise errors.ResourceConflictError(
                        error_code="chat_busy", message_text="A response is already being generated"
                    )
                one_active_turn.status_name = "failed"
                one_active_turn.error_code = "turn_lease_expired"
                one_active_turn.lease_identifier = None
                one_active_turn.lease_expires_at = None
            if session_row.automatic_title:
                normalized_question = " ".join(question_text.split())
                session_row.title_text = (
                    normalized_question
                    if len(normalized_question) <= _AUTOMATIC_TITLE_CHARACTERS
                    else normalized_question[: _AUTOMATIC_TITLE_CHARACTERS - 1].rstrip() + "…"
                )
                session_row.automatic_title = False
            session_row.updated_at = timestamp_value
            turn_row: typing.Final = await self._reserve_request(
                database_session=database_session,
                previous_turn=previous_turn,
                tenant_identifier=tenant_identifier,
                chat_identifier=chat_identifier,
                client_request_identifier=client_request_identifier,
                question_text=question_text,
                lease_seconds=lease_seconds,
                timestamp_value=timestamp_value,
                queue_request=queue_request,
            )
            await database_session.flush()
            result_value: typing.Final = await self._turn_record(database_session=database_session, turn_row=turn_row)
        return result_value

    async def complete_turn(
        self,
        *,
        tenant_identifier: uuid.UUID,
        turn_record: entities.TurnRecord,
        generated_answer: entities.GeneratedAnswer,
        citation_records: tuple[entities.CitationRecord, ...],
    ) -> entities.AnswerRecord:
        timestamp_value: typing.Final = datetime.datetime.now(datetime.UTC)
        async with self._session_scope.transaction_session() as database_session:
            session_row: typing.Final = await self._owned_chat(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                chat_identifier=turn_record.chat_identifier,
                lock_required=True,
            )
            turn_row: typing.Final = await self._leased_turn(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                turn_record=turn_record,
            )
            assistant_row: typing.Final = database.ChatMessageRow(
                message_identifier=uuid.uuid4(),
                chat_identifier=turn_row.chat_identifier,
                tenant_identifier=tenant_identifier,
                turn_identifier=turn_row.turn_identifier,
                role_name="assistant",
                content_text=generated_answer.answer_text,
                sufficient_context=generated_answer.sufficient_context,
                response_duration_ms=generated_answer.response_duration_ms,
                citation_payload=[
                    mapping.citation_payload(one_citation_value) for one_citation_value in citation_records
                ],
                created_at=timestamp_value,
            )
            database_session.add(assistant_row)
            turn_row.status_name = "completed"
            turn_row.lease_identifier = None
            turn_row.lease_expires_at = None
            session_row.updated_at = timestamp_value
            await database_session.flush()
            completed_turn: typing.Final = await self._turn_record(database_session=database_session, turn_row=turn_row)
            if completed_turn.assistant_message is None:
                raise RuntimeError("Completed turn is missing its assistant message")
            result_value: typing.Final = entities.AnswerRecord(
                turn_identifier=turn_row.turn_identifier,
                chat_identifier=turn_row.chat_identifier,
                user_message=completed_turn.user_message,
                assistant_message=completed_turn.assistant_message,
            )
        return result_value

    async def fail_turn(
        self,
        *,
        tenant_identifier: uuid.UUID,
        turn_record: entities.TurnRecord,
        error_code: str,
    ) -> None:
        async with self._session_scope.transaction_session() as database_session:
            session_row = await database_session.scalar(
                select(database.ChatSessionRow)
                .where(
                    database.ChatSessionRow.tenant_identifier == tenant_identifier,
                    database.ChatSessionRow.chat_identifier == turn_record.chat_identifier,
                )
                .with_for_update()
            )
            if session_row is None or session_row.deleted_at is not None:
                return
            turn_row: typing.Final = await database_session.scalar(
                select(database.ChatTurnRow)
                .where(
                    database.ChatTurnRow.tenant_identifier == tenant_identifier,
                    database.ChatTurnRow.chat_identifier == turn_record.chat_identifier,
                    database.ChatTurnRow.turn_identifier == turn_record.turn_identifier,
                    database.ChatTurnRow.status_name == "pending",
                    database.ChatTurnRow.lease_identifier == turn_record.lease_identifier,
                )
                .with_for_update()
            )
            if turn_row is None or turn_record.lease_identifier is None:
                return
            turn_row.status_name = "failed"
            turn_row.lease_identifier = None
            turn_row.lease_expires_at = None
            turn_row.error_code = error_code

    async def claim_queued_turn(self, *, lease_seconds: int) -> entities.ClaimedAnswer | None:
        timestamp_value = datetime.datetime.now(datetime.UTC)
        async with self._session_scope.transaction_session() as database_session:
            # Serialize claiming across workers; the committed pending lease is the global slot.
            await database_session.execute(text("SELECT pg_advisory_xact_lock(741935)"))
            await database_session.execute(
                update(database.ChatTurnRow)
                .where(
                    database.ChatTurnRow.status_name == "pending",
                    database.ChatTurnRow.lease_expires_at <= timestamp_value,
                )
                .values(
                    status_name="failed", error_code="turn_lease_expired", lease_identifier=None, lease_expires_at=None
                )
            )
            occupied_slot = await database_session.scalar(
                select(database.ChatTurnRow.turn_identifier)
                .where(database.ChatTurnRow.status_name == "pending")
                .limit(1)
            )
            if occupied_slot is not None:
                return None
            turn_row = await database_session.scalar(
                select(database.ChatTurnRow)
                .join(
                    database.ChatSessionRow,
                    database.ChatSessionRow.chat_identifier == database.ChatTurnRow.chat_identifier,
                )
                .join(
                    database.ProjectRow,
                    database.ProjectRow.project_identifier == database.ChatSessionRow.project_identifier,
                )
                .where(
                    database.ChatTurnRow.status_name == "queued",
                    database.ChatSessionRow.deleted_at.is_(None),
                    database.ProjectRow.deleted_at.is_(None),
                )
                .order_by(database.ChatTurnRow.queued_at, database.ChatTurnRow.turn_identifier)
                .limit(1)
                .with_for_update(of=database.ChatTurnRow, skip_locked=True)
            )
            if turn_row is None:
                return None
            account_row = await database_session.get(database.UserAccountRow, turn_row.tenant_identifier)
            if account_row is None:
                raise RuntimeError("Queued answer has no owner")
            turn_row.status_name = "pending"
            turn_row.lease_identifier = uuid.uuid4()
            turn_row.lease_expires_at = timestamp_value + datetime.timedelta(seconds=lease_seconds)
            await database_session.flush()
            return entities.ClaimedAnswer(
                tenant_identity=entities.TenantIdentity(
                    user_identifier=account_row.user_identifier, login_name=account_row.login_name
                ),
                turn_record=await self._turn_record(database_session=database_session, turn_row=turn_row),
            )

    async def fetch_turn(self, *, tenant_identifier: uuid.UUID, turn_identifier: uuid.UUID) -> entities.TurnRecord:
        async with self._session_scope.reading_session() as database_session:
            turn_row = await database_session.scalar(
                select(database.ChatTurnRow).where(
                    database.ChatTurnRow.tenant_identifier == tenant_identifier,
                    database.ChatTurnRow.turn_identifier == turn_identifier,
                )
            )
            if turn_row is None:
                raise errors.ResourceNotFoundError(error_code="turn_not_found", message_text="Answer request not found")
            await self._owned_chat(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                chat_identifier=turn_row.chat_identifier,
            )
            return await self._turn_record(database_session=database_session, turn_row=turn_row)

    async def list_active_turns(self, *, tenant_identifier: uuid.UUID) -> tuple[entities.AnswerJobRecord, ...]:
        async with self._session_scope.reading_session() as database_session:
            result_rows = await database_session.execute(
                select(database.ChatTurnRow, database.ChatSessionRow.project_identifier)
                .join(
                    database.ChatSessionRow,
                    database.ChatSessionRow.chat_identifier == database.ChatTurnRow.chat_identifier,
                )
                .where(
                    database.ChatTurnRow.tenant_identifier == tenant_identifier,
                    database.ChatTurnRow.status_name.in_(("queued", "pending")),
                    database.ChatSessionRow.deleted_at.is_(None),
                )
                .order_by(database.ChatTurnRow.queued_at, database.ChatTurnRow.turn_identifier)
            )
            return tuple(
                entities.AnswerJobRecord(
                    turn_identifier=turn_row.turn_identifier,
                    chat_identifier=turn_row.chat_identifier,
                    project_identifier=project_identifier,
                    status_name=mapping.turn_status(turn_row.status_name),
                )
                for turn_row, project_identifier in result_rows
            )

    async def stop_turn(self, *, tenant_identifier: uuid.UUID, turn_identifier: uuid.UUID) -> entities.TurnRecord:
        saved_turn = await self.fetch_turn(tenant_identifier=tenant_identifier, turn_identifier=turn_identifier)
        async with self._session_scope.transaction_session() as database_session:
            await self._owned_chat(
                database_session=database_session,
                tenant_identifier=tenant_identifier,
                chat_identifier=saved_turn.chat_identifier,
                lock_required=True,
            )
            turn_row = await database_session.scalar(
                select(database.ChatTurnRow)
                .where(
                    database.ChatTurnRow.tenant_identifier == tenant_identifier,
                    database.ChatTurnRow.turn_identifier == turn_identifier,
                )
                .with_for_update()
            )
            if turn_row is None:
                raise errors.ResourceNotFoundError(error_code="turn_not_found", message_text="Answer request not found")
            if turn_row.status_name in {"queued", "pending"}:
                turn_row.status_name = "failed"
                turn_row.error_code = "answer_stopped"
                turn_row.lease_identifier = None
                turn_row.lease_expires_at = None
            await database_session.flush()
            return await self._turn_record(database_session=database_session, turn_row=turn_row)

    async def _reserve_request(
        self,
        *,
        database_session: AsyncSession,
        previous_turn: database.ChatTurnRow | None,
        tenant_identifier: uuid.UUID,
        chat_identifier: uuid.UUID,
        client_request_identifier: uuid.UUID,
        question_text: str,
        lease_seconds: int,
        timestamp_value: datetime.datetime,
        queue_request: bool = False,
    ) -> database.ChatTurnRow:
        if previous_turn is not None:
            previous_turn.status_name = "queued" if queue_request else "pending"
            previous_turn.error_code = None
            previous_turn.queued_at = timestamp_value
            previous_turn.lease_identifier = None if queue_request else uuid.uuid4()
            previous_turn.lease_expires_at = (
                None if queue_request else timestamp_value + datetime.timedelta(seconds=lease_seconds)
            )
            return previous_turn
        turn_row: typing.Final = database.ChatTurnRow(
            turn_identifier=uuid.uuid4(),
            chat_identifier=chat_identifier,
            tenant_identifier=tenant_identifier,
            client_request_identifier=client_request_identifier,
            status_name="queued" if queue_request else "pending",
            queued_at=timestamp_value,
            lease_identifier=None if queue_request else uuid.uuid4(),
            lease_expires_at=None if queue_request else timestamp_value + datetime.timedelta(seconds=lease_seconds),
            created_at=timestamp_value,
        )
        database_session.add(turn_row)
        await database_session.flush()
        database_session.add(
            database.ChatMessageRow(
                message_identifier=uuid.uuid4(),
                chat_identifier=chat_identifier,
                tenant_identifier=tenant_identifier,
                turn_identifier=turn_row.turn_identifier,
                role_name="user",
                content_text=question_text,
                citation_payload=[],
                sufficient_context=None,
                created_at=timestamp_value,
            )
        )
        return turn_row

    async def _owned_chat(
        self,
        *,
        database_session: AsyncSession,
        tenant_identifier: uuid.UUID,
        chat_identifier: uuid.UUID,
        lock_required: bool = False,
    ) -> database.ChatSessionRow:
        select_statement = select(database.ChatSessionRow).where(
            database.ChatSessionRow.tenant_identifier == tenant_identifier,
            database.ChatSessionRow.chat_identifier == chat_identifier,
            database.ChatSessionRow.deleted_at.is_(None),
        )
        session_row: typing.Final = await database_session.scalar(
            select_statement.with_for_update() if lock_required else select_statement,
        )
        if session_row is None:
            raise errors.ResourceNotFoundError(error_code="chat_not_found", message_text="Chat not found")
        return session_row

    async def _leased_turn(
        self,
        *,
        database_session: AsyncSession,
        tenant_identifier: uuid.UUID,
        turn_record: entities.TurnRecord,
    ) -> database.ChatTurnRow:
        if turn_record.lease_identifier is None:
            raise errors.LeaseLostError(error_code="lease_lost", message_text="Turn lease is no longer valid")
        turn_row: typing.Final = await database_session.scalar(
            select(database.ChatTurnRow)
            .where(
                database.ChatTurnRow.tenant_identifier == tenant_identifier,
                database.ChatTurnRow.chat_identifier == turn_record.chat_identifier,
                database.ChatTurnRow.turn_identifier == turn_record.turn_identifier,
                database.ChatTurnRow.status_name == "pending",
                database.ChatTurnRow.lease_identifier == turn_record.lease_identifier,
                database.ChatTurnRow.lease_expires_at > datetime.datetime.now(datetime.UTC),
            )
            .with_for_update()
        )
        if turn_row is None:
            raise errors.LeaseLostError(error_code="lease_lost", message_text="Turn lease is no longer valid")
        return turn_row

    async def _turn_record(
        self, *, database_session: AsyncSession, turn_row: database.ChatTurnRow
    ) -> entities.TurnRecord:
        message_rows: typing.Final = await database_session.scalars(
            select(database.ChatMessageRow).where(
                database.ChatMessageRow.turn_identifier == turn_row.turn_identifier,
                database.ChatMessageRow.chat_identifier == turn_row.chat_identifier,
                database.ChatMessageRow.tenant_identifier == turn_row.tenant_identifier,
            )
        )
        message_records: typing.Final = {
            one_message_row.role_name: mapping.message_record(
                one_message_row, turn_row.status_name, error_code=turn_row.error_code
            )
            for one_message_row in message_rows
        }
        user_message: typing.Final = message_records.get("user")
        assistant_message: typing.Final = message_records.get("assistant")
        if user_message is None:
            raise RuntimeError("Turn is missing its user message")
        return entities.TurnRecord(
            turn_identifier=turn_row.turn_identifier,
            chat_identifier=turn_row.chat_identifier,
            client_request_identifier=turn_row.client_request_identifier,
            status_name=mapping.turn_status(turn_row.status_name),
            lease_identifier=turn_row.lease_identifier,
            user_message=user_message,
            assistant_message=assistant_message,
        )
