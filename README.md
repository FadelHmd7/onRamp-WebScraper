# onRamp-WebScraper

A distributed, fault-tolerant web crawler for the onRamp bootcamp. Phase 1 crawls three websites on one theme, then cleans, versions and serves the pages through an authenticated API and a web UI.

## Documents

- [docs/phase1/SPEC.md](docs/phase1/SPEC.md) — Phase 1 requirements, restated from the assignment
- [docs/phase1/TRACKER.md](docs/phase1/TRACKER.md) — ordered tasks and status
- [docs/DECISIONS.md](docs/DECISIONS.md) — chosen stack, each with the alternative we rejected

## Stack

Python 3.12 · Celery on RabbitMQ · Redis · PostgreSQL 16 · FastAPI · Playwright · BeautifulSoup · React + Vite · Docker Compose · GitHub Actions.

## Development

Needs [uv](https://docs.astral.sh/uv/). It installs Python 3.12 and every dependency from `uv.lock`.

```
uv sync                 # create .venv from the lockfile
uv run ruff check .     # lint
uv run ruff format .    # format
uv run mypy             # type-check src/
uv run pytest           # unit tests
```

Layout:

- `src/onramp_scraper/crawler` — fetching, robots.txt, rate limiting, URL normalization, frontier
- `src/onramp_scraper/processing` — cleaning, schema, site fields, change detection
- `src/onramp_scraper/storage` — models, migrations, idempotent writes
- `src/onramp_scraper/worker` — Celery app and tasks
- `src/onramp_scraper/api` — FastAPI app
- `src/onramp_scraper/autoscaler` — bonus autoscaler
- `tests/unit`, `tests/fixtures` — unit tests and saved HTML

## Setup

Needs Docker with Compose v2. From a clean checkout:

```
docker compose up --build
```

No `.env` file is needed to start: every setting has a default. Before crawling real sites, copy `.env.example` to `.env` and put a real contact address in `USER_AGENT`.

| Service | URL |
| --- | --- |
| Web UI | http://localhost:9070 |
| API and Swagger | http://localhost:9060/docs |
| RabbitMQ console | http://localhost:15672 (user `scraper`, password `scraper`) |

Change the host ports with `UI_PORT`, `API_PORT` and `RABBITMQ_UI_PORT` in `.env`.

## Scaling workers

Each worker is its own container and runs one job at a time. Run four:

```
docker compose up --build --scale worker=4
```

Change the count on a running stack with `docker compose up -d --scale worker=N`. Workers hold no state, so you can add or remove them at any time.
