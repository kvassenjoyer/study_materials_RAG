"""Authenticated AG-UI transport around the existing, persisted RAG scenario."""

import dataclasses
import logging
import typing

import fastapi
import pydantic_ai
import pydantic_ai.messages
from ag_ui import core as agui_core
from pydantic_ai.models import function as function_model
from pydantic_ai.ui import SSE_CONTENT_TYPE
from pydantic_ai.ui.ag_ui import AGUIAdapter, AGUIEventStream
from starlette.responses import StreamingResponse

from study_rag.api import dependencies, schemas
from study_rag.application.conversations import ConversationService
from study_rag.domain import entities, errors


api_router: typing.Final = fastapi.APIRouter(prefix="/rpc/study-rag")
maximum_request_bytes: typing.Final = 64 * 1024
maximum_history_messages: typing.Final = 100
application_logger: typing.Final = logging.getLogger(__name__)


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class ValidatedAgentRequest:
    run_input: agui_core.RunAgentInput
    question_request: schemas.QuestionRequest


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class ConversationModelBridge:
    conversation_service: ConversationService
    tenant_identity: entities.TenantIdentity
    question_request: schemas.QuestionRequest

    async def stream_answer(
        self,
        _model_messages: list[pydantic_ai.messages.ModelMessage],
        _agent_information: function_model.AgentInfo,
    ) -> typing.AsyncIterator[str]:
        answer_record: typing.Final = await self.conversation_service.answer_question(
            tenant_identity=self.tenant_identity,
            chat_identifier=self.question_request.chat_identifier,
            client_request_identifier=self.question_request.client_request_identifier,
            question_text=self.question_request.question_text,
        )
        answer_response: typing.Final = schemas.AnswerResponse.model_validate(answer_record)
        # Emit only the committed answer; sources and persistent IDs are hydrated through REST.
        yield answer_response.assistant_message.content_text


@typing.final
class SafeAGUIEventStream(AGUIEventStream[None, str]):
    async def on_error(self, error: Exception) -> typing.AsyncIterator[agui_core.BaseEvent]:  # noqa: COP006 -- Framework hook.
        safe_error = error
        if not isinstance(safe_error, errors.DomainError):
            application_logger.error("Unexpected AG-UI run failure", exc_info=error)
            safe_error = errors.DependencyUnavailableError(
                error_code="agent_run_failed", message_text="Agent run failed; retry this request"
            )
        async for error_event in super().on_error(safe_error):
            if isinstance(error_event, agui_core.RunErrorEvent):
                yield agui_core.RunErrorEvent(
                    code=safe_error.error_code,
                    message=safe_error.message_text,
                    timestamp=error_event.timestamp,
                )
            else:
                yield error_event


