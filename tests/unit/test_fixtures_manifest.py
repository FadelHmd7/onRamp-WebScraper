"""Every saved page is recorded with its source, and every record has a file."""

import json
from datetime import datetime
from pathlib import Path

FIXTURES = Path(__file__).parents[1] / "fixtures"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
SITE_DIRS = ("books-toscrape", "inventaire", "standard-ebooks")


def saved_files() -> set[str]:
    return {
        path.relative_to(FIXTURES).as_posix()
        for site in SITE_DIRS
        for path in (FIXTURES / site).glob("*")
        if path.is_file()
    }


def test_every_file_is_in_the_manifest() -> None:
    assert saved_files() == set(MANIFEST)


def test_every_entry_has_source_and_date() -> None:
    for name, entry in MANIFEST.items():
        assert entry["url"].startswith("https://"), name
        assert entry["status"] == 200, name
        datetime.fromisoformat(entry["fetched_at"])


def test_each_site_has_pages() -> None:
    for site in SITE_DIRS:
        assert any(name.startswith(f"{site}/") and name.endswith(".html") for name in MANIFEST)


def test_javascript_site_raw_html_has_no_content() -> None:
    """The reason site B needs a browser: the book is not in the raw HTML."""
    raw = (FIXTURES / "inventaire" / "work-pride-and-prejudice.raw.html").read_text("utf-8")
    assert "Pride and Prejudice" not in raw
    static = (FIXTURES / "standard-ebooks" / "book-pride-and-prejudice.html").read_text("utf-8")
    assert "Pride and Prejudice" in static
