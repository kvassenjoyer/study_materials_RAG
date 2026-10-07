"""Explicit Ollama and original HF EmbeddingGemma backends with separate profiles."""

import asyncio
import pathlib
import typing

import httpx
import pydantic

from study_rag.adapters.model_support import EmbeddingInputFormatter, EmbeddingVectorValidator, OllamaHttpTransport
from study_rag.domain import entities, errors, ports


EMBEDDING_CONTEXT_TOKENS: typing.Final = 2048
EMBEDDING_NATIVE_DIMENSIONS: typing.Final = 768
MAXIMUM_FORMATTED_BYTES: typing.Final = 1900


@typing.final
class _EmbeddingResponse(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(strict=True, extra="ignore", allow_inf_nan=False)
    model: str
    embeddings: list[list[float]]


@typing.final
class _ModelDescription(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(strict=True, extra="ignore")
    capabilities: list[str]
    model_info: dict[str, object]


class SentenceTokenizer(typing.Protocol):
    def encode(self, text: str, *, add_special_tokens: bool, truncation: bool) -> list[int]: ...  # noqa: COP007, COP006 -- foreign tokenizer signature.


class EmbeddingMatrix(typing.Protocol):
    def tolist(self) -> list[list[float]]: ...  # noqa: COP007 -- NumPy matrix signature.


class SentenceEncoder(typing.Protocol):
    @property
    def tokenizer(self) -> SentenceTokenizer: ...

    def encode(  # noqa: COP007 -- Sentence Transformers signature.
        self,
        sentences: list[str],
        *,
        prompt: str,  # noqa: COP006 -- Sentence Transformers argument.
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
        batch_size: int,
    ) -> EmbeddingMatrix: ...


@typing.final
class OllamaEmbeddingProvider:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        text_tokenizer: ports.TextTokenizer,
        embedding_profile: str,
        model_identifier: str = "embeddinggemma:300m",
        vector_dimensions: int = 768,
        content_byte_limit: int = 1536,
        formatted_byte_limit: int = 1900,
        request_timeout_seconds: float = 40.0,
    ) -> None:
        if (
            vector_dimensions not in {128, 256, 512, 768}
            or not 0 < content_byte_limit < formatted_byte_limit <= MAXIMUM_FORMATTED_BYTES
        ):
            raise ValueError("Invalid EmbeddingGemma dimensions or byte budgets")
        if not embedding_profile:
            raise ValueError("An embedding profile is required")
        self._text_tokenizer: typing.Final = text_tokenizer
        self._embedding_profile: typing.Final = embedding_profile
        self._model_identifier: typing.Final = model_identifier
        self._vector_dimensions: typing.Final = vector_dimensions
        self._content_byte_limit: typing.Final = content_byte_limit
        self._formatted_byte_limit: typing.Final = formatted_byte_limit
        self._input_formatter: typing.Final = EmbeddingInputFormatter()
        self._vector_validator: typing.Final = EmbeddingVectorValidator(vector_dimensions=vector_dimensions)
        self._http_transport: typing.Final = OllamaHttpTransport(
            http_client=http_client, timeout_seconds=request_timeout_seconds
        )
        self._initialized_model = False

    @property
    def embedding_profile(self) -> str:
        return self._embedding_profile

    @property
    def vector_dimensions(self) -> int:
        return self._vector_dimensions

    async def initialize_provider(self) -> None:
        await self._verify_model()
        self._initialized_model = True

    async def close_provider(self) -> None:
        self._initialized_model = False

    async def check_readiness(self) -> bool:
        if not self._initialized_model:
            return False
        try:
            await self._verify_model()
        except errors.DependencyUnavailableError:
            return False
        return True

    async def embed_documents(
        self, *, chunk_records: tuple[entities.ChunkRecord, ...], filename_text: str
    ) -> tuple[entities.EmbeddingVector, ...]:
        if not chunk_records:
            return ()
        formatted_inputs: typing.Final = await asyncio.to_thread(
            self._prepare_documents, chunk_records=chunk_records, filename_text=filename_text
        )
        return await self._embed_inputs(formatted_inputs=formatted_inputs)

    async def embed_question(self, *, question_text: str) -> entities.EmbeddingVector:
        formatted_input: typing.Final = self._input_formatter.format_question(question_text=question_text)
        await asyncio.to_thread(self._validate_formatted_input, formatted_input=formatted_input)
        embedded_vectors: typing.Final = await self._embed_inputs(formatted_inputs=[formatted_input])
        return embedded_vectors[0]

    def _prepare_documents(self, *, chunk_records: tuple[entities.ChunkRecord, ...], filename_text: str) -> list[str]:
        formatted_inputs: typing.Final[list[str]] = []
        for chunk_record in chunk_records:
            if len(chunk_record.content_text.encode("utf-8")) > self._content_byte_limit:
                raise errors.InvalidInputError(
                    error_code="embedding_input_too_long",
                    message_text="A document chunk exceeds the configured embedding byte budget.",
                )
            formatted_input = self._input_formatter.format_document(
                content_text=chunk_record.content_text, filename_text=filename_text
            )
            self._validate_formatted_input(formatted_input=formatted_input)
            formatted_inputs.append(formatted_input)
        return formatted_inputs

    def _validate_formatted_input(self, *, formatted_input: str) -> None:
        if len(formatted_input.encode("utf-8")) > self._formatted_byte_limit:
            raise errors.InvalidInputError(
                error_code="embedding_input_too_long",
                message_text="The question or chunk exceeds the configured embedding byte budget.",
            )
        if self._text_tokenizer.count_tokens(content_text=formatted_input) > EMBEDDING_CONTEXT_TOKENS:
            raise errors.InvalidInputError(
                error_code="embedding_input_too_long",
                message_text="The formatted embedding input exceeds its tokenizer profile budget.",
            )

    async def _verify_model(self) -> None:
        payload_value: typing.Final = await self._http_transport.post_payload(
            endpoint_path="/api/show", payload_value={"model": self._model_identifier}
        )
        try:
            model_description: typing.Final = _ModelDescription.model_validate(payload_value)
        except pydantic.ValidationError as response_error:
            raise errors.DependencyUnavailableError(
                error_code="invalid_model_response", message_text="The local embedding model description is invalid."
            ) from response_error
        reported_dimensions: typing.Final = tuple(
            field_value
            for field_name, field_value in model_description.model_info.items()
            if field_name.endswith("embedding_length")
        )
        if "embedding" not in model_description.capabilities or EMBEDDING_NATIVE_DIMENSIONS not in reported_dimensions:
            raise errors.DependencyUnavailableError(
                error_code="incompatible_embedding_model",
                message_text="The configured local model is not a 768D EmbeddingGemma embedding model.",
            )

    async def _embed_inputs(self, *, formatted_inputs: list[str]) -> tuple[entities.EmbeddingVector, ...]:
        if not self._initialized_model:
            raise errors.DependencyUnavailableError(
                error_code="embedding_not_initialized", message_text="The embedding provider has not been initialized."
            )
        payload_value: typing.Final = await self._http_transport.post_payload(
            endpoint_path="/api/embed",
            payload_value={
                "model": self._model_identifier,
                "input": formatted_inputs,
                "dimensions": self._vector_dimensions,
                "truncate": False,
            },
        )
        try:
            model_response: typing.Final = _EmbeddingResponse.model_validate(payload_value)
        except pydantic.ValidationError as response_error:
            raise errors.DependencyUnavailableError(
                error_code="invalid_embedding_vector", message_text="The embedding model returned invalid vector data."
            ) from response_error
        if model_response.model != self._model_identifier:
            raise errors.DependencyUnavailableError(
                error_code="unexpected_embedding_model",
                message_text="The embedding response came from an unexpected model.",
            )
        return self._vector_validator.normalize_vectors(
            raw_vectors=model_response.embeddings, expected_count=len(formatted_inputs)
        )


@typing.final
class HuggingFaceEmbeddingProvider:
    def __init__(
        self,
        *,
        text_tokenizer: ports.TextTokenizer,
        embedding_profile: str,
        model_identifier: str = "google/embeddinggemma-300m",
        device_name: str = "auto",
        cache_directory: pathlib.Path | None = None,
        vector_dimensions: int = 768,
        encoder_loader: typing.Callable[[], SentenceEncoder] | None = None,
    ) -> None:
        if device_name not in {"auto", "cpu", "mps", "cuda"} or vector_dimensions not in {128, 256, 512, 768}:
            raise ValueError("Use an auto/CPU/MPS/CUDA device and a supported EmbeddingGemma dimensionality")
        if not embedding_profile:
            raise ValueError("An embedding profile is required")
        self._text_tokenizer: typing.Final = text_tokenizer
        self._embedding_profile: typing.Final = embedding_profile
        self._model_identifier: typing.Final = model_identifier
        self._device_name: typing.Final = device_name
        self._cache_directory: typing.Final = cache_directory
        self._vector_dimensions: typing.Final = vector_dimensions
        self._encoder_loader: typing.Final = encoder_loader or self._load_encoder
        self._input_formatter: typing.Final = EmbeddingInputFormatter()
        self._vector_validator: typing.Final = EmbeddingVectorValidator(vector_dimensions=vector_dimensions)
        self._encoding_lock: typing.Final = asyncio.Lock()
        self._sentence_encoder: SentenceEncoder | None = None

    @property
    def embedding_profile(self) -> str:
        return self._embedding_profile

    @property
    def vector_dimensions(self) -> int:
        return self._vector_dimensions

    async def initialize_provider(self) -> None:
        async with self._encoding_lock:
            if self._sentence_encoder is None:
                self._sentence_encoder = await asyncio.to_thread(self._encoder_loader)

    async def close_provider(self) -> None:
        async with self._encoding_lock:
            self._sentence_encoder = None

    async def check_readiness(self) -> bool:
        return self._sentence_encoder is not None

    async def embed_documents(
        self, *, chunk_records: tuple[entities.ChunkRecord, ...], filename_text: str
    ) -> tuple[entities.EmbeddingVector, ...]:
        if not chunk_records:
            return ()
        formatted_inputs: typing.Final = [
            self._input_formatter.format_document(content_text=chunk_record.content_text, filename_text=filename_text)
            for chunk_record in chunk_records
        ]
        return await self._embed_inputs(formatted_inputs=formatted_inputs)

    async def embed_question(self, *, question_text: str) -> entities.EmbeddingVector:
        formatted_inputs: typing.Final = [self._input_formatter.format_question(question_text=question_text)]
        embedded_vectors: typing.Final = await self._embed_inputs(formatted_inputs=formatted_inputs)
        return embedded_vectors[0]

    async def _embed_inputs(self, *, formatted_inputs: list[str]) -> tuple[entities.EmbeddingVector, ...]:
        async with self._encoding_lock:
            encoding_task: typing.Final = asyncio.create_task(
                asyncio.to_thread(self._encode_inputs, formatted_inputs=formatted_inputs)
            )
            try:
                return await asyncio.shield(encoding_task)
            except asyncio.CancelledError:
                # A worker cancellation must not release serialization while PyTorch still executes.
                await encoding_task
                raise

    def _encode_inputs(self, *, formatted_inputs: list[str]) -> tuple[entities.EmbeddingVector, ...]:
        sentence_encoder: typing.Final = self._sentence_encoder
        if sentence_encoder is None:
            raise errors.DependencyUnavailableError(
                error_code="embedding_not_initialized",
                message_text="The HF embedding provider has not been initialized.",
            )
        for formatted_input in formatted_inputs:
            actual_tokens = sentence_encoder.tokenizer.encode(
                formatted_input, add_special_tokens=True, truncation=False
            )
            if (
                len(actual_tokens) > EMBEDDING_CONTEXT_TOKENS
                or self._text_tokenizer.count_tokens(content_text=formatted_input) > EMBEDDING_CONTEXT_TOKENS
            ):
                raise errors.InvalidInputError(
                    error_code="embedding_input_too_long",
                    message_text="The formatted input exceeds EmbeddingGemma's 2048-token context.",
                )
        try:
            # Inputs are already formatted: explicit empty prompt disables Sentence Transformers' default query prefix.
            encoded_matrix: typing.Final = sentence_encoder.encode(
                formatted_inputs,
                prompt="",
                normalize_embeddings=False,
                convert_to_numpy=True,
                show_progress_bar=False,
                batch_size=16,
            )
        except (RuntimeError, ValueError) as encoding_error:
            raise errors.DependencyUnavailableError(
                error_code="embedding_inference_failed",
                message_text="The local HF embedding model could not complete inference.",
            ) from encoding_error
        original_vectors: typing.Final = encoded_matrix.tolist()
        if any(len(embedding_row) != EMBEDDING_NATIVE_DIMENSIONS for embedding_row in original_vectors):
            raise errors.DependencyUnavailableError(
                error_code="invalid_embedding_vector", message_text="The original HF model must return 768D vectors."
            )
        raw_vectors: typing.Final = [embedding_row[: self._vector_dimensions] for embedding_row in original_vectors]
        return self._vector_validator.normalize_vectors(raw_vectors=raw_vectors, expected_count=len(formatted_inputs))

    def _select_device(self, *, mps_available: bool, cuda_available: bool) -> str:
        if self._device_name == "auto":
            return "mps" if mps_available else "cuda" if cuda_available else "cpu"
        if (self._device_name == "mps" and not mps_available) or (self._device_name == "cuda" and not cuda_available):
            raise errors.DependencyUnavailableError(
                error_code="hf_device_unavailable",
                message_text="The explicitly requested HF embedding device is unavailable.",
            )
        return self._device_name

    def _load_encoder(self) -> SentenceEncoder:
        try:
            import sentence_transformers  # noqa: PLC0415 -- optional backend must not be a default startup dependency.
            import torch  # noqa: PLC0415 -- optional backend must not be a default startup dependency.
        except ImportError as import_error:
            raise errors.DependencyUnavailableError(
                error_code="hf_backend_not_installed",
                message_text="Install the project's huggingface extra to use the original HF embedding backend.",
            ) from import_error
        resolved_device: typing.Final = self._select_device(
            mps_available=bool(torch.backends.mps.is_available()), cuda_available=bool(torch.cuda.is_available())
        )
        try:
            loaded_encoder: typing.Final = sentence_transformers.SentenceTransformer(
                self._model_identifier,
                device=resolved_device,
                cache_folder=str(self._cache_directory) if self._cache_directory is not None else None,
                trust_remote_code=False,
                model_kwargs={"torch_dtype": torch.float32},
            )
            loaded_encoder.float()
            loaded_encoder.max_seq_length = EMBEDDING_CONTEXT_TOKENS
        except (OSError, RuntimeError, ValueError) as loading_error:
            raise errors.DependencyUnavailableError(
                error_code="hf_embedding_unavailable",
                message_text=(
                    "The HF embedding model could not load. Verify optional dependencies "
                    "and prior local access to the gated Google model."
                ),
            ) from loading_error
        # Only the untyped third-party constructor crosses into this local structural contract.
        return typing.cast("SentenceEncoder", loaded_encoder)
