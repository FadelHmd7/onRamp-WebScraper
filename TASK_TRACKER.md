# Phase 1 task tracker

Work one task at a time. A task is finished only after its commit. The next task stays blocked until you confirm.

Spec: `PHASE1_TASKS.md`.

## Now

| | |
| --- | --- |
| Finished | **E1** — Python project skeleton |
| Waiting for confirmation | **E2** — Compose stack with a scalable worker service |
| Do not start | Everything after E2 |

## Environment

| ID | Task | Status |
| --- | --- | --- |
| E1 | Choose Python, add the package layout, `.gitignore`, `.env.example`, and the language note in the README | done |
| E2 | `docker-compose.yml` with one container per component, env-based settings, and `docker compose up --scale worker=4` | todo |
| E3 | CI on every push: linter, unit tests, container builds | todo |
| E4 | Unit tests for URL normalization, using saved fixtures | todo |
| E5 | Unit tests for `robots.txt` checks, using saved fixtures | todo |
| E6 | Unit tests for change detection, using saved fixtures | todo |
| E7 | Unit tests for HTML cleaning, using saved fixtures | todo |

## Sites

| ID | Task | Status |
| --- | --- | --- |
| P1 | Pick one theme and three sites (static HTML, JavaScript-rendered, 500+ paginated or infinite-scroll). At most one sandbox site | todo |
| P2 | Record `robots.txt`, terms, and the per-site rate limit the crawler will follow | todo |

## Scraping

| ID | Task | Status |
| --- | --- | --- |
| S1 | Per-site fetch mode: plain HTTP for static pages, headless browser only where configured | todo |
| S2 | HTML parsing with a parser library, not regular expressions | todo |
| S3 | URL normalization and a seen-set shared by all workers | todo |
| S4 | Crawl limits by domain, depth, and page count | todo |
| S5 | Check every URL against `robots.txt` before fetching | todo |
| S6 | Shared per-domain rate limit (8 workers at 1 req/s still means 1 req/s total) | todo |
| S7 | Retries with exponential backoff, jitter, and `Retry-After` on 429, 503, and connection errors | todo |
| S8 | Content hash, `ETag` / `Last-Modified` when present, and re-crawl only when content changed | todo |

## Workers and fault tolerance

| ID | Task | Status |
| --- | --- | --- |
| W1 | Job queue with stateless worker containers sharing only the queue, database, and cache | todo |
| W2 | Idempotent writes: running a job twice matches running it once | todo |
| W3 | Dead-letter queue storing URL, last error, and attempt count, with admin retry | todo |
| W4 | Worker killed mid-task is picked up by another worker, with nothing lost or duplicated | todo |
| W5 | Timeout, HTTP 429, and always-failing URL behave as specified | todo |
| W6 | Benchmark the same 500+ pages with 1, 2, and 4 workers and record time and pages per minute | todo |
| W7 | Bonus: autoscale workers from queue length | todo |

## Data

| ID | Task | Status |
| --- | --- | --- |
| D1 | Store raw HTML, status code, and fetch time | todo |
| D2 | Clean pages into structured blocks and drop scripts, styles, navigation, footers, and ads | todo |
| D3 | Validate cleaned documents against a schema and log rejections | todo |
| D4 | Extract site-specific fields where the page has clear structure | todo |
| D5 | Store one extra content type: tables as rows and columns, or linked PDFs with extracted text | todo |
| D6 | Keep versions on change, and mark disappeared pages removed | todo |
| D7 | Store outbound links: source page, target URL, link text | todo |

## API

| ID | Task | Status |
| --- | --- | --- |
| A1 | Authenticated API with admin and viewer roles, documented with OpenAPI | todo |
| A2 | `POST /crawls` and `GET /crawls/:id` | todo |
| A3 | Raw page fetch details and filterable, paginated cleaned documents | todo |
| A4 | Document version history | todo |
| A5 | Full-text search with highlighted snippets | todo |
| A6 | Dead-letter list and retry | todo |
| A7 | Public `GET /health` with queue length and active workers | todo |

## Web UI

| ID | Task | Status |
| --- | --- | --- |
| U1 | Login with separate admin and viewer views | todo |
| U2 | Live dashboard: progress, queue, workers, errors; admins start crawls and retry failed jobs | todo |
| U3 | Keyword search with snippets and a site filter | todo |
| U4 | Document page with cleaned content, source URL, and version history | todo |

## Report and handoff

| ID | Task | Status |
| --- | --- | --- |
| H1 | README: setup from a clean checkout and how to scale workers | todo |
| H2 | Sample export: at least 100 cleaned documents per site, one with several versions | todo |
| H3 | Report PDF covering every required section | todo |
| H4 | Narrated video, at most 10 minutes, covering the required demo | todo |
