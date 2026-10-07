"""Alembic supports a supplied isolated connection or an explicitly configured database."""

import asyncio
import logging.config
import typing

from alembic import context
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from study_rag.adapters.database import DatabaseBase


@typing.final
class MigrationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STUDY_RAG_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://study_rag@127.0.0.1:55432/study_rag"


configuration: typing.Final = context.config
if configuration.config_file_name is not None:
    logging.config.fileConfig(configuration.config_file_name)


def run_connection_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=DatabaseBase.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    migration_settings: typing.Final = MigrationSettings()
    database_engine: typing.Final = create_async_engine(migration_settings.database_url, poolclass=pool.NullPool)
    try:
        async with database_engine.connect() as connection:
            await connection.run_sync(run_connection_migrations)
    finally:
        await database_engine.dispose()


if context.is_offline_mode():
    migration_settings: typing.Final = MigrationSettings()
    context.configure(
        url=migration_settings.database_url,
        target_metadata=DatabaseBase.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied_connection: object = configuration.attributes.get("connection")
    if isinstance(supplied_connection, Connection):
        run_connection_migrations(supplied_connection)
    else:
        asyncio.run(run_async_migrations())
