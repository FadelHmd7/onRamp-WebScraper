"""Turn a fetched HTML page into a title and an ordered list of content blocks.

Removed: scripts, styles, navigation, page headers and footers, sidebars,
breadcrumbs, ads, hidden elements and form controls.
Kept, as separate blocks in document order: headings, paragraphs, lists,
tables, code and quotes. Phase 2 needs this structure, so a page is never
flattened into one text blob.

Block shapes (stored as JSONB in document_versions.blocks):

    {"type": "heading",   "level": 2, "text": "Summary"}
    {"type": "paragraph", "text": "..."}
    {"type": "list",      "ordered": false, "items": ["...", "..."]}
    {"type": "table",     "caption": null, "header": ["..."], "rows": [["..."]],
                          "row_headers": false}         (see processing/tables.py)
    {"type": "code",      "text": "..."}
    {"type": "quote",     "text": "..."}

Parsed with BeautifulSoup and lxml; never with regular expressions.
"""

from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup, Tag
from bs4.element import Comment, NavigableString

from onramp_scraper.parsing import parse_html
from onramp_scraper.processing.tables import is_layout_table, parse_table
from onramp_scraper.processing.text import collapse

Block = dict[str, Any]

# Never content.
DROP_TAGS = (
    "script", "style", "noscript", "template", "iframe", "object", "embed", "svg",
    "canvas", "video", "audio", "map", "form", "button", "input", "select",
    "textarea", "label", "dialog", "nav", "aside", "link", "meta",
)  # fmt: skip
DROP_SELECTORS = (
    "[hidden]",
    '[aria-hidden="true"]',
    '[role="navigation"]',
    '[role="banner"]',
    '[role="contentinfo"]',
    '[role="search"]',
    '[role="dialog"]',
    ".breadcrumb",
    ".breadcrumbs",
    ".skip-link",
    ".visually-hidden",
    ".sr-only",
)
# Class or id tokens that mark ads and promotions.
AD_TOKENS = frozenset({"ad", "ads", "advert", "advertisement", "sponsored", "promo", "adsbygoogle"})
AD_PREFIXES = ("ad-", "ads-", "advert", "sponsor", "promo-", "cookie")

HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
INLINE = frozenset(
    {
        "a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "del", "dfn", "em",
        "i", "ins", "kbd", "mark", "q", "s", "samp", "small", "span", "strong",
        "sub", "sup", "time", "u", "var", "wbr", "font", "label", "img",
    }
)  # fmt: skip
BLOCK_LEVEL = frozenset(
    {
        "address", "article", "blockquote", "dd", "details", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
        "h6", "header", "hr", "li", "main", "ol", "p", "pre", "section", "summary",
        "table", "ul",
    }
)  # fmt: skip


@dataclass(frozen=True)
class CleanedPage:
    title: str
    blocks: list[Block]
    text: str  # plain text of all blocks, for search and snippets


def _is_ad(tag: Tag) -> bool:
    tokens = [str(t).lower() for t in tag.get_attribute_list("class") if t]
    if tag.get("id"):
        tokens.append(str(tag.get("id")).lower())
    return any(t in AD_TOKENS or t.startswith(AD_PREFIXES) for t in tokens)


def _remove_clutter(soup: BeautifulSoup, extra: list[str]) -> None:
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    for selector in (*DROP_SELECTORS, *extra):
        for tag in soup.select(selector):
            tag.decompose()
    # Page-level header and footer only: an <article>'s own header holds its title.
    for tag in soup.find_all(["header", "footer"]):
        if tag.find_parent(["main", "article"]) is None:
            tag.decompose()
    for tag in soup.find_all(_is_ad):
        tag.decompose()


def _content_root(soup: BeautifulSoup) -> Tag:
    for selector in ("main", '[role="main"]', "body"):
        found = soup.select_one(selector)
        if found is not None:
            return found
    return soup


def _has_block_child(tag: Tag) -> bool:
    return any(isinstance(d, Tag) and d.name in BLOCK_LEVEL for d in tag.descendants)


