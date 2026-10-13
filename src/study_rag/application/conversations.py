"""History-aware retrieval and citation-checked answers for one tenant."""

import asyncio
import dataclasses
import logging
import typing
import uuid

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
        try:
            async with asyncio.timeout(self.answer_timeout_seconds):
                return await self._answer_reserved_turn(
                    tenant_identity=tenant_identity, turn_record=turn_record, question_text=normalized_question
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
        conversation_history: typing.Final = await self.conversation_repository.fetch_history(
            tenant_identifier=tenant_identity.user_identifier,
            chat_identifier=turn_record.chat_identifier,
            message_limit=self.history_message_limit,
        )
        context_chunks: typing.Final = await self._retrieve_context(
            tenant_identity=tenant_identity,
            chat_identifier=turn_record.chat_identifier,
            question_text=question_text,
            conversation_history=conversation_history,
        )
        generated_answer = self._insufficient_answer(question_text=question_text)
        citation_records: tuple[entities.CitationRecord, ...] = ()
        if context_chunks:
            generated_answer = await self.answer_generator.generate_answer(
                question_text=question_text, conversation_history=conversation_history, context_chunks=context_chunks
            )
            citation_records = self._build_citations(generated_answer=generated_answer, context_chunks=context_chunks)
            if not generated_answer.sufficient_context:
                generated_answer = self._insufficient_answer(question_text=question_text)
                citation_records = ()
        return await self.conversation_repository.complete_turn(
            tenant_identifier=tenant_identity.user_identifier,
            turn_record=turn_record,
            generated_answer=generated_answer,
            citation_records=citation_records,
        )

    async def _retrieve_context(
        self,
        *,
        tenant_identity: entities.TenantIdentity,
        chat_identifier: uuid.UUID,
        question_text: str,
        conversation_history: tuple[entities.MessageRecord, ...],
    ) -> tuple[entities.RetrievedChunk, ...]:
        chat_record = await self.conversation_repository.fetch_chat(
            tenant_identifier=tenant_identity.user_identifier,
            chat_identifier=chat_identifier,
        )
        document_identifiers: typing.Final = await self.document_repository.fetch_ready_identifiers(
            tenant_identifier=tenant_identity.user_identifier,
            embedding_profile=self.embedding_provider.embedding_profile,
            project_identifier=chat_record.project_identifier,
        )
        if not document_identifiers:
            return ()
        retrieval_question = question_text
        if conversation_history:
            retrieval_question = await self.answer_generator.rewrite_question(
                question_text=question_text, conversation_history=conversation_history
            )
        question_vector: typing.Final = await self.embedding_provider.embed_question(question_text=retrieval_question)
        vector_matches: typing.Final = await self.vector_store.search_chunks(
            tenant_identifier=tenant_identity.user_identifier,
            embedding_profile=self.embedding_provider.embedding_profile,
            question_vector=question_vector,
            document_identifiers=document_identifiers,
            search_limit=self.retrieval_candidates,
        )
        accepted_matches: typing.Final = tuple(
            vector_match for vector_match in vector_matches if vector_match.similarity_score >= self.minimum_similarity
        )
        if not accepted_matches:
            return ()
        chunk_records: typing.Final = await self.document_repository.fetch_ready_chunks(
            tenant_identifier=tenant_identity.user_identifier,
            embedding_profile=self.embedding_provider.embedding_profile,
            project_identifier=chat_record.project_identifier,
            chunk_identifiers=tuple(vector_match.chunk_identifier for vector_match in accepted_matches),
        )
        available_chunks: typing.Final = {chunk_record.chunk_identifier: chunk_record for chunk_record in chunk_records}
        retrieved_chunks: typing.Final[list[entities.RetrievedChunk]] = []
        available_documents: typing.Final[dict[uuid.UUID, entities.DocumentRecord]] = {}
        for vector_match in accepted_matches:
            chunk_record = available_chunks.get(vector_match.chunk_identifier)
            if chunk_record is None or chunk_record.tenant_identifier != tenant_identity.user_identifier:
                continue
            document_record = available_documents.get(chunk_record.document_identifier)
            if document_record is None:
                try:
                    document_record = await self.document_repository.fetch_document(
                        tenant_identifier=tenant_identity.user_identifier,
                        document_identifier=chunk_record.document_identifier,
                    )
                except errors.ResourceNotFoundError:
                    continue
                available_documents[chunk_record.document_identifier] = document_record
            if (
                document_record.state_name != entities.DocumentState.READY
                or document_record.embedding_profile != self.embedding_provider.embedding_profile
            ):
                continue
            retrieved_chunks.append(
                entities.RetrievedChunk(
                    chunk_record=chunk_record,
                    filename_text=document_record.filename_text,
                    similarity_score=vector_match.similarity_score,
                )
            )
            if len(retrieved_chunks) == self.retrieval_limit:
                break
        return tuple(retrieved_chunks)

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

    def _insufficient_answer(self, *, question_text: str) -> entities.GeneratedAnswer:
        contains_cyrillic: typing.Final = any(
            "\u0400" <= character_value <= "\u04ff" for character_value in question_text
        )
        answer_text: typing.Final = (
            "В ваших материалах недостаточно информации для ответа. Загрузите подходящий документ или уточните вопрос."  # noqa: RUF001 -- Russian text.
            if contains_cyrillic
            else (
                "Your materials do not provide enough information to answer. "
                "Upload a relevant document or clarify the question."
            )
        )
        return entities.GeneratedAnswer(answer_text=answer_text, sufficient_context=False, citation_identifiers=())

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
