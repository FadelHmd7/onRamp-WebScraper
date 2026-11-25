"""The document schema: what a cleaned page must look like before it is stored.

Every document is validated with Pydantic before it reaches the database.
An invalid one is rejected with ``InvalidDocument`` and a readable reason,
which is logged here and recorded on the URL by the worker. Database
constraints (``storage/models.py``) stay as a second check.

A document has:

- ``id``           stable ID, the same for every version of the page. It is a
                   UUIDv5 of the URL, so any worker can compute it without
                   asking the database.
- ``url``          absolute and already normalized (``crawler/urls.py``)
- ``site``         the site key; the URL must be on one of the site's hosts
- ``title``        non-empty
- ``blocks``       at least one content block (shapes below)
- ``fields``       site-specific values from selectors (task D5)
- ``content_hash`` SHA-256 of title + blocks + fields; checked, not trusted
- ``crawled_at``   timezone-aware, not in the future
- ``version``      1 for a new page, +1 for each change (task D6)

Rejection reasons, one per rule: no title, no content, an empty or malformed
block, a ragged table, too much content, a URL that is not normalized or is
off-site, a wrong content hash, a naive or future crawl time, a bad version.
"""

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
)

from onramp_scraper.crawler.urls import normalize_url
from onramp_scraper.processing.clean import CleanedPage, block_text
from onramp_scraper.sites import SiteConfig

log = logging.getLogger(__name__)

MAX_TITLE_CHARS = 1_000
MAX_BLOCKS = 20_000
MAX_TEXT_CHARS = 2_000_000  # all block text together; a normal page is far below this
CLOCK_SKEW = timedelta(minutes=5)  # workers' clocks may differ a little

# Text with at least one visible character.
Text = Annotated[str, StringConstraints(pattern=r"\S")]


def _is_field_value(value: Any) -> bool:
    """Text, a number, true/false, empty, a list of text, or a text-to-text mapping."""
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(isinstance(v, str) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())
    return False


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class HeadingBlock(_Block):
    type: Literal["heading"]
    level: int = Field(ge=1, le=6)
    text: Text


class ParagraphBlock(_Block):
    type: Literal["paragraph"]
    text: Text


class ListBlock(_Block):
    type: Literal["list"]
    ordered: bool
    items: list[Text] = Field(min_length=1)


class TableBlock(_Block):
    type: Literal["table"]
    caption: Text | None
    header: list[str]
    rows: list[list[str]]
    row_headers: bool

    @model_validator(mode="after")
    def check_shape(self) -> Self:
        if not self.rows and not any(self.header):
            raise ValueError("table has no header and no rows")
        widths = {len(row) for row in self.rows} | ({len(self.header)} if self.header else set())
        if len(widths) > 1:
            raise ValueError(f"table rows differ in width: {sorted(widths)}")
        if 0 in widths:
            raise ValueError("table has a row with no cells")
        if self.row_headers and widths and min(widths) < 2:
            raise ValueError("a table with row headers needs a label and a value column")
        return self


class CodeBlock(_Block):
    type: Literal["code"]
    text: Text


class QuoteBlock(_Block):
    type: Literal["quote"]
    text: Text


Block = Annotated[
    HeadingBlock | ParagraphBlock | ListBlock | TableBlock | CodeBlock | QuoteBlock,
    Field(discriminator="type"),
]
_BLOCK_TYPES = frozenset({"heading", "paragraph", "list", "table", "code", "quote"})


def document_id(url: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, url)