def _list_items(tag: Tag) -> list[str]:
    """Item texts in order; nested lists contribute their items right after their parent."""
    items: list[str] = []
    for li in tag.find_all("li", recursive=False):
        own = "".join(
            child.get_text(" ") if isinstance(child, Tag) else str(child)
            for child in li.children
            if not (isinstance(child, Tag) and child.name in ("ul", "ol"))
        )
        if text := collapse(own):
            items.append(text)
        for nested in li.find_all(["ul", "ol"], recursive=False):
            items.extend(_list_items(nested))
    return items


def _definition_items(tag: Tag) -> list[str]:
    items: list[str] = []
    term = ""
    for child in tag.find_all(["dt", "dd"]):
        text = collapse(child.get_text(" "))
        if child.name == "dt":
            term = text
        elif text:
            items.append(f"{term}: {text}" if term else text)
    return items


class _Walker:
    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self._inline: list[str] = []

    def flush(self) -> None:
        if text := collapse(" ".join(self._inline)):
            self.blocks.append({"type": "paragraph", "text": text})
        self._inline = []

    def emit(self, block: Block) -> None:
        self.flush()
        self.blocks.append(block)

    def walk(self, node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                self._inline.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name in HEADINGS:
                if text := collapse(child.get_text(" ")):
                    self.emit({"type": "heading", "level": HEADINGS[name], "text": text})
            elif name == "p":
                self.flush()
                self._inline.append(child.get_text(" "))
                self.flush()
            elif name in ("ul", "ol"):
                if items := _list_items(child):
                    self.emit({"type": "list", "ordered": name == "ol", "items": items})
            elif name == "dl":
                if items := _definition_items(child):
                    self.emit({"type": "list", "ordered": False, "items": items})
            elif name == "table" and not is_layout_table(child):
                table = parse_table(child)
                if table is not None:
                    self.emit(table)
            elif name == "pre":
                if text := child.get_text().strip("\n"):
                    self.emit({"type": "code", "text": text})
            elif name == "blockquote":
                if text := collapse(child.get_text(" ")):
                    self.emit({"type": "quote", "text": text})
            elif name == "br":
                self._inline.append(" ")
            elif name in INLINE and not _has_block_child(child):
                self._inline.append(child.get_text(" "))
            else:  # a container: div, section, article, a layout table and its cells, ...
                self.flush()
                self.walk(child)
                self.flush()


def _dedupe_adjacent(blocks: list[Block]) -> list[Block]:
    result: list[Block] = []
    for block in blocks:
        if not result or result[-1] != block:
            result.append(block)
    return result


def _title(root: Tag, original: str) -> str:
    """The first <h1> in the content, else the document <title>."""
    heading = root.find("h1")
    if heading is not None and (text := collapse(heading.get_text(" "))):
        return text
    return original


def block_text(block: Block) -> str:
    """The plain text of one block, as it goes into the search index."""
    kind = block["type"]
    if kind == "list":
        return "\n".join(block["items"])
    if kind == "table":
        lines = [block["caption"]] if block.get("caption") else []
        lines += [" | ".join(row) for row in [block["header"], *block["rows"]] if any(row)]
        return "\n".join(lines)
    return str(block["text"])


def clean_html(
    html: str, title_selector: str | None = None, remove: list[str] | None = None
) -> CleanedPage:
    soup = parse_html(html)
    original_title = collapse(soup.title.get_text(" ")) if soup.title else ""
    # Title first: a site's title element may sit in a region that cleaning drops.
    custom_title = soup.select_one(title_selector) if title_selector else None
    custom_text = collapse(custom_title.get_text(" ")) if custom_title is not None else ""

    _remove_clutter(soup, remove or [])
    root = _content_root(soup)
    walker = _Walker()
    walker.walk(root)
    walker.flush()
    blocks = _dedupe_adjacent(walker.blocks)

    title = custom_text or _title(root, original_title)
    text = "\n".join(block_text(b) for b in blocks)
    return CleanedPage(title=title, blocks=blocks, text=text)
