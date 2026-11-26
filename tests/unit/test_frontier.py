"""Link extraction on saved pages, crawl scope, and the shared seen-set and page budget."""

import threading
from pathlib import Path

import fakeredis
import pytest

from onramp_scraper.crawler.frontier import (
    Frontier,
    Link,
    extract_links,
    in_scope,
    page_nofollow,
)
from onramp_scraper.sites import SiteConfig, load_sites

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
SITES = load_sites(ROOT / "config" / "sites.yaml")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


# ---------------------------------------------------------------- link extraction


def test_books_listing_links() -> None:
    links = extract_links(
        fixture("books-toscrape/listing-page-1.html"), "https://books.toscrape.com/index.html"
    )
    urls = [link.url for link in links]
    book = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    assert book in urls
    assert "https://books.toscrape.com/catalogue/page-2.html" in urls
    texts = {link.url: link.text for link in links}
    assert texts["https://books.toscrape.com/catalogue/page-2.html"] == "next"
    assert all(url.startswith("https://books.toscrape.com/") for url in urls)


def test_link_text_falls_back_to_image_alt() -> None:
    """books.toscrape wraps cover images in links: the alt text names the book."""
    links = extract_links(
        fixture("books-toscrape/listing-page-1.html"), "https://books.toscrape.com/index.html"
    )
    book = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    texts = {link.text for link in links if link.url == book}
    assert "A Light in the Attic" in texts


def test_standard_ebooks_honeypot_is_nofollow() -> None:
    links = extract_links(
        fixture("standard-ebooks/listing-page-1.html"), "https://standardebooks.org/ebooks?page=1"
    )
    honeypot = [link for link in links if link.url == "https://standardebooks.org/honeypot"]
    assert honeypot and all(link.nofollow for link in honeypot)


def test_rendered_inventaire_page_has_entity_links() -> None:
    links = extract_links(
        fixture("inventaire/author-jane-austen.html"), "https://inventaire.io/entity/wd:Q36322"
    )
    entities = {link.url for link in links if link.url.startswith("https://inventaire.io/entity/")}
    assert len(entities) > 20
    assert "https://inventaire.io/entity/wd:Q170583" in entities  # Pride and Prejudice


def test_raw_inventaire_shell_has_no_entity_links() -> None:
    """Why site B needs the browser: plain HTML offers nothing to crawl."""
    links = extract_links(
        fixture("inventaire/work-pride-and-prejudice.raw.html"),
        "https://inventaire.io/entity/wd:Q170583",
    )
    assert not [link for link in links if "/entity/" in link.url]


def test_extraction_rules() -> None:
    html = """
      <html><head><base href="https://a.example/docs/"></head><body>
        <a href="intro.html">  Intro
           page </a>
        <a href="intro.html">Intro page</a>
        <a href="#top">Top</a>
        <a href="mailto:x@a.example">Mail</a>
        <a href="https://a.example/page"></a>
        <a href="/x" title="Titled">  </a>
        <a href="/y" rel="NoFollow sponsored">Ad</a>
      </body></html>"""
    links = extract_links(html, "https://a.example/page")
    assert links == [
        Link("https://a.example/docs/intro.html", "Intro page"),  # <base>, text collapsed, once
        # "#top" resolves against <base>, not the page, exactly as a browser does.
        Link("https://a.example/docs/", "Top"),
        Link("https://a.example/x", "Titled"),
        Link("https://a.example/y", "Ad", nofollow=True),
    ]


@pytest.mark.parametrize(
    ("meta", "nofollow"),
    [
        ('<meta name="robots" content="NOARCHIVE,NOCACHE">', False),  # books.toscrape
        ('<meta name="robots" content="noindex, nofollow">', True),
        ('<meta name="ROBOTS" content="none">', True),
        ('<meta name="onramp-webscraper" content="nofollow">', True),
        ('<meta name="googlebot" content="nofollow">', False),  # not addressed to us
    ],
)
def test_page_meta_nofollow(meta: str, nofollow: bool) -> None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(f"<html><head>{meta}</head></html>", "lxml")
    assert page_nofollow(soup) is nofollow


# ---------------------------------------------------------------- scope


@pytest.mark.parametrize(
    ("key", "url", "depth", "expected"),
    [
        ("standard-ebooks", "https://standardebooks.org/ebooks/jane-austen/emma", 1, True),
        ("standard-ebooks", "https://standardebooks.org/ebooks/jane-austen/emma", 141, False),
        ("standard-ebooks", "https://standardebooks.org/about", 1, False),  # not included
        ("inventaire", "https://inventaire.io/entity/wd:Q170583", 2, True),
        ("inventaire", "https://inventaire.io/entity/wd:Q170583", 3, False),  # max_depth 2
        ("inventaire", "https://www.wikidata.org/wiki/Q170583", 1, False),  # other host
    ],
)
def test_scope(key: str, url: str, depth: int, expected: bool) -> None:
    assert in_scope(SITES[key], url, depth) is expected


