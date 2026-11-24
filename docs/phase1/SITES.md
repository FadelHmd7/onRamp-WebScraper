# Theme and sites

**Theme: books.** All three sites describe books (title, author, publication details), and two of them cover the same classics, so Phase 2 can answer questions that combine sites. For example: "Which Jane Austen novels have a free Standard Ebooks edition, and how many editions does Inventaire list for each?"

Checked on 2026-11-24. `robots.txt` rules, terms and the rate limit we apply are recorded in detail in `COMPLIANCE.md` (task P2).

## Chosen sites

| Site | Role | Why it fits | Key evidence |
| --- | --- | --- | --- |
| **A** [books.toscrape.com](https://books.toscrape.com) | Mostly static HTML | Plain server-rendered pages: 1,000 books with price, rating, stock and a product-information table (our extra content type). It is our **one** permitted sandbox. | Home page: "We love being scraped!" `robots.txt` returns 404, so nothing is disallowed. Prices are random, so we never present them as real. |
| **B** [inventaire.io](https://inventaire.io) | Rendered by JavaScript | A single-page app: the raw HTML of a book page is a 4.9 KB shell with an empty `<body>`. Only a browser sees the title, authors, editions and dates. | `robots.txt` allows `/entity/<id>` pages and asks for `Crawl-delay: 10`. Entity data is published under CC0, and the code is AGPLv3. |
| **C** [standardebooks.org](https://standardebooks.org) | Pagination, 500+ pages | 129 paginated catalog pages of 12 books each (about 1,550 ebook pages), plus author and subject pages. Static HTML, so no browser is needed. | `robots.txt` disallows only `/honeypot`, `/ebooks/*/downloads/*` and `/ebooks/*/text*`, and we skip those. Their own work is CC0, and the texts are U.S. public domain. |

Overlap for Phase 2: Standard Ebooks is classic literature, and Inventaire covers the same works with edition and author data. We seed the Inventaire crawl from authors who also appear on Standard Ebooks (Austen, Dickens, Shelley, Twain and others), so the two sites share works by design.

## Rejected candidates

| Candidate | Role tried | Why rejected |
| --- | --- | --- |
| University course catalogs (MIT CourseLeaf for A) | Whole theme | No compliant JavaScript site or paginated site could be found to go with it. See the next three rows. |
| UNLV and UTK (Acalog) | C | `Crawl-delay: 120` for generic bots: 500 pages would take about 17 hours. |
| UCF (Kuali catalog) | B | Rendering loads `ucf.kuali.co/api/v1/catalog/...`, and that host's `robots.txt` disallows everything except `/catalog`. A browser would fetch disallowed URLs. |
| archive.org | B | Book pages are server-rendered. Only the search page needs JavaScript, which is too thin for 100+ documents. |
| Project Gutenberg | A or C | Its terms ask that the website not be crawled. It offers feeds and mirrors instead. |
| Open Library | A | Usable, but `/search` is disallowed and bulk users are asked to use the data dumps. books.toscrape.com carries no such risk. |
| quotes.toscrape.com/js | B | A second sandbox is not allowed. |

## Rate limits we will apply

| Site | Limit | Reason |
| --- | --- | --- |
| books.toscrape.com | 2 requests per second | Sandbox built for scraping, but there is no reason to be heavy. |
| inventaire.io | 1 request per 10 seconds | Its `Crawl-delay: 10`. |
| standardebooks.org | 1 request per second | Small volunteer project with no stated limit, so we stay conservative. |

All three are enforced by the shared per-domain rate limiter (S3), so adding workers never raises the request rate to a site.
