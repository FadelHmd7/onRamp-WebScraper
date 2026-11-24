# Compliance

How the crawler follows each site's `robots.txt`, terms of service and rate limits. Checked on 2026-11-24. This is report section 7.

## Rules for every site

- **robots.txt is checked before every request** (task S2), and the result is cached per host. Our `include` / `exclude` patterns in `config/sites.yaml` only narrow the crawl further; they never override robots.txt.
- **Descriptive User-Agent with a contact address**, set by `USER_AGENT` in `.env`. Example: `onRamp-WebScraper/0.1 (+https://github.com/FadelHmd7/onRamp-WebScraper; contact: <your address>)`. Crawls of real sites must not run with the placeholder address.
- **One shared rate limit per host**, enforced across all workers through Redis (task S3). Adding workers never raises the request rate to a site.
- **Backoff on 429 and 503**, honouring `Retry-After` (task W3).
- **No logins, paywalls or CAPTCHAs, and no bot-detection bypass.** A 401 or 403 response, or a CAPTCHA page, is treated as a permanent failure: the URL is not retried.
- **Conditional requests** (`If-None-Match`, `If-Modified-Since`) on re-crawls where a site supports them, so unchanged pages cost the site a 304 instead of a full page.

## Per site

### A — books.toscrape.com

| | |
| --- | --- |
| Purpose | Scraping sandbox: "We love being scraped!" on the home page. Our one permitted sandbox. |
| robots.txt | Returns 404, so nothing is disallowed. |
| Terms | No terms page. The site warns that prices and ratings are random, so we never present them as real. |
| Our rate | 2 requests per second, shared by all workers. |
| Crawl scope | Home, 50 listing pages, book pages. At most 300 pages. |
| Change detection | The site sends `ETag` and `Last-Modified`, so re-crawls use conditional requests. |

### B — inventaire.io

| | |
| --- | --- |
| Purpose | Open, community-edited book database run by a French non-profit association. |
| robots.txt | `Disallow: /entity/new`, `/entity/*/*$`, `/items`, `/search`, `/tasks`, `/transactions`. `Crawl-delay: 10`. |
| What we crawl | Only entity pages that match `^/entity/(wd\|inv\|isbn):<id>$`. All are allowed by the rules above. |
| Rendering | Headless Chromium. The app loads its data from `inventaire.io/api/...` (same host, not disallowed). We checked this because a rejected candidate (UCF) loaded data from a host whose robots.txt forbade it. |
| Terms | The legal notice (wiki.inventaire.io/wiki/Legal_notice) names the association and its hosts and sets no usage restrictions. Entity data is published under CC0 (wiki.inventaire.io/wiki/Entities_data). |
| Our rate | 1 request every 10 seconds (`Crawl-delay: 10`), shared by all workers. Each page load counts as one request. |
| Crawl scope | Five author seeds, depth 2 (author, work, edition). At most 150 pages, about 25 minutes. |

### C — standardebooks.org

| | |
| --- | --- |
| Purpose | Volunteer project publishing public-domain ebooks. |
| robots.txt | For `User-agent: *` (which covers us): `Disallow: /honeypot`. A separate group for named SEO and AI bots (AhrefsBot, claude-user and others) also disallows `/ebooks/*/downloads/*` and `/ebooks/*/text*`; we are not in that group, which the robots tests confirm. No crawl delay. |
| What we crawl | Catalog listing pages (`/ebooks?page=N`) and book pages (`/ebooks/<author>/<title>`). Downloads and full texts are excluded in our config by choice (large files, not pages worth indexing), and the honeypot because robots.txt disallows it. |
| Terms | Their own work is dedicated to the public domain (CC0 1.0), and the texts are U.S. public domain ("About" page). No terms restrict crawling. |
| Our rate | 1 request per second, shared by all workers. Conservative because it is a small volunteer project. |
| Crawl scope | 129 listing pages plus book pages. At most 700 pages, about 12 minutes. |
| Change detection | No `ETag` or `Last-Modified`, so re-crawls compare content hashes. |

### Local test site

`testsite` runs inside our Compose network and is ours, so its rate limit is high (100 requests per second) to let the benchmark measure workers rather than politeness. Its own `robots.txt` disallows `/private/`, which shows the robots check working on a live server.
