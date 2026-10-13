"""Grounding, history and failure tests exercise the public scenario."""

import asyncio
import dataclasses
import datetime
import typing
import unittest.mock
import uuid

import pytest

from study_rag.application.conversations import ConversationService
from study_rag.domain import entities, errors, ports


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class ScenarioContext:
    tenant_identity: entities.TenantIdentity
    turn_record: entities.TurnRecord
    chunk_records: tuple[entities.ChunkRecord, ...]
    conversation_repository: unittest.mock.AsyncMock
    document_repository: unittest.mock.AsyncMock
    embedding_provider: unittest.mock.Mock
    answer_generator: unittest.mock.AsyncMock
    conversation_service: ConversationService


@pytest.fixture
def scenario_context() -> ScenarioContext:
    tenant_identifier: typing.Final = uuid.uuid4()
    chat_identifier: typing.Final = uuid.uuid4()
    turn_identifier: typing.Final = uuid.uuid4()
    document_identifier: typing.Final = uuid.uuid4()
    recorded_timestamp: typing.Final = datetime.datetime(2026, 10, 4, tzinfo=datetime.UTC)
    user_message: typing.Final = entities.MessageRecord(
        message_identifier=uuid.uuid4(),
        chat_identifier=chat_identifier,
        turn_identifier=turn_identifier,
        role_name="user",
        content_text="Explain gradient descent",
        citation_records=(),
        sufficient_context=None,
        turn_status="pending",
        created_at=recorded_timestamp,
    )
    turn_record: typing.Final = entities.TurnRecord(
        turn_identifier=turn_identifier,
        chat_identifier=chat_identifier,
        client_request_identifier=uuid.uuid4(),
        status_name="pending",
        lease_identifier=uuid.uuid4(),
        user_message=user_message,
        assistant_message=None,
    )
    document_record: typing.Final = entities.DocumentRecord(
        project_identifier=uuid.uuid4(),
        document_identifier=document_identifier,
        tenant_identifier=tenant_identifier,
        filename_text="lecture.md",
        media_type="text/markdown",
        storage_key="fixture",
        content_digest="fixture",
        byte_count=30,
        embedding_profile="fixture-profile",
        state_name=entities.DocumentState.READY,
        chunk_count=3,
        page_count=None,
        error_code=None,
        error_message=None,
        created_at=recorded_timestamp,
        updated_at=recorded_timestamp,
    )
    chunk_records: typing.Final = tuple(
        entities.ChunkRecord(
            chunk_identifier=uuid.uuid4(),
            document_identifier=document_identifier,
            tenant_identifier=tenant_identifier,
            ordinal_value=ordinal_value,
            content_text=f"Fixture concept {ordinal_value}",
            token_count=3,
            page_start=None,
            page_end=None,
        )
        for ordinal_value in range(3)
    )
    conversation_repository: typing.Final = unittest.mock.AsyncMock(spec=ports.ConversationRepository)
    conversation_repository.begin_turn.return_value = turn_record
    conversation_repository.fetch_history.return_value = ()
    conversation_repository.fetch_chat.return_value = entities.ChatRecord(
        chat_identifier=chat_identifier,
        tenant_identifier=tenant_identifier,
        project_identifier=document_record.project_identifier,
        title_text="Fixture",
        created_at=recorded_timestamp,
        updated_at=recorded_timestamp,
    )

    async def complete_saved_turn(
        *,
        tenant_identifier: uuid.UUID,
        turn_record: entities.TurnRecord,
        generated_answer: entities.GeneratedAnswer,
        citation_records: tuple[entities.CitationRecord, ...],
    ) -> entities.AnswerRecord:
        assert tenant_identifier == document_record.tenant_identifier
        assistant_message: typing.Final = entities.MessageRecord(
            message_identifier=uuid.uuid4(),
            chat_identifier=turn_record.chat_identifier,
            turn_identifier=turn_record.turn_identifier,
            role_name="assistant",
            content_text=generated_answer.answer_text,
            citation_records=citation_records,
            sufficient_context=generated_answer.sufficient_context,
            turn_status="completed",
            created_at=recorded_timestamp,
        )
        return entities.AnswerRecord(
            turn_identifier=turn_record.turn_identifier,
            chat_identifier=turn_record.chat_identifier,
            user_message=dataclasses.replace(turn_record.user_message, turn_status="completed"),
            assistant_message=assistant_message,
        )

    conversation_repository.complete_turn.side_effect = complete_saved_turn
    document_repository: typing.Final = unittest.mock.AsyncMock(spec=ports.DocumentRepository)
    document_repository.fetch_ready_identifiers.return_value = (document_identifier,)
    document_repository.fetch_ready_chunks.return_value = chunk_records
    document_repository.fetch_document.return_value = document_record
    embedding_provider: typing.Final = unittest.mock.Mock(spec=ports.EmbeddingProvider)
    embedding_provider.embedding_profile = "fixture-profile"
    embedding_provider.embed_question.return_value = (1.0, 0.0)
    vector_store: typing.Final = unittest.mock.AsyncMock(spec=ports.VectorStore)
    vector_store.search_chunks.return_value = tuple(
        entities.VectorMatch(chunk_identifier=chunk_record.chunk_identifier, similarity_score=0.8)
        for chunk_record in chunk_records
    )
    answer_generator: typing.Final = unittest.mock.AsyncMock(spec=ports.AnswerGenerator)
    answer_generator.generate_answer.return_value = entities.GeneratedAnswer(
        answer_text="Supported explanation [S3].",
        sufficient_context=True,
        citation_identifiers=(chunk_records[2].chunk_identifier,),
    )
    return ScenarioContext(
        tenant_identity=entities.TenantIdentity(user_identifier=tenant_identifier, login_name="student@example.com"),
        turn_record=turn_record,
        chunk_records=chunk_records,
        conversation_repository=conversation_repository,
        document_repository=document_repository,
        embedding_provider=embedding_provider,
        answer_generator=answer_generator,
        conversation_service=ConversationService(
            conversation_repository=conversation_repository,
            document_repository=document_repository,
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            answer_generator=answer_generator,
        ),
    )


