"""Small shared model-boundary policies; clients remain owned by the composition root."""

import asyncio
import dataclasses
import json
import math
import secrets
import typing

import httpx

from study_rag.domain import entities, errors


MAXIMUM_REQUEST_SECONDS: typing.Final = 120
MAXIMUM_RETRY_ATTEMPTS: typing.Final = 3


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class EmbeddingInputFormatter:
    maximum_title_bytes: int = 256

    def format_question(self, *, question_text: str) -> str:
        return f"task: search result | query: {question_text}"

    def format_document(self, *, content_text: str, filename_text: str) -> str:
        title_text: typing.Final = " ".join(filename_text.replace("|", " ").split())
        bounded_title: typing.Final = title_text.encode("utf-8")[: self.maximum_title_bytes].decode(
            "utf-8", errors="ignore"
        )
        return f"title: {bounded_title or 'none'} | text: {content_text}"


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class EmbeddingVectorValidator:
    vector_dimensions: int

    def normalize_vectors(
        self, *, raw_vectors: typing.Sequence[typing.Sequence[float]], expected_count: int
    ) -> tuple[entities.EmbeddingVector, ...]:
        if len(raw_vectors) != expected_count:
            self._reject_vector()
        return tuple(self._normalize_vector(raw_vector=raw_vector) for raw_vector in raw_vectors)

    def _normalize_vector(self, *, raw_vector: typing.Sequence[float]) -> entities.EmbeddingVector:
        if len(raw_vector) != self.vector_dimensions or any(not math.isfinite(component) for component in raw_vector):
            self._reject_vector()
        vector_norm: typing.Final = math.hypot(*raw_vector)
        if not math.isfinite(vector_norm) or vector_norm <= 0:
            self._reject_vector()
        return tuple(component / vector_norm for component in raw_vector)

    def _reject_vector(self) -> typing.NoReturn:
        raise errors.DependencyUnavailableError(
            error_code="invalid_embedding_vector", message_text="The embedding model returned an invalid vector."
        )


@typing.final
class OllamaHttpTransport:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        timeout_seconds: float = 40.0,
        retry_attempts: int = 3,
        operation_timeout_seconds: float | None = None,
    ) -> None:
        if not 0 < timeout_seconds <= MAXIMUM_REQUEST_SECONDS or not 1 <= retry_attempts <= MAXIMUM_RETRY_ATTEMPTS:
            raise ValueError("Model requests require bounded timeouts and at most three attempts")
        operation_budget = (
            timeout_seconds * retry_attempts if operation_timeout_seconds is None else operation_timeout_seconds
        )
        if not 0 < operation_budget <= timeout_seconds * retry_attempts:
            raise ValueError("The operation timeout must fit within the bounded request attempts")
        self._http_client: typing.Final = http_client
        self._timeout_seconds: typing.Final = timeout_seconds
        self._retry_attempts: typing.Final = retry_attempts
        self._operation_timeout_seconds: typing.Final = operation_budget
        self._request_lock: typing.Final = asyncio.Lock()

    async def post_payload(self, *, endpoint_path: str, payload_value: dict[str, object]) -> object:
        try:
            # Waiting for another request consumes the same overall budget as retries.
            async with asyncio.timeout(self._operation_timeout_seconds):
                async with self._request_lock:
                    return await self._request_payload(endpoint_path=endpoint_path, payload_value=payload_value)
        except TimeoutError as request_error:
            raise errors.DependencyUnavailableError(
                error_code="model_request_timeout", message_text="The local model request exceeded its time budget."
            ) from request_error

    async def _request_payload(self, *, endpoint_path: str, payload_value: dict[str, object]) -> object:
        for attempt_number in range(self._retry_attempts):
            try:
                http_response = await self._http_client.post(
                    endpoint_path, json=payload_value, timeout=httpx.Timeout(self._timeout_seconds)
                )
                http_response.raise_for_status()
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as request_error:
                if attempt_number + 1 == self._retry_attempts:
                    raise errors.DependencyUnavailableError(
                        error_code="model_request_timeout"
                        if isinstance(request_error, httpx.TimeoutException)
                        else "model_unavailable",
                        message_text="The local model request timed out."
                        if isinstance(request_error, httpx.TimeoutException)
                        else "The local model server is temporarily unavailable.",
                    ) from request_error
            except httpx.HTTPStatusError as request_error:
                if http_response.status_code not in {429, 500, 502, 503, 504}:
                    raise errors.DependencyUnavailableError(
                        error_code="model_request_rejected",
                        message_text="The local model rejected the request; verify its model tag and input limits.",
                    ) from request_error
                if attempt_number + 1 == self._retry_attempts:
                    raise errors.DependencyUnavailableError(
                        error_code="model_unavailable",
                        message_text="The local model server could not complete the request.",
                    ) from request_error
            else:
                try:
                    parsed_value: object = http_response.json()
                except (json.JSONDecodeError, UnicodeDecodeError) as response_error:
                    raise errors.DependencyUnavailableError(
                        error_code="invalid_model_response",
                        message_text="The local model returned an invalid JSON response.",
                    ) from response_error
                return parsed_value
            await asyncio.sleep(0.1 * (2**attempt_number) + secrets.randbelow(51) / 1000)
        raise AssertionError("The bounded request loop must return or raise")
