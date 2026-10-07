import json
import math
import typing
import uuid

import httpx
import pytest

from study_rag.adapters.embeddings import HuggingFaceEmbeddingProvider, OllamaEmbeddingProvider
from study_rag.domain import entities, errors


TEST_VECTOR_DIMENSIONS: typing.Final = 128
ENCODING_BATCH_SIZE: typing.Final = 16
TRANSIENT_ATTEMPT_COUNT: typing.Final = 3


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


def build_chunk(*, content_text: str = "Chloroplasts perform photosynthesis.") -> entities.ChunkRecord:
    return entities.ChunkRecord(
        chunk_identifier=uuid.uuid4(),
        document_identifier=uuid.uuid4(),
        tenant_identifier=uuid.uuid4(),
        ordinal_value=0,
        content_text=content_text,
        token_count=len(content_text),
        page_start=1,
        page_end=1,
    )


async def test_ollama_manual_prefix_and_explicit_no_truncation() -> None:
    captured_inputs: typing.Final[list[dict[str, object]]] = []

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        payload_value: object = json.loads(request_value.content)
        assert isinstance(payload_value, dict)
        if request_value.url.path == "/api/show":
            return httpx.Response(
                200, json={"capabilities": ["embedding"], "model_info": {"gemma3.embedding_length": 768}}
            )
        captured_inputs.append(payload_value)
        assert isinstance(payload_value["input"], list)
        return httpx.Response(
            200, json={"model": "embeddinggemma:300m", "embeddings": [[2.0] * 128 for _ in payload_value["input"]]}
        )

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        embedding_provider = OllamaEmbeddingProvider(
            http_client=http_client,
            text_tokenizer=CountingTokenizer(),
            embedding_profile="ollama-profile",
            vector_dimensions=128,
        )
        await embedding_provider.initialize_provider()
        document_vectors = await embedding_provider.embed_documents(
            chunk_records=(build_chunk(),), filename_text="notes | lesson.md"
        )
        question_vector = await embedding_provider.embed_question(question_text="Where does photosynthesis happen?")

    assert captured_inputs[0]["input"] == ["title: notes lesson.md | text: Chloroplasts perform photosynthesis."]
    assert captured_inputs[1]["input"] == ["task: search result | query: Where does photosynthesis happen?"]
    assert all(
        payload_value["truncate"] is False and payload_value["dimensions"] == TEST_VECTOR_DIMENSIONS
        for payload_value in captured_inputs
    )
    assert len(question_vector) == TEST_VECTOR_DIMENSIONS
    assert math.hypot(*document_vectors[0]) == pytest.approx(1.0)
    assert math.hypot(*question_vector) == pytest.approx(1.0)


@pytest.mark.parametrize("raw_vectors", [[[0.0] * 128], [[1.0] * 127], [[float("nan")] * 128], []])
async def test_ollama_rejects_invalid_vectors(raw_vectors: list[list[float]]) -> None:
    def handle_request(request_value: httpx.Request) -> httpx.Response:
        if request_value.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["embedding"], "model_info": {"embedding_length": 768}})
        if any(math.isnan(component) for raw_vector in raw_vectors for component in raw_vector):
            return httpx.Response(200, content=b'{"model":"embeddinggemma:300m","embeddings":[[NaN]]}')
        return httpx.Response(200, json={"model": "embeddinggemma:300m", "embeddings": raw_vectors})

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        embedding_provider = OllamaEmbeddingProvider(
            http_client=http_client,
            text_tokenizer=CountingTokenizer(),
            embedding_profile="ollama-profile",
            vector_dimensions=128,
        )
        await embedding_provider.initialize_provider()
        with pytest.raises(errors.DependencyUnavailableError) as raised_error:
            await embedding_provider.embed_question(question_text="A question")
    assert raised_error.value.error_code == "invalid_embedding_vector"


async def test_ollama_rejects_overlong_input_before_inference() -> None:
    inference_calls: typing.Final[list[str]] = []

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        inference_calls.append(request_value.url.path)
        return httpx.Response(200, json={"capabilities": ["embedding"], "model_info": {"embedding_length": 768}})

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        embedding_provider = OllamaEmbeddingProvider(
            http_client=http_client, text_tokenizer=CountingTokenizer(), embedding_profile="ollama-profile"
        )
        await embedding_provider.initialize_provider()
        with pytest.raises(errors.InvalidInputError):
            await embedding_provider.embed_documents(
                chunk_records=(build_chunk(content_text="a" * 1537),), filename_text="notes"
            )
    assert inference_calls == ["/api/show"]


@typing.final
class OriginalTokenizer:
    def __init__(self, *, token_count: int = 128) -> None:
        self._token_count: typing.Final = token_count

    def encode(self, text: str, *, add_special_tokens: bool, truncation: bool) -> list[int]:  # noqa: COP007, COP006 -- third-party tokenizer signature.
        assert text.startswith(("title: ", "task: search result | query: "))
        assert add_special_tokens is True
        assert truncation is False
        return [1] * self._token_count


@typing.final
class OriginalMatrix:
    def __init__(self, *, vector_rows: list[list[float]]) -> None:
        self._vector_rows: typing.Final = vector_rows

    def tolist(self) -> list[list[float]]:  # noqa: COP007 -- third-party NumPy matrix signature.
        return self._vector_rows


