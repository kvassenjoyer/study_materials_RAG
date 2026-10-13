"""Immutable records shared by scenarios and infrastructure adapters."""

import dataclasses
import datetime
import enum
import typing
import uuid


type EmbeddingVector = tuple[float, ...]
type Vector = EmbeddingVector
type MessageRole = typing.Literal["user", "assistant"]
type TurnStatus = typing.Literal["pending", "completed", "failed"]


@typing.final
class DocumentState(enum.StrEnum):
    QUEUED = "queued"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class TenantIdentity:
    user_identifier: uuid.UUID
    login_name: str


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class UserAccount:
    user_identifier: uuid.UUID
    login_name: str
    password_hash: str
    created_at: datetime.datetime


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class AuthToken:
    access_token: str
    expires_in: int
    token_type: str = "bearer"  # noqa: S105 -- OAuth token scheme, not a password or secret.


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class PageResult[RecordType]:
    items: tuple[RecordType, ...]  # noqa: COP004 -- Established pagination contract.
    total_count: int
    offset_value: int
    limit_count: int


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class StoredUpload:
    storage_key: str
    content_digest: str
    byte_count: int


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class DocumentRegistration:
    document_identifier: uuid.UUID
    filename_text: str
    media_type: str
    storage_key: str
    content_digest: str
    byte_count: int
    embedding_profile: str
    project_identifier: uuid.UUID | None = None


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class DocumentRecord:
    document_identifier: uuid.UUID
    tenant_identifier: uuid.UUID
    project_identifier: uuid.UUID
    filename_text: str
    media_type: str
    storage_key: str
    content_digest: str
    byte_count: int
    embedding_profile: str
    state_name: DocumentState
    chunk_count: int
    page_count: int | None
    error_code: str | None
    error_message: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    indexed_chunk_count: int = 0

    @property
    def indexing_progress_percent(self) -> int:
        if self.state_name == DocumentState.READY:
            return 100
        if self.chunk_count == 0:
            return 0
        # Final publication is a separate transaction; 100% means searchable.
        return min(99, self.indexed_chunk_count * 100 // self.chunk_count)


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class DocumentLease:
    document_record: DocumentRecord
    lease_identifier: uuid.UUID
    action_name: typing.Literal["index", "delete"]


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class DocumentContent:
    filename_text: str
    media_type: str
    content_bytes: bytes
    page_count: int | None


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class TextSection:
    content_text: str
    page_number: int | None


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class ExtractedDocument:
    text_sections: tuple[TextSection, ...]
    page_count: int | None


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class ChunkRecord:
    chunk_identifier: uuid.UUID
    document_identifier: uuid.UUID
    tenant_identifier: uuid.UUID
    ordinal_value: int
    content_text: str
    token_count: int
    page_start: int | None
    page_end: int | None


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class EmbeddedChunk:
    chunk_record: ChunkRecord
    embedding_vector: EmbeddingVector


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class VectorMatch:
    chunk_identifier: uuid.UUID
    similarity_score: float


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class RetrievedChunk:
    chunk_record: ChunkRecord
    filename_text: str
    similarity_score: float


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class CitationRecord:
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
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class ChatRecord:
    chat_identifier: uuid.UUID
    tenant_identifier: uuid.UUID
    project_identifier: uuid.UUID
    title_text: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class ProjectRecord:
    project_identifier: uuid.UUID
    tenant_identifier: uuid.UUID
    title_text: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class MessageRecord:
    message_identifier: uuid.UUID
    chat_identifier: uuid.UUID
    turn_identifier: uuid.UUID
    role_name: MessageRole
    content_text: str
    citation_records: tuple[CitationRecord, ...]
    sufficient_context: bool | None
    turn_status: TurnStatus
    created_at: datetime.datetime


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class TurnRecord:
    turn_identifier: uuid.UUID
    chat_identifier: uuid.UUID
    client_request_identifier: uuid.UUID
    status_name: TurnStatus
    lease_identifier: uuid.UUID | None
    user_message: MessageRecord
    assistant_message: MessageRecord | None


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class GeneratedAnswer:
    answer_text: str
    sufficient_context: bool
    citation_identifiers: tuple[uuid.UUID, ...]


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class AnswerRecord:
    turn_identifier: uuid.UUID
    chat_identifier: uuid.UUID
    user_message: MessageRecord
    assistant_message: MessageRecord
