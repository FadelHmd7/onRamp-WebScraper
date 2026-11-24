# Phase 1 — Distributed Web Scraper

Source: *Distributed Web Scraper — Phase 1: Crawling, Queuing & Distributed Workers* (Nov 22, 2026, @Most). This file restates the PDF; nothing here is added except the last two sections.

Phase 1 is a distributed, fault-tolerant web crawler that extracts, cleans, versions, and serves data from three websites through an authenticated API and a basic web UI. Phase 2 builds on this codebase and this data, so keep each page’s structure (headings, tables, links, versions) instead of flattening a page into one text blob.

Language, frameworks, and databases are your choice. Anything named below is an example. What is graded is that the system is genuinely distributed, survives failure, and that the main decisions are justified in the report.

## Ground rules

- Work individually.
- No LLM or AI-based extraction in Phase 1. Parse and clean with conventional code (selectors, parsers, rules).
- No secrets in the repository. Provide a `.env.example`.
- The whole system starts from a clean checkout with one command (for example `docker compose up`).
- Honor each site’s `robots.txt` and terms of service. If the terms forbid crawling, pick another site.
- Use a descriptive User-Agent that includes a contact address.
- Do not crawl behind logins, paywalls, or CAPTCHAs, and do not try to bypass bot detection.

## Sites

Use three websites that share a common theme (course catalogs, recipe sites, open-source documentation, book catalogs, and so on). Phase 2 needs answers that combine information from more than one site, which only works if the sites cover the same kinds of things.

| Site | Requirement |
| --- | --- |
| A | Mostly static HTML |
| B | Content rendered by JavaScript |
| C | Pagination or infinite scroll, with 500+ pages |

Scraping sandboxes such as `books.toscrape.com` may be used for **at most one** site.

Record for the report, per site: theme fit, `robots.txt` rules you follow, terms of service, and the rate limit you apply.

## Definition of done

- `docker compose up` starts every component from a clean checkout, with settings from environment variables.
- `docker compose up --scale worker=4` runs four separate worker containers.
- CI runs the linter, unit tests, and container builds on every push.
- All three sites are crawled under the rules above.
- Sample export contains at least 100 cleaned documents per site, including one document with several versions.
- README explains setup and how to scale workers.
- Report (PDF) and a narrated video of at most 10 minutes are linked from the repository.
- Every item in the grading rubric below can be demonstrated.

A system whose workers are processes on one machine, not separate containers, can score at most **10 of the 25** points for distributed workers.

---

## Task 1 — Environment setup (10 points)

1. **Language.** Choose one language and justify it briefly against one alternative.
2. **Git.** Use GitHub or GitLab with a meaningful commit history. One giant commit does not count.
3. **CI.** On every push, run the linter, the unit tests, and the container builds.
4. **Containers.** Each component runs in its own container. `docker-compose.yml` starts everything and supports `docker compose up --scale worker=4`. Settings come from environment variables. Commit `.env.example`; do not commit secrets.
5. **Tests.** Unit tests for URL normalization, `robots.txt` checks, change detection, and HTML cleaning. Use saved HTML files, not live sites.

**Done when:** a clean clone plus one command brings the stack up, CI is green on push, and the four unit-test areas pass against fixtures.

---

## Task 2 — Scraping module (20 points)

### 2.1 Fetching

- Support plain HTTP fetching for static pages and a headless browser (for example Playwright) for JavaScript pages.
- Choose the fetch mode **per site in configuration**. Do not render every page in a browser.
- Parse with an HTML parsing library (for example Cheerio or BeautifulSoup), not regular expressions.
- Normalize URLs.
- Keep a “seen” set shared by all workers.
- Limit each crawl by domain, depth, and page count.

### 2.2 Politeness

- Check every URL against the site’s `robots.txt` before fetching.
- Rate-limit **per domain**, shared by all workers. A domain is the site’s host name (for example `books.toscrape.com`).
- With 8 workers and a limit of 1 request per second, the site must still receive at most 1 request per second **in total**, not 8.
- A delay inside each worker does not meet this. Use a shared store (for example a token bucket in Redis).
- On HTTP 429, HTTP 503, or connection errors, retry with exponential backoff and jitter, and honor `Retry-After`.

