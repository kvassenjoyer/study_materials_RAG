"""Upload registration and tenant-scoped document operations."""

import dataclasses
import logging
import pathlib
import typing
import uuid

from study_rag.domain import entities, errors, ports


application_logger: typing.Final = logging.getLogger(__name__)
maximum_filename_length: typing.Final = 255
supported_formats: typing.Final = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".pdf": "application/pdf",
}


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class DocumentService:
    document_repository: ports.DocumentRepository
    file_storage: ports.FileStorage
    embedding_provider: ports.EmbeddingProvider
    maximum_upload_bytes: int

    async def upload_document(
        self, *, tenant_identity: entities.TenantIdentity, filename_text: str, content_bytes: bytes
    ) -> entities.DocumentRecord:
        normalized_filename: typing.Final = self._normalize_filename(filename_text=filename_text)
        document_media: typing.Final = self._resolve_media_type(filename_text=normalized_filename)
        self._validate_content(content_bytes=content_bytes)
        document_identifier: typing.Final = uuid.uuid4()
        stored_upload: typing.Final = await self.file_storage.save_upload(
            tenant_identifier=tenant_identity.user_identifier,
            document_identifier=document_identifier,
            content_bytes=content_bytes,
            filename_text=normalized_filename,
        )
        registration_data: typing.Final = entities.DocumentRegistration(
            document_identifier=document_identifier,
            filename_text=normalized_filename,
            media_type=document_media,
            storage_key=stored_upload.storage_key,
            content_digest=stored_upload.content_digest,
            byte_count=stored_upload.byte_count,
            embedding_profile=self.embedding_provider.embedding_profile,
        )
        try:
            document_record: typing.Final = await self.document_repository.register_document(
                tenant_identifier=tenant_identity.user_identifier, document_registration=registration_data
            )
        except Exception:
            await self._cleanup_unregistered_upload(
                tenant_identity=tenant_identity,
                document_identifier=document_identifier,
                storage_key=stored_upload.storage_key,
            )
            raise
        if document_record.document_identifier != document_identifier:
            await self.file_storage.delete_document(storage_key=stored_upload.storage_key)
        return document_record

    async def fetch_document(
        self, *, tenant_identity: entities.TenantIdentity, document_identifier: uuid.UUID
    ) -> entities.DocumentRecord:
        return await self.document_repository.fetch_document(
            tenant_identifier=tenant_identity.user_identifier, document_identifier=document_identifier
        )

    async def list_documents(
        self, *, tenant_identity: entities.TenantIdentity, limit_count: int, offset_value: int
    ) -> entities.PageResult[entities.DocumentRecord]:
        return await self.document_repository.list_documents(
            tenant_identifier=tenant_identity.user_identifier, limit_count=limit_count, offset_value=offset_value
        )

    async def fetch_chunk(
        self, *, tenant_identity: entities.TenantIdentity, chunk_identifier: uuid.UUID
    ) -> entities.RetrievedChunk:
        return await self.document_repository.fetch_chunk(
            tenant_identifier=tenant_identity.user_identifier, chunk_identifier=chunk_identifier
        )

    async def delete_document(
        self, *, tenant_identity: entities.TenantIdentity, document_identifier: uuid.UUID
    ) -> entities.DocumentRecord:
        return await self.document_repository.request_deletion(
            tenant_identifier=tenant_identity.user_identifier, document_identifier=document_identifier
        )

    async def retry_document(
        self, *, tenant_identity: entities.TenantIdentity, document_identifier: uuid.UUID
    ) -> entities.DocumentRecord:
        return await self.document_repository.retry_indexing(
            tenant_identifier=tenant_identity.user_identifier, document_identifier=document_identifier
        )

    def _normalize_filename(self, *, filename_text: str) -> str:
        normalized_filename: typing.Final = filename_text.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if (
            not normalized_filename
            or len(normalized_filename) > maximum_filename_length
            or not normalized_filename.isprintable()
        ):
            raise errors.InvalidInputError(error_code="invalid_filename", message_text="A valid filename is required")
        return normalized_filename

    def _resolve_media_type(self, *, filename_text: str) -> str:
        file_extension: typing.Final = pathlib.PurePath(filename_text).suffix.lower()
        document_media: typing.Final = supported_formats.get(file_extension)
        if document_media is None:
            raise errors.InvalidInputError(
                error_code="unsupported_document_format",
                message_text="Supported formats are TXT, Markdown and text PDF",
            )
        return document_media

    def _validate_content(self, *, content_bytes: bytes) -> None:
        if not content_bytes:
            raise errors.InvalidInputError(error_code="empty_document", message_text="The uploaded document is empty")
        if len(content_bytes) > self.maximum_upload_bytes:
            raise errors.InvalidInputError(
                error_code="document_too_large", message_text="Document exceeds the upload limit"
            )

    async def _cleanup_unregistered_upload(
        self, *, tenant_identity: entities.TenantIdentity, document_identifier: uuid.UUID, storage_key: str
    ) -> None:
        try:
            await self.document_repository.fetch_document(
                tenant_identifier=tenant_identity.user_identifier, document_identifier=document_identifier
            )
        except errors.ResourceNotFoundError:
            await self.file_storage.delete_document(storage_key=storage_key)
        except errors.DependencyUnavailableError:
            # A timed-out commit is ambiguous; retain the file instead of breaking a committed document.
            application_logger.warning("Upload cleanup deferred for document %s", document_identifier)
