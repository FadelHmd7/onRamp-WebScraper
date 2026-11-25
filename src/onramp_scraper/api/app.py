"""FastAPI application: the authenticated API over the crawl data.

OpenAPI is published at ``/openapi.json`` with Swagger UI at ``/docs``. Use
its "Authorize" button with the seeded admin or viewer to try every route.

Public: ``/health``, ``/auth/token`` (login) and the API docs. Everything
else needs a bearer token; admin-only routes answer 403 to a viewer.
"""

from fastapi import FastAPI

from onramp_scraper import __version__
from onramp_scraper.api.routes import auth, crawls, dlq, documents, pages, search

DESCRIPTION = """
Distributed web scraper, Phase 1. Log in with **Authorize** (the seeded
`admin` or `viewer` user), then call any route.

| Role | Can |
| --- | --- |
| viewer | read crawls, documents, versions, raw pages and search |
| admin | everything a viewer can, plus start crawls and retry dead-letter jobs |
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="onRamp Web Scraper API",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=[
            {"name": "auth", "description": "Log in and see who you are."},
            {"name": "crawls", "description": "Start crawls (admin) and follow their progress."},
            {"name": "documents", "description": "Cleaned documents and their versions."},
            {"name": "pages", "description": "Raw HTML and fetch details."},
            {"name": "search", "description": "Full-text search with highlighted snippets."},
            {
                "name": "dead-letter queue",
                "description": "Jobs that failed every attempt; retry them (admin).",
            },
            {"name": "health", "description": "Public status of the system."},
        ],
    )
    app.include_router(auth.router)
    app.include_router(crawls.router)
    app.include_router(documents.router)
    app.include_router(pages.router)
    app.include_router(search.router)
    app.include_router(dlq.router)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        """Public liveness check. Queue length and active workers are added in A7."""
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
