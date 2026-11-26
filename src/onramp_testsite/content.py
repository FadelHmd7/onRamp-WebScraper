"""Deterministic catalog content and the HTML layout around it.

Every page carries the clutter a real site has (nav, footer, ad, script,
style) around real structure (headings, paragraphs, a list, a table, links),
so the cleaning code has something to remove and something to keep.
"""

import random
from dataclasses import dataclass
from html import escape

ITEMS_PER_PAGE = 20
CATALOG_PAGES = 30
ITEM_COUNT = ITEMS_PER_PAGE * CATALOG_PAGES  # 600 items + 30 listings = 630 pages

CATEGORIES = ["Databases", "Networking", "Python", "Distributed Systems", "Security", "Web"]
ADJECTIVES = ["Practical", "Modern", "Applied", "Essential", "Advanced", "Hands-on"]
NOUNS = ["Guide", "Handbook", "Patterns", "Workshop", "Primer", "Field Notes"]


@dataclass(frozen=True)
class Item:
    id: int
    title: str
    category: str
    price: str
    sku: str
    pages: int
    year: int
    summary: str
    features: list[str]
    related: list[int]


def item(item_id: int) -> Item:
    """Same id, same item, on every run."""
    rng = random.Random(item_id)  # noqa: S311 - test data, not security
    category = rng.choice(CATEGORIES)
    title = f"{rng.choice(ADJECTIVES)} {category} {rng.choice(NOUNS)} #{item_id}"
    return Item(
        id=item_id,
        title=title,
        category=category,
        price=f"{rng.randint(10, 80)}.{rng.randint(0, 99):02d}",
        sku=f"TS-{item_id:05d}",
        pages=rng.randint(120, 900),
        year=rng.randint(2005, 2026),
        summary=f"An introduction to {category.lower()} for working engineers.",
        features=[f"Chapter on {rng.choice(CATEGORIES).lower()}" for _ in range(3)],
        related=sorted({rng.randint(1, ITEM_COUNT) for _ in range(3)} - {item_id}),
    )


def layout(title: str, body: str) -> str:
    """Wrap page content in the site chrome a crawler must strip."""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(title)} | Test Catalog</title>
  <style>body {{ font-family: sans-serif; }}</style>
  <script>window.analytics = {{ track: function () {{}} }};</script>
</head>
<body>
  <nav class="site-nav">
    <a href="/">Home</a> <a href="/catalog?page=1">Catalog</a> <a href="/about">About</a>
  </nav>
  <div class="ad-banner" id="ad-top">Sponsored: buy more servers!</div>
  <main>
{body}
  </main>
  <footer class="site-footer">
    <p>&copy; 2026 Test Catalog. <a href="/terms">Terms</a></p>
  </footer>
</body>
</html>
"""


def item_body(it: Item) -> str:
    features = "\n".join(f"      <li>{escape(f)}</li>" for f in it.features)
    related = "\n".join(
        f'      <li><a href="/items/{r}">{escape(item(r).title)}</a></li>' for r in it.related
    )
    return f"""    <article class="item" data-sku="{it.sku}">
      <h1>{escape(it.title)}</h1>
      <p class="price">${it.price}</p>
      <p class="category">Category: <a href="/catalog?page=1">{escape(it.category)}</a></p>
      <h2>Summary</h2>
      <p>{escape(it.summary)}</p>
      <h2>Features</h2>
      <ul>
{features}
      </ul>
      <h2>Specifications</h2>
      <table class="specs">
        <thead><tr><th>Field</th><th>Value</th></tr></thead>
        <tbody>
          <tr><td>SKU</td><td>{it.sku}</td></tr>
          <tr><td>Pages</td><td>{it.pages}</td></tr>
          <tr><td>Year</td><td>{it.year}</td></tr>
        </tbody>
      </table>
      <h2>Related</h2>
      <ul class="related">
{related}
      </ul>
    </article>"""


def catalog_body(page: int) -> str:
    first = (page - 1) * ITEMS_PER_PAGE + 1
    rows = "\n".join(
        f'      <li><a href="/items/{i}">{escape(item(i).title)}</a></li>'
        for i in range(first, first + ITEMS_PER_PAGE)
    )
    prev_link = f'<a rel="prev" href="/catalog?page={page - 1}">Previous</a>' if page > 1 else ""
    next_link = (
        f'<a rel="next" href="/catalog?page={page + 1}">Next</a>' if page < CATALOG_PAGES else ""
    )
    return f"""    <h1>Catalog, page {page} of {CATALOG_PAGES}</h1>
    <ul class="listing">
{rows}
    </ul>
    <p class="pagination">{prev_link} {next_link}</p>"""
