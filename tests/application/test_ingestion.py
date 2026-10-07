"""The durable queue resumes after a real database error boundary."""

import asyncio
import typing
import unittest.mock
import uuid

from sqlalchemy import exc as sqlalchemy_errors

from study_rag.application.ingestion import IngestionWorker
from study_rag.domain import entities, ports


async def test_worker_recovers_after_unexpected_queue_failure() -> None:
    document_repository: typing.Final = unittest.mock.AsyncMock(spec=ports.DocumentRepository)
    ingestion_worker: typing.Final = IngestionWorker(
        document_repository=document_repository,
        file_storage=unittest.mock.AsyncMock(spec=ports.FileStorage),
        document_parser=unittest.mock.AsyncMock(spec=ports.DocumentParser),
        text_tokenizer=unittest.mock.Mock(spec=ports.TextTokenizer),
        embedding_provider=unittest.mock.Mock(spec=ports.EmbeddingProvider),
        vector_store=unittest.mock.AsyncMock(spec=ports.VectorStore),
        poll_seconds=0.001,
    )
    request_count = 0

    async def claim_after_failure(*, worker_identifier: uuid.UUID, lease_seconds: int) -> entities.DocumentLease | None:
        nonlocal request_count
        assert worker_identifier == ingestion_worker.worker_identifier
        assert lease_seconds == ingestion_worker.lease_seconds
        request_count += 1
        if request_count == 1:
            raise sqlalchemy_errors.OperationalError("fixture", None, RuntimeError("Disconnected fixture database"))
        ingestion_worker.stop_worker()
        return None

    document_repository.claim_document.side_effect = claim_after_failure

    async with asyncio.timeout(1):
        await ingestion_worker.run_worker()

    expected_request_count: typing.Final = 2
    assert request_count == expected_request_count
