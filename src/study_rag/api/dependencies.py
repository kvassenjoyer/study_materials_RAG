"""Explicit access to the current resource bundle and authenticated user."""

import typing

import fastapi
import fastapi.security

from study_rag.bootstrap import ApplicationResources
from study_rag.domain import entities, errors


bearer_scheme: typing.Final = fastapi.security.HTTPBearer(auto_error=False)


def get_resources(http_request: fastapi.Request) -> ApplicationResources:
    resources_value = http_request.app.state.resources
    if not isinstance(resources_value, ApplicationResources):
        raise errors.DependencyUnavailableError(
            error_code="application_not_ready", message_text="Application is not ready"
        )
    return resources_value


type CurrentResources = typing.Annotated[ApplicationResources, fastapi.Depends(get_resources)]


async def authenticate_current_user(
    application_resources: CurrentResources,
    bearer_credentials: typing.Annotated[
        fastapi.security.HTTPAuthorizationCredentials | None, fastapi.Depends(bearer_scheme)
    ],
) -> entities.TenantIdentity:
    if bearer_credentials is None:
        raise errors.AuthenticationError(
            error_code="authentication_required", message_text="A bearer token is required"
        )
    return await application_resources.authentication_service.authenticate_token(
        access_token=bearer_credentials.credentials
    )


type CurrentIdentity = typing.Annotated[entities.TenantIdentity, fastapi.Depends(authenticate_current_user)]