def content_hash(title: str, blocks: list[dict[str, Any]], fields: dict[str, Any]) -> str:
    """SHA-256 of the content in a canonical form. Same content, same hash, on any worker.

    Change detection (task D6) compares this: equal hash, no new version.
    Crawl time and version are left out on purpose; they change on every crawl.
    """
    canonical = json.dumps(
        {"title": title, "blocks": blocks, "fields": fields},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Document(BaseModel):
    """One cleaned, validated version of a page."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    url: str
    site: str = Field(pattern=r"^[a-z0-9-]+$")
    title: Annotated[str, StringConstraints(pattern=r"\S", max_length=MAX_TITLE_CHARS)]
    blocks: list[Block] = Field(min_length=1, max_length=MAX_BLOCKS)
    fields: dict[str, Any] = {}
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    crawled_at: datetime
    version: int = Field(ge=1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> uuid.UUID:
        return document_id(self.url)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def text(self) -> str:
        """Plain text of all blocks, for full-text search and snippets."""
        return "\n".join(block_text(b.model_dump()) for b in self.blocks)

    @field_validator("url")
    @classmethod
    def url_is_normalized(cls, url: str) -> str:
        if not url.startswith(("http://", "https://")) or normalize_url(url) != url:
            raise ValueError("URL must be absolute and normalized")
        return url

    @field_validator("fields")
    @classmethod
    def fields_are_simple(cls, fields: dict[str, Any]) -> dict[str, Any]:
        for name, value in fields.items():
            if not _is_field_value(value):
                raise ValueError(
                    f"field {name!r} must be text, a number, true/false, a list of text "
                    "or a mapping of text to text"
                )
        return fields

    @field_validator("crawled_at")
    @classmethod
    def crawled_at_is_real(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("crawl time must include a timezone")
        if value > datetime.now(UTC) + CLOCK_SKEW:
            raise ValueError("crawl time is in the future")
        return value

    @model_validator(mode="after")
    def check_document(self, info: ValidationInfo) -> Self:
        site = (info.context or {}).get("site")
        if isinstance(site, SiteConfig):
            if site.key != self.site:
                raise ValueError(f"document is for site {self.site!r}, not {site.key!r}")
            if not site.allows_host(self.url):
                raise ValueError(f"URL is not on an allowed host of site {site.key!r}")
        if sum(len(block_text(b.model_dump())) for b in self.blocks) > MAX_TEXT_CHARS:
            raise ValueError(f"content is longer than {MAX_TEXT_CHARS} characters")
        blocks = [b.model_dump() for b in self.blocks]
        if content_hash(self.title, blocks, self.fields) != self.content_hash:
            raise ValueError("content_hash does not match the content")
        return self


class InvalidDocument(Exception):
    """A document failed validation. ``reason`` is short and readable, for logs and the UI."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


def _message(item: Any) -> str:
    ctx = item.get("ctx") or {}
    empty_text = item["type"] == "string_pattern_mismatch" and ctx.get("pattern") == r"\S"
    if empty_text or (item["type"] == "too_short" and ctx.get("min_length") == 1):
        return "must not be empty"
    return str(item["msg"]).removeprefix("Value error, ")


def _reason(error: ValidationError) -> str:
    """All problems, as ``where: what`` ("blocks.3.level: ... less than or equal to 6")."""
    parts = []
    for item in error.errors(include_url=False):
        # The union's member name ("heading") adds nothing; the block index is enough.
        where = ".".join(str(p) for p in item["loc"] if p not in _BLOCK_TYPES)
        parts.append(f"{where}: {_message(item)}" if where else _message(item))
    return "; ".join(parts)


def validate_document(data: dict[str, Any], site: SiteConfig | None = None) -> Document:
    """Validate a document. Raises ``InvalidDocument`` with the reason, and logs it.

    With ``site``, also checks that the document belongs to that site.
    """
    try:
        return Document.model_validate(data, context={"site": site})
    except ValidationError as exc:
        url = str(data.get("url", ""))
        reason = _reason(exc)
        log.warning(
            "document rejected: %s (%s)",
            reason,
            url,
            extra={"url": url, "site": data.get("site"), "reason": reason},
        )
        raise InvalidDocument(url, reason) from exc


def build_document(
    page: CleanedPage,
    *,
    url: str,
    site: SiteConfig,
    version: int,
    crawled_at: datetime,
    fields: dict[str, Any] | None = None,
) -> Document:
    """A cleaned page plus its metadata, hashed and validated."""
    fields = fields or {}
    return validate_document(
        {
            "url": url,
            "site": site.key,
            "title": page.title,
            "blocks": page.blocks,
            "fields": fields,
            "content_hash": content_hash(page.title, page.blocks, fields),
            "crawled_at": crawled_at,
            "version": version,
        },
        site,
    )
