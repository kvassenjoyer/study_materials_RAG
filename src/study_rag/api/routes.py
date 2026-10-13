"""Thin, tenant-scoped REST endpoints for the study-materials service."""

import typing
import urllib.parse
import uuid

import fastapi
import fastapi.responses

from study_rag.api import dependencies, schemas
from study_rag.domain import errors


api_router: typing.Final = fastapi.APIRouter(prefix="/rest/study-rag")
type LimitParameter = typing.Annotated[int, fastapi.Query(alias="limit", ge=1, le=100)]
type OffsetParameter = typing.Annotated[int, fastapi.Query(alias="offset", ge=0)]
type ProjectParameter = typing.Annotated[uuid.UUID | None, fastapi.Query(alias="project_identifier")]


@api_router.post("/users/", response_model=schemas.AuthResponse, status_code=201, tags=["authentication"])
async def register_account(
    *, credentials_request: schemas.CredentialsRequest, application_resources: dependencies.CurrentResources
) -> schemas.AuthResponse:
    account_token: typing.Final = await application_resources.authentication_service.register_account(
        login_name=str(credentials_request.login_name), password_text=credentials_request.password_text
    )
    return schemas.AuthResponse.model_validate(account_token)


@api_router.post("/sessions/", response_model=schemas.AuthResponse, tags=["authentication"])
async def login_account(
    *, credentials_request: schemas.CredentialsRequest, application_resources: dependencies.CurrentResources
) -> schemas.AuthResponse:
    account_token: typing.Final = await application_resources.authentication_service.login_account(
        login_name=str(credentials_request.login_name), password_text=credentials_request.password_text
    )
    return schemas.AuthResponse.model_validate(account_token)


@api_router.get("/users/current/", response_model=schemas.IdentityResponse, tags=["authentication"])
async def fetch_current_user(tenant_identity: dependencies.CurrentIdentity) -> schemas.IdentityResponse:
    return schemas.IdentityResponse.model_validate(tenant_identity)


@api_router.post("/documents/", response_model=schemas.DocumentResponse, status_code=202, tags=["documents"])
async def upload_document(
    *,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
    uploaded_file: typing.Annotated[fastapi.UploadFile, fastapi.File(alias="file")],
    project_identifier: ProjectParameter = None,
) -> schemas.DocumentResponse:
    upload_limit: typing.Final = application_resources.settings.maximum_upload_bytes
    content_buffer: typing.Final = bytearray()
    try:
        while content_piece := await uploaded_file.read(65536):
            if len(content_buffer) + len(content_piece) > upload_limit:
                raise errors.InvalidInputError(
                    error_code="document_too_large", message_text="Document exceeds the upload limit"
                )
            content_buffer.extend(content_piece)
        document_record: typing.Final = await application_resources.document_service.upload_document(
            tenant_identity=tenant_identity,
            filename_text=uploaded_file.filename or "",
            content_bytes=bytes(content_buffer),
            project_identifier=project_identifier,
        )
        return schemas.DocumentResponse.model_validate(document_record)
    finally:
        await uploaded_file.close()


@api_router.get("/documents/", response_model=schemas.PageResponse[schemas.DocumentResponse], tags=["documents"])
async def list_documents(
    *,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
    limit_count: LimitParameter = 50,
    offset_value: OffsetParameter = 0,
    project_identifier: ProjectParameter = None,
) -> schemas.PageResponse[schemas.DocumentResponse]:
    document_page: typing.Final = await application_resources.document_service.list_documents(
        tenant_identity=tenant_identity,
        limit_count=limit_count,
        offset_value=offset_value,
        project_identifier=project_identifier,
    )
    return schemas.PageResponse[schemas.DocumentResponse](
        items=tuple(
            schemas.DocumentResponse.model_validate(document_record) for document_record in document_page.items
        ),
        total_count=document_page.total_count,
        offset=document_page.offset_value,
        limit=document_page.limit_count,
    )


