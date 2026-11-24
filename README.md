# onRamp-WebScraper

A distributed, fault-tolerant web crawler for the onRamp bootcamp. Phase 1 crawls three websites on one theme, then cleans, versions and serves the pages through an authenticated API and a web UI.

## Documents

- [docs/phase1/SPEC.md](docs/phase1/SPEC.md) — Phase 1 requirements, restated from the assignment
- [docs/phase1/TRACKER.md](docs/phase1/TRACKER.md) — ordered tasks and status
- [docs/DECISIONS.md](docs/DECISIONS.md) — chosen stack, each with the alternative we rejected

## Stack

Python 3.12 · Celery on RabbitMQ · Redis · PostgreSQL 16 · FastAPI · Playwright · BeautifulSoup · React + Vite · Docker Compose · GitHub Actions.

## Setup

Copy `.env.example` to `.env` and set a real contact address in `USER_AGENT` before any crawl.

The stack does not start yet. Container startup is task E1.
