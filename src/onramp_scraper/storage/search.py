"""Full-text search over the current version of every document.

``document_versions.search`` is a generated ``tsvector`` (title weighted A,
text weighted B) with a GIN index, so ``search @@ query`` is an inverted-index
lookup, never a ``LIKE '%term%'`` scan. The query is parsed with
``websearch_to_tsquery``, which understands what people type into a search
box: ``"exact phrase"``, ``-excluded``, ``this or that``.

Only current versions of active pages are searched: a page is found by what
it says now. Snippets come from ``ts_headline``, computed only for the page
of results being returned (it re-parses the text, so it is the costly part).
"""

import html
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

# ts_headline marks matches with these control characters. The snippet is
# HTML-escaped first and only then are the markers turned into <mark> tags,
# so text scraped from a page ("<script>...") can never become markup.
START, STOP = "\x02", "\x03"
HEADLINE_OPTIONS = (
    f"StartSel={START}, StopSel={STOP}, MaxWords=35, MinWords=15, "
    'MaxFragments=2, FragmentDelimiter=" … "'
)

_MATCHES = """
    FROM document_versions v
    JOIN pages p ON p.id = v.page_id AND p.current_version = v.version,
         websearch_to_tsquery('english', :q) AS query
    WHERE v.search @@ query
      AND p.status = 'active'
      AND (CAST(:site AS text) IS NULL OR p.site_key = :site)
"""

# The f-strings below only join the constant fragments above. Everything the
# user sends (q, site, limit, offset) is a bound parameter.
_PAGE = f"""
    WITH hits AS (
        SELECT v.id AS version_id, p.id AS page_id, p.url, p.site_key, v.title, v.version,
               v.crawled_at, ts_rank(v.search, query) AS rank
        {_MATCHES}
        ORDER BY rank DESC, v.crawled_at DESC, v.id
        LIMIT :limit OFFSET :offset
    )
    SELECT hits.*, ts_headline('english', v.text_content, query, :options) AS snippet
    FROM hits
    JOIN document_versions v ON v.id = hits.version_id,
         websearch_to_tsquery('english', :q) AS query
    ORDER BY hits.rank DESC, hits.crawled_at DESC, hits.version_id
"""  # noqa: S608


@dataclass(frozen=True)
class Hit:
    page_id: int
    url: str
    site_key: str
    title: str
    version: int
    crawled_at: datetime
    rank: float
    snippet: str  # safe HTML: escaped text with <mark> around the matches


def safe_snippet(headline: str) -> str:
    return html.escape(headline).replace(START, "<mark>").replace(STOP, "</mark>")


def search(
    session: Session, q: str, site_key: str | None = None, limit: int = 20, offset: int = 0
) -> tuple[list[Hit], int]:
    """Matches ranked by relevance (title matches first), and the total."""
    params = {"q": q, "site": site_key}
    total = session.scalar(text(f"SELECT count(*) {_MATCHES}"), params) or 0
    if total == 0:
        return [], 0
    rows = session.execute(
        text(_PAGE), {**params, "limit": limit, "offset": offset, "options": HEADLINE_OPTIONS}
    )
    hits = [
        Hit(
            page_id=row.page_id,
            url=row.url,
            site_key=row.site_key,
            title=row.title,
            version=row.version,
            crawled_at=row.crawled_at,
            rank=round(float(row.rank), 6),
            snippet=safe_snippet(row.snippet),
        )
        for row in rows
    ]
    return hits, int(total)


def explain(session: Session, q: str) -> str:
    """The query plan of the match, to show (and test) that the GIN index is used."""
    plan = text(f"EXPLAIN SELECT v.id {_MATCHES}")
    rows = session.scalars(plan, {"q": q, "site": None})
    return "\n".join(rows)
