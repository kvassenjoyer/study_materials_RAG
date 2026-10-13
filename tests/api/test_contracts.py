"""Authentication and ownership rules are enforced at the HTTP boundary."""

import asyncio
import http
import secrets
import typing
import unittest.mock
import uuid

import httpx
import pytest

from study_rag.adapters.database import DatabaseResources
from study_rag.adapters.embeddings import OllamaEmbeddingProvider
from study_rag.adapters.generation import OllamaAnswerGenerator
from study_rag.application.answer_queue import AnswerQueue
from study_rag.application.authentication import AuthenticationService
from study_rag.application.conversations import ConversationService
from study_rag.application.documents import DocumentService
from study_rag.application.ingestion import IngestionWorker
from study_rag.application.projects import ProjectService
from study_rag.bootstrap import ApplicationResources
from study_rag.config import Settings
from study_rag.domain import entities, errors, ports
from study_rag.main import create_application


@pytest.fixture
def document_service() -> unittest.mock.Mock:
    service_mock = unittest.mock.Mock(spec=DocumentService)
    service_mock.load_original_pdf.return_value = entities.DocumentContent(
        filename_text="Учебник.pdf", media_type="application/pdf", content_bytes=b"%PDF-original", page_count=2
    )
    return service_mock


@pytest.fixture
async def api_client(document_service: unittest.mock.Mock) -> typing.AsyncIterator[httpx.AsyncClient]:
    authentication_service: typing.Final = unittest.mock.Mock(spec=AuthenticationService)
    authentication_service.authenticate_token.return_value = entities.TenantIdentity(
        user_identifier=uuid.uuid4(), login_name="student@example.com"
    )
    authentication_service.register_account.return_value = entities.AuthToken(
        access_token="fixture-access-token",  # noqa: S106 - Test-only token.
        expires_in=3600,
    )
    authentication_service.login_account.return_value = authentication_service.register_account.return_value
    worker_event: typing.Final = asyncio.Event()

    async def wait_worker() -> None:
        await worker_event.wait()

    worker_task: typing.Final = asyncio.create_task(wait_worker())
    application_resources: typing.Final = ApplicationResources(
        settings=Settings(
            database_url="postgresql+asyncpg://fixture:fixture@localhost/fixture",
            jwt_secret=secrets.token_hex(32),
            maximum_upload_bytes=1024,
        ),
        authentication_service=authentication_service,
        project_service=unittest.mock.Mock(spec=ProjectService),
        document_service=document_service,
        conversation_service=unittest.mock.Mock(spec=ConversationService),
        ingestion_worker=unittest.mock.Mock(spec=IngestionWorker),
        database_resources=unittest.mock.Mock(spec=DatabaseResources),
        vector_store=unittest.mock.AsyncMock(spec=ports.VectorStore),
        embedding_provider=unittest.mock.Mock(spec=OllamaEmbeddingProvider),
        answer_generator=unittest.mock.Mock(spec=OllamaAnswerGenerator),
        worker_task=worker_task,
        answer_queue=unittest.mock.Mock(spec=AnswerQueue),
        answer_worker_task=worker_task,
    )
    application: typing.Final = create_application()
    application.state.resources = application_resources
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://fixture"
        ) as http_client:
            yield http_client
    finally:
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)


async def test_missing_bearer_returns_uniform_unauthorized(api_client: httpx.AsyncClient) -> None:
    response_value: typing.Final = await api_client.get("/rest/study-rag/documents/")

    assert response_value.status_code == http.HTTPStatus.UNAUTHORIZED
    assert response_value.json() == {
        "error_code": "authentication_required",
        "message_text": "A bearer token is required",
    }
    assert response_value.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("endpoint", ["users", "sessions"])
async def test_authentication_accepts_plain_login_at_http_boundary(
    api_client: httpx.AsyncClient, endpoint: str
) -> None:
    response_value = await api_client.post(
        f"/rest/study-rag/{endpoint}/",
        json={"login_name": "student_123", "password_text": "fixture-password"},
    )
    assert response_value.status_code == (http.HTTPStatus.CREATED if endpoint == "users" else http.HTTPStatus.OK)
    assert response_value.json()["access_token"] == "fixture-access-token"  # noqa: S105 - Test-only token.


async def test_current_user_exposes_login_instead_of_email(api_client: httpx.AsyncClient) -> None:
    response_value = await api_client.get("/rest/study-rag/users/current/", headers={"Authorization": "Bearer fixture"})
    assert response_value.status_code == http.HTTPStatus.OK
    assert response_value.json()["login_name"] == "student@example.com"
    assert "email_address" not in response_value.json()


