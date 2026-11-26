"""Save real pages from the three sites as test fixtures.

Run once, politely; tests then use the saved files and never the live sites.

    # static pages (plain HTTP)
    uv run python scripts/fetch_fixtures.py

    # JavaScript pages, rendered in the official Playwright image
    docker run --rm -v "$PWD:/work" -w /work \
        mcr.microsoft.com/playwright/python:v1.65.1-noble \
        python scripts/fetch_fixtures.py --render

Every URL is checked against the site's robots.txt first, requests to the
same host are spaced by that site's rate limit, and the User-Agent names this
project. The script writes tests/fixtures/<site>/<name> plus an entry in
tests/fixtures/manifest.json with the URL, date, status and cache headers.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

USER_AGENT = (
    "onRamp-WebScraper/0.1 "
    "(+https://github.com/FadelHmd7/onRamp-WebScraper; fixture capture for unit tests)"
)
FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
MANIFEST = FIXTURES / "manifest.json"

# Seconds between requests to one host (matches config/sites.yaml).
DELAY = {"books.toscrape.com": 0.5, "standardebooks.org": 1.0, "inventaire.io": 10.0}


@dataclass(frozen=True)
class Page:
    site: str
    name: str
    url: str
    render: bool = False  # True: save the DOM after JavaScript has run


PAGES = [
    # Site A: static
    Page("books-toscrape", "listing-page-1.html", "https://books.toscrape.com/index.html"),
    Page(
        "books-toscrape",
        "listing-page-50.html",
        "https://books.toscrape.com/catalogue/page-50.html",
    ),
    Page(
        "books-toscrape",
        "book-a-light-in-the-attic.html",
        "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
    ),
    Page(
        "books-toscrape",
        "book-sapiens.html",
        "https://books.toscrape.com/catalogue/sapiens-a-brief-history-of-humankind_996/index.html",
    ),
    # Site C: pagination
    Page("standard-ebooks", "robots.txt", "https://standardebooks.org/robots.txt"),
    Page("standard-ebooks", "listing-page-1.html", "https://standardebooks.org/ebooks?page=1"),
    Page("standard-ebooks", "listing-page-129.html", "https://standardebooks.org/ebooks?page=129"),
    Page(
        "standard-ebooks",
        "book-pride-and-prejudice.html",
        "https://standardebooks.org/ebooks/jane-austen/pride-and-prejudice",
    ),
    Page(
        "standard-ebooks",
        "book-war-and-peace.html",
        "https://standardebooks.org/ebooks/leo-tolstoy/war-and-peace/louise-maude_aylmer-maude",
    ),
    # Site B: JavaScript. The raw shell shows why a browser is needed.
    Page("inventaire", "robots.txt", "https://inventaire.io/robots.txt"),
    Page(
        "inventaire", "work-pride-and-prejudice.raw.html", "https://inventaire.io/entity/wd:Q170583"
    ),
    Page(
        "inventaire",
        "work-pride-and-prejudice.html",
        "https://inventaire.io/entity/wd:Q170583",
        render=True,
    ),
    Page(
        "inventaire",
        "author-jane-austen.html",
        "https://inventaire.io/entity/wd:Q36322",
        render=True,
    ),
]


class Polite:
    """robots.txt check and per-host spacing for this script's few requests."""

    def __init__(self) -> None:
        self.robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.last_request: dict[str, float] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.path == "/robots.txt":
            return True
        host = parts.netloc
        if host not in self.robots:
            parser = urllib.robotparser.RobotFileParser(f"{parts.scheme}://{host}/robots.txt")
            self.wait(host)
            parser.read()  # a 404 robots.txt means everything is allowed
            self.robots[host] = parser
        return self.robots[host].can_fetch(USER_AGENT, url)

    def wait(self, host: str) -> None:
        delay = DELAY.get(host, 1.0)
        elapsed = time.monotonic() - self.last_request.get(host, -1e9)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self.last_request[host] = time.monotonic()


def fetch_http(url: str) -> tuple[int, dict[str, str], str]:
    if urlsplit(url).scheme != "https":
        raise ValueError(f"only https fixture URLs are fetched: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - https checked above
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - https checked above
            return response.status, dict(response.headers), response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode("utf-8", "replace")


def fetch_rendered(url: str) -> tuple[int, dict[str, str], str]:
    from playwright.sync_api import sync_playwright  # only inside the Playwright image

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        response = page.goto(url, wait_until="networkidle", timeout=60_000)
        page.wait_for_selector("main", timeout=30_000)
        html = page.content()
        status = response.status if response else 0
        headers = response.headers if response else {}
        browser.close()
    return status, headers, html


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--render", action="store_true", help="fetch only the rendered pages")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    polite = Polite()
    for page in (p for p in PAGES if p.render == args.render):
        if not polite.allowed(page.url):
            print(f"SKIP (robots.txt) {page.url}", file=sys.stderr)
            continue
        polite.wait(urlsplit(page.url).netloc)
        status, headers, body = (fetch_rendered if page.render else fetch_http)(page.url)
        target = FIXTURES / page.site / page.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8", newline="\n")
        lower = {k.lower(): v for k, v in headers.items()}
        manifest[f"{page.site}/{page.name}"] = {
            "url": page.url,
            "fetched_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "status": status,
            "rendered": page.render,
            "etag": lower.get("etag"),
            "last_modified": lower.get("last-modified"),
        }
        print(f"{status} {page.site}/{page.name} ({len(body):,} chars)")
    MANIFEST.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
