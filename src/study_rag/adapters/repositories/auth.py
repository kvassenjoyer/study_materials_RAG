import datetime
import typing
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from study_rag.adapters.database import PostgreSQLSessionScope, UserAccountRow
from study_rag.adapters.repositories.mapping import account_record
from study_rag.domain.entities import UserAccount
from study_rag.domain.errors import ResourceConflictError


@typing.final
class PostgreSQLAuthRepository:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_scope = PostgreSQLSessionScope(session_factory=session_factory)

    async def create_account(self, *, login_name: str, password_hash: str) -> UserAccount:
        account_row: typing.Final = UserAccountRow(
            user_identifier=uuid.uuid4(),
            login_name=login_name,
            password_hash=password_hash,
            created_at=datetime.datetime.now(datetime.UTC),
        )
        try:
            async with self._session_scope.transaction_session() as database_session:
                database_session.add(account_row)
                await database_session.flush()
        except IntegrityError as database_error:
            # A collision must be a duplicate login; other integrity errors retain their cause.
            if await self.fetch_by_login(login_name=login_name) is None:
                raise
            raise ResourceConflictError(
                error_code="login_already_registered", message_text="Login is already registered"
            ) from database_error
        return account_record(account_row)

    async def fetch_by_login(self, *, login_name: str) -> UserAccount | None:
        async with self._session_scope.reading_session() as database_session:
            account_row: typing.Final = await database_session.scalar(
                select(UserAccountRow).where(UserAccountRow.login_name == login_name),
            )
        return None if account_row is None else account_record(account_row)

    async def fetch_by_identifier(self, *, user_identifier: uuid.UUID) -> UserAccount | None:
        async with self._session_scope.reading_session() as database_session:
            account_row: typing.Final = await database_session.get(UserAccountRow, user_identifier)
        return None if account_row is None else account_record(account_row)
