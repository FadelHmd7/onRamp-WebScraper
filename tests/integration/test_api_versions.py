"""A4: a document's version history, and any old version in full."""

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import HttpUrl
from redis import Redis
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.worker.runs import start_crawl
from onramp_testsite import app as testsite
from tests.integration.conftest import VIEWER, login
from tests.integration.harness import LocalCrawl, local_site

pytestmark = pytest.mark.integration


def test_versions_list_every_change_and_keep_the_old_content(
    api: TestClient,
    pg_engine: Engine,
    redis_client: Redis,
    live_testsite: str,
    sessions: sessionmaker[Session],
) -> None:
    testsite.state.reset()
    url = f"{live_testsite}/editable/pricing"
    site = local_site(live_testsite, seeds=[HttpUrl(url)], max_depth=0)
    crawl = LocalCrawl(pg_engine, redis_client, site)
    for price in (None, "$12", "$15"):
        if price:
            body = {"title": "Pricing", "paragraphs": [f"The basic plan costs {price} per month."]}
            httpx.put(f"{live_testsite}/_admin/editable/pricing", json=body).raise_for_status()
        start_crawl(crawl.context(), "testsite")
        crawl.run(workers=1)
    start_crawl(crawl.context(), "testsite")  # unchanged: no new version
    crawl.run(workers=1)

    with sessions() as session:
        doc_id = session.scalar(text("SELECT id FROM pages WHERE url = :u"), {"u": url})
    viewer = login(api, VIEWER)
    history = api.get(f"/documents/{doc_id}/versions", headers=viewer).json()
    assert (history["current_version"], history["status"]) == (3, "active")
    versions = history["versions"]
    assert [v["version"] for v in versions] == [3, 2, 1]  # newest first, unchanged crawl added none
    assert [v["current"] for v in versions] == [True, False, False]
    assert len({v["content_hash"] for v in versions}) == 3
    times = [v["crawled_at"] for v in versions]
    assert times == sorted(times, reverse=True)
    assert all(v["crawl_id"] for v in versions)

    first = api.get(f"/documents/{doc_id}/versions/1", headers=viewer).json()
    current = api.get(f"/documents/{doc_id}", headers=viewer).json()
    texts = [b.get("text", "") for b in first["blocks"]]
    assert "The basic plan costs $10 per month." in texts  # the original is kept
    assert "$15" in str(current["blocks"]) and current["version"] == 3
    assert first["current"] is False and first["fields"] == {}

    assert api.get(f"/documents/{doc_id}/versions/9", headers=viewer).status_code == 404
    assert api.get("/documents/999999/versions", headers=viewer).status_code == 404
