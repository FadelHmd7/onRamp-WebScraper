"""A3: documents (filtered, paginated) and raw pages with their fetch details."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.storage.versions import mark_removed
from onramp_scraper.worker.runs import start_crawl
from onramp_testsite import app as testsite
from onramp_testsite import content
from tests.integration.conftest import VIEWER, login
from tests.integration.harness import LocalCrawl, local_site

pytestmark = pytest.mark.integration


@pytest.fixture
def crawled(
    api: TestClient, pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> LocalCrawl:
    """Five item pages crawled into the database."""
    testsite.state.reset()
    crawl = LocalCrawl(pg_engine, redis_client, local_site(live_testsite, max_depth=0))
    seeds = [f"{live_testsite}/items/{n}" for n in range(1, 6)]
    start_crawl(crawl.context(), "testsite", seeds=seeds)
    crawl.run(workers=2)
    return crawl


def page_id(sessions: sessionmaker[Session], url: str) -> int:
    with sessions() as session:
        found = session.scalar(text("SELECT id FROM pages WHERE url = :u"), {"u": url})
    assert found is not None
    return int(found)


def test_documents_are_listed_newest_first_and_paginated(
    api: TestClient, crawled: LocalCrawl
) -> None:
    viewer = login(api, VIEWER)
    everything = api.get("/documents", headers=viewer).json()
    assert everything["total"] == 5
    times = [d["crawled_at"] for d in everything["items"]]
    assert times == sorted(times, reverse=True)
    first = api.get("/documents", params={"limit": 2}, headers=viewer).json()
    rest = api.get("/documents", params={"limit": 2, "offset": 2}, headers=viewer).json()
    assert [d["id"] for d in first["items"] + rest["items"]] == [
        d["id"] for d in everything["items"][:4]
    ]
    assert first["items"][0].keys() >= {"id", "url", "title", "version", "content_hash", "status"}


def test_documents_filter_by_site_status_and_time(
    api: TestClient, crawled: LocalCrawl, sessions: sessionmaker[Session], live_testsite: str
) -> None:
    viewer = login(api, VIEWER)

    def total(**params: str) -> int:
        return int(api.get("/documents", params=params, headers=viewer).json()["total"])

    assert total(site="testsite") == 5
    assert total(site="books-toscrape") == 0
    with sessions.begin() as session:
        mark_removed(session, f"{live_testsite}/items/1", datetime.now(UTC))
    assert (total(status="active"), total(status="removed")) == (4, 1)
    hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert total(changed_after=hour_ago) == 5
    assert total(changed_after=soon) == 0
    assert total(changed_before=hour_ago) == 0
    assert api.get("/documents", params={"status": "gone"}, headers=viewer).status_code == 422


def test_a_document_has_its_blocks_fields_and_links(
    api: TestClient, crawled: LocalCrawl, sessions: sessionmaker[Session], live_testsite: str
) -> None:
    url = f"{live_testsite}/items/3"
    doc_id = page_id(sessions, url)
    doc = api.get(f"/documents/{doc_id}", headers=login(api, VIEWER)).json()
    item = content.item(3)
    assert (doc["id"], doc["url"], doc["title"], doc["version"], doc["versions"]) == (
        doc_id,
        url,
        item.title,
        1,
        1,
    )
    kinds = {block["type"] for block in doc["blocks"]}
    assert {"heading", "paragraph", "list", "table"} <= kinds  # structure kept, table included
    table = next(b for b in doc["blocks"] if b["type"] == "table")
    assert ["SKU", item.sku] in table["rows"]
    assert doc["fields"] == {"price": float(item.price), "sku": item.sku}
    assert doc["links"] and all(link["url"].startswith("http") for link in doc["links"])
    assert doc["raw"] == f"/pages/{doc_id}/raw"


def test_raw_page_has_the_html_and_fetch_details(
    api: TestClient, crawled: LocalCrawl, sessions: sessionmaker[Session], live_testsite: str
) -> None:
    url = f"{live_testsite}/items/2"
    raw = api.get(f"/pages/{page_id(sessions, url)}/raw", headers=login(api, VIEWER)).json()
    served = httpx.get(url)
    assert raw["html"] == served.text  # byte for byte what the site sent
    assert raw["fetch"]["status_code"] == 200 and raw["fetch"]["outcome"] == "ok"
    assert raw["headers"]["etag"] == served.headers["etag"]
    assert raw["elapsed_ms"] >= 0 and raw["error"] is None
    assert [f["id"] for f in raw["history"]] == [raw["fetch"]["id"]]


def test_raw_defaults_to_the_latest_fetch_with_html(
    api: TestClient, crawled: LocalCrawl, sessions: sessionmaker[Session], live_testsite: str
) -> None:
    """An incremental re-crawl gets a 304 (no body); raw still shows the stored HTML."""
    url = f"{live_testsite}/items/2"
    start_crawl(crawled.context(), "testsite", "incremental", seeds=[url])
    crawled.run(workers=1)
    viewer = login(api, VIEWER)
    raw = api.get(f"/pages/{page_id(sessions, url)}/raw", headers=viewer).json()
    assert [f["outcome"] for f in raw["history"]] == ["not_modified", "ok"]
    assert raw["fetch"]["outcome"] == "ok" and raw["html"]
    not_modified = raw["history"][0]["id"]
    picked = api.get(
        f"/pages/{page_id(sessions, url)}/raw", params={"fetch_id": not_modified}, headers=viewer
    ).json()
    assert (picked["fetch"]["status_code"], picked["html"]) == (304, None)


def test_raw_html_is_json_never_served_as_a_page(
    api: TestClient, crawled: LocalCrawl, sessions: sessionmaker[Session], live_testsite: str
) -> None:
    response = api.get(
        f"/pages/{page_id(sessions, f'{live_testsite}/items/1')}/raw", headers=login(api, VIEWER)
    )
    assert response.headers["content-type"] == "application/json"
    assert "<script" in response.json()["html"]  # the site's script travels as inert text


def test_unknown_documents_and_pages_are_404(api: TestClient) -> None:
    viewer = login(api, VIEWER)
    assert api.get("/documents/999999", headers=viewer).status_code == 404
    assert api.get("/pages/999999/raw", headers=viewer).status_code == 404
