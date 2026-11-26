"""Log in, and see who you are."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from onramp_scraper.api.deps import CurrentUser, SessionDep, SettingsDep
from onramp_scraper.api.security import create_token
from onramp_scraper.passwords import verify_password
from onramp_scraper.storage.users import get_user

router = APIRouter(prefix="/auth", tags=["auth"])


class Token(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 (the OAuth2 token type, not a secret)
    expires_in: int  # seconds
    role: str


class Me(BaseModel):
    username: str
    role: str


@router.post("/token", response_model=Token)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
    settings: SettingsDep,
) -> Token:
    """Exchange a username and password (form fields) for a bearer token.

    The same request is made by Swagger's "Authorize" button, the web UI and
    `curl -d username=... -d password=...`.
    """
    user = get_user(session, form.username)
    if not verify_password(user.password_hash if user else None, form.password) or user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token, lifetime = create_token(user.username, user.role, settings)
    return Token(access_token=token, expires_in=lifetime, role=user.role)


@router.get("/me", response_model=Me)
def me(user: CurrentUser) -> Me:
    """The logged-in user and role."""
    return Me(username=user.username, role=user.role)
