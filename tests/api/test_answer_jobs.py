"""Background answer endpoints preserve the authenticated tenant boundary."""

import dataclasses
import datetime
import http
import typing
import unittest.mock
import uuid

import fastapi
import httpx
import pytest

from study_rag.api import dependencies, routes
from study_rag.application.answer_queue import AnswerQueue
from study_rag.application.authentication import AuthenticationService
from study_rag.application.conversations import ConversationService
from study_rag.bootstrap import ApplicationResources
from study_rag.domain import entities, errors, ports
from study_rag.main import handle_domain_error


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class AnswerApiScenario:
    http_client: httpx.AsyncClient
    tenant_identity: entities.TenantIdentity
    turn_record: entities.TurnRecord
    chat_record: entities.ChatRecord
    answer_queue: unittest.mock.Mock
    conversation_service: unittest.mock.Mock
    conversation_repository: unittest.mock.AsyncMock


@pytest.fixture
async def answer_scenario() -> typing.AsyncIterator[AnswerApiScenario]:
    tenant_identity = entities.TenantIdentity(user_identifier=uuid.uuid4(), login_name="student@example.com")
    timestamp_value = datetime.datetime.now(datetime.UTC)
    chat_record = entities.ChatRecord(
        chat_identifier=uuid.uuid4(),
        tenant_identifier=tenant_identity.user_identifier,
        project_identifier=uuid.uuid4(),
        title_text="Question",
        created_at=timestamp_value,
        updated_at=timestamp_value,
    )
    turn_identifier = uuid.uuid4()
    turn_record = entities.TurnRecord(
        turn_identifier=turn_identifier,
        chat_identifier=chat_record.chat_identifier,
        client_request_identifier=uuid.uuid4(),
        status_name="queued",
        lease_identifier=None,
        assistant_message=None,
        user_message=entities.MessageRecord(
            message_identifier=uuid.uuid4(),
            chat_identifier=chat_record.chat_identifier,
            turn_identifier=turn_identifier,
            role_name="user",
            content_text="Question",
            citation_records=(),
            sufficient_context=None,
            turn_status="queued",
            created_at=timestamp_value,
        ),
    )
    conversation_repository = unittest.mock.AsyncMock(spec=ports.ConversationRepository)
    conversation_repository.list_active_turns.return_value = (
        entities.AnswerJobRecord(
            turn_identifier=turn_identifier,
            chat_identifier=chat_record.chat_identifier,
            project_identifier=chat_record.project_identifier,
            status_name="queued",
        ),
    )
    answer_queue = unittest.mock.Mock(spec=AnswerQueue)
    answer_queue.conversation_repository = conversation_repository
    answer_queue.submit_question.return_value = turn_record
    conversation_service = unittest.mock.Mock(spec=ConversationService)
    conversation_service.fetch_chat.return_value = chat_record
    authentication_service = unittest.mock.Mock(spec=AuthenticationService)
    authentication_service.authenticate_token.return_value = tenant_identity
    resource_bundle = unittest.mock.Mock(spec=ApplicationResources)
    resource_bundle.authentication_service = authentication_service
    resource_bundle.conversation_service = conversation_service
    resource_bundle.answer_queue = answer_queue
    application = fastapi.FastAPI()
    application.include_router(routes.api_router)
    application.add_exception_handler(errors.DomainError, handle_domain_error)

    def provide_resources() -> ApplicationResources:
        return resource_bundle

    application.dependency_overrides[dependencies.get_resources] = provide_resources
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://fixture"
    ) as http_client:
        yield AnswerApiScenario(
            http_client=http_client,
            tenant_identity=tenant_identity,
            turn_record=turn_record,
            chat_record=chat_record,
            answer_queue=answer_queue,
            conversation_service=conversation_service,
            conversation_repository=conversation_repository,
        )


