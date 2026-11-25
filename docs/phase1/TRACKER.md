# Phase 1 tracker

Rules:

- One task = one commit. A task is finished only when its "done when" check passes and it is committed.
- Work one task at a time. The next task starts only after you confirm.
- Each feature task includes its own unit tests. Tests use saved HTML in `tests/fixtures/`, never live sites.
- Branches: milestone 0 goes to `main`. Everything after it goes to `phase-1` (see `docs/DECISIONS.md`).

Spec: `docs/phase1/SPEC.md`. Stack: `docs/DECISIONS.md`.

## Now

| | |
| --- | --- |
| Current | **W8** — Autoscaler (bonus) |
| Next | M6 — API (**A1**) |

Status values: `todo`, `doing`, `done`.

---

## M0 — Foundation (`main`)

| ID | Task | Done when | Status |
| --- | --- | --- | --- |
| F1 | Python package skeleton, `.gitignore`, `.env.example` | Committed as `4f58b31` | done |
| F2 | Planning docs: spec restated, tracker rebuilt, stack decisions | Committed as `61ec486` | done |
| F3 | Tooling: uv lockfile, ruff, pytest and mypy config, `.gitattributes` (LF endings), sub-package layout | `uv run ruff check .` and `uv run pytest` pass, with a smoke test so pytest has something to run. Committed as `53172b2` | done |

After F3: push `main`, then create and push `phase-1`.

## M1 — Environment (Task 1, 10 pts)

| ID | Task | Done when | Status |
| --- | --- | --- | --- |
| E1 | `docker-compose.yml`: postgres, redis, rabbitmq, api stub, worker stub, ui placeholder. Healthchecks, settings from `.env`, no fixed name or port on `worker` | `docker compose up` is healthy and `docker compose up --scale worker=4` shows 4 workers. Verified: 4 consumers, 8 jobs split 2 per worker | done |
| E2 | GitHub Actions on every push: ruff, pytest (with Redis and Postgres services), build of every image | CI is green on `phase-1` | done |
| E3 | `testsite` container: 600 paginated pages, `/slow`, `/flaky`, `/429` with `Retry-After`, `/always-500`, editable pages for versioning, its own `robots.txt` | `curl` reaches each behaviour from inside the compose network | done |

## M2 — Sites and compliance

| ID | Task | Done when | Status |
| --- | --- | --- | --- |
| P1 | Pick one theme and three sites: A static, B JavaScript-rendered, C 500+ pages with pagination. At most one sandbox site | Approved. Committed as `a8b3de1` | done |
| P2 | `docs/phase1/COMPLIANCE.md` (robots rules, terms, rate limit per site) and `config/sites.yaml` (seeds, allowed hosts, fetch mode, depth, page limit, rate, selectors) | Every site has a filled row and a config entry, validated at load. Committed as `27db5da` | done |
| P3 | Save a few HTML pages per site into `tests/fixtures/` (fetched once, politely) | Fixtures committed with their source URL and date. All three sites, including rendered Inventaire pages; every `sites.yaml` selector matches a saved page | done |

## M3 — Crawl core (Task 2, 20 pts)

| ID | Task | Done when | Status |
| --- | --- | --- | --- |
| S1 | URL normalization: scheme and host case, default ports, fragments, dot segments, sorted query, tracking parameters removed | **Required test area 1** passes (42 tests, including every link in saved pages) | done |
| S2 | robots.txt checker (protego), cached in Redis per host, honours our User-Agent | **Required test area 2** passes against saved robots files (27 unit tests + 1 real-Redis test in CI) | done |
| S3 | Shared per-domain token bucket (Redis Lua script) | Test: 8 concurrent clients at 1 req/s get ≈1 req/s in total. Measured on real Redis 7: 8 workers at 4 req/s → 4.00 req/s | done |
| S4 | HTTP fetcher (httpx): timeouts, descriptive User-Agent, conditional `ETag` / `If-Modified-Since`, error classes (retryable vs permanent), `Retry-After` parsing | Unit tests with mocked transport (37), including robots-blocked URLs never requested and 429 pausing the whole host | done |
| S5 | Browser fetcher (Playwright), selected per site by `fetch_mode` in config, started only when needed | Real Chromium renders the test site's JavaScript page that plain HTTP cannot see; static sites never start a browser; worker image launches Chromium (verified by CI's build job) | done |
| S6 | Frontier: link extraction with the parser, allowed-host, depth and page-count limits, shared seen-set in Redis | Tests for each limit; 8 racing workers take each URL once and the page budget holds exactly (fakeredis + real Redis in CI) | done |