@api_router.get("/documents/{document_identifier}/", response_model=schemas.DocumentResponse, tags=["documents"])
async def fetch_document(
    *,
    document_identifier: uuid.UUID,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> schemas.DocumentResponse:
    document_record: typing.Final = await application_resources.document_service.fetch_document(
        tenant_identity=tenant_identity, document_identifier=document_identifier
    )
    return schemas.DocumentResponse.model_validate(document_record)


@api_router.delete(
    "/documents/{document_identifier}/", response_model=schemas.DocumentResponse, status_code=202, tags=["documents"]
)
async def delete_document(
    *,
    document_identifier: uuid.UUID,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> schemas.DocumentResponse:
    document_record: typing.Final = await application_resources.document_service.delete_document(
        tenant_identity=tenant_identity, document_identifier=document_identifier
    )
    return schemas.DocumentResponse.model_validate(document_record)


@api_router.post("/indexing-jobs/", response_model=schemas.DocumentResponse, status_code=202, tags=["documents"])
async def retry_document(
    *,
    indexing_request: schemas.IndexingRequest,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> schemas.DocumentResponse:
    document_record: typing.Final = await application_resources.document_service.retry_document(
        tenant_identity=tenant_identity,
        document_identifier=indexing_request.document_identifier,
        rebuild_requested=indexing_request.rebuild,
    )
    return schemas.DocumentResponse.model_validate(document_record)


@api_router.get("/documents/{document_identifier}/original/", tags=["documents"])
async def fetch_original_pdf(
    *,
    document_identifier: uuid.UUID,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> fastapi.Response:
    document_content = await application_resources.document_service.load_original_pdf(
        tenant_identity=tenant_identity, document_identifier=document_identifier
    )
    encoded_filename = urllib.parse.quote(document_content.filename_text, safe="")
    response_headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if document_content.page_count is not None:
        response_headers["X-PDF-Page-Count"] = str(document_content.page_count)
    return fastapi.Response(
        content=document_content.content_bytes, media_type="application/pdf", headers=response_headers
    )


@api_router.get("/chunks/{chunk_identifier}/", response_model=schemas.ChunkResponse, tags=["documents"])
async def fetch_chunk(
    *,
    chunk_identifier: uuid.UUID,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> schemas.ChunkResponse:
    retrieved_chunk: typing.Final = await application_resources.document_service.fetch_chunk(
        tenant_identity=tenant_identity, chunk_identifier=chunk_identifier
    )
    chunk_record: typing.Final = retrieved_chunk.chunk_record
    return schemas.ChunkResponse(
        chunk_identifier=chunk_record.chunk_identifier,
        document_identifier=chunk_record.document_identifier,
        ordinal_value=chunk_record.ordinal_value,
        content_text=chunk_record.content_text,
        token_count=chunk_record.token_count,
        page_start=chunk_record.page_start,
        page_end=chunk_record.page_end,
        filename_text=retrieved_chunk.filename_text,
    )


@api_router.post("/chats/", response_model=schemas.ChatResponse, status_code=201, tags=["chats"])
async def create_chat(
    *,
    chat_request: schemas.ChatRequest,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> schemas.ChatResponse:
    chat_record: typing.Final = await application_resources.conversation_service.create_chat(
        tenant_identity=tenant_identity,
        title_text=chat_request.title_text,
        project_identifier=chat_request.project_identifier,
    )
    return schemas.ChatResponse.model_validate(chat_record)


@api_router.get("/chats/", response_model=schemas.PageResponse[schemas.ChatResponse], tags=["chats"])
async def list_chats(
    *,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
    limit_count: LimitParameter = 50,
    offset_value: OffsetParameter = 0,
    project_identifier: ProjectParameter = None,
) -> schemas.PageResponse[schemas.ChatResponse]:
    chat_page: typing.Final = await application_resources.conversation_service.list_chats(
        tenant_identity=tenant_identity,
        limit_count=limit_count,
        offset_value=offset_value,
        project_identifier=project_identifier,
    )
    return schemas.PageResponse[schemas.ChatResponse](
        items=tuple(schemas.ChatResponse.model_validate(chat_record) for chat_record in chat_page.items),
        total_count=chat_page.total_count,
        offset=chat_page.offset_value,
        limit=chat_page.limit_count,
    )


@api_router.patch("/chats/{chat_identifier}/", response_model=schemas.ChatResponse, tags=["chats"])
async def rename_chat(
    *,
    chat_identifier: uuid.UUID,
    title_request: schemas.ProjectRequest,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> schemas.ChatResponse:
    chat_record = await application_resources.conversation_service.rename_chat(
        tenant_identity=tenant_identity,
        chat_identifier=chat_identifier,
        title_text=title_request.title_text,
    )
    return schemas.ChatResponse.model_validate(chat_record)


@api_router.delete("/chats/{chat_identifier}/", status_code=204, tags=["chats"])
async def delete_chat(
    *,
    chat_identifier: uuid.UUID,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> fastapi.Response:
    await application_resources.conversation_service.delete_chat(
        tenant_identity=tenant_identity,
        chat_identifier=chat_identifier,
    )
    return fastapi.Response(status_code=204)


@api_router.patch("/projects/{project_identifier}/", response_model=schemas.ProjectResponse, tags=["projects"])
async def rename_project(
    *,
    project_identifier: uuid.UUID,
    title_request: schemas.ProjectRequest,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> schemas.ProjectResponse:
    project_record = await application_resources.project_service.rename_project(
        tenant_identity=tenant_identity,
        project_identifier=project_identifier,
        title_text=title_request.title_text,
    )
    return schemas.ProjectResponse.model_validate(project_record)


@api_router.delete("/projects/{project_identifier}/", status_code=204, tags=["projects"])
async def delete_project(
    *,
    project_identifier: uuid.UUID,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> fastapi.Response:
    await application_resources.project_service.delete_project(
        tenant_identity=tenant_identity,
        project_identifier=project_identifier,
    )
    return fastapi.Response(status_code=204)


@api_router.get("/chats/{chat_identifier}/", response_model=schemas.ChatResponse, tags=["chats"])
async def fetch_chat(
    *,
    chat_identifier: uuid.UUID,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> schemas.ChatResponse:
    chat_record: typing.Final = await application_resources.conversation_service.fetch_chat(
        tenant_identity=tenant_identity, chat_identifier=chat_identifier
    )
    return schemas.ChatResponse.model_validate(chat_record)


@api_router.get("/messages/", response_model=schemas.PageResponse[schemas.MessageResponse], tags=["chats"])
async def list_messages(
    *,
    chat_identifier: uuid.UUID,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
    limit_count: LimitParameter = 50,
    offset_value: OffsetParameter = 0,
) -> schemas.PageResponse[schemas.MessageResponse]:
    message_page: typing.Final = await application_resources.conversation_service.list_messages(
        tenant_identity=tenant_identity,
        chat_identifier=chat_identifier,
        limit_count=limit_count,
        offset_value=offset_value,
    )
    return schemas.PageResponse[schemas.MessageResponse](
        items=tuple(schemas.MessageResponse.model_validate(message_record) for message_record in message_page.items),
        total_count=message_page.total_count,
        offset=message_page.offset_value,
        limit=message_page.limit_count,
    )


@api_router.post("/messages/", response_model=schemas.AnswerResponse, tags=["chats"])
async def answer_question(
    *,
    question_request: schemas.QuestionRequest,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> schemas.AnswerResponse:
    answer_record: typing.Final = await application_resources.conversation_service.answer_question(
        tenant_identity=tenant_identity,
        chat_identifier=question_request.chat_identifier,
        client_request_identifier=question_request.client_request_identifier,
        question_text=question_request.question_text,
    )
    return schemas.AnswerResponse.model_validate(answer_record)


@api_router.post("/projects/", response_model=schemas.ProjectResponse, status_code=201, tags=["projects"])
async def create_project(
    *,
    project_request: schemas.ProjectRequest,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> schemas.ProjectResponse:
    project_record = await application_resources.project_service.create_project(
        tenant_identity=tenant_identity,
        title_text=project_request.title_text,
    )
    return schemas.ProjectResponse.model_validate(project_record)


@api_router.get("/projects/", response_model=schemas.PageResponse[schemas.ProjectResponse], tags=["projects"])
async def list_projects(
    *,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
    limit_count: LimitParameter = 50,
    offset_value: OffsetParameter = 0,
) -> schemas.PageResponse[schemas.ProjectResponse]:
    project_page = await application_resources.project_service.list_projects(
        tenant_identity=tenant_identity,
        limit_count=limit_count,
        offset_value=offset_value,
    )
    return schemas.PageResponse[schemas.ProjectResponse](
        items=tuple(schemas.ProjectResponse.model_validate(project_record) for project_record in project_page.items),
        total_count=project_page.total_count,
        offset=project_page.offset_value,
        limit=project_page.limit_count,
    )


@api_router.get("/projects/{project_identifier}/", response_model=schemas.ProjectResponse, tags=["projects"])
async def fetch_project(
    *,
    project_identifier: uuid.UUID,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> schemas.ProjectResponse:
    project_record = await application_resources.project_service.fetch_project(
        tenant_identity=tenant_identity,
        project_identifier=project_identifier,
    )
    return schemas.ProjectResponse.model_validate(project_record)


@api_router.get("/health/", response_model=schemas.HealthResponse, tags=["health"])
async def fetch_health(
    *,
    application_resources: dependencies.CurrentResources,
) -> schemas.HealthResponse | fastapi.responses.JSONResponse:
    if not await application_resources.check_readiness():
        return fastapi.responses.JSONResponse(content={"status_name": "unavailable"}, status_code=503)
    return schemas.HealthResponse(status_name="ready")
