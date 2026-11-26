# Test fixtures

Real pages saved once from each site, so unit tests never touch a live site. `manifest.json` records each file's source URL, fetch time, HTTP status and cache headers (`ETag`, `Last-Modified`).

| Folder | Site | Role |
| --- | --- | --- |
| `books-toscrape/` | books.toscrape.com | A, static. Sends `ETag` and `Last-Modified`. No `robots.txt` (404). |
| `inventaire/` | inventaire.io | B, JavaScript. `*.raw.html` is the empty shell plain HTTP gets; the other pages are rendered in Chromium. |
| `standard-ebooks/` | standardebooks.org | C, pagination. No cache headers, so change detection uses content hashes. |

Regenerate with `scripts/fetch_fixtures.py` (see its docstring). It checks `robots.txt`, spaces requests by each site's rate limit and identifies itself in the User-Agent. Only regenerate when a test needs a new page: tests are written against these exact files.

The rendered Inventaire pages were captured with a local Chromium, without adding Playwright to the project:

```
uv run --with playwright==1.65.1 python -m playwright install chromium
uv run --with playwright==1.65.1 python scripts/fetch_fixtures.py --render
```
