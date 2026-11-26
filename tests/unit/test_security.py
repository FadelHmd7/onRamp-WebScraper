"""Passwords, tokens and the refusal of development secrets outside development."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import ValidationError

from onramp_scraper.api.security import ALGORITHM, create_token, decode_token
from onramp_scraper.config import Settings
from onramp_scraper.passwords import hash_password, verify_password

SETTINGS = Settings(jwt_secret="x" * 40, jwt_ttl_minutes=30)


def test_password_hash_is_argon2_salted_and_verifies() -> None:
    first, second = hash_password("s3cret"), hash_password("s3cret")
    assert first.startswith("$argon2id$")
    assert first != second  # a fresh salt each time
    assert verify_password(first, "s3cret")
    assert not verify_password(first, "wrong")


def test_unknown_user_and_broken_hash_never_verify() -> None:
    assert not verify_password(None, "not-a-real-password")  # even the dummy's own password
    assert not verify_password("not-a-hash", "anything")


def test_token_round_trip() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    token, lifetime = create_token("ada", "admin", SETTINGS, now=now)
    assert lifetime == 30 * 60
    claims = decode_token(token, SETTINGS)
    assert (claims.username, claims.role) == ("ada", "admin")
    assert claims.expires_at == now + timedelta(minutes=30)


def test_expired_token_is_refused() -> None:
    token, _ = create_token("ada", "admin", SETTINGS, now=datetime.now(UTC) - timedelta(hours=2))
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token, SETTINGS)


def test_token_signed_with_another_secret_is_refused() -> None:
    token, _ = create_token("ada", "admin", Settings(jwt_secret="y" * 40))
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(token, SETTINGS)


def test_unsigned_token_is_refused() -> None:
    """alg "none" is the classic JWT forgery; only HS256 is accepted."""
    now = datetime.now(UTC)
    forged = jwt.encode(
        {"sub": "ada", "role": "admin", "iat": now, "exp": now + timedelta(hours=1)},
        key=None,
        algorithm="none",
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(forged, SETTINGS)


def test_token_without_a_role_is_refused() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": "ada", "iat": now, "exp": now + timedelta(hours=1)},
        SETTINGS.jwt_secret.get_secret_value(),
        algorithm=ALGORITHM,
    )
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_token(token, SETTINGS)


def test_development_defaults_are_refused_outside_development() -> None:
    assert Settings(app_env="development")  # fine locally
    with pytest.raises(ValidationError, match=r"JWT_SECRET.*ADMIN_PASSWORD.*VIEWER_PASSWORD"):
        Settings(app_env="production")
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(
            app_env="production", jwt_secret="short", admin_password="a1", viewer_password="v1"
        )
    assert Settings(
        app_env="production", jwt_secret="z" * 32, admin_password="a1", viewer_password="v1"
    )
