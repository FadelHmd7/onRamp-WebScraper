"""The document schema: real pages pass, and each rule rejects with a readable, logged reason."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from onramp_scraper.processing import document as document_module
from onramp_scraper.processing.clean import clean_html
from onramp_scraper.processing.document import (
    InvalidDocument,
    build_document,
    content_hash,
    document_id,
    validate_document,
)
from onramp_scraper.sites import load_sites
from onramp_testsite import content

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
SITES = load_sites(ROOT / "config" / "sites.yaml")
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

BOOK_URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"


def cleaned(site: str, name: str) -> Any:
    config = SITES[site]
    return clean_html(
        (FIXTURES / site / name).read_text("utf-8"), config.title_selector, config.remove
    )


def valid_data(**changes: Any) -> dict[str, Any]:
    """A valid document as plain data, with ``changes`` applied and the hash recomputed."""
    data: dict[str, Any] = {
        "url": BOOK_URL,
        "site": "books-toscrape",
        "title": "A Light in the Attic",
        "blocks": [
            {"type": "heading", "level": 1, "text": "A Light in the Attic"},
            {"type": "paragraph", "text": "£51.77"},
            {"type": "list", "ordered": False, "items": ["one", "two"]},
            {
                "type": "table",
                "caption": None,
                "header": [],
                "rows": [["UPC", "a897fe39b1053632"]],
                "row_headers": True,
            },
            {"type": "code", "text": "x = 1"},
            {"type": "quote", "text": "A quote."},
        ],
        "fields": {"price": "£51.77", "authors": ["Shel Silverstein"], "info": {"UPC": "a8"}},
        "crawled_at": NOW,
        "version": 1,
    }
    data.update(changes)
    if "content_hash" not in changes:
        data["content_hash"] = content_hash(data["title"], data["blocks"], data["fields"])
    return data


def with_block(block: dict[str, Any]) -> dict[str, Any]:
    return valid_data(blocks=[block])


# ---------------------------------------------------------------- valid documents


@pytest.mark.parametrize(
    ("site", "name", "url"),
    [
        ("books-toscrape", "book-a-light-in-the-attic.html", BOOK_URL),
        ("books-toscrape", "listing-page-1.html", "https://books.toscrape.com/"),
        (
            "standard-ebooks",
            "book-pride-and-prejudice.html",
            "https://standardebooks.org/ebooks/jane-austen/pride-and-prejudice",
        ),
        ("inventaire", "work-pride-and-prejudice.html", "https://inventaire.io/entity/wd:Q170583"),
        ("inventaire", "author-jane-austen.html", "https://inventaire.io/entity/wd:Q36322"),
    ],
)
def test_every_saved_page_becomes_a_valid_document(site: str, name: str, url: str) -> None:
    page = cleaned(site, name)
    doc = build_document(page, url=url, site=SITES[site], version=1, crawled_at=NOW)
    assert doc.title == page.title
    assert doc.text == page.text
    assert [b.model_dump() for b in doc.blocks] == page.blocks
    assert doc.id == document_id(url)


def test_testsite_page_is_valid() -> None:
    page = clean_html(content.layout("x", content.item_body(content.item(1))))
    doc = build_document(
        page, url="http://testsite:8000/item/1", site=SITES["testsite"], version=3, crawled_at=NOW
    )
    assert doc.version == 3


def test_valid_document_from_plain_data() -> None:
    doc = validate_document(valid_data(), SITES["books-toscrape"])
    assert [b.type for b in doc.blocks] == [
        "heading",
        "paragraph",
        "list",
        "table",
        "code",
        "quote",
    ]
    assert doc.fields["authors"] == ["Shel Silverstein"]
    dumped = doc.model_dump()
    assert {"id", "text", "content_hash", "crawled_at", "version"} <= dumped.keys()


def test_id_is_stable_across_versions_and_unique_per_url() -> None:
    v1 = validate_document(valid_data(version=1))
    v2 = validate_document(valid_data(version=2, title="New title"))
    other = validate_document(valid_data(url=BOOK_URL.replace("1000", "1001")))
    assert v1.id == v2.id
    assert v1.id != other.id


def test_content_hash_depends_on_content_only() -> None:
    data = valid_data()
    blocks, fields = data["blocks"], data["fields"]
    same = content_hash("A Light in the Attic", blocks, dict(reversed(fields.items())))
    assert same == data["content_hash"]  # key order does not matter
    assert content_hash("Other", blocks, fields) != data["content_hash"]
    assert content_hash("A Light in the Attic", blocks[:1], fields) != data["content_hash"]
    assert content_hash("A Light in the Attic", blocks, {}) != data["content_hash"]
    # Crawl time and version are not content: a re-crawl of an unchanged page hashes the same.
    later = valid_data(crawled_at=NOW + timedelta(days=1), version=2)
    assert later["content_hash"] == data["content_hash"]


def test_documents_are_immutable() -> None:
    doc = validate_document(valid_data())
    with pytest.raises(ValueError, match="frozen"):
        doc.title = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------- one test per rejection reason


def _too_much_text(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(document_module, "MAX_TEXT_CHARS", 10)
    return with_block({"type": "paragraph", "text": "x" * 11})


TABLE = {"type": "table", "caption": None, "header": [], "row_headers": False}

REJECTIONS: list[tuple[str, Callable[[], dict[str, Any]], str]] = [
    # title
    ("no title", lambda: valid_data(title=""), "title: must not be empty"),
    ("blank title", lambda: valid_data(title="  \n "), "title: must not be empty"),
    ("huge title", lambda: valid_data(title="x" * 1001), "title: String should have at most"),
    # content
    ("no blocks", lambda: valid_data(blocks=[]), "blocks: must not be empty"),
    (
        "unknown block type",
        lambda: with_block({"type": "image", "src": "a.png"}),
        "blocks.0: Input tag 'image' found using 'type' does not match",
    ),
    (
        "heading level 7",
        lambda: with_block({"type": "heading", "level": 7, "text": "x"}),
        "blocks.0.level: Input should be less than or equal to 6",
    ),
    (
        "level as text",
        lambda: with_block({"type": "heading", "level": "2", "text": "x"}),
        "blocks.0.level: Input should be a valid integer",
    ),
    (
        "blank paragraph",
        lambda: with_block({"type": "paragraph", "text": " "}),
        "blocks.0.text: must not be empty",
    ),
    (
        "empty list",
        lambda: with_block({"type": "list", "ordered": True, "items": []}),
        "blocks.0.items: must not be empty",
    ),
    (
        "blank list item",
        lambda: with_block({"type": "list", "ordered": True, "items": ["a", ""]}),
        "blocks.0.items.1: must not be empty",
    ),
    (
        "unknown block key",
        lambda: with_block({"type": "quote", "text": "x", "author": "y"}),
        "blocks.0.author: Extra inputs are not permitted",
    ),
    (
        "ragged table",
        lambda: with_block({**TABLE, "header": ["a", "b"], "rows": [["1", "2"], ["3"]]}),
        "blocks.0: table rows differ in width: [1, 2]",
    ),
    (
        "empty table",
        lambda: with_block({**TABLE, "rows": []}),
        "blocks.0: table has no header and no rows",
    ),
    (
        "table row without cells",
        lambda: with_block({**TABLE, "rows": [[]]}),
        "blocks.0: table has a row with no cells",
    ),
    (
        "row headers without values",
        lambda: with_block({**TABLE, "rows": [["UPC"]], "row_headers": True}),
        "blocks.0: a table with row headers needs a label and a value column",
    ),
    # fields
    (
        "unsupported field value",
        lambda: valid_data(fields={"price": {"amount": 5}}),
        "fields: field 'price' must be text, a number, true/false, a list of text",
    ),
    # identity
    (
        "relative URL",
        lambda: valid_data(url="/catalogue/x.html"),
        "url: URL must be absolute and normalized",
    ),
    (
        "URL not normalized",
        lambda: valid_data(url="HTTPS://Books.ToScrape.com/catalogue/x.html#top"),
        "url: URL must be absolute and normalized",
    ),
    (
        "URL on another site",
        lambda: valid_data(url="https://example.com/x"),
        "URL is not on an allowed host of site 'books-toscrape'",
    ),
    (
        "wrong site",
        lambda: valid_data(site="standard-ebooks"),
        "document is for site 'standard-ebooks', not 'books-toscrape'",
    ),
    ("bad site key", lambda: valid_data(site="Books To Scrape"), "site: String should match"),
    # hash
    (
        "hash does not match",
        lambda: valid_data(content_hash="0" * 64),
        "content_hash does not match the content",
    ),
    ("hash not hex", lambda: valid_data(content_hash="xyz"), "content_hash: String should match"),
    # time and version
    (
        "naive crawl time",
        lambda: valid_data(crawled_at=datetime(2026, 1, 15, 12, 0)),
        "crawled_at: crawl time must include a timezone",
    ),
    (
        "crawl time in the future",
        lambda: valid_data(crawled_at=datetime.now(UTC) + timedelta(hours=1)),
        "crawled_at: crawl time is in the future",
    ),
    ("version 0", lambda: valid_data(version=0), "version: Input should be greater than or equal"),
    (
        "unknown document key",
        lambda: valid_data(html="<p>raw</p>"),
        "html: Extra inputs are not permitted",
    ),
]


@pytest.mark.parametrize(
    ("make", "reason"), [(m, r) for _, m, r in REJECTIONS], ids=[name for name, _, _ in REJECTIONS]
)
def test_invalid_document_is_rejected_with_its_reason(
    make: Callable[[], dict[str, Any]], reason: str
) -> None:
    with pytest.raises(InvalidDocument) as caught:
        validate_document(make(), SITES["books-toscrape"])
    assert reason in caught.value.reason


def test_too_much_content_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(InvalidDocument, match="content is longer than 10 characters"):
        validate_document(_too_much_text(monkeypatch))


def test_rejection_is_logged_with_url_and_reason(caplog: pytest.LogCaptureFixture) -> None:
    with (
        caplog.at_level(logging.WARNING, logger="onramp_scraper.processing.document"),
        pytest.raises(InvalidDocument),
    ):
        validate_document(valid_data(title=""), SITES["books-toscrape"])
    [record] = caplog.records
    assert record.levelname == "WARNING"
    assert record.url == BOOK_URL  # type: ignore[attr-defined]
    assert record.site == "books-toscrape"  # type: ignore[attr-defined]
    assert record.reason == "title: must not be empty"  # type: ignore[attr-defined]
    assert "document rejected: title: must not be empty" in record.getMessage()


def test_every_problem_is_reported_at_once() -> None:
    with pytest.raises(InvalidDocument) as caught:
        validate_document(valid_data(title="", version=0, blocks=[]))
    assert caught.value.reason.split("; ") == [
        "title: must not be empty",
        "blocks: must not be empty",
        "version: Input should be greater than or equal to 1",
    ]


def test_javascript_shell_without_rendering_is_rejected() -> None:
    """Inventaire fetched without a browser cleans to nothing; it must not be stored."""
    page = cleaned("inventaire", "work-pride-and-prejudice.raw.html")
    with pytest.raises(InvalidDocument, match="blocks: must not be empty"):
        build_document(
            page,
            url="https://inventaire.io/entity/wd:Q170583",
            site=SITES["inventaire"],
            version=1,
            crawled_at=NOW,
        )
