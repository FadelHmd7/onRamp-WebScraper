"""A5: full-text search through the GIN index, with safe highlighted snippets."""

import httpx
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from onramp_scraper.storage.search import explain
from onramp_scraper.worker.runs import start_crawl
from onramp_testsite import app as testsite
from onramp_testsite import content
from tests.integration.conftest import VIEWER, login
from tests.integration.harness import LocalCrawl, local_site

pytestmark = pytest.mark.integration


def put_page(base: str, slug: str, title: str, *paragraphs: str) -> None:
    body = {"title": title, "paragraphs": list(paragraphs)}
    httpx.put(f"{base}/_admin/editable/{slug}", json=body).raise_for_status()


@pytest.fixture
def corpus(
    api: TestClient, pg_engine: Engine, redis_client: Redis, live_testsite: str
) -> LocalCrawl:
    """Ten catalog items and three hand-written pages, crawled."""
    testsite.state.reset()
    put_page(live_testsite, "pricing", "Whale watching", "Trips leave daily from the harbour.")
    put_page(live_testsite, "team", "Our team", "Ahab once saw a whale near the harbour.")
    put_page(
        live_testsite,
        "changelog",
        "Changelog",
        "Fixed: <script>alert('x')</script> & a < b for unicorns.",
    )
    seeds = [f"{live_testsite}/items/{n}" for n in range(1, 11)] + [
        f"{live_testsite}/editable/{slug}" for slug in ("pricing", "team", "changelog")
    ]
    crawl = LocalCrawl(pg_engine, redis_client, local_site(live_testsite, max_depth=0))
    start_crawl(crawl.context(), "testsite", seeds=seeds)
    crawl.run(workers=2)
    return crawl


def search(api: TestClient, **params: object) -> dict:  # type: ignore[type-arg]
    response = api.get("/search", params=params, headers=login(api, VIEWER))
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_finds_a_document_by_its_words_with_a_highlighted_snippet(
    api: TestClient, corpus: LocalCrawl
) -> None:
    item = content.item(4)
    result = search(api, q=item.title)
    assert result["total"] >= 1
    top = result["items"][0]
    assert top["title"] == item.title
    assert top["url"].endswith("/items/4")
    assert "<mark>" in top["snippet"]


def test_title_matches_rank_above_body_matches(api: TestClient, corpus: LocalCrawl) -> None:
    result = search(api, q="whale")
    assert [hit["title"] for hit in result["items"]] == ["Whale watching", "Our team"]
    assert result["items"][0]["rank"] > result["items"][1]["rank"]
    assert "<mark>whale</mark>" in result["items"][1]["snippet"]


def test_stemming_and_web_search_syntax(api: TestClient, corpus: LocalCrawl) -> None:
    assert search(api, q="whales")["total"] == 2  # english stemming: whales -> whale
    assert search(api, q="whale -ahab")["total"] == 1  # exclusion
    assert search(api, q='"leave daily"')["total"] == 1  # phrase
    assert search(api, q='"daily leave"')["total"] == 0  # wrong order is not the phrase
    assert search(api, q="unicorns or ahab")["total"] == 2


def test_snippets_escape_scraped_markup(api: TestClient, corpus: LocalCrawl) -> None:
    [hit] = search(api, q="unicorns")["items"]
    snippet = hit["snippet"]
    # ts_headline drops tag-like text itself; whatever markup characters remain
    # are escaped, so the only tags in a snippet are our own <mark>s.
    assert "<script" not in snippet
    assert "a &lt; b" in snippet and "&amp;" in snippet and "&#x27;x&#x27;" in snippet
    assert "<mark>unicorns</mark>" in snippet
    assert snippet.replace("<mark>", "").replace("</mark>", "").count("<") == 0


def test_site_filter_and_pagination(api: TestClient, corpus: LocalCrawl) -> None:
    everything = search(api, q="specifications")  # a heading on every item page
    assert everything["total"] >= 10
    assert search(api, q="specifications", site="books-toscrape")["total"] == 0
    first = search(api, q="specifications", limit=3)
    second = search(api, q="specifications", limit=3, offset=3)
    ids = [h["id"] for h in first["items"] + second["items"]]
    assert ids == [h["id"] for h in everything["items"][:6]] and len(set(ids)) == 6


def test_only_current_versions_of_active_pages_are_searched(
    api: TestClient, corpus: LocalCrawl, live_testsite: str, sessions: sessionmaker[Session]
) -> None:
    put_page(live_testsite, "pricing", "Boat trips", "Trips leave daily from the harbour.")
    seed = f"{live_testsite}/editable/pricing"
    start_crawl(corpus.context(), "testsite", seeds=[seed])
    corpus.run(workers=1)
    assert search(api, q="boat")["total"] == 1
    assert [h["title"] for h in search(api, q="whale")["items"]] == ["Our team"]  # v1 not found

    with sessions.begin() as session:
        session.execute(
            text("UPDATE pages SET status = 'removed' WHERE url LIKE '%/editable/team'")
        )
    assert search(api, q="whale")["total"] == 0


def test_queries_without_searchable_words_return_nothing(
    api: TestClient, corpus: LocalCrawl
) -> None:
    assert search(api, q="the and of")["total"] == 0  # only stop words
    assert search(api, q="'); DROP TABLE pages; --")["total"] == 0  # just text to the parser
    viewer = login(api, VIEWER)
    assert api.get("/search", params={"q": ""}, headers=viewer).status_code == 422
    assert api.get("/search", params={"q": "x" * 201}, headers=viewer).status_code == 422


def test_the_match_uses_the_gin_index(corpus: LocalCrawl, sessions: sessionmaker[Session]) -> None:
    """The planner prefers a sequential scan on a tiny table; with that ruled out, the
    full-text match must be an index lookup on ix_document_versions_search."""
    with sessions() as session:
        session.execute(text("SET enable_seqscan = off"))
        plan = explain(session, "whale")
    assert "ix_document_versions_search" in plan
    assert "Bitmap Index Scan" in plan
