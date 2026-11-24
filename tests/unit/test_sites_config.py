from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from onramp_scraper.sites import SitesFile, load_sites

SITES_FILE = Path(__file__).parents[2] / "config" / "sites.yaml"


@pytest.fixture(scope="module")
def sites() -> dict[str, Any]:
    return load_sites(SITES_FILE)


def test_three_real_sites_cover_the_three_roles(sites: dict[str, Any]) -> None:
    roles = {site.role for site in sites.values() if site.role != "test"}
    assert roles == {"static", "javascript", "paginated"}


def test_only_the_javascript_site_uses_a_browser(sites: dict[str, Any]) -> None:
    browser_sites = [key for key, site in sites.items() if site.fetch_mode == "browser"]
    assert browser_sites == ["inventaire"]


def test_inventaire_respects_crawl_delay(sites: dict[str, Any]) -> None:
    assert sites["inventaire"].requests_per_second <= 0.1  # Crawl-delay: 10


@pytest.mark.parametrize(
    ("key", "url", "allowed"),
    [
        ("books-toscrape", "https://books.toscrape.com/index.html", True),
        ("books-toscrape", "https://books.toscrape.com/catalogue/page-7.html", True),
        (
            "books-toscrape",
            "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
            True,
        ),
        ("books-toscrape", "https://example.com/catalogue/page-7.html", False),
        ("inventaire", "https://inventaire.io/entity/wd:Q36322", True),
        ("inventaire", "https://inventaire.io/entity/wd:Q36322/edit", False),  # robots
        ("inventaire", "https://inventaire.io/search?q=austen", False),  # robots
        ("standard-ebooks", "https://standardebooks.org/ebooks?page=12", True),
        ("standard-ebooks", "https://standardebooks.org/ebooks/jane-austen/emma", True),
        (
            "standard-ebooks",
            "https://standardebooks.org/ebooks/leo-tolstoy/war-and-peace/louise-maude_aylmer-maude",
            True,
        ),
        (
            "standard-ebooks",
            "https://standardebooks.org/ebooks/jane-austen/emma/downloads/cover.jpg",
            False,  # our choice: files, not pages
        ),
        ("standard-ebooks", "https://standardebooks.org/ebooks/jane-austen/emma/text", False),
        ("testsite", "http://testsite:8000/items/42", True),
        ("testsite", "http://testsite:8000/private/secret", False),
    ],
)
def test_include_and_exclude_patterns(
    sites: dict[str, Any], key: str, url: str, allowed: bool
) -> None:
    assert sites[key].allows_path(url) is allowed


def test_every_seed_is_allowed_by_its_own_patterns(sites: dict[str, Any]) -> None:
    for site in sites.values():
        for seed in site.seeds:
            assert site.allows_path(str(seed)), f"{site.key}: seed {seed} is filtered out"


def _minimal_site(**overrides: Any) -> dict[str, Any]:
    site = {
        "key": "demo",
        "name": "Demo",
        "role": "static",
        "base_url": "https://demo.example/",
        "allowed_hosts": ["demo.example"],
        "fetch_mode": "http",
        "seeds": ["https://demo.example/"],
        "include": ["^/$"],
        "max_depth": 1,
        "max_pages": 10,
        "requests_per_second": 1,
    }
    site.update(overrides)
    return site


@pytest.mark.parametrize(
    "overrides",
    [
        {"seeds": ["https://other.example/"]},  # seed on a host we do not allow
        {"fetch_mode": "browser"},  # browser without a wait rule
        {"include": ["(unclosed"]},  # broken regex
        {"requests_per_second": 0},
        {"unknown_option": True},  # typo in a key
    ],
)
def test_invalid_site_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SitesFile.model_validate({"sites": [_minimal_site(**overrides)]})


def test_duplicate_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SitesFile.model_validate({"sites": [_minimal_site(), _minimal_site()]})
