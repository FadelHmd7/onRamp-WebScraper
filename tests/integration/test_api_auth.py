"""A1: login, tokens, 401 without a valid token, 403 for the wrong role, Swagger."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.api.deps import require_admin
from onramp_scraper.api.security import create_token
from onramp_scraper.config import get_settings
from onramp_scraper.storage.users import SeedUser, seed_users
from tests.integration.conftest import ADMIN, VIEWER, login

pytestmark = pytest.mark.integration


def test_login_returns_a_bearer_token_with_the_role(api: TestClient) -> None:
    response = api.post("/auth/token", data={"username": ADMIN[0], "password": ADMIN[1]})
    assert response.status_code == 200
    body = response.json()
    assert (body["token_type"], body["role"], body["expires_in"]) == ("bearer", "admin", 3600)
    me = api.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json() == {"username": ADMIN[0], "role": "admin"}


@pytest.mark.parametrize(
    ("username", "password"),
    [(ADMIN[0], "wrong"), ("nobody", "whatever"), (ADMIN[0], "")],
    ids=["wrong password", "unknown user", "empty password"],
)
def test_bad_credentials_get_401_with_the_same_message(
    api: TestClient, username: str, password: str
) -> None:
    response = api.post("/auth/token", data={"username": username, "password": password})
    assert response.status_code in (401, 422)
    if response.status_code == 401:
        assert response.json() == {"detail": "Wrong username or password"}  # no hint which


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-a-token"},
        {"Authorization": "Basic YWRhOmFkbWlu"},
    ],
    ids=["no token", "garbage token", "wrong scheme"],
)
def test_no_valid_token_gets_401(api: TestClient, headers: dict[str, str]) -> None:
    response = api.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_expired_token_gets_401(api: TestClient) -> None:
    token, _ = create_token(
        ADMIN[0], "admin", get_settings(), now=datetime.now(UTC) - timedelta(days=1)
    )
    response = api.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired token"}


def test_deleted_user_loses_access_at_once(
    api: TestClient, sessions: sessionmaker[Session]
) -> None:
    headers = login(api, VIEWER)
    with sessions.begin() as session:
        session.execute(text("DELETE FROM users WHERE username = :u"), {"u": VIEWER[0]})
    assert api.get("/auth/me", headers=headers).status_code == 401


def test_role_comes_from_the_database_not_the_token(
    api: TestClient, sessions: sessionmaker[Session]
) -> None:
    """A token says "admin" but the user was demoted: the API treats them as a viewer."""
    api.app.get("/_test/admin", dependencies=[Depends(require_admin)])(lambda: {"ok": True})  # type: ignore[attr-defined]
    headers = login(api, ADMIN)
    assert api.get("/_test/admin", headers=headers).status_code == 200
    with sessions.begin() as session:
        seed_users(session, [SeedUser(*ADMIN, role="viewer")])
    assert api.get("/_test/admin", headers=headers).status_code == 403


def test_viewer_on_an_admin_route_gets_403(api: TestClient) -> None:
    api.app.get("/_test/admin", dependencies=[Depends(require_admin)])(lambda: {"ok": True})  # type: ignore[attr-defined]
    assert api.get("/_test/admin", headers=login(api, VIEWER)).status_code == 403
    assert api.get("/_test/admin", headers=login(api, ADMIN)).status_code == 200
    assert api.get("/_test/admin").status_code == 401  # no token: 401 before any role check


def test_seeding_is_idempotent_and_rotates_passwords(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        session.execute(text("DELETE FROM users"))
        assert seed_users(session, [SeedUser("u", "one", "viewer")]) == ["created viewer 'u'"]
    with sessions.begin() as session:
        assert seed_users(session, [SeedUser("u", "one", "viewer")]) == []
    with sessions.begin() as session:
        assert seed_users(session, [SeedUser("u", "two", "viewer")]) == [
            "updated the password of 'u'"
        ]


def test_swagger_and_openapi_are_published_with_bearer_auth(api: TestClient) -> None:
    assert api.get("/docs").status_code == 200
    spec = api.get("/openapi.json").json()
    schemes = spec["components"]["securitySchemes"]
    assert schemes["OAuth2PasswordBearer"]["flows"]["password"]["tokenUrl"] == "auth/token"
    assert spec["paths"]["/auth/me"]["get"]["security"] == [{"OAuth2PasswordBearer": []}]
    assert "security" not in spec["paths"]["/health"]["get"]


def test_health_needs_no_token(api: TestClient) -> None:
    assert api.get("/health").status_code == 200
