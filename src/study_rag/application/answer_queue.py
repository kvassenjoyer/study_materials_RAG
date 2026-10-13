"""Durable FIFO answers. HTTP/SSE subscribers never own model execution."""

import asyncio
import dataclasses
import logging
import typing
import uuid

from study_rag.application.conversations import ConversationService, maximum_question_characters
from study_rag.domain import entities, errors, ports


application_logger: typing.Final = logging.getLogger(__name__)


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class AnswerQueue:
    conversation_repository: ports.ConversationRepository
    conversation_service: ConversationService
    poll_seconds: float = 0.5

    async def submit_question(
        self,
        *,
        tenant_identity: entities.TenantIdentity,
        chat_identifier: uuid.UUID,
        client_request_identifier: uuid.UUID,
        question_text: str,
    ) -> entities.TurnRecord:
        normalized_question = question_text.strip()
        if not normalized_question or len(normalized_question) > maximum_question_characters:
            raise errors.InvalidInputError(
                error_code="invalid_question", message_text="Question must contain 1 to 4000 characters"
            )
        return await self.conversation_repository.begin_turn(
            tenant_identifier=tenant_identity.user_identifier,
            chat_identifier=chat_identifier,
            client_request_identifier=client_request_identifier,
            question_text=normalized_question,
            lease_seconds=self.conversation_service.turn_lease_seconds,
            queue_request=True,
        )

    async def answer_question(
        self,
        *,
        tenant_identity: entities.TenantIdentity,
        chat_identifier: uuid.UUID,
        client_request_identifier: uuid.UUID,
        question_text: str,
    ) -> entities.AnswerRecord:
        turn_record = await self.submit_question(
            tenant_identity=tenant_identity,
            chat_identifier=chat_identifier,
            client_request_identifier=client_request_identifier,
            question_text=question_text,
        )
        while turn_record.status_name in {"queued", "pending"}:
            await asyncio.sleep(self.poll_seconds)
            turn_record = await self.conversation_repository.fetch_turn(
                tenant_identifier=tenant_identity.user_identifier, turn_identifier=turn_record.turn_identifier
            )
        if turn_record.status_name == "failed" or turn_record.assistant_message is None:
            raise errors.DependencyUnavailableError(
                error_code=turn_record.user_message.turn_error_code or "answer_generation_failed",
                message_text="Answer request failed",
            )
        return entities.AnswerRecord(
            turn_identifier=turn_record.turn_identifier,
            chat_identifier=chat_identifier,
            user_message=turn_record.user_message,
            assistant_message=turn_record.assistant_message,
        )

    async def run_worker(self) -> None:
        while True:
            try:
                claimed_answer = await self.conversation_repository.claim_queued_turn(
                    lease_seconds=self.conversation_service.turn_lease_seconds
                )
                if claimed_answer is not None:
                    await self._execute_answer(claimed_answer=claimed_answer)
                    continue
            except errors.DomainError:
                application_logger.exception("Answer queue operation failed")
            await asyncio.sleep(self.poll_seconds)

    async def _execute_answer(self, *, claimed_answer: entities.ClaimedAnswer) -> None:
        answer_task = asyncio.create_task(
            self.conversation_service.execute_reserved_turn(
                tenant_identity=claimed_answer.tenant_identity, turn_record=claimed_answer.turn_record
            ),
            name="study-rag-answer",
        )
        try:
            while not answer_task.done():
                await asyncio.wait({answer_task}, timeout=self.poll_seconds)
                if answer_task.done():
                    break
                try:
                    latest_turn = await self.conversation_repository.fetch_turn(
                        tenant_identifier=claimed_answer.tenant_identity.user_identifier,
                        turn_identifier=claimed_answer.turn_record.turn_identifier,
                    )
                except errors.ResourceNotFoundError:
                    break
                if (
                    latest_turn.status_name != "pending"
                    or latest_turn.lease_identifier != claimed_answer.turn_record.lease_identifier
                ):
                    break
            if answer_task.done():
                try:
                    answer_task.result()
                except errors.DomainError:
                    application_logger.info("Answer request %s failed", claimed_answer.turn_record.turn_identifier)
        finally:
            # Stop/deletion/shutdown cancel the real model call, then release the worker.
            if not answer_task.done():
                answer_task.cancel()
            await asyncio.gather(answer_task, return_exceptions=True)
