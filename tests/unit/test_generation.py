import dataclasses
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
DEFAULT_OUTPUT_TOKENS: typing.Final = 2048
RETRY_OUTPUT_TOKENS: typing.Final = 4096
GENERATION_ATTEMPTS: typing.Final = 2


async def test_search_plan_uses_user_intent_without_copying_old_assistant_claims() -> None:
    model_requests: list[dict[str, typing.Any]] = []

    def handle_request(model_request: httpx.Request) -> httpx.Response:
        model_requests.append(json.loads(model_request.content))
        return build_response(
            response_value={
                "question_text": "All data structures",
                "scope_name": "overview",
                "search_queries": ["data structures", "types of structures"],
                "search_keywords": ["trees", "heaps"],
            }
        )

    saved_history = (
        build_message(content_text="What is a graph?"),
        dataclasses.replace(build_message(content_text="ONLY GRAPHS EXIST"), role_name="assistant"),
    )
    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        search_plan = await OllamaAnswerGenerator(
            http_client=http_client, text_tokenizer=CountingTokenizer()
        ).plan_search(question_text="List all data structures", conversation_history=saved_history)
    assert search_plan.scope_name == "overview"
    request_payload = json.loads(model_requests[0]["messages"][1]["content"])
    assert request_payload["chat_history"] == [{"role": "user", "content": "What is a graph?"}]


@pytest.mark.parametrize(
    ("evidence_quote", "canonical_name", "expected"),
    [
        ("Hash Tables", "Hash table", 1),
        ("Invented quote", "Hash table", 0),
        ("Hash Tables", "Fibonacci heap", 0),
    ],
)
async def test_extraction_requires_real_quote_and_named_item(
    evidence_quote: str, canonical_name: str, expected: int
) -> None:
    retrieved_chunk = build_source(content_text="Contents\n9 Hash Tables\n10 Balanced Binary Search Trees")

    def handle_request(_request: httpx.Request) -> httpx.Response:
        if review_response := mock_review_response(_request):
            return review_response
        return build_response(
            response_value={
                "evidence_items": [
                    {
                        "canonical_name": canonical_name,
                        "item_name": "Hash Tables" if canonical_name == "Hash table" else canonical_name,
                        "item_kind": "data_structure",
                        "description_text": "Named in contents",
                        "evidence_quote": evidence_quote,
                        "source_label": "S1",
                    }
                ]
            }
        )

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        evidence_items = await OllamaAnswerGenerator(
            http_client=http_client, text_tokenizer=CountingTokenizer()
        ).extract_evidence(question_text="List structures", context_chunks=(retrieved_chunk,), covered_names=())
    assert len(evidence_items) == expected


async def test_extraction_rejects_unknown_source_identifier() -> None:
    def handle_request(_request: httpx.Request) -> httpx.Response:
        return build_response(
            response_value={
                "evidence_items": [
                    {
                        "canonical_name": "Hash table",
                        "item_name": "Hash Tables",
                        "item_kind": "data_structure",
                        "description_text": "Named in contents",
                        "evidence_quote": "Hash Tables",
                        "source_label": "S9",
                    }
                ]
            }
        )

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        with pytest.raises(errors.DependencyUnavailableError) as raised_error:
            await OllamaAnswerGenerator(http_client=http_client, text_tokenizer=CountingTokenizer()).extract_evidence(
                question_text="List structures", context_chunks=(build_source(),), covered_names=()
            )
    assert raised_error.value.error_code == "unknown_answer_citation"


@pytest.mark.parametrize(
    ("source_name", "item_kind", "expected"),
    [
        ("Массивы", "data_structure", 1),
        ("LEDA", "software", 0),
        ("Suffix Tree Construction Algorithm", "data_structure", 0),
    ],
)
async def test_catalog_accepts_russian_original_names_and_excludes_software(
    source_name: str,
    item_kind: str,
    expected: int,
) -> None:
    def handle_request(_request: httpx.Request) -> httpx.Response:
        if review_response := mock_review_response(_request):
            return review_response
        return build_response(
            response_value={
                "evidence_items": [
                    {
                        "canonical_name": "Array",
                        "item_name": source_name,
                        "item_kind": item_kind,
                        "description_text": "",
                        "evidence_quote": f"{source_name} представлены в разделе",
                        "source_label": "S1",
                    }
                ]
            }
        )

    retrieved_chunk = build_source(content_text=f"{source_name} представлены в разделе")
    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        evidence_items = await OllamaAnswerGenerator(
            http_client=http_client, text_tokenizer=CountingTokenizer()
        ).extract_evidence(
            question_text="Перечисли все структуры данных", context_chunks=(retrieved_chunk,), covered_names=()
        )
    assert len(evidence_items) == expected


