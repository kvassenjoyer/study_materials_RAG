# Test-only credential literals are intentional.
# ruff: noqa: S105, S106
import dataclasses
import datetime
import typing
import unittest.mock
import uuid

import pytest

from study_rag.application.authentication import AuthenticationService
from study_rag.domain import entities, errors, ports


@typing.final
@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class AuthenticationHarness:
    authentication_service: AuthenticationService
    auth_repository: unittest.mock.Mock
    password_security: unittest.mock.Mock
    token_codec: unittest.mock.Mock
    user_account: entities.UserAccount


@pytest.fixture
def authentication_harness() -> AuthenticationHarness:
    auth_repository = unittest.mock.Mock(spec=ports.AuthRepository)
    password_security = unittest.mock.Mock(spec=ports.PasswordSecurity)
    token_codec = unittest.mock.Mock(spec=ports.AccessTokenCodec)
    user_account = entities.UserAccount(
        user_identifier=uuid.uuid4(),
        login_name="student",
        password_hash="fixture-hash",
        created_at=datetime.datetime.now(datetime.UTC),
    )
    auth_repository.create_account.return_value = user_account
    auth_repository.fetch_by_login.return_value = user_account
    auth_repository.fetch_by_identifier.return_value = user_account
    password_security.hash_password.return_value = "fixture-hash"
    password_security.verify_password.return_value = True
    token_codec.issue_token.return_value = entities.AuthToken(access_token="fixture-token", expires_in=3600)
    token_codec.decode_token.return_value = user_account.user_identifier
    return AuthenticationHarness(
        authentication_service=AuthenticationService(
            auth_repository=auth_repository,
            password_security=password_security,
            access_token_codec=token_codec,
            dummy_password_hash="dummy-hash",
        ),
        auth_repository=auth_repository,
        password_security=password_security,
        token_codec=token_codec,
        user_account=user_account,
    )


@pytest.mark.parametrize("login_name", ["Student", "  Student.Name_1-2  ", "Ученик_123", "a" * 64])
async def test_registration_normalizes_plain_login_without_email(
    authentication_harness: AuthenticationHarness, login_name: str
) -> None:
    auth_token = await authentication_harness.authentication_service.register_account(
        login_name=login_name, password_text="fixture-password"
    )
    authentication_harness.auth_repository.create_account.assert_awaited_once_with(
        login_name=login_name.strip().lower(), password_hash="fixture-hash"
    )
    assert auth_token.access_token == "fixture-token"


@pytest.mark.parametrize(
    "login_name", ["", "  ", "ab", "a" * 65, "first last", "user@example.com", "user/name", "name\nname"]
)
async def test_registration_rejects_invalid_logins_before_hashing(
    authentication_harness: AuthenticationHarness, login_name: str
) -> None:
    with pytest.raises(errors.InvalidInputError) as raised_error:
        await authentication_harness.authentication_service.register_account(
            login_name=login_name, password_text="fixture-password"
        )
    assert raised_error.value.error_code == "invalid_login_name"
    authentication_harness.password_security.hash_password.assert_not_called()
    authentication_harness.auth_repository.create_account.assert_not_awaited()


@pytest.mark.parametrize("login_name", ["  Student  ", "Test@Example.com", "a" * 100 + "@example.com"])
async def test_login_accepts_normalized_names_and_existing_addresses(
    authentication_harness: AuthenticationHarness, login_name: str
) -> None:
    await authentication_harness.authentication_service.login_account(
        login_name=login_name, password_text="fixture-password"
    )
    authentication_harness.auth_repository.fetch_by_login.assert_awaited_once_with(
        login_name=login_name.strip().lower()
    )
    authentication_harness.password_security.verify_password.assert_called_once_with(
        password_text="fixture-password", password_hash="fixture-hash"
    )


@pytest.mark.parametrize("missing_account", [True, False])
async def test_invalid_credentials_use_uniform_error_and_verify_a_hash(
    authentication_harness: AuthenticationHarness, missing_account: bool
) -> None:
    if missing_account:
        authentication_harness.auth_repository.fetch_by_login.return_value = None
    else:
        authentication_harness.password_security.verify_password.return_value = False
    with pytest.raises(errors.AuthenticationError) as raised_error:
        await authentication_harness.authentication_service.login_account(
            login_name="student", password_text="wrong-password"
        )
    assert raised_error.value.error_code == "invalid_credentials"
    authentication_harness.password_security.verify_password.assert_called_once_with(
        password_text="wrong-password", password_hash="dummy-hash" if missing_account else "fixture-hash"
    )
    authentication_harness.token_codec.issue_token.assert_not_called()


@pytest.mark.parametrize("login_name", ["", "  ", "a" * 321])
async def test_invalid_login_does_not_query_repository(
    authentication_harness: AuthenticationHarness, login_name: str
) -> None:
    with pytest.raises(errors.InvalidInputError) as raised_error:
        await authentication_harness.authentication_service.login_account(
            login_name=login_name, password_text="fixture-password"
        )
    assert raised_error.value.error_code == "invalid_login_name"
    authentication_harness.auth_repository.fetch_by_login.assert_not_awaited()


async def test_existing_token_still_resolves_account_by_unchanged_identifier(
    authentication_harness: AuthenticationHarness,
) -> None:
    identity = await authentication_harness.authentication_service.authenticate_token(access_token="existing-token")
    assert identity == entities.TenantIdentity(
        user_identifier=authentication_harness.user_account.user_identifier, login_name="student"
    )
    authentication_harness.auth_repository.fetch_by_identifier.assert_awaited_once_with(
        user_identifier=authentication_harness.user_account.user_identifier
    )
