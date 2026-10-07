"""Bound the upload request before Starlette writes multipart spool files."""

import http
import typing

import starlette.formparsers
import starlette.responses
import starlette.types

from study_rag.bootstrap import ApplicationResources


multipart_overhead_bytes: typing.Final = 65536


@typing.final
class UploadBodyLimitMiddleware:
    def __init__(self, app: starlette.types.ASGIApp, *, maximum_body_bytes: int | None = None) -> None:  # noqa: COP006 -- Starlette constructor contract.
        self._application: typing.Final = app
        self._maximum_body_bytes: typing.Final = maximum_body_bytes

    async def __call__(
        self,
        http_scope: starlette.types.Scope,
        receive_message: starlette.types.Receive,
        send_message: starlette.types.Send,
    ) -> None:
        if (
            http_scope["type"] != "http"
            or http_scope["method"] != "POST"
            or http_scope["path"] != "/rest/study-rag/documents/"
        ):
            await self._application(http_scope, receive_message, send_message)
            return
        body_limit: typing.Final = self._resolve_body_limit(http_scope=http_scope)
        if not await self._preflight_request(
            http_scope=http_scope, receive_message=receive_message, send_message=send_message, body_limit=body_limit
        ):
            return
        received_bytes = 0
        overflow_detected = False
        response_replaced = False

        async def receive_limited() -> starlette.types.Message:
            nonlocal received_bytes, overflow_detected
            request_message: typing.Final = await receive_message()
            if request_message["type"] == "http.request":
                received_bytes += len(request_message.get("body", b""))
                if received_bytes > body_limit:
                    overflow_detected = True
                    # MultipartParser handles this exception by closing all temporary files.
                    raise starlette.formparsers.MultiPartException("Upload body exceeded its configured limit")
            return request_message

        async def send_limited(response_message: starlette.types.Message) -> None:
            nonlocal response_replaced
            if not overflow_detected:
                await send_message(response_message)
                return
            if response_message["type"] == "http.response.start" and not response_replaced:
                response_replaced = True
                await self._reject_request(
                    http_scope=http_scope,
                    receive_message=receive_message,
                    send_message=send_message,
                    status_code=413,
                    error_code="document_too_large",
                )

        await self._application(http_scope, receive_limited, send_limited)

    async def _preflight_request(
        self,
        *,
        http_scope: starlette.types.Scope,
        receive_message: starlette.types.Receive,
        send_message: starlette.types.Send,
        body_limit: int,
    ) -> bool:
        request_headers: typing.Final = dict(http_scope["headers"])
        content_length: typing.Final = request_headers.get(b"content-length")
        if content_length is not None:
            try:
                announced_length: typing.Final = int(content_length)
            except ValueError:
                await self._reject_request(
                    http_scope=http_scope,
                    receive_message=receive_message,
                    send_message=send_message,
                    status_code=http.HTTPStatus.BAD_REQUEST,
                    error_code="invalid_content_length",
                )
                return False
            if announced_length > body_limit or announced_length < 0:
                await self._reject_request(
                    http_scope=http_scope,
                    receive_message=receive_message,
                    send_message=send_message,
                    status_code=http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    error_code="document_too_large",
                )
                return False
        if not request_headers.get(b"authorization", b"").lower().startswith(b"bearer "):
            await self._reject_request(
                http_scope=http_scope,
                receive_message=receive_message,
                send_message=send_message,
                status_code=http.HTTPStatus.UNAUTHORIZED,
                error_code="authentication_required",
            )
            return False
        return True

    def _resolve_body_limit(self, *, http_scope: starlette.types.Scope) -> int:
        if self._maximum_body_bytes is not None:
            return self._maximum_body_bytes
        application_resources = http_scope["app"].state.resources
        if not isinstance(application_resources, ApplicationResources):
            raise TypeError("Upload limiter requires initialized application resources")
        return application_resources.settings.maximum_upload_bytes + multipart_overhead_bytes

    async def _reject_request(
        self,
        *,
        http_scope: starlette.types.Scope,
        receive_message: starlette.types.Receive,
        send_message: starlette.types.Send,
        status_code: int,
        error_code: str,
    ) -> None:
        message_text: typing.Final = (
            "A bearer token is required"
            if status_code == http.HTTPStatus.UNAUTHORIZED
            else "Upload request exceeds or violates its body limit"
        )
        response_value: typing.Final = starlette.responses.JSONResponse(
            status_code=status_code,
            content={"error_code": error_code, "message_text": message_text},
            headers={"WWW-Authenticate": "Bearer"} if status_code == http.HTTPStatus.UNAUTHORIZED else None,
        )
        await response_value(http_scope, receive_message, send_message)
