"""Required test area 1: URL normalization."""

from html.parser import HTMLParser
from pathlib import Path

import pytest

from onramp_scraper.crawler.urls import host_of, normalize_url

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # scheme and host case, default ports
        ("HTTPS://Books.ToScrape.COM/index.html", "https://books.toscrape.com/index.html"),
        ("https://books.toscrape.com:443/", "https://books.toscrape.com/"),
        ("http://testsite:80/items/1", "http://testsite/items/1"),
        ("http://testsite:8000/items/1", "http://testsite:8000/items/1"),
        # empty path, fragment, trailing dot on host
        ("https://standardebooks.org", "https://standardebooks.org/"),
        ("https://standardebooks.org/ebooks#main", "https://standardebooks.org/ebooks"),
        ("https://standardebooks.org./ebooks", "https://standardebooks.org/ebooks"),
        # dot segments
        ("https://a.example/x/./y/../z", "https://a.example/x/z"),
        ("https://a.example/../../z", "https://a.example/z"),
        ("https://a.example/x/y/..", "https://a.example/x/"),
        # percent-encoding: decode unreserved, uppercase the rest, escape spaces
        ("https://a.example/%7euser/%2fx", "https://a.example/~user/%2Fx"),
        ("https://a.example/my page", "https://a.example/my%20page"),
        # query: sorted, tracking removed, empty dropped, "+" untouched
        ("https://a.example/s?z=1&a=2", "https://a.example/s?a=2&z=1"),
        ("https://a.example/s?a=1&utm_source=x&UTM_Medium=y&fbclid=f", "https://a.example/s?a=1"),
        ("https://a.example/s?", "https://a.example/s"),
        ("https://a.example/s?q=c++", "https://a.example/s?q=c++"),
        ("https://a.example/s?flag", "https://a.example/s?flag"),
        # credentials dropped, international domain to ASCII, IPv6 kept
        ("https://user:pw@a.example/x", "https://a.example/x"),
        ("https://bücher.example/x", "https://xn--bcher-kva.example/x"),
        ("http://[::1]:9050/x", "http://[::1]:9050/x"),
        # what we deliberately do NOT change
        ("https://a.example/Dir/", "https://a.example/Dir/"),
        ("https://a.example/dir/index.html", "https://a.example/dir/index.html"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def test_spellings_of_one_page_collapse_to_one_url() -> None:
    spellings = [
        "HTTPS://Books.ToScrape.com:443/catalogue/./x/../page-2.html#top",
        "https://books.toscrape.com/catalogue/page-2.html",
        "https://books.toscrape.com/catalogue/page-2.html?utm_campaign=mail",
    ]
    assert len({normalize_url(url) for url in spellings}) == 1


def test_is_idempotent() -> None:
    once = normalize_url("HTTPS://A.example:443/x/../y?b=2&a=1#f")
    assert once is not None
    assert normalize_url(once) == once


@pytest.mark.parametrize(
    "raw",
    [
        "mailto:someone@example.com",
        "javascript:void(0)",
        "tel:+15551234",
        "data:text/html,hi",
        "ftp://files.example/x",
        "https://",
        "https://a.example:99999/",  # invalid port
        "",
    ],
)
def test_rejects_uncrawlable(raw: str) -> None:
    assert normalize_url(raw) is None


@pytest.mark.parametrize(
    ("href", "base", "expected"),
    [
        (
            "../../../a-light-in-the-attic_1000/index.html",
            "https://books.toscrape.com/catalogue/category/books/poetry_23/index.html",
            "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
        ),
        (
            "page-3.html",
            "https://books.toscrape.com/catalogue/page-2.html",
            "https://books.toscrape.com/catalogue/page-3.html",
        ),
        (
            "/ebooks?page=3",
            "https://standardebooks.org/ebooks?page=2",
            "https://standardebooks.org/ebooks?page=3",
        ),
        (
            "//standardebooks.org/about",
            "https://books.toscrape.com/",
            "https://standardebooks.org/about",
        ),
        ("#reviews", "https://a.example/book", "https://a.example/book"),
    ],
)
def test_resolves_relative_links(href: str, base: str, expected: str) -> None:
    assert normalize_url(href, base=base) == expected


def test_host_of_is_lowercase_host_without_port() -> None:
    assert host_of("https://Books.ToScrape.com:443/x") == "books.toscrape.com"
    assert host_of("http://testsite:8000/items/1") == "testsite"


class _Hrefs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(value for name, value in attrs if name == "href" and value)


@pytest.mark.parametrize(
    ("fixture", "base"),
    [
        ("books-toscrape/listing-page-1.html", "https://books.toscrape.com/index.html"),
        (
            "books-toscrape/listing-page-50.html",
            "https://books.toscrape.com/catalogue/page-50.html",
        ),
        ("standard-ebooks/listing-page-1.html", "https://standardebooks.org/ebooks?page=1"),
    ],
)
def test_every_link_in_saved_pages_normalizes(fixture: str, base: str) -> None:
    """Real hrefs from saved pages: each becomes an absolute, stable URL."""
    parser = _Hrefs()
    parser.feed((FIXTURES / fixture).read_text(encoding="utf-8"))
    assert parser.hrefs, "fixture has links"
    for href in parser.hrefs:
        url = normalize_url(href, base=base)
        if url is None:  # mailto: and similar are fine to drop
            continue
        assert url.startswith(("https://", "http://")), href
        assert "#" not in url and "/../" not in url and "/./" not in url, href
        assert normalize_url(url) == url, href  # stable


def test_books_toscrape_listing_links_stay_on_site() -> None:
    parser = _Hrefs()
    parser.feed((FIXTURES / "books-toscrape/listing-page-1.html").read_text(encoding="utf-8"))
    base = "https://books.toscrape.com/index.html"
    hosts = {host_of(u) for h in parser.hrefs if (u := normalize_url(h, base=base))}
    assert hosts == {"books.toscrape.com"}
