import datetime
import json
import typing
import uuid

import httpx
import pytest

from study_rag.adapters.generation import OllamaAnswerGenerator
from study_rag.domain import entities, errors


MAXIMUM_HISTORY_MESSAGES: typing.Final = 8
HISTORY_TOKEN_BUDGET: typing.Final = 100


@typing.final
class CountingTokenizer:
    def count_tokens(self, *, content_text: str) -> int:
        return len(content_text)

    async def split_sections(
        self,
        *,
        tenant_identifier: uuid.UUID,
        document_identifier: uuid.UUID,
        embedding_profile: str,
        extracted_document: entities.ExtractedDocument,
        filename_text: str,
    ) -> tuple[entities.ChunkRecord, ...]:
        raise AssertionError(
            f"Unexpected chunk splitting for {tenant_identifier}/{document_identifier}, "
            f"profile {embedding_profile}, document {extracted_document}, filename {filename_text}."
        )


def build_source(*, content_text: str = "Photosynthesis takes place in chloroplasts.") -> entities.RetrievedChunk:
    return entities.RetrievedChunk(
        chunk_record=entities.ChunkRecord(
            chunk_identifier=uuid.uuid4(),
            document_identifier=uuid.uuid4(),
            tenant_identifier=uuid.uuid4(),
            ordinal_value=0,
            content_text=content_text,
            token_count=len(content_text),
            page_start=2,
            page_end=2,
        ),
        filename_text="biology.md",
        similarity_score=0.8,
    )


def build_message(*, content_text: str, turn_status: entities.TurnStatus = "completed") -> entities.MessageRecord:
    return entities.MessageRecord(
        message_identifier=uuid.uuid4(),
        chat_identifier=uuid.uuid4(),
        turn_identifier=uuid.uuid4(),
        role_name="user",
        content_text=content_text,
        citation_records=(),
        sufficient_context=None,
        turn_status=turn_status,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def build_response(*, response_value: dict[str, object], finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "qwen3.5:4b",
            "done": True,
            "done_reason": finish_reason,
            "message": {"content": json.dumps(response_value)},
        },
    )


async def test_grounded_answer_disables_thinking_and_returns_verified_citation() -> None:
    captured_payloads: typing.Final[list[dict[str, object]]] = []
    retrieved_source = build_source()

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        payload_value: object = json.loads(request_value.content)
        assert isinstance(payload_value, dict)
        captured_payloads.append(payload_value)
        return build_response(
            response_value={"answer_text": "In chloroplasts [S1].", "sufficient_context": True, "source_labels": ["S1"]}
        )

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        answer_generator = OllamaAnswerGenerator(http_client=http_client, text_tokenizer=CountingTokenizer())
        generated_answer = await answer_generator.generate_answer(
            question_text="Where does photosynthesis happen?",
            conversation_history=(),
            context_chunks=(retrieved_source,),
        )

    assert generated_answer.answer_text == "In chloroplasts [S1]."
    assert generated_answer.citation_identifiers == (retrieved_source.chunk_record.chunk_identifier,)
    assert generated_answer.sufficient_context is True
    assert captured_payloads[0]["think"] is False
    assert captured_payloads[0]["stream"] is False
    assert captured_payloads[0]["model"] == "qwen3.5:4b"
    assert isinstance(captured_payloads[0]["format"], dict)


async def test_no_context_refuses_without_calling_model() -> None:
    captured_calls: typing.Final[list[str]] = []

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        captured_calls.append(request_value.url.path)
        return httpx.Response(500)

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        answer_generator = OllamaAnswerGenerator(http_client=http_client, text_tokenizer=CountingTokenizer())
        generated_answer = await answer_generator.generate_answer(
            question_text="Where?", conversation_history=(), context_chunks=()
        )
    assert not generated_answer.sufficient_context
    assert generated_answer.citation_identifiers == ()
    assert captured_calls == []


@pytest.mark.parametrize(
    ("answer_text", "source_labels", "expected_answer", "expected_source_order"),
    [
        ("A documented answer.", ["S1"], "A documented answer. [S1]", (0,)),
        ("A documented answer [S1].", ["S1", "S2"], "A documented answer [S1]. [S2]", (0, 1)),
        ("A documented answer.", ["S2", "S1", "S2"], "A documented answer. [S2] [S1]", (1, 0)),
    ],
)
async def test_answer_adds_only_missing_validated_markers_once(
    answer_text: str,
    source_labels: list[str],
    expected_answer: str,
    expected_source_order: tuple[int, ...],
) -> None:
    captured_calls: typing.Final[list[str]] = []
    context_chunks: typing.Final = (build_source(), build_source(content_text="A second supporting excerpt."))

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        captured_calls.append(request_value.url.path)
        return build_response(
            response_value={"answer_text": answer_text, "sufficient_context": True, "source_labels": source_labels}
        )

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        answer_generator = OllamaAnswerGenerator(http_client=http_client, text_tokenizer=CountingTokenizer())
        generated_answer = await answer_generator.generate_answer(
            question_text="What is documented?", conversation_history=(), context_chunks=context_chunks
        )

    assert generated_answer.answer_text == expected_answer
    assert generated_answer.citation_identifiers == tuple(
        context_chunks[source_position].chunk_record.chunk_identifier for source_position in expected_source_order
    )
    assert generated_answer.sufficient_context is True
    assert captured_calls == ["/api/chat"]