async def test_comparison_can_cite_more_than_six_supplied_sources() -> None:
    retrieved_chunks = tuple(build_source(content_text=f"Evidence {item_index}") for item_index in range(12))

    def handle_request(model_request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(model_request.content)
        evidence = json.loads(request_payload["messages"][1]["content"])["document_evidence"]
        assert len(evidence) == len(retrieved_chunks)
        return build_response(
            response_value={"answer_text": "Comparison [S12].", "sufficient_context": True, "source_labels": ["S12"]}
        )

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        generated_answer = await OllamaAnswerGenerator(
            http_client=http_client, text_tokenizer=CountingTokenizer()
        ).generate_answer(question_text="Compare subjects", conversation_history=(), context_chunks=retrieved_chunks)
    assert generated_answer.citation_identifiers == (retrieved_chunks[-1].chunk_record.chunk_identifier,)


async def test_extraction_uses_a_separate_semantic_review_before_publishing_items() -> None:
    model_requests: list[dict[str, typing.Any]] = []
    retrieved_chunk = build_source(content_text="Hash Tables store key-value pairs. A circle is a geometric shape.")

    def handle_request(model_request: httpx.Request) -> httpx.Response:
        request_payload = json.loads(model_request.content)
        model_requests.append(request_payload)
        if "same_concept" in request_payload["format"]["properties"]:
            return build_response(response_value={"same_concept": [True]})
        if "verdicts" in request_payload["format"]["properties"]:
            review_payload = json.loads(request_payload["messages"][1]["content"])
            assert "item_kind" not in review_payload["candidates"][0]
            return build_response(
                response_value={
                    "verdicts": [
                        {
                            "item_identifier": f"I{item_index}",
                            "category_name": "geometry" if original_name == "circle" else "structure",
                            "relevant": True,
                            "canonical_name": original_name,
                            "definition_quote": (
                                "A circle is a geometric shape."
                                if original_name == "circle"
                                else "Hash Tables store key-value pairs."
                            ),
                            "equivalent_to": None,
                        }
                        for item_index, original_name in enumerate(("Hash Tables", "circle"), 1)
                    ]
                }
            )
        return build_response(
            response_value={
                "evidence_items": [
                    {
                        "canonical_name": original_name,
                        "item_name": original_name,
                        "item_kind": "data_structure",
                        "description_text": "Unverified generated description",
                        "evidence_quote": original_name,
                        "source_label": "S1",
                    }
                    for original_name in ("Hash Tables", "circle")
                ]
            }
        )

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        reviewed_items = await OllamaAnswerGenerator(
            http_client=http_client, text_tokenizer=CountingTokenizer()
        ).extract_evidence(
            question_text="List data structures", context_chunks=(retrieved_chunk,), covered_names=("Хеш-таблицы",)
        )
    assert len(model_requests) == GENERATION_ATTEMPTS + 1
    assert tuple(evidence_item.source_name for evidence_item in reviewed_items) == ("Hash Tables",)
    assert reviewed_items[0].description_text == ""
    assert reviewed_items[0].canonical_name == "Хеш-таблицы"
    assert reviewed_items[0].chunk_identifier == retrieved_chunk.chunk_record.chunk_identifier


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


def build_response(*, response_value: dict[str, object] | str, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "qwen3.5:4b",
            "done": True,
            "done_reason": finish_reason,
            "message": {"content": response_value if isinstance(response_value, str) else json.dumps(response_value)},
        },
    )


