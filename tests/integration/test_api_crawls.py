"""A2: admins start crawls; viewers read them but cannot start one."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.api.deps import crawl_context
from onramp_testsite import app as testsite
from tests.integration.conftest import ADMIN, VIEWER, login
from tests.integration.harness import LocalCrawl, local_site

pytestmark = pytest.mark.integration


@pytest.fixture
def local(
    api: TestClient, pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> Iterator[LocalCrawl]:
    """Crawls started through the API run on worker threads against the live test site."""
    testsite.state.reset()
    crawl = LocalCrawl(pg_engine, redis_client, local_site(live_testsite, max_depth=0))
    api.app.dependency_overrides[crawl_context] = crawl.context  # type: ignore[attr-defined]
    yield crawl


def test_admin_starts_a_crawl_and_follows_it_to_the_end(
    api: TestClient, local: LocalCrawl, live_testsite: str
) -> None:
    admin = login(api, ADMIN)
    seeds = [f"{live_testsite}/items/1", f"{live_testsite}/items/2"]
    started = api.post("/crawls", json={"site": "testsite", "seeds": seeds}, headers=admin)
    assert started.status_code == 202
    body = started.json()
    assert started.headers["location"] == f"/crawls/{body['id']}"
    assert (body["status"], body["mode"], body["total"], body["done"]) == (
        "running",
        "full",
        2,
        0,
    )

    local.run(workers=2)
    detail = api.get(f"/crawls/{body['id']}", headers=login(api, VIEWER)).json()
    assert (detail["status"], detail["done"], detail["counts"]["fetched"]) == ("finished", 2, 2)
    assert detail["recent_errors"] == []
    assert detail["seconds"] is not None and detail["pages_per_minute"] > 0


def test_the_crawl_records_who_started_it(
    api: TestClient, local: LocalCrawl, sessions: sessionmaker[Session], live_testsite: str
) -> None:
    body = api.post(
        "/crawls",
        json={"site": "testsite", "seeds": [f"{live_testsite}/items/1"]},
        headers=login(api, ADMIN),
    ).json()
    with sessions() as session:
        who = session.scalar(
            text(
                "SELECT u.username FROM crawls c JOIN users u ON u.id = c.created_by "
                "WHERE c.id = :c"
            ),
            {"c": body["id"]},
        )
    assert who == ADMIN[0]


def test_viewer_cannot_start_a_crawl(api: TestClient, local: LocalCrawl) -> None:
    response = api.post("/crawls", json={"site": "testsite"}, headers=login(api, VIEWER))
    assert response.status_code == 403
    assert local.outstanding == 0  # nothing was sent


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"site": "nope"}, "unknown site 'nope'"),
        ({"site": "testsite", "seeds": ["https://example.com/"]}, "seeds outside site"),
        ({"site": "testsite", "max_depth": 999}, "max_depth must be between 0"),
    ],
)
def test_bad_crawl_requests_get_422(
    api: TestClient, local: LocalCrawl, body: dict[str, object], message: str
) -> None:
    response = api.post("/crawls", json=body, headers=login(api, ADMIN))
    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_crawl_errors_are_listed_with_their_reason(
    api: TestClient, local: LocalCrawl, live_testsite: str
) -> None:
    admin = login(api, ADMIN)
    seeds = [f"{live_testsite}/items/99999", f"{live_testsite}/items/3"]  # a 404 and a page
    crawl_id = api.post("/crawls", json={"site": "testsite", "seeds": seeds}, headers=admin).json()[
        "id"
    ]
    local.run(workers=1)
    detail = api.get(f"/crawls/{crawl_id}", headers=admin).json()
    assert detail["errors"] == 1
    [error] = detail["recent_errors"]
    assert (error["url"], error["status"], error["last_error"]) == (
        f"{live_testsite}/items/99999",
        "failed",
        "HTTP 404",
    )


def test_list_is_paginated_and_filtered(
    api: TestClient, local: LocalCrawl, live_testsite: str
) -> None:
    admin = login(api, ADMIN)
    ids = [
        api.post(
            "/crawls",
            json={"site": "testsite", "seeds": [f"{live_testsite}/items/{n}"]},
            headers=admin,
        ).json()["id"]
        for n in range(1, 4)
    ]
    viewer = login(api, VIEWER)
    first = api.get("/crawls", params={"limit": 2}, headers=viewer).json()
    assert (first["total"], first["limit"], first["offset"]) == (3, 2, 0)
    assert [c["id"] for c in first["items"]] == ids[::-1][:2]  # newest first
    second = api.get("/crawls", params={"limit": 2, "offset": 2}, headers=viewer).json()
    assert [c["id"] for c in second["items"]] == [ids[0]]

    assert (
        api.get("/crawls", params={"site": "books-toscrape"}, headers=viewer).json()["total"] == 0
    )
    assert api.get("/crawls", params={"status": "running"}, headers=viewer).json()["total"] == 3
    assert api.get("/crawls", params={"status": "bogus"}, headers=viewer).status_code == 422
    assert api.get("/crawls", params={"limit": 101}, headers=viewer).status_code == 422


def test_unknown_crawl_is_404(api: TestClient) -> None:
    viewer = login(api, VIEWER)
    assert (
        api.get("/crawls/00000000-0000-0000-0000-000000000000", headers=viewer).status_code == 404
    )
    assert api.get("/crawls/not-a-uuid", headers=viewer).status_code == 422


def test_sites_lists_the_configuration(api: TestClient) -> None:
    sites = api.get("/sites", headers=login(api, VIEWER)).json()
    by_key = {s["key"]: s for s in sites}
    assert set(by_key) == {"books-toscrape", "inventaire", "standard-ebooks", "testsite"}
    assert by_key["inventaire"]["fetch_mode"] == "browser"
    assert by_key["inventaire"]["requests_per_second"] == 0.1
