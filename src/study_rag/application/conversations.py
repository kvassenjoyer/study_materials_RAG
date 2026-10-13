"""History-aware retrieval and citation-checked answers for one tenant."""

import asyncio
import dataclasses
import logging
import re
import time
import typing
import uuid

from study_rag.application.adaptive_search import AdaptiveSearch
from study_rag.domain import entities, errors, ports


application_logger: typing.Final = logging.getLogger(__name__)
maximum_question_characters: typing.Final = 4000
maximum_excerpt_characters: typing.Final = 1000
maximum_chat_title_characters: typing.Final = 200


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class ConversationService:
    conversation_repository: ports.ConversationRepository
    document_repository: ports.DocumentRepository
    embedding_provider: ports.EmbeddingProvider
    vector_store: ports.VectorStore
    answer_generator: ports.AnswerGenerator
    retrieval_limit: int = 6
    retrieval_candidates: int = 24
    minimum_similarity: float = 0.35
    history_message_limit: int = 8
    turn_lease_seconds: int = 300
    answer_timeout_seconds: float = 240.0
    overview_chunk_limit: int = 40
    scan_chunk_limit: int = 20000
    maximum_search_passes: int = 3

    async def create_chat(
        self,
        *,
        tenant_identity: entities.TenantIdentity,
        title_text: str | None = None,
        project_identifier: uuid.UUID | None = None,
    ) -> entities.ChatRecord:
        return await self.conversation_repository.create_chat(
            tenant_identifier=tenant_identity.user_identifier,
            title_text=self._validated_title(title_text=title_text) if title_text is not None else None,
            project_identifier=project_identifier,
        )

    async def rename_chat(
        self, *, tenant_identity: entities.TenantIdentity, chat_identifier: uuid.UUID, title_text: str
    ) -> entities.ChatRecord:
        return await self.conversation_repository.rename_chat(
            tenant_identifier=tenant_identity.user_identifier,
            chat_identifier=chat_identifier,
            title_text=self._validated_title(title_text=title_text),
        )

    async def delete_chat(self, *, tenant_identity: entities.TenantIdentity, chat_identifier: uuid.UUID) -> None:
        await self.conversation_repository.delete_chat(
            tenant_identifier=tenant_identity.user_identifier, chat_identifier=chat_identifier
        )

    def _validated_title(self, *, title_text: str) -> str:
        normalized_title = title_text.strip()
        if not normalized_title or len(normalized_title) > maximum_chat_title_characters:
            raise errors.InvalidInputError(
                error_code="invalid_chat_title", message_text="Chat title must contain 1 to 200 characters"
            )
        return normalized_title

    async def fetch_chat(
        self, *, tenant_identity: entities.TenantIdentity, chat_identifier: uuid.UUID
    ) -> entities.ChatRecord:
        return await self.conversation_repository.fetch_chat(
            tenant_identifier=tenant_identity.user_identifier, chat_identifier=chat_identifier
        )

    async def list_chats(
        self,
        *,
        tenant_identity: entities.TenantIdentity,
        limit_count: int,
        offset_value: int,
        project_identifier: uuid.UUID | None = None,
    ) -> entities.PageResult[entities.ChatRecord]:
        return await self.conversation_repository.list_chats(
            tenant_identifier=tenant_identity.user_identifier,
            limit_count=limit_count,
            offset_value=offset_value,
            project_identifier=project_identifier,
        )

    async def list_messages(
        self,
        *,
        tenant_identity: entities.TenantIdentity,
        chat_identifier: uuid.UUID,
        limit_count: int,
        offset_value: int,
    ) -> entities.PageResult[entities.MessageRecord]:
        return await self.conversation_repository.list_messages(
            tenant_identifier=tenant_identity.user_identifier,
            chat_identifier=chat_identifier,
            limit_count=limit_count,
            offset_value=offset_value,
        )

    async def answer_question(
        self,
        *,
        tenant_identity: entities.TenantIdentity,
        chat_identifier: uuid.UUID,
        client_request_identifier: uuid.UUID,
        question_text: str,
    ) -> entities.AnswerRecord:
        normalized_question: typing.Final = question_text.strip()
        if not normalized_question or len(normalized_question) > maximum_question_characters:
            raise errors.InvalidInputError(
                error_code="invalid_question", message_text="Question must contain 1 to 4000 characters"
            )
        turn_record: typing.Final = await self.conversation_repository.begin_turn(
            tenant_identifier=tenant_identity.user_identifier,
            chat_identifier=chat_identifier,
            client_request_identifier=client_request_identifier,
            question_text=normalized_question,
            lease_seconds=self.turn_lease_seconds,
        )
        if turn_record.status_name == "completed":
            if turn_record.assistant_message is None:
                raise errors.DependencyUnavailableError(
                    error_code="invalid_saved_turn", message_text="Saved turn is incomplete"
                )
            return entities.AnswerRecord(
                turn_identifier=turn_record.turn_identifier,
                chat_identifier=chat_identifier,
                user_message=turn_record.user_message,
                assistant_message=turn_record.assistant_message,
            )
        return await self.execute_reserved_turn(
            tenant_identity=tenant_identity, turn_record=turn_record, question_text=normalized_question
        )

    async def execute_reserved_turn(
        self,
        *,
        tenant_identity: entities.TenantIdentity,
        turn_record: entities.TurnRecord,
        question_text: str | None = None,
    ) -> entities.AnswerRecord:
        try:
            async with asyncio.timeout(self.answer_timeout_seconds):
                return await self._answer_reserved_turn(
                    tenant_identity=tenant_identity,
                    turn_record=turn_record,
                    question_text=turn_record.user_message.content_text if question_text is None else question_text,
                )
        except TimeoutError as timeout_error:
            await self._fail_reserved_turn(
                tenant_identity=tenant_identity, turn_record=turn_record, error_code="answer_timeout"
            )
            raise errors.DependencyUnavailableError(
                error_code="answer_timeout", message_text="Answer generation timed out"
            ) from timeout_error
        except asyncio.CancelledError:
            await asyncio.shield(
                self._fail_reserved_turn(
                    tenant_identity=tenant_identity, turn_record=turn_record, error_code="answer_cancelled"
                )
            )
            raise
        except errors.DomainError as answer_error:
            await self._fail_reserved_turn(
                tenant_identity=tenant_identity, turn_record=turn_record, error_code=answer_error.error_code
            )
            raise
        except Exception as answer_error:
            application_logger.exception("Unexpected answer failure for turn %s", turn_record.turn_identifier)
            await self._fail_reserved_turn(
                tenant_identity=tenant_identity, turn_record=turn_record, error_code="answer_generation_failed"
            )
            raise errors.DependencyUnavailableError(
                error_code="answer_generation_failed", message_text="Answer generation failed; retry this request"
            ) from answer_error

    async def _answer_reserved_turn(
        self, *, tenant_identity: entities.TenantIdentity, turn_record: entities.TurnRecord, question_text: str
    ) -> entities.AnswerRecord:
        started_at = time.monotonic()
        chat_record = await self.conversation_repository.fetch_chat(
            tenant_identifier=tenant_identity.user_identifier,
            chat_identifier=turn_record.chat_identifier,
        )
        conversation_history: typing.Final = await self.conversation_repository.fetch_history(
            tenant_identifier=tenant_identity.user_identifier,
            chat_identifier=turn_record.chat_identifier,
            message_limit=self.history_message_limit,
        )
        search_service = AdaptiveSearch(
            document_repository=self.document_repository,
            embedding_provider=self.embedding_provider,
            vector_store=self.vector_store,
            answer_generator=self.answer_generator,
            retrieval_limit=self.retrieval_limit,
            retrieval_candidates=self.retrieval_candidates,
            minimum_similarity=self.minimum_similarity,
            overview_chunk_limit=self.overview_chunk_limit,
            scan_chunk_limit=self.scan_chunk_limit,
            maximum_search_passes=self.maximum_search_passes,
        )
        search_result = (
            None
            if self._is_small_talk(question_text=question_text)
            else await search_service.search_context(
                tenant_identity=tenant_identity,
                project_identifier=chat_record.project_identifier,
                question_text=question_text,
                conversation_history=conversation_history,
            )
        )
        context_chunks = search_result.context_chunks if search_result is not None else ()
        if (
            search_result is not None
            and context_chunks
            and search_result.search_plan.scope_name in {"overview", "expansion"}
        ):
            generated_answer = await search_service.compose_overview(
                search_result=search_result,
                conversation_history=conversation_history,
                time_budget_seconds=max(1.0, self.answer_timeout_seconds - (time.monotonic() - started_at) - 15.0),
            )
        else:
            generated_answer = await self.answer_generator.generate_answer(
                question_text=question_text,
                conversation_history=conversation_history,
                context_chunks=context_chunks,
            )
            if search_result is not None and context_chunks and not generated_answer.sufficient_context:
                expanded_result = await search_service.expand_context(
                    search_result=search_result,
                    tenant_identity=tenant_identity,
                    project_identifier=chat_record.project_identifier,
                    conversation_history=conversation_history,
                )
                if expanded_result.context_chunks != context_chunks:
                    context_chunks = expanded_result.context_chunks
                    generated_answer = await self.answer_generator.generate_answer(
                        question_text=question_text,
                        conversation_history=conversation_history,
                        context_chunks=context_chunks,
                    )
        citation_records: tuple[entities.CitationRecord, ...] = ()
        if context_chunks and generated_answer.sufficient_context:
            citation_records = self._build_citations(generated_answer=generated_answer, context_chunks=context_chunks)
        else:
            if context_chunks:
                generated_answer = await self.answer_generator.generate_answer(
                    question_text=question_text,
                    conversation_history=conversation_history,
                    context_chunks=(),
                )
            if (
                generated_answer.sufficient_context
                or generated_answer.citation_identifiers
                or not generated_answer.answer_text.strip()
            ):
                raise errors.DependencyUnavailableError(
                    error_code="invalid_general_answer",
                    message_text="A general answer must not claim document evidence",
                )
            knowledge_notice = (
                "Ответ из знаний модели, без источников из материалов проекта."
                if any("\u0400" <= character <= "\u04ff" for character in question_text)
                else "Answer from model knowledge, without sources from project materials."
            )
            generated_answer = dataclasses.replace(
                generated_answer, answer_text=f"> {knowledge_notice}\n\n{generated_answer.answer_text.strip()}"
            )
        timed_answer = dataclasses.replace(
            generated_answer, response_duration_ms=max(0, round((time.monotonic() - started_at) * 1000))
        )
        return await self.conversation_repository.complete_turn(
            tenant_identifier=tenant_identity.user_identifier,
            turn_record=turn_record,
            generated_answer=timed_answer,
            citation_records=citation_records,
        )

    def _build_citations(
        self, *, generated_answer: entities.GeneratedAnswer, context_chunks: tuple[entities.RetrievedChunk, ...]
    ) -> tuple[entities.CitationRecord, ...]:
        available_identifiers: typing.Final = {
            context_chunk.chunk_record.chunk_identifier for context_chunk in context_chunks
        }
        cited_identifiers: typing.Final = set(generated_answer.citation_identifiers)
        if not cited_identifiers.issubset(available_identifiers) or (
            generated_answer.sufficient_context and (not cited_identifiers or not generated_answer.answer_text.strip())
        ):
            raise errors.DependencyUnavailableError(
                error_code="invalid_answer_citations", message_text="Model returned unsupported citations"
            )
        return tuple(
            entities.CitationRecord(
                source_label=f"S{source_number}",
                chunk_identifier=context_chunk.chunk_record.chunk_identifier,
                document_identifier=context_chunk.chunk_record.document_identifier,
                filename_text=context_chunk.filename_text,
                page_start=context_chunk.chunk_record.page_start,
                page_end=context_chunk.chunk_record.page_end,
                excerpt_text=context_chunk.chunk_record.content_text[:maximum_excerpt_characters],
                similarity_score=context_chunk.similarity_score,
                source_url=f"/rest/study-rag/chunks/{context_chunk.chunk_record.chunk_identifier}/",
            )
            for source_number, context_chunk in enumerate(context_chunks, start=1)
            if context_chunk.chunk_record.chunk_identifier in cited_identifiers
        )

    def _is_small_talk(self, *, question_text: str) -> bool:
        normalized_question = re.sub(r"[^\w\s]", " ", question_text.casefold())
        return " ".join(normalized_question.split()) in {
            "привет",
            "здравствуй",
            "здравствуйте",
            "добрый день",
            "доброе утро",
            "добрый вечер",
            "как дела",
            "привет как дела",
            "спасибо",
            "благодарю",
            "пока",
            "до свидания",
            "до встречи",
            "что ты умеешь",
            "что ты можешь",
            "чем ты можешь помочь",
            "какие у тебя возможности",  # noqa: RUF001 - Russian user input
            "расскажи о себе",  # noqa: RUF001 - Russian user input
            "кто ты",
            "hi",
            "hello",
            "hey",
            "how are you",
            "hi how are you",
            "thanks",
            "thank you",
            "bye",
            "goodbye",
            "what can you do",
            "who are you",
        }

    async def _fail_reserved_turn(
        self, *, tenant_identity: entities.TenantIdentity, turn_record: entities.TurnRecord, error_code: str
    ) -> None:
        try:
            await self.conversation_repository.fail_turn(
                tenant_identifier=tenant_identity.user_identifier, turn_record=turn_record, error_code=error_code
            )
        except (errors.DependencyUnavailableError, errors.LeaseLostError):
            application_logger.exception(
                "Could not fail turn %s; lease recovery remains available", turn_record.turn_identifier
            )
