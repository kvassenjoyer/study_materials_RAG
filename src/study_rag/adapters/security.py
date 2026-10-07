import datetime
import typing
import uuid

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from study_rag.domain import entities, errors


_MINIMUM_SECRET_BYTES: typing.Final = 32


@typing.final
class ArgonPasswordSecurity:
    def __init__(self) -> None:
        self._password_hasher = PasswordHash.recommended()

    def hash_password(self, *, password_text: str) -> str:
        return self._password_hasher.hash(password_text)

    def verify_password(self, *, password_text: str, password_hash: str) -> bool:
        try:
            return self._password_hasher.verify(password_text, password_hash)
        except (UnknownHashError, ValueError):
            return False


@typing.final
class JwtAccessTokenCodec:
    def __init__(
        self,
        *,
        secret_value: str,
        issuer_name: str = "study-rag",
        audience_name: str = "study-rag-api",
        expiry_seconds: int = 3600,
    ) -> None:
        if len(secret_value.encode("utf-8")) < _MINIMUM_SECRET_BYTES:
            raise ValueError("JWT secret must contain at least 32 UTF-8 bytes")
        if expiry_seconds <= 0:
            raise ValueError("JWT lifetime must be positive")
        self._secret_value = secret_value
        self._issuer_name = issuer_name
        self._audience_name = audience_name
        self._expiry_seconds = expiry_seconds

    def issue_token(self, *, tenant_identity: entities.TenantIdentity) -> entities.AuthToken:
        timestamp_value: typing.Final = datetime.datetime.now(datetime.UTC)
        encoded_token: typing.Final = jwt.encode(
            {
                "sub": str(tenant_identity.user_identifier),
                "iat": timestamp_value,
                "exp": timestamp_value + datetime.timedelta(seconds=self._expiry_seconds),
                "iss": self._issuer_name,
                "aud": self._audience_name,
            },
            self._secret_value,
            algorithm="HS256",
        )
        return entities.AuthToken(access_token=encoded_token, expires_in=self._expiry_seconds)

    def decode_token(self, *, access_token: str) -> uuid.UUID:
        try:
            decoded_claims: typing.Final = jwt.decode(
                access_token,
                self._secret_value,
                algorithms=["HS256"],
                issuer=self._issuer_name,
                audience=self._audience_name,
                options={"require": ["sub", "exp", "iat", "iss", "aud"], "strict_aud": True},
            )
            subject_value: object = decoded_claims["sub"]
            return self._decode_subject(subject_value=subject_value)
        except (jwt.InvalidTokenError, ValueError, KeyError, TypeError) as token_error:
            raise errors.AuthenticationError(
                error_code="invalid_access_token",
                message_text="Access token is invalid or expired",
            ) from token_error

    def _decode_subject(self, *, subject_value: object) -> uuid.UUID:
        if not isinstance(subject_value, str):
            raise TypeError("Token subject must be a UUID string")
        return uuid.UUID(subject_value)
