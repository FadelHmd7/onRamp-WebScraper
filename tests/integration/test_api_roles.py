"""A8: every endpoint, called with no token, as a viewer and as an admin.

The matrix is built from the app's own operations (its OpenAPI document,
which lists every route it serves) and compared with the table below, the
spec's Task 5 table. A new route that is not in the table fails
``test_every_route_is_in_the_matrix``, so nothing can be added without
deciding who may call it.
"""

import re
from typing import Literal

import pytest
from fastapi.testclient import TestClient

from onramp_scraper.api.app import create_app
from tests.integration.conftest import ADMIN, VIEWER, login

pytestmark = pytest.mark.integration

Access = Literal["public", "viewer", "admin"]

MATRIX: dict[tuple[str, str], Access] = {
    ("GET", "/health"): "public",
    ("POST", "/auth/token"): "public",
    ("GET", "/auth/me"): "viewer",
    ("GET", "/sites"): "viewer",
    ("POST", "/crawls"): "admin",
    ("GET", "/crawls"): "viewer",
    ("GET", "/crawls/{crawl_id}"): "viewer",
    ("GET", "/pages/{page_id}/raw"): "viewer",
    ("GET", "/documents"): "viewer",
    ("GET", "/documents/{document_id}"): "viewer",
    ("GET", "/documents/{document_id}/versions"): "viewer",
    ("GET", "/documents/{document_id}/versions/{version}"): "viewer",
    ("GET", "/search"): "viewer",
    ("GET", "/dlq"): "admin",
    ("POST", "/dlq/{dead_letter_id}/retry"): "admin",
}

# Harmless values: ids that do not exist, a site that does not exist. An allowed
# caller gets past the role check (to a 200, 404 or 422) and nothing is started.
PATH_VALUES = {
    "crawl_id": "00000000-0000-0000-0000-000000000000",
    "page_id": "999999",
    "document_id": "999999",
    "version": "1",
    "dead_letter_id": "999999",
}
BODIES = {("POST", "/crawls"): {"site": "no-such-site"}}
QUERIES = {("GET", "/search"): {"q": "anything"}}


def app_routes() -> set[tuple[str, str]]:
    """Every operation the app serves. (FastAPI 0.165 keeps included routers lazily,
    so ``app.routes`` does not list them; the OpenAPI document does.)"""
    paths = create_app().openapi()["paths"]
    return {(method.upper(), path) for path, operations in paths.items() for method in operations}


def call(api: TestClient, method: str, path: str, headers: dict[str, str]) -> int:
    url = re.sub(r"\{(\w+)\}", lambda m: PATH_VALUES[m.group(1)], path)
    response = api.request(
        method,
        url,
        headers=headers,
        json=BODIES.get((method, path)),
        params=QUERIES.get((method, path)),
    )
    return response.status_code


CASES = sorted((m, p, access) for (m, p), access in MATRIX.items() if access != "public")


def test_every_route_is_in_the_matrix() -> None:
    assert app_routes() == set(MATRIX)


@pytest.mark.parametrize(("method", "path", "access"), CASES, ids=[f"{m} {p}" for m, p, _ in CASES])
def test_role_matrix(api: TestClient, method: str, path: str, access: Access) -> None:
    viewer, admin = login(api, VIEWER), login(api, ADMIN)

    assert call(api, method, path, {}) == 401  # no token
    assert call(api, method, path, {"Authorization": "Bearer forged.token.value"}) == 401

    viewer_status = call(api, method, path, viewer)
    if access == "admin":
        assert viewer_status == 403
    else:
        assert viewer_status not in (401, 403), viewer_status

    assert call(api, method, path, admin) not in (401, 403)  # admins can call everything


def test_public_routes_need_no_token(api: TestClient) -> None:
    assert api.get("/health").status_code == 200
    assert api.post("/auth/token", data={"username": "x", "password": "y"}).status_code == 401
    assert api.get("/docs").status_code == 200
    assert api.get("/openapi.json").status_code == 200


def test_openapi_marks_exactly_the_protected_routes() -> None:
    spec = create_app().openapi()
    for (method, path), access in MATRIX.items():
        operation = spec["paths"][path][method.lower()]
        secured = operation.get("security") == [{"OAuth2PasswordBearer": []}]
        assert secured == (access != "public"), (method, path)
