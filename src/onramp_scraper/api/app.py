"""FastAPI application. E1 stub: only the public health check."""

from fastapi import FastAPI

from onramp_scraper import __version__

app = FastAPI(title="onRamp Web Scraper API", version=__version__)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Public liveness check. Queue length and active workers are added in A7."""
    return {"status": "ok", "version": __version__}
