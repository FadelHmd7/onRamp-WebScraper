# One image recipe for every Python component. Build targets:
#   api       — FastAPI served by uvicorn
#   worker    — Celery worker, with Playwright and headless Chromium
#   testsite  — local test site for failure demos and the benchmark
#
# Only the worker installs Playwright and Chromium; the other images stay small.

ARG PYTHON_VERSION=3.12
# Must match the playwright pin in pyproject.toml (a unit test checks this).
ARG PLAYWRIGHT_VERSION=1.65.1

# ---------------------------------------------------------------- build
FROM python:${PYTHON_VERSION}-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
# The uv cache mounts use sharing=locked: Docker builds stages in parallel,
# and two uv processes downloading the same wheel into one cache would wait
# on each other's lock and time out on a slow connection.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable

# Same environment plus the "browser" extra (Playwright) for the worker.
FROM build AS build-browser
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable --extra browser

# ---------------------------------------------------------------- runtime base
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN useradd --create-home --uid 1000 app
WORKDIR /app

# ---------------------------------------------------------------- api
FROM runtime AS api
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app config ./config
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "onramp_scraper.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------- worker
FROM runtime AS worker
ARG PLAYWRIGHT_VERSION
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# Chromium (headless shell only) and its system libraries. This layer depends
# only on PLAYWRIGHT_VERSION, not on our code, so Docker caches it and a code
# change never downloads the browser again.
# libatomic1 first: Playwright's bundled Node.js driver needs it just to start,
# before --with-deps can install Chromium's own libraries.
RUN --mount=from=ghcr.io/astral-sh/uv:0.13.9,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && uv tool run --from "playwright==${PLAYWRIGHT_VERSION}" \
        playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /ms-playwright
COPY --from=build-browser --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app config ./config
USER app
# %h is the container hostname, so every scaled worker has a unique name.
CMD ["celery", "--app", "onramp_scraper.worker.app", "worker", \
     "--hostname", "worker@%h", "--queues", "crawl", \
     "--concurrency", "1", "--prefetch-multiplier", "1", "--loglevel", "INFO"]

# ---------------------------------------------------------------- testsite
FROM runtime AS testsite
COPY --from=build --chown=app:app /app/.venv /app/.venv
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "onramp_testsite.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
