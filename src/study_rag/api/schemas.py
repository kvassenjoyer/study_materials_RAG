"""External schemas intentionally omit secrets and internal lease details."""

import datetime
import typing
import uuid

import pydantic

from study_rag.domain import entities


class ResponseModel(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(from_attributes=True)


@typing.final
class CredentialsRequest(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")
    login_name: str = pydantic.Field(min_length=1, max_length=320)
    password_text: str = pydantic.Field(min_length=12, max_length=128)


@typing.final
class AuthResponse(ResponseModel):
    access_token: str
    expires_in: int
    token_type: str


@typing.final
class IdentityResponse(ResponseModel):
    user_identifier: uuid.UUID
    login_name: str


@typing.final
class PageResponse[RecordType](ResponseModel):
    items: tuple[RecordType, ...]  # noqa: COP004 -- Established JSON pagination contract.
    total_count: int
    offset: int  # noqa: COP004 -- External pagination query field.
    limit: int  # noqa: COP004 -- External pagination query field.


@typing.final
class DocumentResponse(ResponseModel):
    document_identifier: uuid.UUID
    project_identifier: uuid.UUID
    filename_text: str
    media_type: str
    byte_count: int
    state_name: entities.DocumentState
    chunk_count: int
    indexed_chunk_count: int
    indexing_progress_percent: int = pydantic.Field(ge=0, le=100)
    page_count: int | None
    embedding_profile: str
    error_code: str | None
    error_message: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


@typing.final
class IndexingRequest(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")
    document_identifier: uuid.UUID
    rebuild: bool = False


@typing.final
class ChunkResponse(ResponseModel):
    chunk_identifier: uuid.UUID
    document_identifier: uuid.UUID
    ordinal_value: int
    content_text: str
    token_count: int
    page_start: int | None
    page_end: int | None
    filename_text: str


@typing.final
class CitationResponse(ResponseModel):
    source_label: str
    chunk_identifier: uuid.UUID
    document_identifier: uuid.UUID
    filename_text: str
    page_start: int | None
    page_end: int | None
    excerpt_text: str
    similarity_score: float
    source_url: str


@typing.final
class ChatRequest(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")
    title_text: str | None = pydantic.Field(default=None, min_length=1, max_length=200)
    project_identifier: uuid.UUID | None = None


@typing.final
class ChatResponse(ResponseModel):
    chat_identifier: uuid.UUID
    project_identifier: uuid.UUID
    title_text: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


@typing.final
class ProjectRequest(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")
    title_text: str = pydantic.Field(min_length=1, max_length=200)


@typing.final
class ProjectResponse(ResponseModel):
    project_identifier: uuid.UUID
    title_text: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


@typing.final
class MessageResponse(ResponseModel):
    message_identifier: uuid.UUID
    chat_identifier: uuid.UUID
    turn_identifier: uuid.UUID
    role_name: entities.MessageRole
    content_text: str
    citation_records: tuple[CitationResponse, ...]
    sufficient_context: bool | None
    turn_status: entities.TurnStatus
    created_at: datetime.datetime
    turn_error_code: str | None = None
    response_duration_ms: int | None = pydantic.Field(default=None, ge=0)


@typing.final
class QuestionRequest(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")
    chat_identifier: uuid.UUID
    client_request_identifier: uuid.UUID
    question_text: str = pydantic.Field(min_length=1, max_length=4000)


@typing.final
class AnswerResponse(ResponseModel):
    turn_identifier: uuid.UUID
    chat_identifier: uuid.UUID
    user_message: MessageResponse
    assistant_message: MessageResponse


@typing.final
class AnswerJobResponse(ResponseModel):
    turn_identifier: uuid.UUID
    chat_identifier: uuid.UUID
    project_identifier: uuid.UUID
    status_name: entities.TurnStatus


@typing.final
class ErrorResponse(ResponseModel):
    error_code: str
    message_text: str


@typing.final
class HealthResponse(ResponseModel):
    status_name: typing.Literal["ready", "unavailable"]
