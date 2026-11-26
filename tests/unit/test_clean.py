"""Required test area 4: HTML cleaning, on saved pages and on small examples."""

from pathlib import Path
from typing import Any

import pytest

from onramp_scraper.processing.clean import CleanedPage, block_text, clean_html
from onramp_scraper.processing.text import collapse
from onramp_scraper.sites import load_sites
from onramp_testsite import content

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
SITES = load_sites(ROOT / "config" / "sites.yaml")


def clean_fixture(site: str, name: str) -> CleanedPage:
    config = SITES[site]
    html = (FIXTURES / site / name).read_text("utf-8")
    return clean_html(html, config.title_selector, config.remove)


def kinds(page: CleanedPage) -> list[str]:
    return [b["type"] for b in page.blocks]


# ---------------------------------------------------------------- saved pages


def test_books_product_page() -> None:
    page = clean_fixture("books-toscrape", "book-a-light-in-the-attic.html")
    assert page.title == "A Light in the Attic"
    assert page.blocks[0] == {"type": "heading", "level": 1, "text": "A Light in the Attic"}
    assert {"type": "paragraph", "text": "£51.77"} in page.blocks
    headings = [b["text"] for b in page.blocks if b["type"] == "heading"]
    assert headings == ["A Light in the Attic", "Product Description", "Product Information"]
    assert "table" in kinds(page)
    for clutter in ("Books to Scrape", "Warning!", "Add to basket", "Home"):
        assert clutter not in page.text, clutter


def test_books_listing_page_drops_sidebar_and_breadcrumb() -> None:
    page = clean_fixture("books-toscrape", "listing-page-1.html")
    assert page.title == "All products"
    assert "A Light in the..." in page.text  # product titles are kept (site truncates them)
    for clutter in ("Travel", "Historical Fiction", "Add to basket"):  # sidebar, buttons
        assert clutter not in page.text, clutter


def test_standard_ebooks_book_page() -> None:
    page = clean_fixture("standard-ebooks", "book-pride-and-prejudice.html")
    assert page.title == "Pride and Prejudice"
    assert page.blocks[:2] == [
        {"type": "heading", "level": 1, "text": "Pride and Prejudice"},
        {"type": "paragraph", "text": "Jane Austen"},
    ]
    description = page.blocks.index({"type": "heading", "level": 2, "text": "Description"})
    assert page.blocks[description + 1]["type"] == "paragraph"
    history = [b for b in page.blocks if b["type"] == "list" and b["ordered"]]
    assert history, "the ebook's change history is an ordered list"
    for clutter in ("Get Involved", "Sponsored by", "Following this link will ban"):
        assert clutter not in page.text, clutter  # nav, footer, hidden honeypot


def test_standard_ebooks_article_header_is_kept() -> None:
    """The page <header> goes, but the <header> inside the <article> holds the title."""
    page = clean_fixture("standard-ebooks", "book-war-and-peace.html")
    assert page.title == "War and Peace"
    assert page.blocks[0]["type"] == "heading"


def test_inventaire_rendered_work() -> None:
    page = clean_fixture("inventaire", "work-pride-and-prejudice.html")
    assert page.title == "Pride and Prejudice"  # not the "inventaire" logo in <h1>
    assert "First publication date: January 28, 1813" in page.text
    assert "Genre: novel of manners, satirical fiction" in page.text  # punctuation tidied
    for clutter in ("Edit bibliographical info", "invalid user", "Sign up", "Filter by"):
        assert clutter not in page.text, clutter


def test_inventaire_raw_shell_has_no_content() -> None:
    page = clean_fixture("inventaire", "work-pride-and-prejudice.raw.html")
    assert "Pride and Prejudice" not in page.text


@pytest.mark.parametrize(
    ("site", "name"),
    [
        ("books-toscrape", "book-sapiens.html"),
        ("books-toscrape", "listing-page-50.html"),
        ("standard-ebooks", "listing-page-1.html"),
        ("standard-ebooks", "listing-page-129.html"),
        ("inventaire", "author-jane-austen.html"),
    ],
)
def test_every_saved_page_cleans_to_titled_structured_blocks(site: str, name: str) -> None:
    page = clean_fixture(site, name)
    assert page.title
    assert "heading" in kinds(page)
    for block in page.blocks:
        assert block_text(block).strip(), block  # no empty blocks
    assert not any(
        tag in page.text for tag in ("<div", "<span", "<a ", "<script")
    )  # text, not markup


# ---------------------------------------------------------------- rules, one at a time


