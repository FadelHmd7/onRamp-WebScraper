"""Site-specific fields: exact values from saved pages, and each rule option on small examples."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from onramp_scraper.parsing import parse_html
from onramp_scraper.processing.clean import clean_html
from onramp_scraper.processing.document import build_document
from onramp_scraper.processing.fields import extract_field, extract_fields
from onramp_scraper.sites import FieldRule, load_sites
from onramp_testsite import content

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
SITES = load_sites(ROOT / "config" / "sites.yaml")


def fields_of(site: str, name: str) -> dict[str, Any]:
    return extract_fields((FIXTURES / site / name).read_text("utf-8"), SITES[site].fields)


# ---------------------------------------------------------------- books.toscrape


def test_books_product_fields() -> None:
    assert fields_of("books-toscrape", "book-a-light-in-the-attic.html") == {
        "price": 51.77,
        "currency": "£",
        "availability": "In stock (22 available)",
        "in_stock": 22,
        "rating": "Three",
        "category": "Poetry",
        "product_info": {
            "UPC": "a897fe39b1053632",
            "Product Type": "Books",
            "Price (excl. tax)": "£51.77",
            "Price (incl. tax)": "£51.77",
            "Tax": "£0.00",
            "Availability": "In stock (22 available)",
            "Number of reviews": "0",
        },
    }


def test_books_second_product() -> None:
    fields = fields_of("books-toscrape", "book-sapiens.html")
    assert (fields["price"], fields["in_stock"], fields["rating"]) == (54.23, 20, "Five")
    assert fields["category"] == "History"
    assert fields["product_info"]["UPC"] == "4165285e1663650f"


# ---------------------------------------------------------------- Inventaire (rendered)


def test_inventaire_author_fields() -> None:
    assert fields_of("inventaire", "author-jane-austen.html") == {
        "name": "Jane Austen",
        "entity_type": "Author",
        "birth_year": 1775,
        "death_year": 1817,
    }


def test_inventaire_work_fields() -> None:
    fields = fields_of("inventaire", "work-pride-and-prejudice.html")
    assert fields["name"] == "Pride and Prejudice"
    assert fields["entity_type"] == "Work"
    assert fields["authors"] == ["Jane Austen"]
    assert fields["summary"].startswith("Pride and Prejudice is a novel by English author")
    assert fields["claims"]["First publication date"] == "January 28, 1813"  # trailing ":" gone
    assert fields["claims"]["Followed by"] == "Mansfield Park"
    # The work page shows its author's dates too; they are not the work's.
    assert "birth_year" not in fields and "death_year" not in fields


def test_inventaire_raw_shell_has_no_fields() -> None:
    assert fields_of("inventaire", "work-pride-and-prejudice.raw.html") == {}


# ---------------------------------------------------------------- Standard Ebooks


def test_standard_ebooks_book_fields() -> None:
    assert fields_of("standard-ebooks", "book-pride-and-prejudice.html") == {
        "author": "Jane Austen",
        "abstract": "A Regency-era novel of manners in which five women try to adjust to "
        "their new neighbor, an eligible gentleman.",
        "word_count": 121970,
        "collections": [
            "BBC\u2019s 100 Greatest British Novels (2015)",  # a typographic apostrophe
            "Harvard Classics Shelf of Fiction",
            "Guardian\u2019s 100 Best Novels of All Time (2026)",
        ],
        "language": "en-GB",
        "published": "2014-05-25",
        "wikipedia_url": "https://en.wikipedia.org/wiki/Pride_and_Prejudice",
    }


def test_standard_ebooks_second_book() -> None:
    fields = fields_of("standard-ebooks", "book-war-and-peace.html")
    assert (fields["author"], fields["word_count"], fields["language"]) == (
        "Leo Tolstoy",
        563500,
        "en-US",
    )


# ---------------------------------------------------------------- all sites


@pytest.mark.parametrize(
    ("site", "name"),
    [
        ("books-toscrape", "listing-page-1.html"),
        ("books-toscrape", "listing-page-50.html"),
        ("standard-ebooks", "listing-page-1.html"),
        ("standard-ebooks", "listing-page-129.html"),
    ],
)
def test_listing_pages_have_no_product_fields(site: str, name: str) -> None:
    """Missing fields are left out, not stored as empty values."""
    assert fields_of(site, name) == {}


def test_testsite_fields() -> None:
    item = content.item(4)
    fields = extract_fields(content.layout("x", content.item_body(item)), SITES["testsite"].fields)
    assert fields == {"price": float(item.price), "sku": item.sku}  # "$42.15" -> 42.15


@pytest.mark.parametrize(
    ("site", "name", "url"),
    [
        (
            "books-toscrape",
            "book-a-light-in-the-attic.html",
            "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
        ),
        ("inventaire", "work-pride-and-prejudice.html", "https://inventaire.io/entity/wd:Q170583"),
        (
            "standard-ebooks",
            "book-pride-and-prejudice.html",
            "https://standardebooks.org/ebooks/jane-austen/pride-and-prejudice",
        ),
    ],
)
def test_extracted_fields_pass_the_document_schema(site: str, name: str, url: str) -> None:
    config = SITES[site]
    html = (FIXTURES / site / name).read_text("utf-8")
    fields = extract_fields(html, config.fields)  # before cleaning, which changes nothing here
    page = clean_html(html, config.title_selector, config.remove)
    doc = build_document(
        page, url=url, site=config, version=1, crawled_at=datetime.now(UTC), fields=fields
    )
    assert doc.fields == fields


# ---------------------------------------------------------------- rule options, one at a time


def field(html: str, **rule: Any) -> Any:
    return extract_field(parse_html(html), FieldRule(**rule))


def test_text_of_the_first_match_with_a_value() -> None:
    html = "<p class='a'> </p><p class='a'>  Two \n words </p><p class='a'>Three</p>"
    assert field(html, selector="p.a") == "Two words"


def test_missing_field_is_none() -> None:
    assert field("<p>x</p>", selector="span.price") is None
    assert extract_fields("<p>x</p>", {"price": FieldRule(selector="span.price")}) == {}


def test_attribute_and_class_list() -> None:
    html = "<a href='/x' class='star-rating Four'>link</a>"
    assert field(html, selector="a", attr="href") == "/x"
    assert field(html, selector="a", attr="class") == "star-rating Four"
    assert field(html, selector="a", attr="class", pattern=r"star-rating (\w+)") == "Four"
    assert field(html, selector="a", attr="title") is None


def test_many_keeps_order_and_drops_empty_and_repeated_values() -> None:
    html = "<li>b</li><li></li><li>a</li><li>b</li>"
    assert field(html, selector="li", many=True) == ["b", "a"]
    assert field("<p>x</p>", selector="li", many=True) is None


def test_key_value_pairs() -> None:
    html = """
      <div class="c"><b>Genre:</b><i>novel</i></div>
      <div class="c"><b>Genre:</b><i>ignored, first wins</i></div>
      <div class="c"><b>Pages</b><i>345</i></div>
      <div class="c"><b>No value</b></div>"""
    assert field(html, selector=".c", key="b", value="i") == {"Genre": "novel", "Pages": "345"}


@pytest.mark.parametrize(
    ("text", "rule", "expected"),
    [
        ("£51.77", {"pattern": r"(\d+(?:\.\d+)?)", "type": "float"}, 51.77),
        ("In stock (22 available)", {"pattern": r"\((\d+) available\)", "type": "int"}, 22),
        ("1,234 words", {"pattern": r"[\d,]+", "type": "int"}, 1234),  # whole match, no group
        ("£51.77", {"pattern": r"^\D+"}, "£"),
        ("Out of stock", {"pattern": r"\((\d+) available\)", "type": "int"}, None),
        ("n/a", {"type": "int"}, None),
        ("12.5", {"type": "int"}, None),  # not silently truncated
        ("n/a", {"type": "float"}, None),
        ("-3", {"type": "int"}, -3),
    ],
)
def test_pattern_and_type(text: str, rule: dict[str, Any], expected: Any) -> None:
    assert field(f"<p>{text}</p>", selector="p", **rule) == expected


@pytest.mark.parametrize(
    ("rule", "error"),
    [
        ({"pattern": "(unclosed"}, "invalid regex"),
        ({"pattern": "(a)(b)"}, "use at most one"),
        ({"many": True, "type": "int"}, "type applies to single values"),
        ({"key": "b", "value": "i", "type": "float"}, "type applies to single values"),
        ({"key": "b"}, "key and value must be given together"),
        ({"type": "date"}, "type"),
    ],
)
def test_bad_rules_are_refused_at_startup(rule: dict[str, Any], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        FieldRule(selector="p", **rule)
