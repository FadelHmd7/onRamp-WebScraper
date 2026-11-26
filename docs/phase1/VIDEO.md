# Video script (Phase 1, at most 10 minutes)

A shot-by-shot plan for the narrated demo. Every item the spec asks to show is covered, in the order below; the times add up to about 9½ minutes, leaving slack. Say the narration in your own words; it is written out so nothing important is forgotten.

| Spec item | Where |
| --- | --- |
| the system starting with one command | 1 |
| crawls of all three sites | 2, 3 |
| scaling workers up | 4 |
| each failure scenario | 5 |
| an incremental re-crawl with a page's version history | 6 |
| the UI | 2, 3, 6, 7 (throughout) |

## Before recording

1. **Clean start** — so step 1 is real: `docker compose down` (keep the volumes: the real-site data from earlier crawls stays, and step 3 relies on it).
2. **Terminal** in the repository, font large enough to read; a browser with two tabs ready: http://localhost:9070 and http://localhost:9060/docs.
3. **Real sites** — the full crawls take 12–30 minutes each, too long for the video. Show them *running* for a few seconds, then cut to the finished crawls from the earlier run (they stay in the dashboard's crawl list). Say so in the narration.
4. **Pre-edit nothing**: step 6 edits the test site live.
5. Do a dry run of steps 5 and 6 once: `uv run python scripts/demo/run_all.py`.

## 1. One command (0:00–1:00)

**Show:** the terminal.

```
docker compose up -d --build
docker compose ps
```

**Say:** "Everything starts with one command: PostgreSQL, Redis, RabbitMQ, a migration step that creates the schema and the users, the API, the workers, the web UI and a local test site. Each is its own container; the workers share only the queue, the database and the cache." Point at the `docker compose ps` list.

## 2. A crawl, and the UI (1:00–2:30)

**Show:** http://localhost:9070, log in as **admin**. Dashboard → Start a crawl → **Local test site**, Full → Start.

**Say:** "This is the dashboard. I start a crawl of our local test site: 630 pages." While it runs, point at:
- the **queue length** and **workers** with the URL each is fetching,
- the **progress bar** and counts of the crawl,
- the health badges (database, Redis, RabbitMQ).

"It refreshes every three seconds from the API. A viewer sees the same dashboard, but not the start form or the dead-letter queue." (Optionally sign out and in as `viewer` to show it.)

## 3. The three real sites (2:30–3:45)

**Show:** Dashboard → start a crawl of **Books to Scrape** for a few seconds of progress; then the crawl list with the finished earlier crawls.

**Say:** "The three real sites: books.toscrape.com — static HTML; Standard Ebooks — 129 paginated catalog pages; Inventaire — a JavaScript application, rendered in headless Chromium. Each has its own rate limit shared by all workers — two requests a second, one, and one every ten seconds as its robots.txt asks — so the full crawls take minutes; here are the ones I ran earlier: 300 and 700 pages, no failures." For Inventaire: "59 pages: the site went down twice during the crawl, and each time the crawl was stopped rather than left retrying." Optionally open *Jane Austen* on Inventaire → Versions: the second rendering added her works list.

**Show:** Search → `sherlock holmes` → open *The Memoirs of Sherlock Holmes* (Standard Ebooks: author, collections, the description); then a books.toscrape book with its **product table** (Search, site Books to Scrape, no words → any book).

## 4. Scaling workers (3:45–4:45)

**Show:** the terminal, with the dashboard visible beside it.

```
docker compose up -d --scale worker=6
```

**Say:** "Workers are stateless, so I can add them during a crawl." Point at the dashboard: active workers go from 4 to 6. "They share one queue; Redis hands each URL to exactly one worker and keeps each site's rate limit across all of them. Measured on the test site: 396 pages a minute with one worker, 1,083 with four, 1,273 with eight — the speedup flattens because this machine has four CPUs." Optionally show `docs/phase1/BENCHMARK.md`'s chart.

## 5. Failure scenarios (4:45–7:30)

**Show:** the terminal running each demo (about 40 s each), the dashboard beside it.

```
uv run python scripts/demo/kill_worker.py
```

"I kill a worker in the middle of a page. RabbitMQ gives its unacknowledged job to another worker — here within seconds — the page is stored once, and the dead worker's heartbeat expires." Read out the PASS lines.

```
uv run python scripts/demo/timeout.py
uv run python scripts/demo/rate_limited.py
```

"A page that takes 40 seconds: the fetcher gives up at 30 and retries with backoff; the second attempt succeeds. A 429 with Retry-After 10: the retry waits exactly ten seconds, and the whole site is paused for every worker meanwhile."

```
uv run python scripts/demo/always_fail.py
```

"A page that always fails: five attempts with growing, random delays, then the dead-letter queue." **Show:** the Dashboard's **Dead-letter queue** panel with the entry, click **Retry**. "An admin can send it again."

## 6. Incremental re-crawl and version history (7:30–9:00)

**Show:** the terminal, then the UI.

```
curl -X PUT localhost:9050/_admin/editable/pricing -H "Content-Type: application/json" \
     -d '{"title": "Pricing", "paragraphs": ["The basic plan now costs $19 per month."]}'
docker compose exec worker python -m onramp_scraper.worker.cli start testsite --mode incremental --seed http://testsite:8000/editable --max-depth 1
```

**Say:** "I change one page on the test site and run an incremental crawl. It sends each page's stored ETag back: unchanged pages answer 304 Not Modified, so nothing is downloaded; only the edited page is fetched and gets a new version."

**Show:** Search → `pricing` → open the Pricing document → the **Versions** list → click an older version: "every version is kept; here is what changed — the old price removed, the new one added."

## 7. API and close (9:00–9:30)

**Show:** http://localhost:9060/docs → Authorize as viewer → `GET /search` → Execute; then `POST /crawls` as viewer → 403.

**Say:** "Everything the UI does goes through this API: documented with OpenAPI, every route behind a login except health, and roles enforced — a viewer cannot start a crawl. The code, report and this video are linked from the README."