## M4 — Processing and storage (Task 4, 15 pts)

| ID | Task | Done when | Status |
| --- | --- | --- | --- |
| D1 | Data model and Alembic migrations: `sites`, `crawls`, `pages`, `fetches` (raw HTML, status, time, headers), `document_versions` (blocks JSONB, fields, hash, `tsvector`), `links`, `dead_letters`, `users`. A `migrate` service runs on start | Migrations apply on an empty database in compose; migration matches models; GIN full-text index used; design in `DATA_MODEL.md` | done |
| D2 | Cleaning: drop scripts, styles, nav, footer, ads; keep title, headings, paragraphs, lists, tables as ordered blocks | **Required test area 4** passes on fixtures from all three sites (23 tests); every cleaning selector matches a saved page | done |
| D3 | Tables stored as header plus rows and columns (extra content type) | Fixture table round-trips with the same cells | done |
| D4 | Pydantic document schema; invalid documents rejected with a logged reason | Tests for a valid document and each rejection reason | done |
| D5 | Site-specific fields from selectors in `sites.yaml` (for example price, code) | Fixture tests per site | done |
| D6 | Change detection: `304` / unchanged `ETag` / same content hash means no new version; a changed hash means version + 1 | **Required test area 3** passes (18 unit tests); on real Postgres, old versions are kept, removed pages keep their versions, and 8 concurrent workers never duplicate a version | done |
| D7 | Idempotent writes for fetch, version and links (`ON CONFLICT`, unique keys) | Test: saving the same result twice leaves the same rows (snapshot of every row in pages, fetches, versions and links), also with 6 concurrent deliveries of one job | done |

## M5 — Distributed workers and fault tolerance (Task 3, 25 pts)

| ID | Task | Done when | Status |
| --- | --- | --- | --- |
| W1 | Celery app on RabbitMQ: `crawl_page` task (fetch → clean → validate → store → enqueue links), `acks_late`, `reject_on_worker_lost`, prefetch 1, concurrency 1 | Crawl of `testsite` with 4 workers stores every page once. Verified: 4 Celery containers stored all 630 pages once in about 25 s (157/161/157/155 per worker); integration test with 4 worker threads; a redelivered job re-sends links its crashed run accepted | done |
| W2 | Crawl runs: create a crawl, enqueue seeds, count queued, done, failed and skipped pages, mark the run finished | Progress counts match the database after a run. Verified in compose: `cli wait` shows a 4-worker re-crawl reach 630/630 (all unchanged) and finish; finishing happens once even when 8 jobs race | done |
| W3 | Retries: exponential backoff with jitter, honour `Retry-After`, max N attempts, then a dead-letter row (URL, last error, attempts); re-enqueue from the dead-letter queue | `/flaky` succeeds after retries, `/always-500` lands in the dead-letter queue, `/429` waits as asked. Verified with 4 Celery workers: flaky stored on attempt 3, `/broken` dead after 5 jittered attempts, `/throttle` retried after exactly its 5 s `Retry-After`; dead-letter retry resolves on success | done |
| W4 | Worker heartbeats in Redis (TTL keys), used for "active workers" | Killing a worker drops the count within the TTL. Verified: 4 heartbeats, `docker kill` one, count 3 within 16 s (TTL 15 s + 1 s polling); a clean stop leaves at once | done |
| W5 | Incremental re-crawl (conditional requests, only changed pages versioned) and removal marking (404/410, or not reached by a full crawl) | Editing a `testsite` page gives exactly one new version; deleting one marks it removed. Verified in compose: incremental re-crawl after editing `pricing` = 633 × 304 (no body) + 1 × 200, `pricing` at version 2; 404/410 and unreached pages marked removed with versions kept (not after custom seeds or a spent budget) | done |
| W6 | Failure demo scripts in `scripts/demo/`: kill mid-task, timeout, 429, always-fail | Each script prints the expected outcome. All four PASS against the compose stack (`run_all.py`); the kill demo found that a killed container sometimes never closes its RabbitMQ connection, fixed with `broker_heartbeat=5` (takeover 5-12 s in 3 runs) | done |
| W7 | Benchmark: the same 500+ `testsite` pages with 1, 2 and 4 workers; results CSV and chart | Chart saved in `docs/phase1/` with a short analysis. `BENCHMARK.md`: 396 / 674 / 1,083 / 1,273 pages per minute with 1 / 2 / 4 / 8 workers (median of 3); the 4-CPU machine is the limit, and on real sites the per-domain rate limit is | done |
| W8 | Bonus: `autoscaler` container scales `worker` from queue depth | Queue grows → workers increase; queue drains → workers decrease | todo |

