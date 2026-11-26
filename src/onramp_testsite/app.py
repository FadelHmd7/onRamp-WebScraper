"""Test site server.

Normal pages
    /                       home
    /catalog?page=N         30 listing pages, 20 items each, with prev/next links
    /items/{id}             600 item pages (title, price, list, specs table, links)
    /js/items/{id}          same item, but the content is rendered by JavaScript
    /editable/{slug}        pages that /_admin can change or delete (versioning demo)
    /private/...            disallowed by robots.txt
    /whoami                 echoes the User-Agent the crawler sent

Failure modes (all counters are per id, reset with POST /_admin/reset)
    /slow/{id}?delay=S[&times=N]    answers after S seconds (the first N times only)
    /flaky/{id}?fail=N              503 for the first N requests, then 200
    /throttle/{id}?retry_after=S    429 with Retry-After until S seconds pass, then 200
    /broken/{id}                    503 forever (dead-letter demo)

Every HTML page sends ETag and Last-Modified and answers conditional
requests with 304, like a well-behaved real site.

The /_admin routes have no auth. This server is a local test fixture only.
"""

import asyncio
import hashlib
import os
import time
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from html import escape

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from onramp_testsite import content

LATENCY_SECONDS = int(os.environ.get("TESTSITE_LATENCY_MS", "50")) / 1000
SITE_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)  # Last-Modified of generated pages

ROBOTS_TXT = """User-agent: *
Disallow: /private/
Disallow: /_admin/
"""


