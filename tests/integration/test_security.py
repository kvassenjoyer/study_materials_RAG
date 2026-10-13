# Test-only credential literals are intentional.
# ruff: noqa: S106
import datetime
import uuid

import jwt
import pytest

from study_rag.adapters.security import ArgonPasswordSecurity, JwtAccessTokenCodec
from study_rag.domain import entities, errors


_TOKEN_SECRET = "test-secret-long-enough-for-hs256-" * 2


def test_argon_hash_verifies_only_original_password() -> None:
    password_security = ArgonPasswordSecurity()
    password_hash = password_security.hash_password(password_text="correct-horse-battery")

    assert password_hash.startswith("$argon2id$")
    assert password_security.verify_password(password_text="correct-horse-battery", password_hash=password_hash)
    assert not password_security.verify_password(password_text="different-password", password_hash=password_hash)
    assert not password_security.verify_password(password_text="correct-horse-battery", password_hash="corrupted")


def test_access_token_roundtrip_contains_required_claims() -> None:
    token_codec = JwtAccessTokenCodec(secret_value=_TOKEN_SECRET)
    tenant_identity = entities.TenantIdentity(user_identifier=uuid.uuid4(), login_name="learner@example.test")

    auth_token = token_codec.issue_token(tenant_identity=tenant_identity)

    assert token_codec.decode_token(access_token=auth_token.access_token) == tenant_identity.user_identifier
    decoded_claims = jwt.decode(auth_token.access_token, _TOKEN_SECRET, algorithms=["HS256"], audience="study-rag-api")
    assert {"sub", "exp", "iat", "iss", "aud"} <= decoded_claims.keys()
    assert auth_token.token_type == "bearer"  # noqa: S105 - OAuth protocol constant


@pytest.mark.parametrize(
    "invalid_case",
    ["expired", "missing_iat", "wrong_issuer", "wrong_audience", "invalid_subject", "wrong_algorithm", "bad_signature"],
)
def test_invalid_tokens_rejected(invalid_case: str) -> None:
    timestamp_value = datetime.datetime.now(datetime.UTC)
    token_claims: dict[str, str | datetime.datetime] = {
        "sub": str(uuid.uuid4()),
        "exp": timestamp_value + datetime.timedelta(hours=1),
        "iat": timestamp_value,
        "iss": "study-rag",
        "aud": "study-rag-api",
    }
    token_secret = "other-secret-long-enough-for-hs256" * 2 if invalid_case == "bad_signature" else _TOKEN_SECRET
    algorithm_name = "HS384" if invalid_case == "wrong_algorithm" else "HS256"
    if invalid_case == "expired":
        token_claims["exp"] = timestamp_value - datetime.timedelta(seconds=1)
    elif invalid_case == "missing_iat":
        del token_claims["iat"]
    elif invalid_case == "wrong_issuer":
        token_claims["iss"] = "another-service"
    elif invalid_case == "wrong_audience":
        token_claims["aud"] = "another-api"
    elif invalid_case == "invalid_subject":
        token_claims["sub"] = "not-a-uuid"
    encoded_token = jwt.encode(token_claims, token_secret, algorithm=algorithm_name)
    token_codec = JwtAccessTokenCodec(secret_value=_TOKEN_SECRET)

    with pytest.raises(errors.AuthenticationError, match="invalid or expired"):
        token_codec.decode_token(access_token=encoded_token)
