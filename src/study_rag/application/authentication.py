"""Registration and authentication without infrastructure-specific logic."""

import asyncio
import dataclasses
import re
import typing

from study_rag.domain import entities, errors, ports


minimum_password_length: typing.Final = 12
maximum_password_length: typing.Final = 128
minimum_login_length: typing.Final = 3
maximum_login_length: typing.Final = 64
maximum_stored_login_length: typing.Final = 320


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class AuthenticationService:
    auth_repository: ports.AuthRepository
    password_security: ports.PasswordSecurity
    access_token_codec: ports.AccessTokenCodec
    dummy_password_hash: str

    async def register_account(self, *, login_name: str, password_text: str) -> entities.AuthToken:
        normalized_login: typing.Final = self._normalize_login(login_name=login_name)
        if (
            not minimum_login_length <= len(normalized_login) <= maximum_login_length
            or re.fullmatch(r"[\w.-]+", normalized_login) is None
        ):
            raise errors.InvalidInputError(
                error_code="invalid_login_name",
                message_text="Login must contain 3 to 64 letters, digits, dots, hyphens or underscores",
            )
        self._validate_password(password_text=password_text)
        password_digest: typing.Final = await asyncio.to_thread(
            self.password_security.hash_password, password_text=password_text
        )
        account_record: typing.Final = await self.auth_repository.create_account(
            login_name=normalized_login, password_hash=password_digest
        )
        return self._issue_account_token(account_record=account_record)

    async def login_account(self, *, login_name: str, password_text: str) -> entities.AuthToken:
        normalized_login: typing.Final = self._normalize_login(login_name=login_name)
        self._validate_password(password_text=password_text)
        account_record: typing.Final = await self.auth_repository.fetch_by_login(login_name=normalized_login)
        password_digest: typing.Final = (
            self.dummy_password_hash if account_record is None else account_record.password_hash
        )
        password_matches: typing.Final = await asyncio.to_thread(
            self.password_security.verify_password, password_text=password_text, password_hash=password_digest
        )
        if account_record is None or not password_matches:
            raise errors.AuthenticationError(error_code="invalid_credentials", message_text="Invalid login or password")
        return self._issue_account_token(account_record=account_record)

    async def authenticate_token(self, *, access_token: str) -> entities.TenantIdentity:
        account_identifier: typing.Final = self.access_token_codec.decode_token(access_token=access_token)
        account_record: typing.Final = await self.auth_repository.fetch_by_identifier(
            user_identifier=account_identifier
        )
        if account_record is None:
            raise errors.AuthenticationError(error_code="invalid_token", message_text="Invalid bearer token")
        return entities.TenantIdentity(
            user_identifier=account_record.user_identifier, login_name=account_record.login_name
        )

    def _issue_account_token(self, *, account_record: entities.UserAccount) -> entities.AuthToken:
        account_identity: typing.Final = entities.TenantIdentity(
            user_identifier=account_record.user_identifier, login_name=account_record.login_name
        )
        return self.access_token_codec.issue_token(tenant_identity=account_identity)

    def _normalize_login(self, *, login_name: str) -> str:
        normalized_login: typing.Final = login_name.strip().lower()
        # Legacy accounts use their entire previous address as the login.
        if not normalized_login or len(normalized_login) > maximum_stored_login_length:
            raise errors.InvalidInputError(error_code="invalid_login_name", message_text="A valid login is required")
        return normalized_login

    def _validate_password(self, *, password_text: str) -> None:
        if not minimum_password_length <= len(password_text) <= maximum_password_length:
            raise errors.InvalidInputError(
                error_code="invalid_password_length", message_text="Password must contain 12 to 128 characters"
            )
