# onRamp-WebScraper

Phase 1 of the onRamp bootcamp distributed web scraper. Requirements are in `PHASE1_TASKS.md`. Progress is in `TASK_TRACKER.md`.

## Language

Python 3.12.

Python fits this phase better than Node.js. The graded work is fetching, parsing, cleaning, and validating HTML with ordinary libraries, and rendering in a browser only for the JavaScript site. Node.js with a queue such as BullMQ is a reasonable alternative for the worker side, but the parse-and-validate path is more direct in Python, and the workers are still separate containers either way.

## Layout

- `src/onramp_scraper/` — application package
- `.env.example` — settings with no secrets
- `PHASE1_TASKS.md` — assignment spec
- `TASK_TRACKER.md` — categorized task status

## Setup

Copy `.env.example` to `.env` and set a real contact address in `USER_AGENT` before any crawl.

The stack does not start yet. Container startup is task E2.