@typing.final
class StudyAGUIAdapter(AGUIAdapter[None, str]):
    def build_event_stream(self) -> SafeAGUIEventStream:
        return SafeAGUIEventStream(self.run_input, accept=self.accept, ag_ui_version=self.ag_ui_version)


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class AgentRunEndpoint:
    conversation_service: ConversationService

    async def build_response(
        self, *, http_request: fastapi.Request, tenant_identity: entities.TenantIdentity
    ) -> StreamingResponse:
        self._validate_content_type(http_request=http_request)
        request_bytes: typing.Final = await self._read_bounded_body(http_request=http_request)
        validated_request: typing.Final = self._validate_run_input(request_bytes=request_bytes)
        await self.conversation_service.fetch_chat(
            tenant_identity=tenant_identity,
            chat_identifier=validated_request.question_request.chat_identifier,
        )
        model_bridge: typing.Final = ConversationModelBridge(
            conversation_service=self.conversation_service,
            tenant_identity=tenant_identity,
            question_request=validated_request.question_request,
        )
        agent_model: typing.Final = function_model.FunctionModel(
            stream_function=model_bridge.stream_answer, model_name="study-rag-conversation"
        )
        agent_instance: typing.Final = pydantic_ai.Agent(agent_model, output_type=str, retries=0)
        agent_adapter: typing.Final = StudyAGUIAdapter(
            agent=agent_instance, run_input=validated_request.run_input, accept=SSE_CONTENT_TYPE
        )
        stream_response: typing.Final = agent_adapter.streaming_response(agent_adapter.run_stream())
        stream_response.headers["Cache-Control"] = "no-cache"
        stream_response.headers["X-Accel-Buffering"] = "no"
        return stream_response

    def _validate_content_type(self, *, http_request: fastapi.Request) -> None:
        content_type: typing.Final = http_request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise errors.InvalidInputError(
                error_code="unsupported_agent_content_type", message_text="AG-UI requests require application/json"
            )

    async def _read_bounded_body(self, *, http_request: fastapi.Request) -> bytes:
        content_buffer: typing.Final = bytearray()
        async for content_piece in http_request.stream():
            if len(content_buffer) + len(content_piece) > maximum_request_bytes:
                raise errors.InvalidInputError(
                    error_code="agent_request_too_large", message_text="AG-UI request exceeds the 64 KiB limit"
                )
            content_buffer.extend(content_piece)
        return bytes(content_buffer)

    def _validate_run_input(self, *, request_bytes: bytes) -> ValidatedAgentRequest:
        try:
            run_input: typing.Final = agui_core.RunAgentInput.model_validate_json(request_bytes)
            self._validate_supported_features(run_input=run_input)
            question_request: typing.Final = self._validate_question_request(run_input=run_input)
        except (ValueError, TypeError) as validation_error:
            raise errors.InvalidInputError(
                error_code="invalid_agent_request", message_text="Request fields do not match the AG-UI schema"
            ) from validation_error
        # Only this question reaches Pydantic AI. PostgreSQL history remains authoritative.
        sanitized_input: typing.Final = agui_core.RunAgentInput(
            thread_id=run_input.thread_id,
            run_id=run_input.run_id,
            messages=[agui_core.UserMessage(id=run_input.messages[-1].id, content=question_request.question_text)],
            tools=[],
            context=[],
            state={},
            forwarded_props={},
        )
        return ValidatedAgentRequest(run_input=sanitized_input, question_request=question_request)

    def _validate_question_request(self, *, run_input: agui_core.RunAgentInput) -> schemas.QuestionRequest:
        if not run_input.messages or len(run_input.messages) > maximum_history_messages:
            raise ValueError("AG-UI requests require between 1 and 100 messages")
        latest_message: typing.Final = run_input.messages[-1]
        if not isinstance(latest_message, agui_core.UserMessage) or not isinstance(latest_message.content, str):
            raise TypeError("The latest AG-UI message must contain a textual user question")
        if not latest_message.id.strip():
            raise ValueError("AG-UI user message ID is required")
        return schemas.QuestionRequest.model_validate(
            {
                "chat_identifier": run_input.thread_id,
                "client_request_identifier": run_input.run_id,
                "question_text": latest_message.content.strip(),
            }
        )

    def _validate_supported_features(self, *, run_input: agui_core.RunAgentInput) -> None:
        # JS AG-UI 1.0 clients add this marker; the pinned Python SDK retains it as an extra.
        supported_protocol: typing.Final = run_input.model_extra in (None, {}, {"protocolVersion": "1.0"})
        if (
            not supported_protocol
            or run_input.tools
            or run_input.context
            or run_input.state not in ({}, None)
            or run_input.forwarded_props not in ({}, None)
            or run_input.resume
            or run_input.parent_run_id is not None
        ):
            raise ValueError("Frontend tools, shared state, context and resumed runs are not supported")


@api_router.post("/agent/", tags=["agent"], response_class=StreamingResponse)
async def run_agent(
    *,
    http_request: fastapi.Request,
    tenant_identity: dependencies.CurrentIdentity,
    application_resources: dependencies.CurrentResources,
) -> StreamingResponse:
    return await AgentRunEndpoint(conversation_service=application_resources.conversation_service).build_response(
        http_request=http_request, tenant_identity=tenant_identity
    )