async def test_no_materials_persist_refusal_without_model(scenario_context: ScenarioContext) -> None:
    scenario_context.document_repository.fetch_ready_identifiers.return_value = ()

    answer_record: typing.Final = await scenario_context.conversation_service.answer_question(
        tenant_identity=scenario_context.tenant_identity,
        chat_identifier=scenario_context.turn_record.chat_identifier,
        client_request_identifier=scenario_context.turn_record.client_request_identifier,
        question_text="Explain gradient descent",
    )

    assert answer_record.assistant_message.sufficient_context is False
    assert not answer_record.assistant_message.citation_records
    scenario_context.answer_generator.generate_answer.assert_not_awaited()
    scenario_context.embedding_provider.embed_question.assert_not_awaited()


async def test_citation_subset_keeps_original_source_label(scenario_context: ScenarioContext) -> None:
    answer_record: typing.Final = await scenario_context.conversation_service.answer_question(
        tenant_identity=scenario_context.tenant_identity,
        chat_identifier=scenario_context.turn_record.chat_identifier,
        client_request_identifier=scenario_context.turn_record.client_request_identifier,
        question_text="Explain gradient descent",
    )

    assert answer_record.assistant_message.sufficient_context is True
    assert answer_record.assistant_message.citation_records[0].source_label == "S3"
    assert (
        answer_record.assistant_message.citation_records[0].chunk_identifier
        == scenario_context.chunk_records[2].chunk_identifier
    )


async def test_followup_rewrites_query_using_completed_history(scenario_context: ScenarioContext) -> None:
    saved_history: typing.Final = (
        dataclasses.replace(scenario_context.turn_record.user_message, turn_status="completed"),
    )
    scenario_context.conversation_repository.fetch_history.return_value = saved_history
    scenario_context.answer_generator.rewrite_question.return_value = "Numerical example of gradient descent"

    answer_record: typing.Final = await scenario_context.conversation_service.answer_question(
        tenant_identity=scenario_context.tenant_identity,
        chat_identifier=scenario_context.turn_record.chat_identifier,
        client_request_identifier=scenario_context.turn_record.client_request_identifier,
        question_text="Show an example",
    )

    assert answer_record.assistant_message.sufficient_context is True
    scenario_context.answer_generator.rewrite_question.assert_awaited_once_with(
        question_text="Show an example",
        conversation_history=saved_history,
    )
    scenario_context.embedding_provider.embed_question.assert_awaited_once_with(
        question_text="Numerical example of gradient descent"
    )


async def test_hallucinated_citation_fails_turn_without_assistant(scenario_context: ScenarioContext) -> None:
    scenario_context.answer_generator.generate_answer.return_value = entities.GeneratedAnswer(
        answer_text="Fabricated evidence [S9].",
        sufficient_context=True,
        citation_identifiers=(uuid.uuid4(),),
    )

    with pytest.raises(errors.DependencyUnavailableError, match="unsupported citations"):
        await scenario_context.conversation_service.answer_question(
            tenant_identity=scenario_context.tenant_identity,
            chat_identifier=scenario_context.turn_record.chat_identifier,
            client_request_identifier=scenario_context.turn_record.client_request_identifier,
            question_text="Explain gradient descent",
        )

    scenario_context.conversation_repository.complete_turn.assert_not_awaited()
    scenario_context.conversation_repository.fail_turn.assert_awaited_once()


async def test_whole_answer_timeout_fails_reserved_turn(scenario_context: ScenarioContext) -> None:
    async def blocked_generation(**_request_fields: object) -> entities.GeneratedAnswer:
        await asyncio.Event().wait()
        raise AssertionError("Blocked model returned unexpectedly")

    scenario_context.answer_generator.generate_answer.side_effect = blocked_generation
    bounded_service: typing.Final = dataclasses.replace(
        scenario_context.conversation_service, answer_timeout_seconds=0.01
    )

    with pytest.raises(errors.DependencyUnavailableError, match="timed out"):
        await bounded_service.answer_question(
            tenant_identity=scenario_context.tenant_identity,
            chat_identifier=scenario_context.turn_record.chat_identifier,
            client_request_identifier=scenario_context.turn_record.client_request_identifier,
            question_text="Explain gradient descent",
        )

    scenario_context.conversation_repository.complete_turn.assert_not_awaited()
    scenario_context.conversation_repository.fail_turn.assert_awaited_once_with(
        tenant_identifier=scenario_context.tenant_identity.user_identifier,
        turn_record=scenario_context.turn_record,
        error_code="answer_timeout",
    )
