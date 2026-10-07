"""Registration and authentication without infrastructure-specific logic."""

import asyncio
import dataclasses
import typing

import email_validator

from study_rag.domain import entities, errors, ports


minimum_password_length: typing.Final = 12
maximum_password_length: typing.Final = 128


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class AuthenticationService:
    auth_repository: ports.AuthRepository
    password_security: ports.PasswordSecurity
    access_token_codec: ports.AccessTokenCodec
    dummy_password_hash: str

    async def register_account(self, *, email_address: str, password_text: str) -> entities.AuthToken:
        normalized_email: typing.Final = self._normalize_address(email_address=email_address)
        self._validate_password(password_text=password_text)
        password_digest: typing.Final = await asyncio.to_thread(
            self.password_security.hash_password, password_text=password_text
        )
        account_record: typing.Final = await self.auth_repository.create_account(
            email_address=normalized_email, password_hash=password_digest
        )
        return self._issue_account_token(account_record=account_record)

    async def login_account(self, *, email_address: str, password_text: str) -> entities.AuthToken:
        normalized_email: typing.Final = self._normalize_address(email_address=email_address)
        self._validate_password(password_text=password_text)
        account_record: typing.Final = await self.auth_repository.fetch_by_email(email_address=normalized_email)
        password_digest: typing.Final = (
            self.dummy_password_hash if account_record is None else account_record.password_hash
        )
        password_matches: typing.Final = await asyncio.to_thread(
            self.password_security.verify_password, password_text=password_text, password_hash=password_digest
        )
        if account_record is None or not password_matches:
            raise errors.AuthenticationError(error_code="invalid_credentials", message_text="Invalid email or password")
        return self._issue_account_token(account_record=account_record)

    async def authenticate_token(self, *, access_token: str) -> entities.TenantIdentity:
        account_identifier: typing.Final = self.access_token_codec.decode_token(access_token=access_token)
        account_record: typing.Final = await self.auth_repository.fetch_by_identifier(
            user_identifier=account_identifier
        )
        if account_record is None:
            raise errors.AuthenticationError(error_code="invalid_token", message_text="Invalid bearer token")
        return entities.TenantIdentity(
            user_identifier=account_record.user_identifier, email_address=account_record.email_address
        )

    def _issue_account_token(self, *, account_record: entities.UserAccount) -> entities.AuthToken:
        account_identity: typing.Final = entities.TenantIdentity(
            user_identifier=account_record.user_identifier, email_address=account_record.email_address
        )
        return self.access_token_codec.issue_token(tenant_identity=account_identity)

    def _normalize_address(self, *, email_address: str) -> str:
        try:
            validated_email: typing.Final = email_validator.validate_email(
                email_address.strip(), check_deliverability=False
            )
        except email_validator.EmailNotValidError as validation_error:
            raise errors.InvalidInputError(
                error_code="invalid_email_address", message_text="A valid email address is required"
            ) from validation_error
        return validated_email.normalized.lower()

    def _validate_password(self, *, password_text: str) -> None:
        if not minimum_password_length <= len(password_text) <= maximum_password_length:
            raise errors.InvalidInputError(
                error_code="invalid_password_length", message_text="Password must contain 12 to 128 characters"
            )