# ---------------------------------------------------------------- shared seen-set and budget


def small_site(max_pages: int = 100, max_depth: int = 5) -> SiteConfig:
    return SITES["testsite"].model_copy(update={"max_pages": max_pages, "max_depth": max_depth})


def items(n: int, start: int = 1) -> list[Link]:
    return [Link(f"http://testsite:8000/items/{i}", f"Item {i}") for i in range(start, start + n)]


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


def test_seeds_are_accepted_once(redis: fakeredis.FakeRedis) -> None:
    frontier = Frontier(redis)
    site = SITES["standard-ebooks"]
    assert frontier.seed("c1", site) == ["https://standardebooks.org/ebooks?page=1"]
    assert frontier.seed("c1", site) == []


def test_a_link_seen_by_one_worker_is_skipped_by_another(redis: fakeredis.FakeRedis) -> None:
    worker_a, worker_b = Frontier(redis), Frontier(redis)
    site = small_site()
    assert len(worker_a.discover("c1", site, items(5), depth=0)) == 5
    assert worker_b.discover("c1", site, items(7), depth=0) == [
        "http://testsite:8000/items/6",
        "http://testsite:8000/items/7",
    ]


def test_each_crawl_has_its_own_seen_set(redis: fakeredis.FakeRedis) -> None:
    frontier = Frontier(redis)
    site = small_site()
    assert len(frontier.discover("full-1", site, items(3), depth=0)) == 3
    assert len(frontier.discover("recrawl-2", site, items(3), depth=0)) == 3  # visits again


def test_depth_limit(redis: fakeredis.FakeRedis) -> None:
    frontier = Frontier(redis)
    site = small_site(max_depth=2)
    assert frontier.discover("c1", site, items(1), depth=1) != []  # children at depth 2
    assert frontier.discover("c1", site, items(1, start=50), depth=2) == []  # would be depth 3


def test_nofollow_links_and_pages_are_not_followed(redis: fakeredis.FakeRedis) -> None:
    frontier = Frontier(redis)
    site = small_site()
    hidden = Link("http://testsite:8000/items/9", "trap", nofollow=True)
    assert frontier.discover("c1", site, [hidden], depth=0) == []
    assert frontier.discover("c1", site, items(3), depth=0, follow=False) == []


def test_out_of_scope_links_are_not_counted(redis: fakeredis.FakeRedis) -> None:
    frontier = Frontier(redis)
    site = small_site()
    outside = [Link("http://testsite:8000/private/x", "p"), Link("https://other.example/", "o")]
    assert frontier.discover("c1", site, outside, depth=0) == []
    assert frontier.accepted_count("c1") == 0


def test_page_budget_is_exact(redis: fakeredis.FakeRedis) -> None:
    frontier = Frontier(redis)
    site = small_site(max_pages=10)
    assert len(frontier.discover("c1", site, items(8), depth=0)) == 8
    assert len(frontier.discover("c1", site, items(8, start=100), depth=0)) == 2
    assert frontier.discover("c1", site, items(5, start=200), depth=0) == []
    assert frontier.accepted_count("c1") == 10


def test_racing_workers_share_one_seen_set_and_budget(redis: fakeredis.FakeRedis) -> None:
    """8 workers find overlapping links at the same moment: each URL is taken once,
    and the budget of 50 holds exactly."""
    site = small_site(max_pages=50)
    accepted: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def worker(offset: int) -> None:
        frontier = Frontier(redis)  # one per worker, like separate containers
        start.wait()
        for batch in range(10):
            got = frontier.discover("race", site, items(10, start=batch * 5 + offset), depth=0)
            with lock:
                accepted.extend(got)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(accepted) == 50
    assert len(set(accepted)) == 50  # no URL accepted twice


def test_state_expires(redis: fakeredis.FakeRedis) -> None:
    Frontier(redis).discover("c1", small_site(), items(1), depth=0)
    assert 0 < redis.ttl("crawl:c1:seen") <= 7 * 24 * 3600


def test_crawl_of_saved_listing_follows_pagination_and_books(redis: fakeredis.FakeRedis) -> None:
    """One step of a real crawl: Standard Ebooks page 1 -> page 2 and the book pages."""
    site = SITES["standard-ebooks"]
    frontier = Frontier(redis)
    frontier.seed("se", site)
    links = extract_links(
        fixture("standard-ebooks/listing-page-1.html"), "https://standardebooks.org/ebooks?page=1"
    )
    next_urls = frontier.discover("se", site, links, depth=0)
    assert "https://standardebooks.org/ebooks?page=2" in next_urls
    books = [u for u in next_urls if u.count("/") >= 5 and "?" not in u]
    assert len(books) >= 12
    assert "https://standardebooks.org/honeypot" not in next_urls
    assert not [u for u in next_urls if "/downloads" in u or u.endswith("/text")]
