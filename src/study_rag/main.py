"""FastAPI entry point with explicit ownership of startup and shutdown."""

import contextlib
import typing

import fastapi
import fastapi.exceptions
import fastapi.responses

from study_rag.api import ag_ui, routes
from study_rag.api.frontend import mount_frontend_routes
from study_rag.api.upload_limit import UploadBodyLimitMiddleware
from study_rag.bootstrap import ResourceFactory, initialize_resources
from study_rag.config import Settings
from study_rag.domain import errors


def create_application(
    *, settings: Settings | None = None, resource_factory: ResourceFactory | None = None
) -> fastapi.FastAPI:
    @contextlib.asynccontextmanager
    async def application_lifespan(application: fastapi.FastAPI) -> typing.AsyncIterator[None]:
        resources_context: typing.Final = (
            initialize_resources(settings=settings) if resource_factory is None else resource_factory()
        )
        async with resources_context as application_resources:
            application.state.resources = application_resources
            yield
            application.state.resources = None

    application: typing.Final = fastapi.FastAPI(
        title="RAGumentation",
        description=(
            "RAGent - помощник для локального поиска и ответов по собственным TXT, Markdown и PDF. "
            "Каждый пользователь видит только свои материалы и чаты."
        ),
        version="0.1.0",
        lifespan=application_lifespan,
    )
    application.add_exception_handler(errors.DomainError, handle_domain_error)
    application.add_exception_handler(fastapi.exceptions.RequestValidationError, handle_validation_error)
    application.include_router(routes.api_router)
    application.include_router(ag_ui.api_router)
    mount_frontend_routes(application=application)
    application.add_middleware(UploadBodyLimitMiddleware)
    return application


async def handle_domain_error(
    _http_request: fastapi.Request, domain_error: Exception
) -> fastapi.responses.JSONResponse:
    if not isinstance(domain_error, errors.DomainError):
        return fastapi.responses.JSONResponse(
            status_code=500, content={"error_code": "internal_error", "message_text": "Internal server error"}
        )
    response_status = 400
    response_headers: dict[str, str] | None = None
    if isinstance(domain_error, errors.AuthenticationError):
        response_status = 401
        response_headers = {"WWW-Authenticate": "Bearer"}
    elif isinstance(domain_error, errors.ResourceNotFoundError):
        response_status = 404
    elif isinstance(domain_error, (errors.ResourceConflictError, errors.LeaseLostError)):
        response_status = 409
    elif isinstance(domain_error, errors.DependencyUnavailableError):
        response_status = 503
    elif isinstance(domain_error, errors.InvalidInputError):
        response_status = {
            "document_too_large": 413,
            "upload_too_large": 413,
            "agent_request_too_large": 413,
            "unsupported_document_format": 415,
            "unsupported_agent_content_type": 415,
        }.get(domain_error.error_code, 422)
    return fastapi.responses.JSONResponse(
        status_code=response_status,
        headers=response_headers,
        content={"error_code": domain_error.error_code, "message_text": domain_error.message_text},
    )


async def handle_validation_error(
    _http_request: fastapi.Request, _validation_error: Exception
) -> fastapi.responses.JSONResponse:
    return fastapi.responses.JSONResponse(
        status_code=422,
        content={"error_code": "invalid_request", "message_text": "Request fields do not match the API schema"},
    )
