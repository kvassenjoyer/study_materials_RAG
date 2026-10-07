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
from study_rag.application.authentication import AuthenticationService
from study_rag.application.conversations import ConversationService
from study_rag.application.documents import DocumentService
from study_rag.application.ingestion import IngestionWorker
from study_rag.bootstrap import ApplicationResources
from study_rag.config import Settings
from study_rag.domain import entities, ports
from study_rag.main import create_application


@pytest.fixture
async def api_client() -> typing.AsyncIterator[httpx.AsyncClient]:
    authentication_service: typing.Final = unittest.mock.Mock(spec=AuthenticationService)
    authentication_service.authenticate_token.return_value = entities.TenantIdentity(
        user_identifier=uuid.uuid4(), email_address="student@example.com"
    )
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
        document_service=unittest.mock.Mock(spec=DocumentService),
        conversation_service=unittest.mock.Mock(spec=ConversationService),
        ingestion_worker=unittest.mock.Mock(spec=IngestionWorker),
        database_resources=unittest.mock.Mock(spec=DatabaseResources),
        vector_store=unittest.mock.AsyncMock(spec=ports.VectorStore),
        embedding_provider=unittest.mock.Mock(spec=OllamaEmbeddingProvider),
        answer_generator=unittest.mock.Mock(spec=OllamaAnswerGenerator),
        worker_task=worker_task,
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