@pytest.mark.parametrize(
    ("answer_text", "source_labels", "error_code"),
    [
        ("An answer [S7].", ["S1"], "unknown_answer_citation"),
        ("An answer.", ["S7"], "unknown_answer_citation"),
        ("An answer.", [], "unsupported_answer"),
    ],
)
async def test_answer_rejects_unknown_inline_or_declared_citations_and_no_evidence(
    answer_text: str, source_labels: list[str], error_code: str
) -> None:
    def handle_request(request_value: httpx.Request) -> httpx.Response:  # noqa: ARG001 -- transport boundary receives request.
        return build_response(
            response_value={"answer_text": answer_text, "sufficient_context": True, "source_labels": source_labels}
        )

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        answer_generator = OllamaAnswerGenerator(http_client=http_client, text_tokenizer=CountingTokenizer())
        with pytest.raises(errors.DependencyUnavailableError) as raised_error:
            await answer_generator.generate_answer(
                question_text="Where?", conversation_history=(), context_chunks=(build_source(),)
            )
    assert raised_error.value.error_code == error_code


async def test_length_finish_cannot_publish_partial_answer() -> None:
    def handle_request(request_value: httpx.Request) -> httpx.Response:  # noqa: ARG001 -- transport boundary receives request.
        return build_response(
            response_value={"answer_text": "Partial [S1].", "sufficient_context": True, "source_labels": ["S1"]},
            finish_reason="length",
        )

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        answer_generator = OllamaAnswerGenerator(http_client=http_client, text_tokenizer=CountingTokenizer())
        with pytest.raises(errors.DependencyUnavailableError) as raised_error:
            await answer_generator.generate_answer(
                question_text="Where?", conversation_history=(), context_chunks=(build_source(),)
            )
    assert raised_error.value.error_code == "incomplete_model_response"


async def test_rewrite_bounds_history_and_excludes_uncompleted_messages() -> None:
    captured_payloads: typing.Final[list[dict[str, object]]] = []

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        payload_value: object = json.loads(request_value.content)
        assert isinstance(payload_value, dict)
        captured_payloads.append(payload_value)
        return build_response(response_value={"question_text": "Where does photosynthesis occur?"})

    history_messages = (
        *(build_message(content_text=f"History {message_number}") for message_number in range(12)),
        build_message(content_text="A failed turn", turn_status="failed"),
    )
    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        answer_generator = OllamaAnswerGenerator(
            http_client=http_client, text_tokenizer=CountingTokenizer(), history_tokens=100
        )
        rewritten_query = await answer_generator.rewrite_question(
            question_text="Where does it occur?", conversation_history=history_messages
        )

    assert rewritten_query == "Where does photosynthesis occur?"
    request_messages = captured_payloads[0]["messages"]
    assert isinstance(request_messages, list)
    user_payload: object = json.loads(request_messages[1]["content"])
    assert isinstance(user_payload, dict)
    chat_history = user_payload["chat_history"]
    assert isinstance(chat_history, list)
    assert 0 < len(chat_history) <= MAXIMUM_HISTORY_MESSAGES
    assert sum(len(history_entry["content"]) + 16 for history_entry in chat_history) <= HISTORY_TOKEN_BUDGET
    assert all(history_entry["content"] != "A failed turn" for history_entry in chat_history)


async def test_generation_drops_complete_sources_to_fit_budget() -> None:
    captured_evidence: typing.Final[list[dict[str, object]]] = []
    first_source = build_source(content_text="Evidence " + "a" * 900)
    second_source = build_source(content_text="Second " + "b" * 900)

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        request_payload: object = json.loads(request_value.content)
        assert isinstance(request_payload, dict)
        request_messages = request_payload["messages"]
        assert isinstance(request_messages, list)
        user_payload: object = json.loads(request_messages[1]["content"])
        assert isinstance(user_payload, dict)
        evidence_sources = user_payload["document_evidence"]
        assert isinstance(evidence_sources, list)
        captured_evidence.extend(evidence_sources)
        return build_response(
            response_value={"answer_text": "Evidence [S1].", "sufficient_context": True, "source_labels": ["S1"]}
        )

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        answer_generator = OllamaAnswerGenerator(
            http_client=http_client,
            text_tokenizer=CountingTokenizer(),
            context_tokens=2300,
            output_tokens=100,
            prompt_reserve_tokens=100,
        )
        generated_answer = await answer_generator.generate_answer(
            question_text="Which evidence?", conversation_history=(), context_chunks=(first_source, second_source)
        )

    assert len(captured_evidence) == 1
    assert captured_evidence[0]["source_label"] == "S1"
    assert captured_evidence[0]["content_text"] == first_source.chunk_record.content_text
    assert generated_answer.citation_identifiers == (first_source.chunk_record.chunk_identifier,)