def blocks_of(body: str, **kwargs: Any) -> list[dict[str, Any]]:
    return clean_html(
        f"<html><head><title>T</title></head><body>{body}</body></html>", **kwargs
    ).blocks


def test_scripts_styles_nav_footer_ads_hidden_and_forms_are_removed() -> None:
    blocks = blocks_of(
        """
        <header>Site name</header>
        <nav><a href="/">Home</a></nav>
        <script>var x = 1;</script><style>p { color: red }</style>
        <div class="ad-banner">Buy now</div><div id="sponsored-box">Promo</div>
        <p hidden>secret</p><div aria-hidden="true">icon</div>
        <form><label>Search</label><input><button>Go</button></form>
        <main><p>Real content.</p></main>
        <aside>Related</aside>
        <footer>Copyright</footer>
        """
    )
    assert blocks == [{"type": "paragraph", "text": "Real content."}]


def test_structure_is_kept_in_order() -> None:
    blocks = blocks_of(
        """<main>
          <h1>Title</h1><p>Intro <em>with</em> <a href="/x">a link</a>.</p>
          <h2>List</h2><ol><li>one</li><li>two<ul><li>two-a</li></ul></li></ol>
          <dl><dt>Pages</dt><dd>345</dd></dl>
          <table><tr><th>Field</th><th>Value</th></tr><tr><td>SKU</td><td>A1</td></tr></table>
          <pre>code  line</pre>
          <blockquote>A quote.</blockquote>
        </main>"""
    )
    assert blocks == [
        {"type": "heading", "level": 1, "text": "Title"},
        {"type": "paragraph", "text": "Intro with a link."},
        {"type": "heading", "level": 2, "text": "List"},
        {"type": "list", "ordered": True, "items": ["one", "two", "two-a"]},
        {"type": "list", "ordered": False, "items": ["Pages: 345"]},
        {
            "type": "table",
            "caption": None,
            "header": ["Field", "Value"],
            "rows": [["SKU", "A1"]],
            "row_headers": False,
        },
        {"type": "code", "text": "code  line"},
        {"type": "quote", "text": "A quote."},
    ]


def test_loose_text_in_divs_becomes_paragraphs() -> None:
    blocks = blocks_of("<div>First <span>part</span></div><div><div>Second</div></div>")
    assert blocks == [
        {"type": "paragraph", "text": "First part"},
        {"type": "paragraph", "text": "Second"},
    ]


def test_title_order_of_preference() -> None:
    page = clean_html("<title>Doc</title><nav><h1>Logo</h1></nav><main><h2>Name</h2></main>")
    assert page.title == "Doc"  # the only <h1> was navigation
    page = clean_html(
        "<title>Doc</title><nav><h1>Logo</h1></nav><main><h2 class='n'>Name</h2></main>",
        title_selector="main h2.n",
    )
    assert page.title == "Name"


def test_site_specific_removals() -> None:
    blocks = blocks_of("<p>Keep</p><div class='notice'>Drop</div>", remove=[".notice"])
    assert blocks == [{"type": "paragraph", "text": "Keep"}]


def test_adjacent_duplicates_are_merged() -> None:
    assert blocks_of("<p>Same</p><p>Same</p><p>Other</p><p>Same</p>") == [
        {"type": "paragraph", "text": "Same"},
        {"type": "paragraph", "text": "Other"},
        {"type": "paragraph", "text": "Same"},
    ]


def test_testsite_item_page() -> None:
    page = clean_html(content.layout("x", content.item_body(content.item(7))))
    title = content.item(7).title
    assert page.title == title
    assert "Sponsored" not in page.text and "analytics" not in page.text
    assert "Catalog" not in page.text and "Terms" not in page.text  # nav, footer
    assert [b["text"] for b in page.blocks if b["type"] == "heading"] == [
        title,
        "Summary",
        "Features",
        "Specifications",
        "Related",
    ]


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("  a \n\t b  ", "a b"),
        ("Austen , Brontë .", "Austen, Brontë."),
        ("( 1813 )", "(1813)"),
        ("50 %", "50%"),
    ],
)
def test_collapse(raw: str, clean: str) -> None:
    assert collapse(raw) == clean


def test_text_is_the_plain_text_of_all_blocks() -> None:
    page = clean_html(
        "<main><h1>T</h1><ul><li>a</li><li>b</li></ul>"
        "<table><tr><th>k</th><th>v</th></tr><tr><td>1</td><td>2</td></tr></table></main>"
    )
    assert page.text == "T\na\nb\nk | v\n1 | 2"
