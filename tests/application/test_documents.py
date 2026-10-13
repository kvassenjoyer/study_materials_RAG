import dataclasses
import datetime
import typing
import unittest.mock
import uuid

import pytest

from study_rag.application.documents import DocumentService
from study_rag.domain import entities, errors, ports


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class OriginalDocumentHarness:
    document_service: DocumentService
    document_repository: unittest.mock.AsyncMock
    file_storage: unittest.mock.AsyncMock
    document_record: entities.DocumentRecord
    tenant_identity: entities.TenantIdentity


@pytest.fixture
def original_document_harness() -> OriginalDocumentHarness:
    timestamp_value = datetime.datetime.now(datetime.UTC)
    document_record = entities.DocumentRecord(
        document_identifier=uuid.uuid4(),
        tenant_identifier=uuid.uuid4(),
        project_identifier=uuid.uuid4(),
        filename_text="Учебник.pdf",
        media_type="application/pdf",
        storage_key="private-storage-key",
        content_digest="fixture",
        byte_count=100,
        embedding_profile="fixture",
        state_name=entities.DocumentState.READY,
        chunk_count=2,
        page_count=2,
        error_code=None,
        error_message=None,
        created_at=timestamp_value,
        updated_at=timestamp_value,
    )
    document_repository = unittest.mock.AsyncMock(spec=ports.DocumentRepository)
    document_repository.fetch_document.return_value = document_record
    file_storage = unittest.mock.AsyncMock(spec=ports.FileStorage)
    file_storage.load_document.return_value = b"%PDF-original"
    return OriginalDocumentHarness(
        document_service=DocumentService(
            document_repository=document_repository,
            file_storage=file_storage,
            embedding_provider=unittest.mock.Mock(spec=ports.EmbeddingProvider),
            maximum_upload_bytes=1000,
        ),
        document_repository=document_repository,
        file_storage=file_storage,
        document_record=document_record,
        tenant_identity=entities.TenantIdentity(
            user_identifier=document_record.tenant_identifier, login_name="student"
        ),
    )


async def test_pdf_original_is_loaded_only_after_resolving_owned_document(
    original_document_harness: OriginalDocumentHarness,
) -> None:
    fixture_value = original_document_harness
    document_content = await fixture_value.document_service.load_original_pdf(
        tenant_identity=fixture_value.tenant_identity,
        document_identifier=fixture_value.document_record.document_identifier,
    )
    fixture_value.document_repository.fetch_document.assert_awaited_once_with(
        tenant_identifier=fixture_value.tenant_identity.user_identifier,
        document_identifier=fixture_value.document_record.document_identifier,
    )
    fixture_value.file_storage.load_document.assert_awaited_once_with(storage_key="private-storage-key")
    assert document_content.content_bytes == b"%PDF-original"
    assert document_content.page_count == fixture_value.document_record.page_count


async def test_foreign_document_does_not_reach_file_storage(original_document_harness: OriginalDocumentHarness) -> None:
    fixture_value = original_document_harness
    fixture_value.document_repository.fetch_document.side_effect = errors.ResourceNotFoundError(
        error_code="document_not_found", message_text="Document not found"
    )
    with pytest.raises(errors.ResourceNotFoundError):
        await fixture_value.document_service.load_original_pdf(
            tenant_identity=fixture_value.tenant_identity, document_identifier=uuid.uuid4()
        )
    fixture_value.file_storage.load_document.assert_not_awaited()


@pytest.mark.parametrize("state_name", [entities.DocumentState.DELETING, entities.DocumentState.DELETED])
async def test_deleted_original_is_not_served(
    original_document_harness: OriginalDocumentHarness, state_name: entities.DocumentState
) -> None:
    fixture_value = original_document_harness
    fixture_value.document_repository.fetch_document.return_value = dataclasses.replace(
        fixture_value.document_record, state_name=state_name
    )
    with pytest.raises(errors.ResourceNotFoundError):
        await fixture_value.document_service.load_original_pdf(
            tenant_identity=fixture_value.tenant_identity,
            document_identifier=fixture_value.document_record.document_identifier,
        )
    fixture_value.file_storage.load_document.assert_not_awaited()


async def test_non_pdf_keeps_text_only_source_view(original_document_harness: OriginalDocumentHarness) -> None:
    fixture_value = original_document_harness
    fixture_value.document_repository.fetch_document.return_value = dataclasses.replace(
        fixture_value.document_record, media_type="text/markdown", filename_text="notes.md"
    )
    with pytest.raises(errors.InvalidInputError) as raised_error:
        await fixture_value.document_service.load_original_pdf(
            tenant_identity=fixture_value.tenant_identity,
            document_identifier=fixture_value.document_record.document_identifier,
        )
    assert raised_error.value.error_code == "document_not_pdf"
    fixture_value.file_storage.load_document.assert_not_awaited()
