import asyncio
import typing

import httpx
import pytest

from study_rag.adapters.model_support import OllamaHttpTransport
from study_rag.domain import errors


GENERATION_TIMEOUT_SECONDS: typing.Final = 120.0


async def test_generation_request_can_wait_longer_without_extending_operation_budget() -> None:
    async def handle_request(request_value: httpx.Request) -> httpx.Response:
        assert request_value.extensions["timeout"]["read"] == GENERATION_TIMEOUT_SECONDS
        await asyncio.Event().wait()
        raise AssertionError("The operation deadline must cancel this request")

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        model_transport = OllamaHttpTransport(
            http_client=http_client, timeout_seconds=120.0, operation_timeout_seconds=0.01
        )
        with pytest.raises(errors.DependencyUnavailableError) as raised_error:
            await model_transport.post_payload(endpoint_path="/api/chat", payload_value={})
    assert raised_error.value.error_code == "model_request_timeout"


async def test_exhausted_read_timeouts_are_reported_as_timeout_instead_of_unavailability() -> None:
    def handle_request(request_value: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Model generation took too long", request=request_value)

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        model_transport = OllamaHttpTransport(http_client=http_client, retry_attempts=1)
        with pytest.raises(errors.DependencyUnavailableError) as raised_error:
            await model_transport.post_payload(endpoint_path="/api/chat", payload_value={})
    assert raised_error.value.error_code == "model_request_timeout"


async def test_transient_generation_server_failure_still_retries() -> None:
    captured_calls: list[str] = []

    def handle_request(request_value: httpx.Request) -> httpx.Response:
        captured_calls.append(request_value.url.path)
        return httpx.Response(503) if len(captured_calls) == 1 else httpx.Response(200, json={"done": True})

    async with httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handle_request)
    ) as http_client:
        model_transport = OllamaHttpTransport(
            http_client=http_client, timeout_seconds=120.0, operation_timeout_seconds=120.0
        )
        assert await model_transport.post_payload(endpoint_path="/api/chat", payload_value={}) == {"done": True}
    assert captured_calls == ["/api/chat", "/api/chat"]
