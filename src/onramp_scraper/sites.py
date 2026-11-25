"""Per-site crawl configuration, loaded from ``config/sites.yaml``.

Validation happens once, at startup: a bad pattern or a seed on the wrong
host stops the container with a clear message instead of failing mid-crawl.
"""

import re
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from onramp_scraper.config import get_settings


class FieldRule(BaseModel):
    """How to pull one site-specific value out of a page with CSS selectors.

    - ``selector`` alone: the text of the first match.
    - ``attr``: an attribute of the first match instead of its text.
    - ``many``: every match, as a list (for example several authors).
    - ``key`` + ``value``: every match becomes one pair, read from these two
      sub-selectors, and the field is a dictionary (for example an infobox
      of "First publication date: 1813" rows).
    - ``pattern``: a regex applied to the text; its first group (or the whole
      match) is kept, for example ``"\\((\\d+) available\\)"``.
    - ``type``: ``int`` or ``float``, for single values only. Lists and pairs
      stay text.

    Extraction itself is in ``processing/fields.py``.
    """

    model_config = ConfigDict(extra="forbid")

    selector: str = Field(min_length=1)
    attr: str | None = None
    many: bool = False
    key: str | None = None
    value: str | None = None
    pattern: str | None = None
    type: Literal["text", "int", "float"] = "text"

    @field_validator("pattern")
    @classmethod
    def pattern_compiles(cls, pattern: str | None) -> str | None:
        if pattern is None:
            return None
        try:
            groups = re.compile(pattern).groups
        except re.error as exc:
            raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
        if groups > 1:
            raise ValueError(f"pattern {pattern!r} has {groups} groups; use at most one")
        return pattern

    @model_validator(mode="after")
    def check_mode(self) -> Self:
        if (self.key is None) != (self.value is None):
            raise ValueError("key and value must be given together")
        if self.key is not None and (self.attr is not None or self.many):
            raise ValueError("key/value pairs cannot be combined with attr or many")
        if self.type != "text" and (self.many or self.key is not None):
            raise ValueError("type applies to single values; lists and pairs stay text")
        return self


class SiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z0-9-]+$")
    name: str
    role: Literal["static", "javascript", "paginated", "test"]
    base_url: HttpUrl
    allowed_hosts: list[str] = Field(min_length=1)
    fetch_mode: Literal["http", "browser"]
    browser_wait_until: Literal["load", "domcontentloaded", "networkidle"] = "load"
    browser_wait_for: str | None = None
    seeds: list[HttpUrl] = Field(min_length=1)
    include: list[str] = Field(min_length=1)
    exclude: list[str] = []
    max_depth: int = Field(ge=0)
    max_pages: int = Field(ge=1)
    requests_per_second: float = Field(gt=0)
    fields: dict[str, FieldRule] = {}
    # Cleaning (processing/clean.py). All optional; the generic rules cover most pages.
    title_selector: str | None = None  # where the page title is, if not the first <h1>
    remove: list[str] = []  # extra clutter to drop, as CSS selectors

    @field_validator("include", "exclude")
    @classmethod
    def patterns_compile(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
        return patterns

    @model_validator(mode="after")
    def check_consistency(self) -> Self:
        for seed in self.seeds:
            if seed.host not in self.allowed_hosts:
                raise ValueError(f"seed {seed} is not on an allowed host {self.allowed_hosts}")
        if self.fetch_mode == "browser" and not self.browser_wait_for:
            raise ValueError("fetch_mode 'browser' needs browser_wait_for")
        return self

    def allows_host(self, url: str) -> bool:
        return urlsplit(url).hostname in self.allowed_hosts

    def allows_path(self, url: str) -> bool:
        """Include/exclude check on path + query. robots.txt is checked elsewhere."""
        parts = urlsplit(url)
        if not self.allows_host(url):
            return False
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        if any(re.search(pattern, target) for pattern in self.exclude):
            return False
        return any(re.search(pattern, target) for pattern in self.include)


class SitesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sites: list[SiteConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_keys(self) -> Self:
        keys = [site.key for site in self.sites]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate site keys in {keys}")
        return self


def load_sites(path: Path | None = None) -> dict[str, SiteConfig]:
    """Read and validate the sites file (``SITES_FILE``). Raises on any invalid entry."""
    path = path or get_settings().sites_file
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {site.key: site for site in SitesFile.model_validate(raw).sites}