@typing.final
class OriginalEncoder:
    def __init__(self, *, token_count: int = 128) -> None:
        self.tokenizer: typing.Final = OriginalTokenizer(token_count=token_count)
        self.captured_inputs: typing.Final[list[str]] = []

    def encode(  # noqa: COP007 -- third-party Sentence Transformers signature.
        self,
        sentences: list[str],
        *,
        prompt: str,  # noqa: COP006 -- Sentence Transformers argument.
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
        batch_size: int,  # noqa: COP006 -- third-party arguments cannot be renamed.
    ) -> OriginalMatrix:
        assert prompt == ""
        assert normalize_embeddings is False
        assert convert_to_numpy is True
        assert show_progress_bar is False
        assert batch_size == ENCODING_BATCH_SIZE
        self.captured_inputs.extend(sentences)
        return OriginalMatrix(vector_rows=[[2.0] * 768 for _ in sentences])


async def test_original_hf_prefix_is_applied_once_and_mrl_normalized() -> None:
    sentence_encoder = OriginalEncoder()
    embedding_provider = HuggingFaceEmbeddingProvider(
        text_tokenizer=CountingTokenizer(),
        embedding_profile="original-hf-profile",
        vector_dimensions=128,
        encoder_loader=lambda: sentence_encoder,
    )
    assert not await embedding_provider.check_readiness()
    await embedding_provider.initialize_provider()
    await embedding_provider.embed_documents(chunk_records=(build_chunk(),), filename_text="biology.md")
    question_vector = await embedding_provider.embed_question(question_text="Where?")
    await embedding_provider.close_provider()

    assert sentence_encoder.captured_inputs == [
        "title: biology.md | text: Chloroplasts perform photosynthesis.",
        "task: search result | query: Where?",
    ]
    assert len(question_vector) == TEST_VECTOR_DIMENSIONS
    assert math.hypot(*question_vector) == pytest.approx(1.0)
    assert not await embedding_provider.check_readiness()


async def test_original_hf_exact_tokenizer_guard_prevents_silent_truncation() -> None:
    sentence_encoder = OriginalEncoder(token_count=2049)
    embedding_provider = HuggingFaceEmbeddingProvider(
        text_tokenizer=CountingTokenizer(), embedding_profile="hf-profile", encoder_loader=lambda: sentence_encoder
    )
    await embedding_provider.initialize_provider()

    with pytest.raises(errors.InvalidInputError) as raised_error:
        await embedding_provider.embed_question(question_text="Where?")
    assert raised_error.value.error_code == "embedding_input_too_long"
    assert sentence_encoder.captured_inputs == []


async def test_transient_status_retries_but_input_rejection_does_not() -> None:
    from study_rag.adapters.model_support import OllamaHttpTransport  # noqa: PLC0415 -- local test-only subject.

    captured_calls: typing.Final[list[str]] = []

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        captured_calls.append(request_value.url.path)
        if request_value.url.path == "/transient" and captured_calls.count("/transient") < TRANSIENT_ATTEMPT_COUNT:
            return httpx.Response(503, json={"error": "busy"})
        if request_value.url.path == "/rejected":
            return httpx.Response(400, json={"error": "input too long"})
        return httpx.Response(200, json={"ready": True})

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        http_transport = OllamaHttpTransport(http_client=http_client)
        assert await http_transport.post_payload(endpoint_path="/transient", payload_value={}) == {"ready": True}
        with pytest.raises(errors.DependencyUnavailableError) as raised_error:
            await http_transport.post_payload(endpoint_path="/rejected", payload_value={})
    assert raised_error.value.error_code == "model_request_rejected"
    assert captured_calls == ["/transient", "/transient", "/transient", "/rejected"]


@pytest.mark.parametrize(
    ("device_name", "mps_available", "cuda_available", "expected_device"),
    [
        ("auto", True, False, "mps"),
        ("auto", False, True, "cuda"),
        ("auto", False, False, "cpu"),
        ("auto", True, True, "mps"),
        ("cpu", True, True, "cpu"),
        ("mps", True, False, "mps"),
        ("cuda", False, True, "cuda"),
    ],
)
def test_hf_device_resolves_explicitly_without_constructor_io(
    device_name: str,
    mps_available: bool,
    cuda_available: bool,
    expected_device: str,
) -> None:
    embedding_provider = HuggingFaceEmbeddingProvider(
        text_tokenizer=CountingTokenizer(), embedding_profile="hf-profile", device_name=device_name
    )
    resolved_device = embedding_provider._select_device(mps_available=mps_available, cuda_available=cuda_available)  # noqa: SLF001 -- pure policy boundary, no optional Torch installation required.
    assert resolved_device == expected_device


@pytest.mark.parametrize("device_name", ["mps", "cuda"])
def test_hf_unavailable_explicit_device_has_clear_domain_error(device_name: str) -> None:
    embedding_provider = HuggingFaceEmbeddingProvider(
        text_tokenizer=CountingTokenizer(), embedding_profile="hf-profile", device_name=device_name
    )
    with pytest.raises(errors.DependencyUnavailableError) as raised_error:
        embedding_provider._select_device(mps_available=False, cuda_available=False)  # noqa: SLF001 -- pure device policy boundary.
    assert raised_error.value.error_code == "hf_device_unavailable"