async def test_accept_answer_returns_before_generation(answer_scenario: AnswerApiScenario) -> None:
    response_value = await answer_scenario.http_client.post(
        "/rest/study-rag/answer-jobs/",
        headers={"Authorization": "Bearer fixture"},
        json={
            "chat_identifier": str(answer_scenario.chat_record.chat_identifier),
            "client_request_identifier": str(answer_scenario.turn_record.client_request_identifier),
            "question_text": "Question",
        },
    )
    assert response_value.status_code == http.HTTPStatus.ACCEPTED
    assert response_value.json() == {
        "turn_identifier": str(answer_scenario.turn_record.turn_identifier),
        "chat_identifier": str(answer_scenario.chat_record.chat_identifier),
        "project_identifier": str(answer_scenario.chat_record.project_identifier),
        "status_name": "queued",
    }
    answer_scenario.answer_queue.submit_question.assert_awaited_once_with(
        tenant_identity=answer_scenario.tenant_identity,
        chat_identifier=answer_scenario.chat_record.chat_identifier,
        client_request_identifier=answer_scenario.turn_record.client_request_identifier,
        question_text="Question",
    )
    answer_scenario.conversation_service.answer_question.assert_not_awaited()
    answer_scenario.answer_queue.answer_question.assert_not_awaited()


async def test_active_answer_list_is_scoped_to_authenticated_user(answer_scenario: AnswerApiScenario) -> None:
    response_value = await answer_scenario.http_client.get(
        "/rest/study-rag/answer-jobs/", headers={"Authorization": "Bearer fixture"}
    )
    assert response_value.status_code == http.HTTPStatus.OK
    assert response_value.json()[0]["turn_identifier"] == str(answer_scenario.turn_record.turn_identifier)
    answer_scenario.conversation_repository.list_active_turns.assert_awaited_once_with(
        tenant_identifier=answer_scenario.tenant_identity.user_identifier
    )


async def test_stop_passes_owner_and_turn_id_to_repository(answer_scenario: AnswerApiScenario) -> None:
    response_value = await answer_scenario.http_client.post(
        f"/rest/study-rag/answer-jobs/{answer_scenario.turn_record.turn_identifier}/stop/",
        headers={"Authorization": "Bearer fixture"},
    )
    assert response_value.status_code == http.HTTPStatus.NO_CONTENT
    assert not response_value.content
    answer_scenario.conversation_repository.stop_turn.assert_awaited_once_with(
        tenant_identifier=answer_scenario.tenant_identity.user_identifier,
        turn_identifier=answer_scenario.turn_record.turn_identifier,
    )


async def test_stop_unknown_or_foreign_turn_returns_404(answer_scenario: AnswerApiScenario) -> None:
    answer_scenario.conversation_repository.stop_turn.side_effect = errors.ResourceNotFoundError(
        error_code="turn_not_found", message_text="Answer request not found"
    )
    response_value = await answer_scenario.http_client.post(
        f"/rest/study-rag/answer-jobs/{uuid.uuid4()}/stop/", headers={"Authorization": "Bearer fixture"}
    )
    assert response_value.status_code == http.HTTPStatus.NOT_FOUND
    assert response_value.json()["error_code"] == "turn_not_found"


async def test_synchronous_answers_use_same_queue(answer_scenario: AnswerApiScenario) -> None:
    saved_user = dataclasses.replace(answer_scenario.turn_record.user_message, turn_status="completed")
    saved_assistant = dataclasses.replace(
        saved_user, message_identifier=uuid.uuid4(), role_name="assistant", content_text="Answer"
    )
    answer_scenario.answer_queue.answer_question.return_value = entities.AnswerRecord(
        turn_identifier=answer_scenario.turn_record.turn_identifier,
        chat_identifier=answer_scenario.chat_record.chat_identifier,
        user_message=saved_user,
        assistant_message=saved_assistant,
    )
    response_value = await answer_scenario.http_client.post(
        "/rest/study-rag/messages/",
        headers={"Authorization": "Bearer fixture"},
        json={
            "chat_identifier": str(answer_scenario.chat_record.chat_identifier),
            "client_request_identifier": str(answer_scenario.turn_record.client_request_identifier),
            "question_text": "Question",
        },
    )
    assert response_value.status_code == http.HTTPStatus.OK
    assert response_value.json()["assistant_message"]["content_text"] == "Answer"
    answer_scenario.answer_queue.answer_question.assert_awaited_once()
    answer_scenario.conversation_service.answer_question.assert_not_awaited()
