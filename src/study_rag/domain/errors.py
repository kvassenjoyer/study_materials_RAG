"""Stable application failures with safe, client-visible descriptions."""

import typing


class DomainError(Exception):
    """The extensible boundary between application failures and HTTP responses."""

    def __init__(self, *, error_code: str, message_text: str) -> None:
        super().__init__(message_text)
        self.error_code: typing.Final = error_code
        self.message_text: typing.Final = message_text


@typing.final
class InvalidInputError(DomainError):
    """The input does not meet a domain or format constraint."""


@typing.final
class AuthenticationError(DomainError):
    """Credentials or a bearer token could not be authenticated."""


@typing.final
class ResourceNotFoundError(DomainError):
    """The requested object is absent or belongs to another tenant."""


@typing.final
class ResourceConflictError(DomainError):
    """The requested transition conflicts with committed state."""


@typing.final
class DependencyUnavailableError(DomainError):
    """An external dependency failed to complete its promised operation."""


@typing.final
class LeaseLostError(DomainError):
    """A background operation no longer owns the current state lease."""
