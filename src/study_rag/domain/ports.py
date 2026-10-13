"""Consumer-owned ports for persistence, parsing, security and local models."""

import typing
import uuid

from study_rag.domain import entities


class AuthRepository(typing.Protocol):
    async def create_account(self, *, login_name: str, password_hash: str) -> entities.UserAccount: ...

    async def fetch_by_login(self, *, login_name: str) -> entities.UserAccount | None: ...

    async def fetch_by_identifier(self, *, user_identifier: uuid.UUID) -> entities.UserAccount | None: ...


class ProjectRepository(typing.Protocol):
    async def rename_project(
        self, *, tenant_identifier: uuid.UUID, project_identifier: uuid.UUID, title_text: str
    ) -> entities.ProjectRecord: ...

    async def delete_project(self, *, tenant_identifier: uuid.UUID, project_identifier: uuid.UUID) -> None: ...

    async def create_project(self, *, tenant_identifier: uuid.UUID, title_text: str) -> entities.ProjectRecord: ...

    async def fetch_project(
        self, *, tenant_identifier: uuid.UUID, project_identifier: uuid.UUID
    ) -> entities.ProjectRecord: ...

    async def list_projects(
        self, *, tenant_identifier: uuid.UUID, limit_count: int, offset_value: int
    ) -> entities.PageResult[entities.ProjectRecord]: ...


class DocumentRepository(typing.Protocol):
    async def list_ready_chunks(
        self,
        *,
        tenant_identifier: uuid.UUID,
        project_identifier: uuid.UUID,
        embedding_profile: str,
        limit_count: int,
        offset_value: int,
    ) -> tuple[entities.RetrievedChunk, ...]: ...

    async def register_document(
        self, *, tenant_identifier: uuid.UUID, document_registration: entities.DocumentRegistration
    ) -> entities.DocumentRecord: ...

    async def fetch_document(
        self, *, tenant_identifier: uuid.UUID, document_identifier: uuid.UUID
    ) -> entities.DocumentRecord: ...

    async def list_documents(
        self,
        *,
        tenant_identifier: uuid.UUID,
        limit_count: int,
        offset_value: int,
        project_identifier: uuid.UUID | None = None,
    ) -> entities.PageResult[entities.DocumentRecord]: ...

    async def fetch_ready_identifiers(
        self, *, tenant_identifier: uuid.UUID, embedding_profile: str, project_identifier: uuid.UUID
    ) -> tuple[uuid.UUID, ...]: ...

    async def fetch_ready_chunks(
        self,
        *,
        tenant_identifier: uuid.UUID,
        embedding_profile: str,
        chunk_identifiers: tuple[uuid.UUID, ...],
        project_identifier: uuid.UUID,
    ) -> tuple[entities.ChunkRecord, ...]: ...

    async def fetch_chunk(
        self, *, tenant_identifier: uuid.UUID, chunk_identifier: uuid.UUID
    ) -> entities.RetrievedChunk: ...

    async def request_deletion(
        self, *, tenant_identifier: uuid.UUID, document_identifier: uuid.UUID
    ) -> entities.DocumentRecord: ...

    async def retry_indexing(
        self, *, tenant_identifier: uuid.UUID, document_identifier: uuid.UUID, rebuild_requested: bool = False
    ) -> entities.DocumentRecord: ...

    async def claim_document(
        self, *, worker_identifier: uuid.UUID, lease_seconds: int
    ) -> entities.DocumentLease | None: ...

    async def renew_lease(self, *, document_lease: entities.DocumentLease, lease_seconds: int) -> bool: ...

    async def replace_chunks(
        self,
        *,
        document_lease: entities.DocumentLease,
        chunk_records: tuple[entities.ChunkRecord, ...],
        page_count: int | None,
    ) -> None: ...

    async def complete_indexing(self, *, document_lease: entities.DocumentLease) -> None: ...

    async def update_indexing_progress(
        self, *, document_lease: entities.DocumentLease, indexed_chunk_count: int
    ) -> None: ...

    async def fail_indexing(
        self, *, document_lease: entities.DocumentLease, error_code: str, error_message: str
    ) -> None: ...

    async def complete_deletion(self, *, document_lease: entities.DocumentLease) -> None: ...

    async def fail_deletion(
        self, *, document_lease: entities.DocumentLease, error_code: str, error_message: str
    ) -> None: ...