### 2.3 Deduplication and re-crawls

- Store a content hash for each page and skip content you already have.
- On a re-crawl, only pages whose content changed produce a new version (Task 4).
- Use `ETag` and `Last-Modified` when the site sends them; otherwise use the content hash.
- Real sites may not change during the course. To show versioning, you may serve a copy of a few pages from a local test server and edit them between crawls.

**Done when:** site A is fetched over HTTP, site B is rendered in a browser only where configured, site C yields 500+ pages, robots and the shared rate limit are enforced across workers, and a second crawl versions only changed pages.

---

## Task 3 — Distributed workers and fault tolerance (25 points)

### 3.1 Queue and workers

- Distribute crawl tasks through a job queue (for example BullMQ, RabbitMQ, or Celery). Justify it against one alternative in the report.
- Workers are separate, stateless containers. They share only the queue, database, and cache.
- Threads or processes inside one container do not count as distributed workers.
- Jobs may run more than once. Database writes must be **idempotent**: running a job twice gives the same result as running it once.

### 3.2 Failures

Demonstrate each scenario in the video. A small local test server that returns delays and errors on demand is a good way to reproduce these without stressing real sites.

| Scenario | Expected behavior |
| --- | --- |
| A worker is killed mid-task (`docker kill`) | Another worker picks the job up. Nothing is lost or duplicated. |
| Network timeout | Retry with backoff, then success or the dead-letter queue. |
| Throttling (HTTP 429) | Backoff. `Retry-After` is honored. |
| A URL that always fails | After N attempts it moves to the dead-letter queue with its error. |

The dead-letter queue keeps each failed job’s URL, last error, and attempt count. Admins can retry jobs from it.

### 3.3 Scaling benchmark

- Crawl the same 500+ pages with 1, 2, and 4 workers.
- Use a local test server or a site whose limits allow it.
- Report time and pages per minute for each run in a chart.
- Explain where the speedup stops growing and why.

**Bonus (5 points):** scale workers automatically based on queue length.

**Done when:** four worker containers can be scaled independently, all four failure scenarios recover as specified, the dead-letter queue supports admin retry, and the 1/2/4-worker benchmark is recorded.

---

## Task 4 — Data processing and storage (15 points)

### Raw data

Store each fetched page’s HTML, status code, and fetch time.

### Cleaning

Remove scripts, styles, navigation, footers, and ads. Keep the page’s structure as separate blocks: title, headings, paragraphs, lists, and tables.

### Schema

Validate every cleaned document against a schema (for example Zod, Pydantic, or database constraints). Reject invalid documents with a logged reason.

A document has at least:

- ID
- URL
- site
- title
- content blocks
- content hash
- crawl time
- version

Add site-specific fields extracted with selectors where the site has clear structure (for example price or course code).

### Content types

Besides text, handle **at least one** of:

- tables, stored as rows and columns, or
- linked PDF documents, with text extracted and linked to the page.

### Versioning

- A re-crawl that changes a page creates a new version. Old versions are kept.
- Pages that disappear are marked removed, not deleted.

### Links

For each page, store the links it contains: source page, target URL, and link text. This shows which pages link to which, and Phase 2 reuses it.

**Done when:** cleaned documents keep structure, invalid documents are rejected with a reason, one extra content type is stored, versions and removals behave as specified, and outbound links are queryable.

---

## Task 5 — API (10 points)

Build an API with a framework of your choice, documented with OpenAPI/Swagger.

Every endpoint except the health check requires authentication. Two roles:

- **admin** — manages crawls and failed jobs, and can call every viewer endpoint
- **viewer** — read-only

Paths below are suggestions.

| Endpoint | Role | Purpose |
| --- | --- | --- |
| `POST /crawls` | admin | Start a crawl (full or incremental) |
| `GET /crawls/:id` | viewer | See a crawl’s progress and errors |
| `GET /pages/:id/raw` | viewer | Raw HTML and fetch details |
| `GET /documents`, `GET /documents/:id` | viewer | Cleaned documents, filterable and paginated |
| `GET /documents/:id/versions` | viewer | Version history |
| `GET /search?q=` | viewer | Keyword search using a real full-text index (not `LIKE '%term%'`), with highlighted snippets |
| `GET /dlq`, `POST /dlq/:id/retry` | admin | Failed jobs and retry |
| `GET /health` | public | Health, queue length, active workers |

