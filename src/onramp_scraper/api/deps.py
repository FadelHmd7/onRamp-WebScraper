"""Request dependencies: a database session, the current user, and role checks.

Every router except health and login depends on ``current_user`` (any role)
or ``require_admin``; the role-matrix test (A8) checks that no route slips
through unprotected.

- No token, or a forged, expired or malformed one: **401** with
  ``WWW-Authenticate: Bearer``.
- A valid token whose user no longer exists: **401**.
- A viewer on an admin route: **403**.
"""

from collections.abc import Iterator
from typing import TYPE_CHECKING, Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.api.security import decode_token
from onramp_scraper.config import Settings, get_settings
from onramp_scraper.storage.db import get_engine
from onramp_scraper.storage.models import User
from onramp_scraper.storage.users import get_user

if TYPE_CHECKING:
    from onramp_scraper.worker.pipeline import CrawlContext

# tokenUrl makes Swagger's "Authorize" button log in through POST /auth/token.
oauth2 = OAuth2PasswordBearer(tokenUrl="auth/token", auto_error=False)


def get_sessions() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session(
    sessions: Annotated[sessionmaker[Session], Depends(get_sessions)],
) -> Iterator[Session]:
    with sessions() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_user(
    token: Annotated[str | None, Depends(oauth2)],
    session: SessionDep,
    settings: SettingsDep,
) -> User:
    if token is None:
        raise _unauthorized("Not authenticated")
    try:
        claims = decode_token(token, settings)
    except jwt.InvalidTokenError:
        raise _unauthorized("Invalid or expired token") from None
    user = get_user(session, claims.username)
    if user is None:
        raise _unauthorized("User no longer exists")
    return user  # the role comes from the database, not from the token


def require_admin(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


def crawl_context() -> "CrawlContext":
    """What starting a crawl needs: the frontier, sites and Celery to send jobs.

    Imported on first use, so the API process loads the worker code only
    when an admin actually starts a crawl. Tests replace this dependency.
    """
    from onramp_scraper.worker.app import context

    return context()


CurrentUser = Annotated[User, Depends(current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