class ConversationRepository(typing.Protocol):
    async def rename_chat(
        self, *, tenant_identifier: uuid.UUID, chat_identifier: uuid.UUID, title_text: str
    ) -> entities.ChatRecord: ...

    async def delete_chat(self, *, tenant_identifier: uuid.UUID, chat_identifier: uuid.UUID) -> None: ...

    async def create_chat(
        self,
        *,
        tenant_identifier: uuid.UUID,
        title_text: str | None = None,
        project_identifier: uuid.UUID | None = None,
    ) -> entities.ChatRecord: ...

    async def fetch_chat(self, *, tenant_identifier: uuid.UUID, chat_identifier: uuid.UUID) -> entities.ChatRecord: ...

    async def list_chats(
        self,
        *,
        tenant_identifier: uuid.UUID,
        limit_count: int,
        offset_value: int,
        project_identifier: uuid.UUID | None = None,
    ) -> entities.PageResult[entities.ChatRecord]: ...

    async def list_messages(
        self,
        *,
        tenant_identifier: uuid.UUID,
        chat_identifier: uuid.UUID,
        limit_count: int,
        offset_value: int,
    ) -> entities.PageResult[entities.MessageRecord]: ...

    async def fetch_history(
        self, *, tenant_identifier: uuid.UUID, chat_identifier: uuid.UUID, message_limit: int
    ) -> tuple[entities.MessageRecord, ...]: ...

    async def begin_turn(
        self,
        *,
        tenant_identifier: uuid.UUID,
        chat_identifier: uuid.UUID,
        client_request_identifier: uuid.UUID,
        question_text: str,
        lease_seconds: int,
        queue_request: bool = False,
    ) -> entities.TurnRecord: ...

    async def claim_queued_turn(self, *, lease_seconds: int) -> entities.ClaimedAnswer | None: ...

    async def fetch_turn(self, *, tenant_identifier: uuid.UUID, turn_identifier: uuid.UUID) -> entities.TurnRecord: ...

    async def list_active_turns(self, *, tenant_identifier: uuid.UUID) -> tuple[entities.AnswerJobRecord, ...]: ...

    async def stop_turn(self, *, tenant_identifier: uuid.UUID, turn_identifier: uuid.UUID) -> entities.TurnRecord: ...

    async def complete_turn(
        self,
        *,
        tenant_identifier: uuid.UUID,
        turn_record: entities.TurnRecord,
        generated_answer: entities.GeneratedAnswer,
        citation_records: tuple[entities.CitationRecord, ...],
    ) -> entities.AnswerRecord: ...

    async def fail_turn(
        self, *, tenant_identifier: uuid.UUID, turn_record: entities.TurnRecord, error_code: str
    ) -> None: ...


class PasswordSecurity(typing.Protocol):
    def hash_password(self, *, password_text: str) -> str: ...

    def verify_password(self, *, password_text: str, password_hash: str) -> bool: ...


class AccessTokenCodec(typing.Protocol):
    def issue_token(self, *, tenant_identity: entities.TenantIdentity) -> entities.AuthToken: ...

    def decode_token(self, *, access_token: str) -> uuid.UUID: ...


class FileStorage(typing.Protocol):
    async def save_upload(
        self,
        *,
        tenant_identifier: uuid.UUID,
        document_identifier: uuid.UUID,
        content_bytes: bytes,
        filename_text: str,
    ) -> entities.StoredUpload: ...

    async def load_document(self, *, storage_key: str) -> bytes: ...

    async def delete_document(self, *, storage_key: str) -> None: ...


class DocumentParser(typing.Protocol):
    async def extract_document(self, *, content_bytes: bytes, media_type: str) -> entities.ExtractedDocument: ...


class TextTokenizer(typing.Protocol):
    def count_tokens(self, *, content_text: str) -> int: ...

    async def split_sections(
        self,
        *,
        tenant_identifier: uuid.UUID,
        document_identifier: uuid.UUID,
        embedding_profile: str,
        extracted_document: entities.ExtractedDocument,
        filename_text: str,
    ) -> tuple[entities.ChunkRecord, ...]: ...


class EmbeddingProvider(typing.Protocol):
    @property
    def embedding_profile(self) -> str: ...

    @property
    def vector_dimensions(self) -> int: ...

    async def embed_documents(
        self, *, chunk_records: tuple[entities.ChunkRecord, ...], filename_text: str
    ) -> tuple[entities.EmbeddingVector, ...]: ...

    async def embed_question(self, *, question_text: str) -> entities.EmbeddingVector: ...

    async def initialize_provider(self) -> None: ...

    async def close_provider(self) -> None: ...


class VectorStore(typing.Protocol):
    async def initialize_store(self, *, embedding_profile: str, vector_dimensions: int) -> None: ...

    async def upsert_chunks(
        self,
        *,
        tenant_identifier: uuid.UUID,
        embedding_profile: str,
        embedded_chunks: tuple[entities.EmbeddedChunk, ...],
    ) -> None: ...

    async def delete_document(
        self, *, tenant_identifier: uuid.UUID, document_identifier: uuid.UUID, embedding_profile: str
    ) -> None: ...

    async def search_chunks(
        self,
        *,
        tenant_identifier: uuid.UUID,
        embedding_profile: str,
        question_vector: entities.EmbeddingVector,
        document_identifiers: tuple[uuid.UUID, ...],
        search_limit: int,
    ) -> tuple[entities.VectorMatch, ...]: ...

    async def check_readiness(self) -> bool: ...

    async def close_store(self) -> None: ...


class AnswerGenerator(typing.Protocol):
    async def plan_search(
        self, *, question_text: str, conversation_history: tuple[entities.MessageRecord, ...]
    ) -> entities.SearchPlan: ...

    async def extract_evidence(
        self,
        *,
        question_text: str,
        context_chunks: tuple[entities.RetrievedChunk, ...],
        covered_names: tuple[str, ...],
    ) -> tuple[entities.EvidenceItem, ...]: ...

    async def rewrite_question(
        self, *, question_text: str, conversation_history: tuple[entities.MessageRecord, ...]
    ) -> str: ...

    async def generate_answer(
        self,
        *,
        question_text: str,
        conversation_history: tuple[entities.MessageRecord, ...],
        context_chunks: tuple[entities.RetrievedChunk, ...],
    ) -> entities.GeneratedAnswer: ...

    async def check_readiness(self) -> bool: ...

    async def close_provider(self) -> None: ...
