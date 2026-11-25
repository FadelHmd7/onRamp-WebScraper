"""Every selector in config/sites.yaml matches real saved pages.

If a site changes its markup, refreshing the fixtures makes this test fail
and points at the exact field that broke.
"""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from onramp_scraper.sites import FieldRule, load_sites
from onramp_testsite import content

FIXTURES = Path(__file__).parents[1] / "fixtures"
SITES = load_sites(Path(__file__).parents[2] / "config" / "sites.yaml")


def pages(site: str) -> list[BeautifulSoup]:
    if site == "testsite":
        html = content.layout("x", content.item_body(content.item(1)))
        return [BeautifulSoup(html, "lxml")]
    files = [p for p in (FIXTURES / site).glob("*.html") if not p.name.endswith(".raw.html")]
    return [BeautifulSoup(p.read_text("utf-8"), "lxml") for p in files]


def matches(rule: FieldRule, soup: BeautifulSoup) -> bool:
    found = soup.select(rule.selector)
    if not found:
        return False
    if rule.attr is not None:
        return any(el.get(rule.attr) for el in found)
    if rule.key is not None and rule.value is not None:
        return any(el.select_one(rule.key) and el.select_one(rule.value) for el in found)
    return any(el.get_text(strip=True) for el in found)


FIELD_CASES = [(key, name) for key, site in SITES.items() for name in site.fields]


@pytest.mark.parametrize(("site", "field"), FIELD_CASES)
def test_field_selector_matches_a_saved_page(site: str, field: str) -> None:
    rule = SITES[site].fields[field]
    assert any(matches(rule, soup) for soup in pages(site)), f"{site}.{field}: {rule.selector}"


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


def test_inventaire_values_from_rendered_pages() -> None:
    fields = SITES["inventaire"].fields
    work = BeautifulSoup(
        (FIXTURES / "inventaire" / "work-pride-and-prejudice.html").read_text("utf-8"), "lxml"
    )
    author = BeautifulSoup(
        (FIXTURES / "inventaire" / "author-jane-austen.html").read_text("utf-8"), "lxml"
    )

    def text(soup: BeautifulSoup, name: str) -> str:
        element = soup.select_one(fields[name].selector)
        assert element is not None, name
        return element.get_text(strip=True)

    assert text(work, "name") == "Pride and Prejudice"
    assert text(work, "entity_type") == "Work"
    assert [a.get_text(strip=True) for a in work.select(fields["authors"].selector)] == [
        "Jane Austen"
    ]
    claims = {
        c.select_one(".property").get_text(strip=True): c.select_one(".values").get_text(strip=True)
        for c in work.select(fields["claims"].selector)
    }
    assert claims["First publication date:"] == "January 28, 1813"
    assert text(author, "name") == "Jane Austen"
    assert text(author, "entity_type") == "Author"
    assert text(author, "birth_year") == "1775"
    assert text(author, "death_year") == "1817"


CLEANING_CASES = [
    (key, selector)
    for key, site in SITES.items()
    for selector in [*site.remove, *([site.title_selector] if site.title_selector else [])]
]


@pytest.mark.parametrize(("site", "selector"), CLEANING_CASES)
def test_cleaning_selector_matches_a_saved_page(site: str, selector: str) -> None:
    """Every remove / title selector does something on a real page; none is dead config."""
    assert any(soup.select(selector) for soup in pages(site)), f"{site}: {selector}"
