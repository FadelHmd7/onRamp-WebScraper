from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from onramp_testsite import app as site
from onramp_testsite import content


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(site, "LATENCY_SECONDS", 0)
    site.state.reset()
    yield TestClient(site.app)
    site.state.reset()


def test_site_has_more_than_500_pages() -> None:
    assert content.ITEM_COUNT + content.CATALOG_PAGES >= 500


def test_items_are_deterministic() -> None:
    assert content.item(42) == content.item(42)
    assert content.item(42) != content.item(43)


def test_robots_disallows_private(client: TestClient) -> None:
    body = client.get("/robots.txt").text
    assert "Disallow: /private/" in body


def test_catalog_pagination(client: TestClient) -> None:
    first = client.get("/catalog?page=1").text
    assert 'rel="next" href="/catalog?page=2"' in first
    assert 'rel="prev"' not in first
    last = client.get(f"/catalog?page={content.CATALOG_PAGES}").text
    assert 'rel="next"' not in last
    assert client.get(f"/catalog?page={content.CATALOG_PAGES + 1}").status_code == 404


def test_item_page_has_structure_and_clutter(client: TestClient) -> None:
    html = client.get("/items/7").text
    for keep in ("<h1>", "<table", "<ul>", 'href="/items/'):
        assert keep in html
    for clutter in ("<nav", "<footer", "ad-banner", "<script>", "<style>"):
        assert clutter in html


def test_js_page_hides_content_from_plain_http(client: TestClient) -> None:
    html = client.get("/js/items/7").text
    assert content.item(7).title not in html
    assert client.get("/api/items/7").json()["title"] == content.item(7).title


def test_conditional_requests_return_304(client: TestClient) -> None:
    first = client.get("/items/3")
    etag, modified = first.headers["etag"], first.headers["last-modified"]
    assert client.get("/items/3", headers={"If-None-Match": etag}).status_code == 304
    assert client.get("/items/3", headers={"If-Modified-Since": modified}).status_code == 304


def test_editing_a_page_changes_its_etag(client: TestClient) -> None:
    before = client.get("/editable/pricing")
    client.put(
        "/_admin/editable/pricing",
        json={"title": "Pricing", "paragraphs": ["The basic plan now costs $12."]},
    )
    after = client.get("/editable/pricing", headers={"If-None-Match": before.headers["etag"]})
    assert after.status_code == 200
    assert "$12" in after.text


def test_deleted_page_returns_404(client: TestClient) -> None:
    assert client.delete("/_admin/editable/team").status_code == 200
    assert client.get("/editable/team").status_code == 404


def test_flaky_fails_n_times_then_succeeds(client: TestClient) -> None:
    codes = [client.get("/flaky/1?fail=2").status_code for _ in range(3)]
    assert codes == [503, 503, 200]


def test_throttle_sends_retry_after(client: TestClient) -> None:
    response = client.get("/throttle/1?retry_after=30")
    assert response.status_code == 429
    assert 1 <= int(response.headers["retry-after"]) <= 30


def test_broken_always_fails(client: TestClient) -> None:
    assert {client.get("/broken/1").status_code for _ in range(3)} == {503}


def test_reset_restores_state(client: TestClient) -> None:
    client.get("/flaky/1?fail=1")
    client.delete("/_admin/editable/team")
    client.post("/_admin/reset")
    assert client.get("/flaky/1?fail=1").status_code == 503
    assert client.get("/editable/team").status_code == 200
