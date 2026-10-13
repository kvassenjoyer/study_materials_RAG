"""Durable document processing with a cancellable lease heartbeat."""

import asyncio
import dataclasses
import logging
import typing
import uuid

from study_rag.domain import entities, errors, ports


application_logger: typing.Final = logging.getLogger(__name__)


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class IngestionWorker:
    document_repository: ports.DocumentRepository
    file_storage: ports.FileStorage
    document_parser: ports.DocumentParser
    text_tokenizer: ports.TextTokenizer
    embedding_provider: ports.EmbeddingProvider
    vector_store: ports.VectorStore
    poll_seconds: float = 1.0
    lease_seconds: int = 120
    heartbeat_seconds: int = 30
    embedding_batch_size: int = 16
    worker_identifier: uuid.UUID = dataclasses.field(default_factory=uuid.uuid4)
    _stop_event: asyncio.Event = dataclasses.field(default_factory=asyncio.Event, repr=False)

    async def run_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                document_lease = await self.document_repository.claim_document(
                    worker_identifier=self.worker_identifier, lease_seconds=self.lease_seconds
                )
                if document_lease is not None:
                    await self.process_document(document_lease=document_lease)
                    continue
            except errors.DependencyUnavailableError:
                application_logger.exception("Document queue dependency is unavailable")
            except Exception:
                # A disconnected repository must not permanently kill the durable queue runner.
                application_logger.exception("Unexpected document queue failure; polling will resume")
            await self._wait_for_work()

    async def process_document(self, *, document_lease: entities.DocumentLease) -> None:
        processing_task: typing.Final = asyncio.create_task(self._execute_document(document_lease=document_lease))
        heartbeat_task: typing.Final = asyncio.create_task(self._maintain_lease(document_lease=document_lease))
        try:
            completed_tasks: typing.Final = await asyncio.wait(
                (processing_task, heartbeat_task), return_when=asyncio.FIRST_COMPLETED
            )
            if processing_task in completed_tasks[0]:
                await processing_task
            else:
                await heartbeat_task
        except errors.LeaseLostError:
            application_logger.info("Document lease revoked: %s", document_lease.document_record.document_identifier)
        except asyncio.CancelledError:
            raise
        except errors.DomainError as processing_error:
            await self._save_processing_failure(document_lease=document_lease, processing_error=processing_error)
        except Exception as processing_error:
            application_logger.exception(
                "Unexpected document processing failure: %s", document_lease.document_record.document_identifier
            )
            safe_failure: typing.Final = errors.DependencyUnavailableError(
                error_code="document_processing_failed", message_text="Document processing failed; retry is available"
            )
            safe_failure.__cause__ = processing_error
            await self._save_processing_failure(document_lease=document_lease, processing_error=safe_failure)
        finally:
            processing_task.cancel()
            heartbeat_task.cancel()
            await asyncio.gather(processing_task, heartbeat_task, return_exceptions=True)

    def stop_worker(self) -> None:
        self._stop_event.set()

    async def _execute_document(self, *, document_lease: entities.DocumentLease) -> None:
        if document_lease.action_name == "delete":
            await self._delete_document(document_lease=document_lease)
            return
        await self._index_document(document_lease=document_lease)

    async def _index_document(self, *, document_lease: entities.DocumentLease) -> None:
        document_record: typing.Final = document_lease.document_record
        if document_record.embedding_profile != self.embedding_provider.embedding_profile:
            raise errors.InvalidInputError(
                error_code="embedding_profile_changed",
                message_text="Upload the document again under the current profile",
            )
        content_bytes: typing.Final = await self.file_storage.load_document(storage_key=document_record.storage_key)
        extracted_document: typing.Final = await self.document_parser.extract_document(
            content_bytes=content_bytes, media_type=document_record.media_type
        )
        chunk_records: typing.Final = await self.text_tokenizer.split_sections(
            tenant_identifier=document_record.tenant_identifier,
            document_identifier=document_record.document_identifier,
            embedding_profile=document_record.embedding_profile,
            extracted_document=extracted_document,
            filename_text=document_record.filename_text,
        )
        if not chunk_records:
            raise errors.InvalidInputError(
                error_code="no_extractable_text", message_text="Document contains no usable text"
            )
        await self._ensure_lease(document_lease=document_lease)
        await self.document_repository.replace_chunks(
            document_lease=document_lease, chunk_records=chunk_records, page_count=extracted_document.page_count
        )
        await self.vector_store.delete_document(
            tenant_identifier=document_record.tenant_identifier,
            document_identifier=document_record.document_identifier,
            embedding_profile=document_record.embedding_profile,
        )
        for batch_offset in range(0, len(chunk_records), self.embedding_batch_size):
            batch_records = chunk_records[batch_offset : batch_offset + self.embedding_batch_size]
            batch_vectors = await self.embedding_provider.embed_documents(
                chunk_records=batch_records, filename_text=document_record.filename_text
            )
            if len(batch_vectors) != len(batch_records):
                raise errors.DependencyUnavailableError(
                    error_code="invalid_embedding_response", message_text="Embedding count does not match chunk count"
                )
            embedded_chunks = tuple(
                entities.EmbeddedChunk(chunk_record=chunk_record, embedding_vector=embedding_vector)
                for chunk_record, embedding_vector in zip(batch_records, batch_vectors, strict=True)
            )
            await self._ensure_lease(document_lease=document_lease)
            await self.vector_store.upsert_chunks(
                tenant_identifier=document_record.tenant_identifier,
                embedding_profile=document_record.embedding_profile,
                embedded_chunks=embedded_chunks,
            )
            await self.document_repository.update_indexing_progress(
                document_lease=document_lease, indexed_chunk_count=batch_offset + len(batch_records)
            )
        await self._ensure_lease(document_lease=document_lease)
        await self.document_repository.complete_indexing(document_lease=document_lease)

    async def _delete_document(self, *, document_lease: entities.DocumentLease) -> None:
        document_record: typing.Final = document_lease.document_record
        await self._ensure_lease(document_lease=document_lease)
        await self.vector_store.delete_document(
            tenant_identifier=document_record.tenant_identifier,
            document_identifier=document_record.document_identifier,
            embedding_profile=document_record.embedding_profile,
        )
        await self.file_storage.delete_document(storage_key=document_record.storage_key)
        await self.document_repository.complete_deletion(document_lease=document_lease)

    async def _maintain_lease(self, *, document_lease: entities.DocumentLease) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            await self._ensure_lease(document_lease=document_lease)

    async def _ensure_lease(self, *, document_lease: entities.DocumentLease) -> None:
        lease_current: typing.Final = await self.document_repository.renew_lease(
            document_lease=document_lease, lease_seconds=self.lease_seconds
        )
        if not lease_current:
            raise errors.LeaseLostError(
                error_code="document_lease_lost", message_text="Document lease is no longer current"
            )

    async def _save_processing_failure(
        self, *, document_lease: entities.DocumentLease, processing_error: errors.DomainError
    ) -> None:
        application_logger.warning(
            "Document %s failed at %s", document_lease.document_record.document_identifier, processing_error.error_code
        )
        try:
            if document_lease.action_name == "delete":
                await self.document_repository.fail_deletion(
                    document_lease=document_lease,
                    error_code=processing_error.error_code,
                    error_message=processing_error.message_text,
                )
            else:
                await self.document_repository.fail_indexing(
                    document_lease=document_lease,
                    error_code=processing_error.error_code,
                    error_message=processing_error.message_text,
                )
        except errors.LeaseLostError:
            application_logger.info("Failure belongs to an expired document lease")
        except Exception:
            application_logger.exception("Could not persist processing failure; expired lease will be recovered")

    async def _wait_for_work(self) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_seconds)
        except TimeoutError:
            return