**Done when:** OpenAPI is published, role checks match the table, search uses a full-text index with highlighted snippets, and health is the only unauthenticated route.

---

## Task 6 — Web UI (5 points)

A basic UI in a framework of your choice. Function matters, not looks.

1. **Login**, with admin and viewer views.
2. **Dashboard.** Everyone sees crawl progress, queue length, active workers, and errors updating live (polling is fine). Admins can also start crawls and retry failed jobs.
3. **Search.** Keyword search with snippets and a site filter.
4. **Document page.** Cleaned content, source URL, and version history.

**Done when:** a viewer can log in, search, and open a document with its versions, and an admin can start a crawl and retry a dead-letter job from the dashboard.

---

## Report and video (15 points)

### Report

1. Theme and the three sites.
2. An architecture diagram (containers, queue, database, cache) and a flowchart of a URL’s life, including retries.
3. One sequence diagram: a worker crashing mid-job and the job being recovered.
4. Data model, including versioning.
5. Each main technology choice with one alternative you rejected and why.
6. Fault tolerance and the scaling benchmark results.
7. Compliance: `robots.txt`, terms of service, and rate limits for each site.

### Video (at most 10 minutes, narrated)

Show:

- the system starting with one command
- crawls of all three sites
- scaling workers up
- each failure scenario from Task 3.2
- an incremental re-crawl with a page’s version history
- the UI

---

## Deliverables

- Repository link, with CI passing
- `README.md` with setup and how to scale workers
- `.env.example` and no secrets in the repo
- Sample data export: at least 100 cleaned documents per site, including one with several versions
- Report (PDF) and video link

## Grading

| Area | Points | Full marks |
| --- | --- | --- |
| Distributed workers and fault tolerance | 25 | Separate worker containers, idempotent jobs, all failure scenarios recover, working dead-letter queue |
| Scraping module | 20 | Static and JS pages, shared per-domain rate limit, robots.txt, backoff, re-crawls of changed pages only |
| Data processing and storage | 15 | Structure kept, schema validation, one extra content type, versioning, links |
| Report and video | 15 | All sections, justified choices, clear demo |
| Environment and CI | 10 | Meaningful history, CI, one-command start, tests |
| API | 10 | Auth with roles, required endpoints, full-text search |
| Web UI | 5 | All four views working |
| Bonus | 5 | Autoscaling on queue length |
| **Total** | **100 + 5** | |

Workers that are only processes on one machine cap the distributed-workers area at 10/25.

## Requirements the PDF implies but does not list

These follow from the rules above. The tracker covers each one.

- **Local test server.** The failure scenarios, the versioning demo and the 1/2/4-worker benchmark all need pages we control: slow responses, 429 with `Retry-After`, URLs that always fail, pages we can edit between crawls, and 500+ paginated pages.
- **Crawl records.** `POST /crawls` (full or incremental) and `GET /crawls/:id` (progress and errors) need a crawl entity with counters.
- **Removal detection.** "Pages that disappear are marked removed" needs a rule: a page that returns 404/410, or that a full crawl no longer reaches, gets `status = removed`.
- **Worker liveness.** `/health` reports active workers, so workers publish a heartbeat.
- **Users.** Two roles need stored users, hashed passwords and a seeded admin and viewer from environment variables.
- **One worker = one fetch at a time.** Threads or processes inside one container do not count, so each worker container runs one task at a time. Scaling then means more containers.
- **Sample export.** A script that writes at least 100 cleaned documents per site, including one with several versions.
- **Diagrams.** Architecture, URL lifecycle with retries, and a crash-recovery sequence diagram for the report.

## Where the plan lives

- `docs/DECISIONS.md` — chosen stack, each with the rejected alternative.
- `docs/phase1/TRACKER.md` — ordered tasks, each with a "done when" check.
