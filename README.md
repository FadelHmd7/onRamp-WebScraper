# onRamp-WebScraper

[![CI](https://github.com/FadelHmd7/onRamp-WebScraper/actions/workflows/ci.yml/badge.svg)](https://github.com/FadelHmd7/onRamp-WebScraper/actions/workflows/ci.yml)

A distributed, fault-tolerant web crawler. Phase 1 crawls three book sites — [books.toscrape.com](https://books.toscrape.com), [Standard Ebooks](https://standardebooks.org) and [Inventaire](https://inventaire.io) (a JavaScript app) — then cleans, validates, versions and serves the pages through an authenticated API and a web UI.

- **Distributed:** any number of identical worker containers share one RabbitMQ queue; a shared Redis frontier hands each URL to exactly one worker, and a shared rate limiter keeps every site's limit across all of them.
- **Fault tolerant:** kill a worker mid-page and another finishes it; timeouts, 5xx and 429 are retried with backoff (never sooner than `Retry-After`); pages that fail every attempt go to a dead-letter queue an admin can retry.
- **Polite:** `robots.txt` and `Crawl-delay` are honoured, every request names this project, and each site has its own rate.
- **Versioned:** a page that changes gets a new version; old versions are kept; pages that disappear are marked removed, not deleted.

```mermaid
flowchart LR
    UI[Web UI<br/>React, nginx :9070] -->|/api| API[API<br/>FastAPI :9060]
    API --> PG[(PostgreSQL<br/>pages, versions,<br/>links, full-text index)]
    API -->|start crawl| MQ[[RabbitMQ<br/>crawl queue]]
    MQ --> W1[worker 1]
    MQ --> W2[worker 2]
    MQ --> WN[worker N]
    W1 & W2 & WN --> R[(Redis<br/>frontier, rate limits,<br/>robots cache, heartbeats)]
    W1 & W2 & WN --> PG
    W1 & W2 & WN -->|HTTP or Chromium| WEB((websites))
    W1 & W2 & WN -->|new links| MQ
```

## Quick start

Needs Docker with Compose v2. From a clean clone:

```
docker compose up -d --build
```

The first build takes a few minutes (it downloads Chromium for the workers). No `.env` file is needed: every setting has a working default for local use. A one-shot `migrate` service creates the database schema, syncs `config/sites.yaml` and creates the two users; the API and workers start after it.

| | Where | Login |
| --- | --- | --- |
| Web UI | http://localhost:9070 | `admin` / `admin-dev-password` or `viewer` / `viewer-dev-password` |
| API and Swagger | http://localhost:9060/docs | **Authorize** with the same users |
| RabbitMQ console | http://localhost:15672 | `scraper` / `scraper` |
| Test site | http://localhost:9050 | local only |

Then, as admin in the UI, pick **Local test site** and **Start crawl**: the dashboard shows 630 pages being crawled by the workers. `docker compose down` stops everything; add `-v` to also delete the data.

Before crawling the real sites, copy `.env.example` to `.env` and check `USER_AGENT`: it is sent with every request and must let a site owner reach you. Outside local development set `APP_ENV=production` and real values for `JWT_SECRET`, `ADMIN_PASSWORD` and `VIEWER_PASSWORD`; the API refuses to start on the defaults.

## Scaling workers

Each worker is a separate container that runs one page at a time and keeps no state, so workers can be added or removed at any moment, even during a crawl:

```
docker compose up -d --scale worker=4
```

A worker that is stopped finishes its current page first (30 s grace); one that is killed loses nothing, because RabbitMQ hands its unacknowledged job to another worker. The dashboard and `GET /health` show how many workers are alive (from their heartbeats).

**Autoscaling (optional).** `docker compose --profile autoscale up -d` adds a container that scales `worker` from the queue backlog: one worker per 25 waiting jobs, between 1 and 8, up at once and down one at a time after 30 s of low backlog (`AUTOSCALE_*` in `.env.example`). It uses the Docker socket, which gives it full control of Docker, so it is off by default and meant for local use.

How far scaling helps is measured in [docs/phase1/BENCHMARK.md](docs/phase1/BENCHMARK.md): 396, 674, 1,083 and 1,273 pages per minute with 1, 2, 4 and 8 workers on a 4-CPU machine.

## Using it

**Web UI** (http://localhost:9070):

- **Dashboard** — queue length, active workers and what each is fetching, crawl progress and errors, refreshed every 3 s. Admins also start crawls and retry dead-letter jobs.
- **Search** — full-text search with highlighted snippets and a site filter; with no query, the newest documents.
- **Document** — the cleaned content (headings, lists, tables), site fields, links, the raw HTML, and every version with what changed between them.

**API** (Swagger at http://localhost:9060/docs). Every route needs a token except `GET /health` and the login:

```
TOKEN=$(curl -s -d username=admin -d password=admin-dev-password localhost:9060/auth/token | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"site": "testsite", "mode": "full"}' localhost:9060/crawls
curl -H "Authorization: Bearer $TOKEN" "localhost:9060/search?q=distributed+systems&site=testsite"
```

| Endpoint | Role | |
| --- | --- | --- |
| `POST /auth/token`, `GET /auth/me` | anyone / logged in | log in (JWT), who am I |
| `GET /health` | public | dependencies, queue length, active workers |
| `GET /sites` | viewer | the configured sites |
| `POST /crawls` | admin | start a full or incremental crawl (409 while the site already has one running) |
| `GET /crawls`, `GET /crawls/{id}` | viewer | progress, counts by status, recent errors |
| `GET /crawls/{id}/urls?status=` | viewer | the crawl's URLs and their state, newest first |
| `GET /documents`, `GET /documents/{id}` | viewer | cleaned documents, filtered and paginated |
| `GET /documents/{id}/versions[/{n}]` | viewer | version history, any old version in full |
| `GET /pages/{id}/raw` | viewer | the HTML as fetched, with status, headers and time |
| `GET /search?q=&site=` | viewer | PostgreSQL full-text search (GIN index) with snippets |
| `GET /dlq`, `POST /dlq/{id}/retry` | admin | failed jobs and retry (409 if its crawl was cancelled) |

**Command line**, inside any worker container:

```
docker compose exec worker python -m onramp_scraper.worker.cli start testsite            # prints the crawl id
docker compose exec worker python -m onramp_scraper.worker.cli start testsite --mode incremental
docker compose exec worker python -m onramp_scraper.worker.cli start testsite --seed http://testsite:8000/items/1 --max-depth 0
docker compose exec worker python -m onramp_scraper.worker.cli wait <crawl-id>           # progress until it ends
docker compose exec worker python -m onramp_scraper.worker.cli workers                   # live heartbeats
docker compose exec worker python -m onramp_scraper.worker.cli dead                      # dead-letter queue
docker compose exec worker python -m onramp_scraper.worker.cli retry <dead-letter-id>
```

A **full** crawl downloads every page; an **incremental** crawl sends the stored `ETag` / `Last-Modified`, so an unchanged page costs a `304` and nothing is downloaded. Either way a new version is stored only when the cleaned content changed.

## Failure demos

With the stack running (`docker compose up -d --scale worker=4`), each script causes one failure on the test site, watches the system recover and ends with PASS or FAIL and the evidence:

```
uv run python scripts/demo/run_all.py      # all four, about three minutes
uv run python scripts/demo/kill_worker.py  # docker kill a worker mid-page; another finishes it
uv run python scripts/demo/timeout.py      # a 40 s page times out at 30 s; the retry succeeds
uv run python scripts/demo/rate_limited.py # 429 with Retry-After: 10 is waited out exactly
uv run python scripts/demo/always_fail.py  # 5 failed attempts -> dead-letter queue -> admin retry
```

They need only Python 3.12 and Docker (`python scripts/demo/run_all.py` works too).

## Sample export

```
uv run python scripts/export.py            # writes exports/<site>.jsonl and exports/manifest.json
```

It logs in as the viewer and writes 120 cleaned documents per site (detail pages first), each with its blocks, fields, links and hash, and every older version in full when a document has several. The committed sample is in [exports/](exports/).

## Configuration

| File | What |
| --- | --- |
| [.env.example](.env.example) | every setting with its default: ports, logins, `USER_AGENT`, retries, timeouts, heartbeats, autoscaling |
| [config/sites.yaml](config/sites.yaml) | per site: seeds, URL patterns, depth and page limits, rate, HTTP or browser, cleaning rules, field selectors |

Sites are validated when the containers start, so a bad pattern or selector stops the stack with a clear message instead of failing mid-crawl.

## Test site

`testsite` is a local site we control, so the demos and the benchmark never stress a real server. Workers reach it at `http://testsite:8000`, your browser at http://localhost:9050.

| Path | Behaviour |
| --- | --- |
| `/catalog?page=1..30`, `/items/1..600` | 630 linked pages with nav, footer, ads, lists and tables |
| `/js/items/{id}[?late_ms=N]` | Content appears only after JavaScript runs (a second part N ms later) |
| `/editable/{slug}` | Pages to change between crawls (versioning demo) |
| `/slow/{id}?delay=S[&times=N]` | Answers after S seconds (the first N times only) |
| `/flaky/{id}?fail=N` | 503 for the first N requests, then 200 |
| `/throttle/{id}?retry_after=S` | 429 with `Retry-After` until S seconds pass |
| `/broken/{id}` | 503 forever |
| `/private/...` | Disallowed by its `robots.txt` |

Every page sends `ETag` and `Last-Modified` and answers `304` when unchanged. Edit, delete and reset pages:

```
curl -X PUT localhost:9050/_admin/editable/pricing -H "Content-Type: application/json" \
     -d '{"title": "Pricing", "paragraphs": ["The basic plan now costs $12."]}'
curl -X DELETE localhost:9050/_admin/editable/team
curl -X POST localhost:9050/_admin/reset
```

## Development

Python needs [uv](https://docs.astral.sh/uv/), which installs Python 3.12 and every dependency from `uv.lock`. The UI needs Node 22 or newer.

```
uv sync --all-extras                  # .venv from the lockfile, Playwright included
uv run playwright install chromium    # once, for the browser tests
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest                         # unit tests; integration tests are skipped without the variables below
```

Integration tests run against a real PostgreSQL and Redis. They **empty the tables**, so point them at throwaway containers, never at the compose database:

```
docker run -d --rm --name test-pg -p 127.0.0.1:55432:5432 -e POSTGRES_USER=scraper -e POSTGRES_PASSWORD=scraper -e POSTGRES_DB=scraper postgres:16-alpine
docker run -d --rm --name test-redis -p 127.0.0.1:56379:6379 redis:7-alpine
DATABASE_URL=postgresql://scraper:scraper@127.0.0.1:55432/scraper REDIS_URL=redis://127.0.0.1:56379/0 uv run pytest
```

The UI:

```
cd ui
npm ci
npm run dev          # http://localhost:5173, /api goes to the API on :9060
npm run lint && npm run typecheck && npm test && npm run build
```

On Windows, run `npm test` from a path that starts with a capital drive letter (`C:\...`): Vitest loads modules twice when the drive letter is lower case. On a slow machine add `-- --pool=threads --maxWorkers=1`.

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs on every push: Python lint and types; unit, integration and browser tests with real PostgreSQL, Redis and Chromium; the UI's lint, types, tests and build; then it builds every image, starts the stack with two workers and checks it end to end.

| Directory | |
| --- | --- |
| `src/onramp_scraper/crawler` | URL normalization, robots.txt, rate limiting, HTTP and browser fetching, frontier |
| `src/onramp_scraper/processing` | cleaning, tables, document schema, site fields, change detection |
| `src/onramp_scraper/storage` | models, migrations, idempotent writes, queries |
| `src/onramp_scraper/worker` | the crawl pipeline, Celery app, retries, heartbeats, crawl runs, CLI |
| `src/onramp_scraper/api` | FastAPI app, auth, routes |
| `src/onramp_scraper/autoscaler` | the optional autoscaler |
| `src/onramp_testsite` | the local test site |
| `ui/` | React app and its nginx image |
| `scripts/` | failure demos, benchmark, export, fixture fetching |
| `tests/` | unit, integration (real services) and browser tests; saved HTML in `tests/fixtures` |

## Documents

- **[docs/phase1/REPORT.pdf](docs/phase1/REPORT.pdf)** — the Phase 1 report: sites, architecture and diagrams, data model, choices, fault tolerance and benchmark, compliance ([Markdown source](docs/phase1/REPORT.md); rebuild with `uv run python scripts/build_report.py`)
- [docs/phase1/VIDEO.md](docs/phase1/VIDEO.md) — the script for the narrated demo video
- [docs/phase1/SPEC.md](docs/phase1/SPEC.md) — the Phase 1 requirements
- [docs/phase1/TRACKER.md](docs/phase1/TRACKER.md) — tasks, what "done" meant for each, and how it was verified
- [docs/DECISIONS.md](docs/DECISIONS.md) — the stack, each choice with the alternative rejected and why
- [docs/phase1/SITES.md](docs/phase1/SITES.md) — the theme, the three sites and the candidates rejected
- [docs/phase1/COMPLIANCE.md](docs/phase1/COMPLIANCE.md) — robots.txt, terms and rate limits per site
- [docs/phase1/DATA_MODEL.md](docs/phase1/DATA_MODEL.md) — tables, versioning, removal and the full-text index
- [docs/phase1/BENCHMARK.md](docs/phase1/BENCHMARK.md) — 1, 2, 4 and 8 workers, and where scaling stops