async def test_original_pdf_is_private_binary_with_safe_unicode_filename(api_client: httpx.AsyncClient) -> None:
    response_value = await api_client.get(
        f"/rest/study-rag/documents/{uuid.uuid4()}/original/", headers={"Authorization": "Bearer fixture"}
    )
    assert response_value.status_code == http.HTTPStatus.OK
    assert response_value.content == b"%PDF-original"
    assert response_value.headers["content-type"] == "application/pdf"
    assert response_value.headers["cache-control"] == "private, no-store"
    assert response_value.headers["x-content-type-options"] == "nosniff"
    assert response_value.headers["x-pdf-page-count"] == "2"
    assert response_value.headers["content-disposition"].startswith("inline; filename*=UTF-8''%")


async def test_original_pdf_without_bearer_is_denied(api_client: httpx.AsyncClient) -> None:
    response_value = await api_client.get(f"/rest/study-rag/documents/{uuid.uuid4()}/original/")
    assert response_value.status_code == http.HTTPStatus.UNAUTHORIZED


async def test_original_pdf_has_uniform_foreign_document_error(
    api_client: httpx.AsyncClient, document_service: unittest.mock.Mock
) -> None:
    document_service.load_original_pdf.side_effect = errors.ResourceNotFoundError(
        error_code="document_not_found", message_text="Document not found"
    )
    response_value = await api_client.get(
        f"/rest/study-rag/documents/{uuid.uuid4()}/original/", headers={"Authorization": "Bearer fixture"}
    )
    assert response_value.status_code == http.HTTPStatus.NOT_FOUND
    assert response_value.json() == {"error_code": "document_not_found", "message_text": "Document not found"}


async def test_client_cannot_supply_tenant_in_question(api_client: httpx.AsyncClient) -> None:
    response_value: typing.Final = await api_client.post(
        "/rest/study-rag/messages/",
        headers={"Authorization": "Bearer fixture"},
        json={
            "chat_identifier": str(uuid.uuid4()),
            "client_request_identifier": str(uuid.uuid4()),
            "question_text": "Explain this",
            "tenant_identifier": str(uuid.uuid4()),
        },
    )

    assert response_value.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    assert response_value.json()["error_code"] == "invalid_request"


async def test_streamed_file_size_limit_returns_structured_error(api_client: httpx.AsyncClient) -> None:
    response_value: typing.Final = await api_client.post(
        "/rest/study-rag/documents/",
        headers={"Authorization": "Bearer fixture"},
        files={"file": ("lecture.txt", b"x" * 1025, "text/plain")},
    )

    assert response_value.status_code == http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert response_value.json()["error_code"] == "document_too_large"


def test_openapi_documents_bearer_security_without_owner_fields() -> None:
    application: typing.Final = create_application()

    openapi_document: typing.Final = application.openapi()

    assert openapi_document["paths"]["/rest/study-rag/messages/"]["post"]["security"] == [{"HTTPBearer": []}]
    assert "tenant_identifier" not in openapi_document["components"]["schemas"]["QuestionRequest"]["properties"]


async def test_chunked_oversized_body_stops_before_remaining_parts(api_client: httpx.AsyncClient) -> None:
    emitted_parts = 0

    async def streaming_payload() -> typing.AsyncIterator[bytes]:
        nonlocal emitted_parts
        emitted_parts += 1
        yield (
            b'--study-boundary\r\nContent-Disposition: form-data; name="file"; filename="lecture.txt"\r\n'
            b"Content-Type: text/plain\r\n\r\n"
        )
        emitted_parts += 1
        yield b"x" * 70000
        emitted_parts += 1
        yield b"x" * 70000 + b"\r\n--study-boundary--\r\n"

    response_value: typing.Final = await api_client.post(
        "/rest/study-rag/documents/",
        headers={"Authorization": "Bearer fixture", "Content-Type": "multipart/form-data; boundary=study-boundary"},
        content=streaming_payload(),
    )

    expected_emitted_parts: typing.Final = 2
    assert response_value.status_code == http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert response_value.json()["error_code"] == "document_too_large"
    assert emitted_parts == expected_emitted_parts


async def test_unauthenticated_upload_does_not_consume_body(api_client: httpx.AsyncClient) -> None:
    emitted_parts = 0

    async def streaming_payload() -> typing.AsyncIterator[bytes]:
        nonlocal emitted_parts
        emitted_parts += 1
        yield b"x" * 100000

    response_value: typing.Final = await api_client.post(
        "/rest/study-rag/documents/",
        headers={"Content-Type": "multipart/form-data; boundary=study-boundary"},
        content=streaming_payload(),
    )

    assert response_value.status_code == http.HTTPStatus.UNAUTHORIZED
    assert emitted_parts == 0
