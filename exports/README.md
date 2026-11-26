# Sample export

Cleaned documents from the crawls, written by [scripts/export.py](../scripts/export.py) through the API (`uv run python scripts/export.py`). [manifest.json](manifest.json) records when, from where, and how many.

| File | Documents | From | Site fields in every record |
| --- | ---: | --- | --- |
| [books-toscrape.jsonl](books-toscrape.jsonl) | 120 of 300 | a full crawl of books.toscrape.com | price, currency, stock, rating, category, product table |
| [standard-ebooks.jsonl](standard-ebooks.jsonl) | 120 of 700 | a full crawl of standardebooks.org | author, abstract, word count, collections, language, date |
| [inventaire.jsonl](inventaire.jsonl) | 23 | a crawl of inventaire.io, rendered in Chromium | name, entity type, authors, claims, birth and death years |
| [testsite.jsonl](testsite.jsonl) | 120 of 631 | the local test site | price, SKU; includes the document with several versions |

**Inventaire is incomplete.** Its crawl had stored 23 pages when inventaire.io stopped accepting connections (refused from two different networks, so an outage on their side, not a block of this crawler). The crawl was cancelled so no retries kept knocking. This file is refreshed with 100+ documents once the site is back.

**The document with several versions** is `http://testsite:8000/editable/pricing`: its page was edited twice between crawls, so it has versions 1, 2 and 3. The real sites did not change during the crawls; the test site exists to show versioning on demand.

## Format

One JSON object per line, keys sorted:

| Key | |
| --- | --- |
| `id` | the document id in the API (`/documents/{id}`), the same for every version |
| `url`, `site`, `status` | source URL (normalized), site key, `active` or `removed` |
| `title`, `blocks` | the cleaned content in page order: `heading`, `paragraph`, `list`, `table` (header, rows, row headers), `code`, `quote` |
| `fields` | site-specific values from CSS selectors in [config/sites.yaml](../config/sites.yaml) |
| `links` | the links on the page, in order: target URL, link text, nofollow |
| `version`, `content_hash`, `crawled_at` | the current version, the SHA-256 of title + blocks + fields, and when it was stored |
| `first_seen_at`, `last_seen_at` | when the crawler first and last saw the page |
| `versions` | only when there are older versions: each one in full (`version`, `crawled_at`, `content_hash`, `title`, `blocks`, `fields`, `crawl_id`), newest first |

Which documents: detail pages first (those with site fields), then the rest, by URL; every document with several versions is always included.