def mock_review_response(model_request: httpx.Request) -> httpx.Response | None:
    request_payload = json.loads(model_request.content)
    if "verdicts" not in request_payload["format"]["properties"]:
        return None
    review_payload = json.loads(request_payload["messages"][1]["content"])
    return build_response(
        response_value={
            "verdicts": [
                {
                    "item_identifier": candidate["item_identifier"],
                    "category_name": "structure",
                    "relevant": True,
                    "canonical_name": candidate["original_name"],
                    "definition_quote": candidate["context"],
                    "equivalent_to": None,
                }
                for candidate in review_payload["candidates"]
            ]
        }
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


async def test_no_context_answers_from_model_knowledge_without_citations() -> None:
    captured_calls: typing.Final[list[str]] = []
    captured_prompts: typing.Final[list[str]] = []

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        captured_calls.append(request_value.url.path)
        captured_prompts.append(json.loads(request_value.content)["messages"][0]["content"])
        return build_response(response_value={"answer_text": "Hello! How can I help?"})

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        answer_generator = OllamaAnswerGenerator(http_client=http_client, text_tokenizer=CountingTokenizer())
        generated_answer = await answer_generator.generate_answer(
            question_text="Where?", conversation_history=(), context_chunks=()
        )
    assert not generated_answer.sufficient_context
    assert generated_answer.citation_identifiers == ()
    assert generated_answer.answer_text == "Hello! How can I help?"
    assert captured_calls == ["/api/chat"]
    assert "You are RAGent" in captured_prompts[0]
    assert "inside RAGumentation" in captured_prompts[0]
    assert "Ignore outdated names or capability claims in older chat history" in captured_prompts[0]


async def test_general_answer_cannot_invent_document_citations() -> None:
    def handle_request(_request_value: httpx.Request) -> httpx.Response:
        return build_response(response_value={"answer_text": "Fabricated [S1]"})

    async with httpx.AsyncClient(
        base_url="http://ollama.test",
        transport=httpx.MockTransport(handle_request),
    ) as http_client:
        answer_generator = OllamaAnswerGenerator(http_client=http_client, text_tokenizer=CountingTokenizer())
        with pytest.raises(errors.DependencyUnavailableError, match="invent document citations"):
            await answer_generator.generate_answer(question_text="Hello", conversation_history=(), context_chunks=())


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
    captured_calls: list[str] = []

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        captured_calls.append(request_value.url.path)
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
    assert raised_error.value.error_code == "model_response_truncated"
    assert len(captured_calls) == GENERATION_ATTEMPTS


@pytest.mark.parametrize("grounded_answer", [True, False])
async def test_truncated_json_is_regenerated_and_only_complete_answer_is_returned(grounded_answer: bool) -> None:
    captured_payloads: list[dict[str, typing.Any]] = []
    retrieved_source = build_source()

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request_value.content))
        if len(captured_payloads) == 1:
            return build_response(response_value='{"answer_text":"TRUNCATED', finish_reason="length")
        response_value: dict[str, object] = {
            "answer_text": "Complete answer [S1]." if grounded_answer else "Complete answer."
        }
        if grounded_answer:
            response_value.update(sufficient_context=True, source_labels=["S1"])
        return build_response(response_value=response_value)

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        generated_answer = await OllamaAnswerGenerator(
            http_client=http_client, text_tokenizer=CountingTokenizer()
        ).generate_answer(
            question_text="Explain this",
            conversation_history=(),
            context_chunks=(retrieved_source,) if grounded_answer else (),
        )

    assert [one_payload["options"]["num_predict"] for one_payload in captured_payloads] == [
        DEFAULT_OUTPUT_TOKENS,
        RETRY_OUTPUT_TOKENS,
    ]
    assert captured_payloads[0]["messages"] == captured_payloads[1]["messages"]
    assert captured_payloads[0]["format"] == captured_payloads[1]["format"]
    assert "TRUNCATED" not in generated_answer.answer_text
    assert generated_answer.citation_identifiers == (
        (retrieved_source.chunk_record.chunk_identifier,) if grounded_answer else ()
    )


async def test_length_retry_respects_remaining_context_and_still_validates_sources() -> None:
    captured_payloads: list[dict[str, typing.Any]] = []
    context_tokens = 5700
    reserve_tokens = 1024

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        captured_payloads.append(json.loads(request_value.content))
        if len(captured_payloads) == 1:
            return build_response(response_value='{"answer_text":"Partial', finish_reason="length")
        return build_response(
            response_value={
                "answer_text": "Fabricated source [S9].",
                "sufficient_context": True,
                "source_labels": ["S9"],
            }
        )

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        answer_generator = OllamaAnswerGenerator(
            http_client=http_client, text_tokenizer=CountingTokenizer(), context_tokens=context_tokens
        )
        with pytest.raises(errors.DependencyUnavailableError) as raised_error:
            await answer_generator.generate_answer(
                question_text="Explain this", conversation_history=(), context_chunks=(build_source(),)
            )

    assert raised_error.value.error_code == "unknown_answer_citation"
    assert len(captured_payloads) == GENERATION_ATTEMPTS
    retry_payload = captured_payloads[-1]
    prompt_tokens = sum(len(one_message["content"]) for one_message in retry_payload["messages"])
    assert DEFAULT_OUTPUT_TOKENS <= retry_payload["options"]["num_predict"] < RETRY_OUTPUT_TOKENS
    assert prompt_tokens + reserve_tokens + retry_payload["options"]["num_predict"] <= context_tokens


@pytest.mark.parametrize("finish_reason", ["load", "unknown", "stop", "length"])
async def test_unfinished_envelope_is_not_retried_or_published(finish_reason: str) -> None:
    captured_calls: list[str] = []

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        captured_calls.append(request_value.url.path)
        return httpx.Response(
            200, json={"model": "qwen3.5:4b", "done": False, "done_reason": finish_reason, "message": {"content": "{}"}}
        )

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        with pytest.raises(errors.DependencyUnavailableError) as raised_error:
            await OllamaAnswerGenerator(http_client=http_client, text_tokenizer=CountingTokenizer()).generate_answer(
                question_text="Hello", conversation_history=(), context_chunks=()
            )

    assert raised_error.value.error_code == "incomplete_model_response"
    assert captured_calls == ["/api/chat"]


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
            context_tokens=3700,
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
