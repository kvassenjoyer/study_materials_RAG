"""The durable queue resumes after a real database error boundary."""

import asyncio
import dataclasses
import datetime
import typing
import unittest.mock
import uuid

import pytest
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


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class IndexingScenario:
    ingestion_worker: IngestionWorker
    document_lease: entities.DocumentLease
    document_repository: unittest.mock.AsyncMock
    vector_store: unittest.mock.AsyncMock
    operations: unittest.mock.Mock


@pytest.fixture
def indexing_scenario() -> IndexingScenario:
    timestamp_value = datetime.datetime.now(datetime.UTC)
    document_record = entities.DocumentRecord(
        document_identifier=uuid.uuid4(),
        tenant_identifier=uuid.uuid4(),
        project_identifier=uuid.uuid4(),
        filename_text="book.pdf",
        media_type="application/pdf",
        storage_key="fixture",
        content_digest="fixture",
        byte_count=100,
        embedding_profile="fixture-profile",
        state_name=entities.DocumentState.INDEXING,
        chunk_count=0,
        page_count=None,
        error_code=None,
        error_message=None,
        created_at=timestamp_value,
        updated_at=timestamp_value,
    )
    chunk_records = tuple(
        entities.ChunkRecord(
            chunk_identifier=uuid.uuid4(),
            document_identifier=document_record.document_identifier,
            tenant_identifier=document_record.tenant_identifier,
            ordinal_value=ordinal_value,
            content_text="A page",
            token_count=2,
            page_start=ordinal_value + 1,
            page_end=ordinal_value + 1,
        )
        for ordinal_value in range(3)
    )
    document_repository = unittest.mock.AsyncMock(spec=ports.DocumentRepository)
    document_repository.renew_lease.return_value = True
    file_storage = unittest.mock.AsyncMock(spec=ports.FileStorage)
    file_storage.load_document.return_value = b"Book"
    document_parser = unittest.mock.AsyncMock(spec=ports.DocumentParser)
    document_parser.extract_document.return_value = entities.ExtractedDocument(text_sections=(), page_count=3)
    text_tokenizer = unittest.mock.Mock(spec=ports.TextTokenizer)
    text_tokenizer.split_sections.return_value = chunk_records
    embedding_provider = unittest.mock.AsyncMock(spec=ports.EmbeddingProvider)
    embedding_provider.embedding_profile = document_record.embedding_profile
    embedding_provider.embed_documents.side_effect = [((1.0,), (1.0,)), ((1.0,),)]
    vector_store = unittest.mock.AsyncMock(spec=ports.VectorStore)
    operations = unittest.mock.Mock()
    operations.attach_mock(vector_store.upsert_chunks, "store_vectors")
    operations.attach_mock(document_repository.update_indexing_progress, "save_progress")
    operations.attach_mock(document_repository.complete_indexing, "mark_ready")
    return IndexingScenario(
        ingestion_worker=IngestionWorker(
            document_repository=document_repository,
            file_storage=file_storage,
            document_parser=document_parser,
            text_tokenizer=text_tokenizer,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            embedding_batch_size=2,
        ),
        document_lease=entities.DocumentLease(
            document_record=document_record, lease_identifier=uuid.uuid4(), action_name="index"
        ),
        document_repository=document_repository,
        vector_store=vector_store,
        operations=operations,
    )


async def test_progress_is_saved_only_after_successful_vector_batches(indexing_scenario: IndexingScenario) -> None:
    await indexing_scenario.ingestion_worker.process_document(document_lease=indexing_scenario.document_lease)
    assert [operation[0] for operation in indexing_scenario.operations.mock_calls] == [
        "store_vectors",
        "save_progress",
        "store_vectors",
        "save_progress",
        "mark_ready",
    ]
    assert indexing_scenario.document_repository.update_indexing_progress.await_args_list == [
        unittest.mock.call(document_lease=indexing_scenario.document_lease, indexed_chunk_count=2),
        unittest.mock.call(document_lease=indexing_scenario.document_lease, indexed_chunk_count=3),
    ]


async def test_failed_vector_batch_does_not_advance_progress_or_publish_ready(
    indexing_scenario: IndexingScenario,
) -> None:
    indexing_scenario.vector_store.upsert_chunks.side_effect = [None, RuntimeError("Failed vector write")]
    await indexing_scenario.ingestion_worker.process_document(document_lease=indexing_scenario.document_lease)
    indexing_scenario.document_repository.update_indexing_progress.assert_awaited_once_with(
        document_lease=indexing_scenario.document_lease,
        indexed_chunk_count=2,
    )
    indexing_scenario.document_repository.complete_indexing.assert_not_awaited()
    assert (
        indexing_scenario.document_repository.fail_indexing.await_args.kwargs["error_code"]
        == "document_processing_failed"
    )
