"""Pure conversions between persistence rows and immutable domain records."""

import typing
import uuid

from study_rag.adapters import database
from study_rag.domain import entities


def account_record(account_row: database.UserAccountRow) -> entities.UserAccount:
    return entities.UserAccount(
        user_identifier=account_row.user_identifier,
        login_name=account_row.login_name,
        password_hash=account_row.password_hash,
        created_at=account_row.created_at,
    )


def document_record(document_row: database.DocumentRow) -> entities.DocumentRecord:
    return entities.DocumentRecord(
        document_identifier=document_row.document_identifier,
        tenant_identifier=document_row.tenant_identifier,
        project_identifier=document_row.project_identifier,
        filename_text=document_row.filename_text,
        media_type=document_row.media_type,
        storage_key=document_row.storage_key,
        content_digest=document_row.content_digest,
        byte_count=document_row.byte_count,
        embedding_profile=document_row.embedding_profile,
        state_name=entities.DocumentState(document_row.state_name),
        chunk_count=document_row.chunk_count,
        page_count=document_row.page_count,
        error_code=document_row.error_code,
        error_message=document_row.error_message,
        created_at=document_row.created_at,
        updated_at=document_row.updated_at,
    )


def chunk_record(chunk_row: database.ChunkRow) -> entities.ChunkRecord:
    return entities.ChunkRecord(
        chunk_identifier=chunk_row.chunk_identifier,
        document_identifier=chunk_row.document_identifier,
        tenant_identifier=chunk_row.tenant_identifier,
        ordinal_value=chunk_row.ordinal_value,
        content_text=chunk_row.content_text,
        token_count=chunk_row.token_count,
        page_start=chunk_row.page_start,
        page_end=chunk_row.page_end,
    )


def chunk_database_row(chunk_record: entities.ChunkRecord) -> database.ChunkRow:
    return database.ChunkRow(
        chunk_identifier=chunk_record.chunk_identifier,
        document_identifier=chunk_record.document_identifier,
        tenant_identifier=chunk_record.tenant_identifier,
        ordinal_value=chunk_record.ordinal_value,
        content_text=chunk_record.content_text,
        token_count=chunk_record.token_count,
        page_start=chunk_record.page_start,
        page_end=chunk_record.page_end,
    )


def chat_record(session_row: database.ChatSessionRow) -> entities.ChatRecord:
    return entities.ChatRecord(
        chat_identifier=session_row.chat_identifier,
        tenant_identifier=session_row.tenant_identifier,
        project_identifier=session_row.project_identifier,
        title_text=session_row.title_text,
        created_at=session_row.created_at,
        updated_at=session_row.updated_at,
    )


def citation_payload(citation_record: entities.CitationRecord) -> database.CitationPayload:
    return {
        "chunk_identifier": str(citation_record.chunk_identifier),
        "document_identifier": str(citation_record.document_identifier),
        "filename_text": citation_record.filename_text,
        "page_start": citation_record.page_start,
        "page_end": citation_record.page_end,
        "excerpt_text": citation_record.excerpt_text,
        "similarity_score": citation_record.similarity_score,
        "source_url": citation_record.source_url,
        "source_label": citation_record.source_label,
    }


def citation_record(citation_payload: database.CitationPayload) -> entities.CitationRecord:
    return entities.CitationRecord(
        chunk_identifier=uuid.UUID(citation_payload["chunk_identifier"]),
        document_identifier=uuid.UUID(citation_payload["document_identifier"]),
        filename_text=citation_payload["filename_text"],
        page_start=citation_payload["page_start"],
        page_end=citation_payload["page_end"],
        excerpt_text=citation_payload["excerpt_text"],
        similarity_score=citation_payload["similarity_score"],
        source_url=citation_payload["source_url"],
        source_label=citation_payload["source_label"],
    )


def turn_status(status_name: str) -> typing.Literal["pending", "completed", "failed"]:
    if status_name == "pending":
        return "pending"
    if status_name == "completed":
        return "completed"
    if status_name == "failed":
        return "failed"
    raise ValueError("Invalid persisted turn status")


def message_record(message_row: database.ChatMessageRow, status_name: str) -> entities.MessageRecord:
    if message_row.role_name not in ("user", "assistant"):
        raise ValueError("Invalid persisted message role")
    role_name: typing.Literal["user", "assistant"] = "user" if message_row.role_name == "user" else "assistant"
    return entities.MessageRecord(
        message_identifier=message_row.message_identifier,
        chat_identifier=message_row.chat_identifier,
        turn_identifier=message_row.turn_identifier,
        role_name=role_name,
        content_text=message_row.content_text,
        citation_records=tuple(
            citation_record(one_citation_value) for one_citation_value in message_row.citation_payload
        ),
        sufficient_context=message_row.sufficient_context,
        turn_status=turn_status(status_name),
        created_at=message_row.created_at,
    )