## M6 — API (Task 5, 10 pts)

| ID | Task | Done when | Status |
| --- | --- | --- | --- |
| A1 | FastAPI app: login returns a JWT, `admin` / `viewer` role dependency, admin and viewer seeded from env, Swagger with "Authorize" | Swagger at `/docs`; wrong role gets 403, no token gets 401 | todo |
| A2 | `POST /crawls` (full or incremental), `GET /crawls`, `GET /crawls/{id}` | Admin starts a crawl; viewer can read but not start | todo |
| A3 | `GET /pages/{id}/raw`, `GET /documents` (site, status and date filters, pagination), `GET /documents/{id}` | Paginated results with totals | todo |
| A4 | `GET /documents/{id}/versions` | Lists every version with its time and hash | todo |
| A5 | `GET /search?q=&site=` using the GIN index, with `ts_headline` snippets | `EXPLAIN` shows the index; snippets have highlights | todo |
| A6 | `GET /dlq`, `POST /dlq/{id}/retry` | Retried job runs again and leaves the queue on success | todo |
| A7 | Public `GET /health`: database, Redis and RabbitMQ status, queue length, active workers | Works without a token | todo |
| A8 | Role-matrix tests: every endpoint × no token / viewer / admin | All pass in CI | todo |

## M7 — Web UI (Task 6, 5 pts)

| ID | Task | Done when | Status |
| --- | --- | --- | --- |
| U1 | React + Vite + TypeScript scaffold, nginx image proxying `/api`, lint and build in CI | UI container serves the app in compose | todo |
| U2 | Login; navigation and actions differ for admin and viewer | Viewer never sees admin actions | todo |
| U3 | Dashboard polling every few seconds: crawl progress, queue length, active workers, errors. Admin: start crawl, retry dead-letter jobs | Numbers move during a crawl | todo |
| U4 | Search page with snippets and a site filter | Highlighted results, filter works | todo |
| U5 | Document page: cleaned blocks (tables included), source URL, version history | Multi-version document shows every version | todo |

## M8 — Real crawls and deliverables (15 pts + release)

| ID | Task | Done when | Status |
| --- | --- | --- | --- |
| H1 | Crawl all three real sites within their limits | 100+ valid documents per site | todo |
| H2 | Export script and committed sample: 100+ documents per site, one with several versions | `exports/` sample checked in | todo |
| H3 | README: one-command start, scaling, demo scripts, API and UI tour | A clean clone follows it without help | todo |
| H4 | Report PDF: theme and sites, architecture diagram, URL-life flowchart, crash sequence diagram, data model, choices, fault tolerance and benchmark, compliance | Every one of the 7 sections present | todo |
| H5 | Video script and recording (≤10 min, narrated) | Covers every item in the spec's video list | todo |
| H6 | Final rubric check, pull request `phase-1` → `main`, tag `v1.0-phase1` | Merged with green CI | todo |