class EditablePage(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    paragraphs: list[str] = Field(min_length=1)


def _seed_pages() -> dict[str, tuple[EditablePage, datetime]]:
    now = datetime.now(UTC).replace(microsecond=0)
    seeds = {
        "pricing": EditablePage(
            title="Pricing", paragraphs=["The basic plan costs $10 per month."]
        ),
        "changelog": EditablePage(title="Changelog", paragraphs=["Version 1.0 released."]),
        "team": EditablePage(title="Our team", paragraphs=["We are three engineers."]),
    }
    return {slug: (page, now) for slug, page in seeds.items()}


class State:
    """Mutable server state. One uvicorn process, one event loop, so no locks."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.flaky_hits: dict[int, int] = {}
        self.slow_hits: dict[int, int] = {}
        self.throttle_started: dict[int, float] = {}
        self.editable = _seed_pages()


state = State()
app = FastAPI(title="onRamp test site", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------- helpers


def html_page(request: Request, html: str, last_modified: datetime) -> Response:
    """HTML response with ETag and Last-Modified, answering 304 when unchanged."""
    etag = '"' + hashlib.sha256(html.encode()).hexdigest()[:16] + '"'
    headers = {
        "ETag": etag,
        "Last-Modified": format_datetime(last_modified, usegmt=True),
        "Cache-Control": "no-cache",
    }
    if_none_match = request.headers.get("if-none-match")
    if if_none_match is not None:
        if etag in [tag.strip() for tag in if_none_match.split(",")]:
            return Response(status_code=304, headers=headers)
    elif (if_modified_since := request.headers.get("if-modified-since")) is not None:
        try:
            if last_modified <= parsedate_to_datetime(if_modified_since):
                return Response(status_code=304, headers=headers)
        except (TypeError, ValueError):
            pass  # malformed date: ignore it, as HTTP says
    return HTMLResponse(html, headers=headers)


async def simulated_latency() -> None:
    if LATENCY_SECONDS > 0:
        await asyncio.sleep(LATENCY_SECONDS)


def item_or_404(item_id: int) -> content.Item:
    if not 1 <= item_id <= content.ITEM_COUNT:
        raise HTTPException(status_code=404, detail="No such item")
    return content.item(item_id)


# ---------------------------------------------------------------- normal pages


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    return ROBOTS_TXT


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def home(request: Request) -> Response:
    body = """    <h1>Test Catalog</h1>
    <p>A local site for crawler tests.</p>
    <ul>
      <li><a href="/catalog?page=1">Catalog</a></li>
      <li><a href="/editable">Editable pages</a></li>
      <li><a href="/js/items/1">JavaScript-rendered item</a></li>
    </ul>"""
    return html_page(request, content.layout("Home", body), SITE_EPOCH)


@app.get("/catalog")
async def catalog(request: Request, page: int = Query(1, ge=1)) -> Response:
    if page > content.CATALOG_PAGES:
        raise HTTPException(status_code=404, detail="No such page")
    await simulated_latency()
    html = content.layout(f"Catalog page {page}", content.catalog_body(page))
    return html_page(request, html, SITE_EPOCH)


@app.get("/items/{item_id}")
async def item_page(request: Request, item_id: int) -> Response:
    it = item_or_404(item_id)
    await simulated_latency()
    return html_page(request, content.layout(it.title, content.item_body(it)), SITE_EPOCH)


@app.get("/api/items/{item_id}")
def item_json(item_id: int) -> JSONResponse:
    it = item_or_404(item_id)
    return JSONResponse({"html": content.item_body(it), "title": it.title})


@app.get("/js/items/{item_id}")
async def js_item_page(
    request: Request, item_id: int, late_ms: int = Query(0, ge=0, le=10_000)
) -> Response:
    """The raw HTML has an empty <div id="app">. Only a browser sees the item.

    ``late_ms``: a "related" section is added that long after the item, like
    an app that fills in sections one after another.
    """
    item_or_404(item_id)
    await simulated_latency()
    late = (
        f"""
          setTimeout(() => {{
            const section = document.createElement("section");
            section.id = "late";
            section.innerHTML = '<h2>Also loaded later</h2><a href="/items/{item_id + 1}">Next</a>';
            document.getElementById("app").appendChild(section);
          }}, {late_ms});"""
        if late_ms
        else ""
    )
    body = f"""    <div id="app"><p class="loading">Loading…</p></div>
    <script>
      fetch("/api/items/{item_id}")
        .then((r) => r.json())
        .then((data) => {{
          document.title = data.title + " | Test Catalog";
          document.getElementById("app").innerHTML = data.html;{late}
        }});
    </script>"""
    return html_page(request, content.layout("Loading", body), SITE_EPOCH)


@app.get("/whoami")
def whoami(request: Request) -> HTMLResponse:
    """Echoes the caller's User-Agent, to check what a crawler really sends."""
    agent = escape(request.headers.get("user-agent", ""))
    return HTMLResponse(content.layout("Who am I", f'    <p id="user-agent">{agent}</p>'))


@app.get("/private/{path:path}")
def private(request: Request, path: str) -> Response:
    body = f"    <h1>Private</h1>\n    <p>robots.txt disallows {escape(path)}.</p>"
    return html_page(request, content.layout("Private", body), SITE_EPOCH)


# ---------------------------------------------------------------- editable pages


@app.get("/editable")
def editable_index(request: Request) -> Response:
    links = "\n".join(
        f'      <li><a href="/editable/{slug}">{escape(page.title)}</a></li>'
        for slug, (page, _) in sorted(state.editable.items())
    )
    body = f"    <h1>Editable pages</h1>\n    <ul>\n{links}\n    </ul>"
    return html_page(request, content.layout("Editable pages", body), SITE_EPOCH)


@app.get("/editable/{slug}")
def editable_page(request: Request, slug: str) -> Response:
    if slug not in state.editable:
        raise HTTPException(status_code=404, detail="Page removed or never existed")
    page, updated = state.editable[slug]
    paragraphs = "\n".join(f"    <p>{escape(p)}</p>" for p in page.paragraphs)
    body = f"    <h1>{escape(page.title)}</h1>\n{paragraphs}"
    return html_page(request, content.layout(page.title, body), updated)


@app.put("/_admin/editable/{slug}")
def put_editable(slug: str, page: EditablePage) -> dict[str, str]:
    state.editable[slug] = (page, datetime.now(UTC).replace(microsecond=0))
    return {"status": "saved", "slug": slug}


@app.delete("/_admin/editable/{slug}")
def delete_editable(slug: str) -> dict[str, str]:
    if state.editable.pop(slug, None) is None:
        raise HTTPException(status_code=404, detail="No such page")
    return {"status": "deleted", "slug": slug}


@app.post("/_admin/reset")
def reset() -> dict[str, str]:
    state.reset()
    return {"status": "reset"}


# ---------------------------------------------------------------- failure modes


@app.get("/slow/{item_id}")
async def slow(
    request: Request,
    item_id: int,
    delay: float = Query(10, ge=0, le=120),
    times: int | None = Query(None, ge=0, le=100),
) -> Response:
    hits = state.slow_hits.get(item_id, 0) + 1
    state.slow_hits[item_id] = hits
    if times is None or hits <= times:  # slow every time, or only the first `times`
        await asyncio.sleep(delay)
    it = item_or_404(item_id)
    return html_page(request, content.layout(it.title, content.item_body(it)), SITE_EPOCH)


@app.get("/flaky/{item_id}")
def flaky(request: Request, item_id: int, fail: int = Query(2, ge=0, le=100)) -> Response:
    hits = state.flaky_hits.get(item_id, 0) + 1
    state.flaky_hits[item_id] = hits
    if hits <= fail:
        return PlainTextResponse(f"Temporary failure {hits} of {fail}", status_code=503)
    it = item_or_404(item_id)
    return html_page(request, content.layout(it.title, content.item_body(it)), SITE_EPOCH)


@app.get("/throttle/{item_id}")
def throttle(request: Request, item_id: int, retry_after: int = Query(5, ge=1, le=300)) -> Response:
    started = state.throttle_started.setdefault(item_id, time.monotonic())
    remaining = retry_after - (time.monotonic() - started)
    if remaining > 0:
        wait = max(1, round(remaining))
        return PlainTextResponse(
            "Too many requests", status_code=429, headers={"Retry-After": str(wait)}
        )
    it = item_or_404(item_id)
    return html_page(request, content.layout(it.title, content.item_body(it)), SITE_EPOCH)


@app.get("/broken/{item_id}")
def broken(item_id: int) -> PlainTextResponse:
    return PlainTextResponse(f"Item {item_id} is permanently broken", status_code=503)
