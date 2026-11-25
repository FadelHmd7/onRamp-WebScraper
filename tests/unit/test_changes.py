"""Required test area 3: change detection.

304 / same ETag / same content hash -> no new version; a different hash -> version + 1.
The database side (locking, kept versions, removal) is in tests/integration/test_versions.py.
"""

from pathlib import Path

import pytest

from onramp_scraper.crawler.fetch import FetchResult, Outcome
from onramp_scraper.processing.changes import (
    Change,
    StoredPage,
    conditional_headers,
    decide,
    unchanged_by_headers,
)
from onramp_scraper.processing.clean import clean_html
from onramp_scraper.processing.document import content_hash
from onramp_scraper.processing.fields import extract_fields
from onramp_scraper.sites import load_sites
from onramp_testsite import content

ROOT = Path(__file__).parents[2]
SITES = load_sites(ROOT / "config" / "sites.yaml")
URL = "http://testsite:8000/items/1"
HASH_A, HASH_B = "a" * 64, "b" * 64
STORED = StoredPage(current_version=3, content_hash=HASH_A, etag='"v3"', last_modified="x")


def ok(etag: str | None = None) -> FetchResult:
    headers = {"etag": etag} if etag else {}
    return FetchResult(url=URL, outcome=Outcome.OK, status=200, body="<p>x</p>", headers=headers)


# ---------------------------------------------------------------- the four rules


def test_first_fetch_is_version_1() -> None:
    for stored in (None, StoredPage()):  # unknown page, or a page row without a version yet
        decision = decide(stored, HASH_A)
        assert (decision.change, decision.version) == (Change.NEW, 1)
        assert decision.is_new_version


def test_304_means_no_new_version() -> None:
    not_modified = FetchResult(url=URL, outcome=Outcome.NOT_MODIFIED, status=304)
    decision = unchanged_by_headers(STORED, not_modified)
    assert decision is not None
    assert (decision.change, decision.version, decision.reason) == (
        Change.UNCHANGED,
        3,
        "not modified (304)",
    )
    assert not decision.is_new_version


def test_same_etag_means_no_new_version_without_hashing() -> None:
    decision = unchanged_by_headers(STORED, ok(etag='"v3"'))
    assert decision is not None
    assert (decision.change, decision.version, decision.reason) == (
        Change.UNCHANGED,
        3,
        "same ETag",
    )


def test_same_content_hash_means_no_new_version() -> None:
    decision = decide(STORED, HASH_A)
    assert (decision.change, decision.version, decision.reason) == (
        Change.UNCHANGED,
        3,
        "same content hash",
    )


def test_changed_hash_is_the_next_version() -> None:
    decision = decide(STORED, HASH_B)
    assert (decision.change, decision.version, decision.reason) == (
        Change.CHANGED,
        4,
        "content hash changed",
    )


# ---------------------------------------------------------------- when headers decide nothing


@pytest.mark.parametrize(
    "fetch",
    [
        ok(),  # no ETag at all (Standard Ebooks, Inventaire)
        ok(etag='"v4"'),  # a different ETag
        ok(etag='W/"v3"'),  # a weak ETag is a different validator
    ],
    ids=["no etag", "new etag", "weak etag"],
)
def test_other_responses_go_on_to_the_hash(fetch: FetchResult) -> None:
    assert unchanged_by_headers(STORED, fetch) is None


def test_headers_never_decide_for_a_page_without_a_version() -> None:
    not_modified = FetchResult(url=URL, outcome=Outcome.NOT_MODIFIED, status=304)
    assert unchanged_by_headers(None, not_modified) is None
    assert unchanged_by_headers(StoredPage(etag='"v3"'), ok(etag='"v3"')) is None


@pytest.mark.parametrize("outcome", [Outcome.RETRY, Outcome.PERMANENT, Outcome.REDIRECT])
def test_failed_fetches_are_not_unchanged(outcome: Outcome) -> None:
    failed = FetchResult(url=URL, outcome=outcome, headers={"etag": '"v3"'})
    assert unchanged_by_headers(STORED, failed) is None


def test_matching_last_modified_alone_is_not_trusted() -> None:
    fetch = FetchResult(
        url=URL, outcome=Outcome.OK, status=200, body="x", headers={"last-modified": "x"}
    )
    assert unchanged_by_headers(StoredPage(current_version=1, last_modified="x"), fetch) is None


def test_content_can_return_to_an_earlier_state_as_a_new_version() -> None:
    """A -> B -> A is three versions: the third state is a real change from the second."""
    v1 = decide(None, HASH_A)
    v2 = decide(StoredPage(current_version=v1.version, content_hash=HASH_A), HASH_B)
    v3 = decide(StoredPage(current_version=v2.version, content_hash=HASH_B), HASH_A)
    assert [v1.version, v2.version, v3.version] == [1, 2, 3]
    assert v3.change is Change.CHANGED


# ---------------------------------------------------------------- conditional requests


def test_conditional_headers_come_from_the_stored_page() -> None:
    assert conditional_headers(STORED) == {"etag": '"v3"', "last_modified": "x"}
    assert conditional_headers(StoredPage(current_version=1)) == {}
    assert conditional_headers(None) == {}
    # Validators without a stored version would let a 304 leave us with nothing.
    assert conditional_headers(StoredPage(etag='"v3"')) == {}


# ---------------------------------------------------------------- hashes of real pages


def _hash_of(html: str) -> str:
    page = clean_html(html)
    return content_hash(page.title, page.blocks, extract_fields(html, SITES["testsite"].fields))


def test_markup_noise_does_not_change_the_hash() -> None:
    """Ads, scripts, comments, whitespace and attributes differ; the content does not."""
    item = content.item(5)
    html = content.layout("x", content.item_body(item))
    noisy = (
        html.replace("<main>", "<main><!-- rendered 12:00:01 --><script>t=1</script>")
        .replace("</main>", '<div class="ad-banner">New offer!</div></main>')
        .replace("<h2>", '<h2   class="x" data-session="abc123">')
        .replace("</p>", "</p>\n\n   ")
    )
    assert noisy != html
    assert _hash_of(noisy) == _hash_of(html)


def test_a_content_edit_changes_the_hash() -> None:
    item = content.item(5)
    html = content.layout("x", content.item_body(item))
    edited = html.replace(item.summary, item.summary + " Now with an extra sentence.")
    assert _hash_of(edited) != _hash_of(html)


def test_a_field_change_alone_changes_the_hash() -> None:
    """The SKU is only in an attribute: every text block stays the same, the hash does not."""
    item = content.item(5)
    html = content.layout("x", content.item_body(item))
    resku = html.replace(f'data-sku="{item.sku}"', 'data-sku="TS-99999"')
    assert resku != html
    assert clean_html(resku).blocks == clean_html(html).blocks
    assert _hash_of(resku) != _hash_of(html)
