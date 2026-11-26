"""Every selector in config/sites.yaml matches real saved pages.

If a site changes its markup, refreshing the fixtures makes this test fail
and points at the exact field that broke.
"""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from onramp_scraper.parsing import parse_html
from onramp_scraper.processing.fields import extract_field
from onramp_scraper.sites import load_sites
from onramp_testsite import content

FIXTURES = Path(__file__).parents[1] / "fixtures"
SITES = load_sites(Path(__file__).parents[2] / "config" / "sites.yaml")


def pages(site: str) -> list[BeautifulSoup]:
    if site == "testsite":
        html = content.layout("x", content.item_body(content.item(1)))
        return [parse_html(html)]
    files = [p for p in (FIXTURES / site).glob("*.html") if not p.name.endswith(".raw.html")]
    return [parse_html(p.read_text("utf-8")) for p in files]


FIELD_CASES = [(key, name) for key, site in SITES.items() for name in site.fields]


@pytest.mark.parametrize(("site", "field"), FIELD_CASES)
def test_field_has_a_value_on_a_saved_page(site: str, field: str) -> None:
    """Selector, pattern and type together yield a value somewhere (exact values: test_fields)."""
    rule = SITES[site].fields[field]
    found = [extract_field(soup, rule) for soup in pages(site)]
    assert any(v is not None for v in found), f"{site}.{field}: {rule.selector}"


def test_every_site_has_fields() -> None:
    assert all(site.fields for site in SITES.values())


def test_inventaire_wait_selector_is_absent_from_raw_html() -> None:
    """The browser waits for this element, which only JavaScript creates."""
    wait_for = SITES["inventaire"].browser_wait_for
    assert wait_for is not None
    raw = (FIXTURES / "inventaire" / "work-pride-and-prejudice.raw.html").read_text("utf-8")
    rendered = (FIXTURES / "inventaire" / "work-pride-and-prejudice.html").read_text("utf-8")
    assert BeautifulSoup(raw, "lxml").select_one(wait_for) is None
    assert BeautifulSoup(rendered, "lxml").select_one(wait_for) is not None


CLEANING_CASES = [
    (key, selector)
    for key, site in SITES.items()
    for selector in [*site.remove, *([site.title_selector] if site.title_selector else [])]
]


@pytest.mark.parametrize(("site", "selector"), CLEANING_CASES)
def test_cleaning_selector_matches_a_saved_page(site: str, selector: str) -> None:
    """Every remove / title selector does something on a real page; none is dead config."""
    assert any(soup.select(selector) for soup in pages(site)), f"{site}: {selector}"
