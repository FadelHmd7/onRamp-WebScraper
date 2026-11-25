# onRamp-WebScraper

[![CI](https://github.com/FadelHmd7/onRamp-WebScraper/actions/workflows/ci.yml/badge.svg)](https://github.com/FadelHmd7/onRamp-WebScraper/actions/workflows/ci.yml)

A distributed, fault-tolerant web crawler for the onRamp bootcamp. Phase 1 crawls three websites on one theme, then cleans, versions and serves the pages through an authenticated API and a web UI.

## Documents

- [docs/phase1/SPEC.md](docs/phase1/SPEC.md) — Phase 1 requirements, restated from the assignment
- [docs/phase1/TRACKER.md](docs/phase1/TRACKER.md) — ordered tasks and status
- [docs/DECISIONS.md](docs/DECISIONS.md) — chosen stack, each with the alternative we rejected
- [docs/phase1/SITES.md](docs/phase1/SITES.md) — theme, the three sites, and the candidates we rejected
- [docs/phase1/COMPLIANCE.md](docs/phase1/COMPLIANCE.md) — robots.txt, terms and rate limits per site
- [config/sites.yaml](config/sites.yaml) — per-site crawl rules: seeds, limits, fetch mode, rate, selectors

## Stack

Python 3.12 · Celery on RabbitMQ · Redis · PostgreSQL 16 · FastAPI · Playwright · BeautifulSoup · React + Vite · Docker Compose · GitHub Actions.

## Development

Needs [uv](https://docs.astral.sh/uv/). It installs Python 3.12 and every dependency from `uv.lock`.

```
uv sync --all-extras    # create .venv from the lockfile, including Playwright
uv run ruff check .     # lint
uv run ruff format .    # format
uv run mypy             # type-check src/
uv run pytest           # unit tests
```

The browser tests need Chromium once: `uv run playwright install chromium`. Without it they are skipped.

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs on every push: lint and type checks, unit tests, then it builds every image, starts the stack with two workers and checks that both are consuming from the queue.

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
| Test site | http://localhost:9050 (local only) |

Change the host ports with `UI_PORT`, `API_PORT`, `RABBITMQ_UI_PORT` and `TESTSITE_PORT` in `.env`.

## Test site

`testsite` is a local site we control, so failure demos and the benchmark never stress a real server. Workers reach it at `http://testsite:8000`.

| Path | Behaviour |
| --- | --- |
| `/catalog?page=1..30`, `/items/1..600` | 630 linked pages with nav, footer, ads, lists and tables |
| `/js/items/{id}` | Content appears only after JavaScript runs |
| `/editable/{slug}` | Pages to change between crawls (versioning demo) |
| `/slow/{id}?delay=10` | Answers after the delay (timeout demo) |
| `/flaky/{id}?fail=2` | 503 for the first N requests, then 200 |
| `/throttle/{id}?retry_after=5` | 429 with `Retry-After` until the time passes |
| `/broken/{id}` | 503 forever (dead-letter demo) |
| `/private/...` | Disallowed by its `robots.txt` |

Every page sends `ETag` and `Last-Modified` and answers 304 when unchanged. Edit, delete and reset:

```
curl -X PUT localhost:9050/_admin/editable/pricing -H "Content-Type: application/json"      -d '{"title": "Pricing", "paragraphs": ["The basic plan now costs $12."]}'
curl -X DELETE localhost:9050/_admin/editable/team
curl -X POST localhost:9050/_admin/reset
```

## Scaling workers

Each worker is its own container and runs one job at a time. Run four:

```
docker compose up --build --scale worker=4
```

Change the count on a running stack with `docker compose up -d --scale worker=N`. Workers hold no state, so you can add or remove them at any time.
