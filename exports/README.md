# Sample export

Cleaned documents from the crawls, written by [scripts/export.py](../scripts/export.py) through the API (`uv run python scripts/export.py`). [manifest.json](manifest.json) records when, from where, and how many.

| File | Documents | From | Site fields in every record |
| --- | ---: | --- | --- |
| [books-toscrape.jsonl](books-toscrape.jsonl) | 120 of 300 | a full crawl of books.toscrape.com | price, currency, stock, rating, category, product table |
| [standard-ebooks.jsonl](standard-ebooks.jsonl) | 120 of 700 | a full crawl of standardebooks.org | author, abstract, word count, collections, language, date |
| [inventaire.jsonl](inventaire.jsonl) | 59 of 59 | two crawls of inventaire.io, rendered in Chromium | name, entity type, authors, claims, birth and death years |
| [testsite.jsonl](testsite.jsonl) | 120 of 634 | the local test site | price, SKU; includes the document with several versions |

**Inventaire has 59 documents, not 100.** inventaire.io went down twice during this phase, refusing connections from two different networks, so these are outages on their side, not a block of this crawler. Each time, the crawl was cancelled. The file holds every Inventaire page that was stored. `scripts/export.py` warns about it (`fewer than 100 documents for: inventaire`) and exits with status 1.

**Documents with several versions.**
- `http://testsite:8000/editable/pricing`: the test-site page was edited twice between crawls, so it has versions 1, 2 and 3. The test site exists to show versioning on demand.
- Seven Inventaire author pages were rendered again by the second crawl and got a version 2. Jane Austen, Mary Shelley, Mark Twain and Leo Tolstoy gained their works lists, which had not finished loading the first time. Samuel Johnson and Henry Fielding list the same works in a different order: the site does not return them in a fixed order, and list order is part of the content. Charles Dickens went the other way: his version 2 was captured before the works list appeared, and version 1 keeps it (see section 1.3 of the [report](../docs/phase1/REPORT.md)).

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
