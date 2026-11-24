# One image recipe for every Python component. Build targets:
#   api     — FastAPI served by uvicorn
#   worker  — Celery worker (Playwright and Chromium are added in task S5)

ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------- build
FROM python:${PYTHON_VERSION}-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------- runtime base
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
USER app

# ---------------------------------------------------------------- api
FROM runtime AS api
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "onramp_scraper.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------- worker
FROM runtime AS worker
# %h is the container hostname, so every scaled worker has a unique name.
CMD ["celery", "--app", "onramp_scraper.worker.app", "worker", \
     "--hostname", "worker@%h", "--queues", "crawl", \
     "--concurrency", "1", "--prefetch-multiplier", "1", "--loglevel", "INFO"]
