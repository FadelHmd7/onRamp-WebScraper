"""The crawl frontier: which links a page offers, and which of them we crawl.

``extract_links`` parses a page with BeautifulSoup (never regex) and returns
its links with their text. The same list is stored for the page (task D7).

``Frontier`` decides what to enqueue. A URL is crawled only if it:

1. is in the site's scope: allowed host, include/exclude patterns, depth;
2. has not been seen in this crawl by any worker;
3. fits in the crawl's page budget (``max_pages``).

Steps 2 and 3 run as one Lua script in Redis, for a whole page's links at
once. Redis runs a script atomically, so two workers that find the same link
can never both enqueue it, and the page budget holds exactly however many
workers race for it. The state is per crawl: a re-crawl visits every page again.
"""

from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag
from redis import Redis

from onramp_scraper.crawler.urls import normalize_url
from onramp_scraper.parsing import parse_html
from onramp_scraper.sites import SiteConfig

STATE_TTL = 7 * 24 * 3600  # crawl state expires a week after its last change

_OFFER = """
local seen, count = KEYS[1], KEYS[2]
local max_pages, ttl = tonumber(ARGV[1]), tonumber(ARGV[2])
local accepted = {}
for i = 3, #ARGV do
  local url = ARGV[i]
  if redis.call('SISMEMBER', seen, url) == 0 then
    if tonumber(redis.call('GET', count) or '0') >= max_pages then
      break
    end
    redis.call('SADD', seen, url)
    redis.call('INCR', count)
    table.insert(accepted, url)
  end
end
redis.call('EXPIRE', seen, ttl)
redis.call('EXPIRE', count, ttl)
return accepted
"""


@dataclass(frozen=True)
class Link:
    url: str  # absolute, normalized
    text: str  # whitespace-collapsed anchor text, or the title/aria-label
    nofollow: bool = False


def _anchor_text(anchor: Tag) -> str:
    text = " ".join(anchor.get_text(" ", strip=True).split())
    if not text:
        for attr in ("title", "aria-label"):
            value = anchor.get(attr)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())
        image = anchor.select_one("img[alt]")
        if image is not None:
            return " ".join(str(image.get("alt", "")).split())
    return text


def page_nofollow(soup: BeautifulSoup) -> bool:
    """True if <meta name="robots"> asks crawlers not to follow this page's links."""
    for meta in soup.select("meta[name][content]"):
        name = str(meta.get("name", "")).lower()
        if name in ("robots", "onramp-webscraper"):
            directives = {d.strip().lower() for d in str(meta.get("content", "")).split(",")}
            if "nofollow" in directives or "none" in directives:
                return True
    return False


def extract_links(html: str | BeautifulSoup, page_url: str) -> list[Link]:
    """Every crawlable link on the page, in document order, without duplicates."""
    soup = html if isinstance(html, BeautifulSoup) else parse_html(html)
    base = page_url
    base_tag = soup.select_one("base[href]")
    if base_tag is not None:
        resolved = normalize_url(str(base_tag.get("href")), base=page_url)
        if resolved is not None:
            base = resolved

    page = normalize_url(page_url)
    links: list[Link] = []
    seen: set[tuple[str, str]] = set()
    for anchor in soup.select("a[href]"):
        url = normalize_url(str(anchor.get("href")), base=base)
        if url is None or url == page:
            continue
        text = _anchor_text(anchor)
        if (url, text) in seen:
            continue
        seen.add((url, text))
        # bs4 gives rel as a list of values (it is a multi-valued attribute).
        rel_values = [str(value).lower() for value in anchor.get_attribute_list("rel")]
        links.append(Link(url=url, text=text, nofollow="nofollow" in rel_values))
    return links


def in_scope(site: SiteConfig, url: str, depth: int) -> bool:
    """Allowed host, include/exclude patterns, and depth. robots.txt is checked at fetch time."""
    return depth <= site.max_depth and site.allows_path(url)


class Frontier:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self._offer = redis.register_script(_OFFER)

    @staticmethod
    def _keys(crawl_id: str) -> list[str]:
        return [f"crawl:{crawl_id}:seen", f"crawl:{crawl_id}:accepted"]

    def offer(self, crawl_id: str, site: SiteConfig, urls: list[str]) -> list[str]:
        """Atomically accept the URLs not yet seen in this crawl, up to the page budget."""
        if not urls:
            return []
        accepted = self._offer(keys=self._keys(crawl_id), args=[site.max_pages, STATE_TTL, *urls])
        return [u.decode() if isinstance(u, bytes) else str(u) for u in accepted]

    def seed(self, crawl_id: str, site: SiteConfig) -> list[str]:
        """Start a crawl: the site's seeds at depth 0."""
        seeds = [u for s in site.seeds if (u := normalize_url(str(s))) is not None]
        return self.offer(crawl_id, site, [u for u in seeds if in_scope(site, u, 0)])

    @staticmethod
    def candidates(
        site: SiteConfig, links: list[Link], depth: int, follow: bool = True
    ) -> list[str]:
        """The links on a page at ``depth`` that qualify for depth + 1, in page order.

        ``follow`` is False when the page's meta robots says nofollow.
        """
        if not follow or depth + 1 > site.max_depth:
            return []
        found: list[str] = []
        for link in links:
            if not link.nofollow and link.url not in found and in_scope(site, link.url, depth + 1):
                found.append(link.url)
        return found

    def discover(
        self,
        crawl_id: str,
        site: SiteConfig,
        links: list[Link],
        depth: int,
        follow: bool = True,
    ) -> list[str]:
        """The links found on a page at ``depth`` that should be crawled next (at depth + 1)."""
        return self.offer(crawl_id, site, self.candidates(site, links, depth, follow))

    def seen_many(self, crawl_id: str, urls: list[str]) -> list[str]:
        """The URLs already accepted in this crawl, by any worker."""
        if not urls:
            return []
        flags = self.redis.smismember(self._keys(crawl_id)[0], urls)
        return [url for url, flag in zip(urls, flags, strict=True) if flag]

    def accepted_count(self, crawl_id: str) -> int:
        raw = self.redis.get(self._keys(crawl_id)[1])
        return int(raw) if raw is not None else 0

    def seen(self, crawl_id: str, url: str) -> bool:
        return bool(self.redis.sismember(self._keys(crawl_id)[0], url))
