"""Access tokens (JWT, HS256). Passwords are in ``onramp_scraper/passwords.py``.

A login returns a signed JWT with the username, role and expiry. The API
still loads the user from the database on every request (``deps.py``), so a
deleted user or a changed role takes effect at once, not at expiry.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from onramp_scraper.config import Settings

ALGORITHM = "HS256"


@dataclass(frozen=True)
class TokenClaims:
    username: str
    role: str
    expires_at: datetime


def create_token(
    username: str, role: str, settings: Settings, now: datetime | None = None
) -> tuple[str, int]:
    """A signed token and its lifetime in seconds."""
    issued = now or datetime.now(UTC)
    lifetime = timedelta(minutes=settings.jwt_ttl_minutes)
    claims = {"sub": username, "role": role, "iat": issued, "exp": issued + lifetime}
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)
    return token, int(lifetime.total_seconds())


def decode_token(token: str, settings: Settings) -> TokenClaims:
    """Raises ``jwt.InvalidTokenError`` when the token is forged, expired or malformed."""
    claims = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[ALGORITHM],  # never trust the token's own "alg" header
        options={"require": ["sub", "role", "exp", "iat"]},
    )
    return TokenClaims(
        username=str(claims["sub"]),
        role=str(claims["role"]),
        expires_at=datetime.fromtimestamp(claims["exp"], UTC),
    )
