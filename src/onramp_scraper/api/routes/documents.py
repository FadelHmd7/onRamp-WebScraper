"""Cleaned documents: a page's current version, filterable and paginated."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from onramp_scraper.api.deps import CurrentUser, SessionDep
from onramp_scraper.api.schemas import Page
from onramp_scraper.storage import documents as store
from onramp_scraper.storage.models import DocumentVersion

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentSummary(BaseModel):
    id: int = Field(description="The page id: the same for every version of the document")
    url: str
    site: str
    title: str
    status: Literal["active", "removed"]
    version: int = Field(description="The current version")
    content_hash: str
    crawled_at: datetime = Field(description="When the current version was stored")
    last_seen_at: datetime
    removed_at: datetime | None


class OutboundLink(BaseModel):
    url: str
    text: str
    nofollow: bool


class Document(DocumentSummary):
    first_seen_at: datetime
    versions: int = Field(description="How many versions are stored")
    blocks: list[dict[str, Any]] = Field(
        description="Content in page order: heading, paragraph, list, table, code, quote"
    )
    fields: dict[str, Any] = Field(description="Site-specific values (price, author, ...)")
    links: list[OutboundLink] = Field(description="Links on the current version, in order")
    raw: str = Field(description="Where to get the raw HTML and fetch details")


def summary(row: store.DocumentRow) -> DocumentSummary:
    page, version = row.page, row.version
    return DocumentSummary(
        id=page.id,
        url=page.url,
        site=page.site_key,
        title=version.title,
        status=page.status,  # type: ignore[arg-type]  # checked by a database constraint
        version=version.version,
        content_hash=version.content_hash,
        crawled_at=version.crawled_at,
        last_seen_at=page.last_seen_at,
        removed_at=page.removed_at,
    )


@router.get("", response_model=Page[DocumentSummary])
def list_documents(
    _: CurrentUser,
    session: SessionDep,
    site: str | None = None,
    page_status: Annotated[
        Literal["active", "removed"] | None,
        Query(alias="status", description="removed: gone from the site, versions kept"),
    ] = None,
    changed_after: Annotated[
        datetime | None, Query(description="Current version stored at or after (ISO 8601)")
    ] = None,
    changed_before: Annotated[
        datetime | None, Query(description="Current version stored before (ISO 8601)")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DocumentSummary]:
    """Documents, most recently changed first."""
    rows, total = store.list_documents(
        session,
        site_key=site,
        status=page_status,
        changed_after=changed_after,
        changed_before=changed_before,
        limit=limit,
        offset=offset,
    )
    return Page(items=[summary(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/{document_id}", response_model=Document)
def get_document(document_id: int, _: CurrentUser, session: SessionDep) -> Document:
    """One document: its current version's blocks, fields and links."""
    row = store.get_document(session, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such document")
    links = [
        OutboundLink(url=link.target_url, text=link.text, nofollow=link.nofollow)
        for link in store.version_links(session, row.version.id)
    ]
    return Document(
        **summary(row).model_dump(),
        first_seen_at=row.page.first_seen_at,
        versions=store.version_count(session, row.page.id),
        blocks=row.version.blocks,
        fields=row.version.fields,
        links=links,
        raw=f"/pages/{row.page.id}/raw",
    )


class VersionSummary(BaseModel):
    version: int
    crawled_at: datetime = Field(description="When this version was stored")
    content_hash: str = Field(description="SHA-256 of title, blocks and fields")
    title: str
    crawl_id: uuid.UUID | None = Field(description="The crawl that found this version")
    current: bool


class Version(VersionSummary):
    blocks: list[dict[str, Any]]
    fields: dict[str, Any]


class VersionHistory(BaseModel):
    id: int
    url: str
    current_version: int
    status: Literal["active", "removed"]
    versions: list[VersionSummary] = Field(description="Newest first")


def _version_summary(row: DocumentVersion, current: int) -> VersionSummary:
    return VersionSummary(
        version=row.version,
        crawled_at=row.crawled_at,
        content_hash=row.content_hash,
        title=row.title,
        crawl_id=row.crawl_id,
        current=row.version == current,
    )


@router.get("/{document_id}/versions", response_model=VersionHistory)
def list_versions(document_id: int, _: CurrentUser, session: SessionDep) -> VersionHistory:
    """Every stored version of a document, newest first. Old versions are never changed."""
    row = store.get_document(session, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such document")
    current = row.version.version
    return VersionHistory(
        id=row.page.id,
        url=row.page.url,
        current_version=current,
        status=row.page.status,  # type: ignore[arg-type]  # checked by a database constraint
        versions=[_version_summary(v, current) for v in store.list_versions(session, row.page.id)],
    )


@router.get("/{document_id}/versions/{version}", response_model=Version)
def get_version(document_id: int, version: int, _: CurrentUser, session: SessionDep) -> Version:
    """One version in full, for example to compare it with the current one."""
    row = store.get_document(session, document_id)
    found = store.get_version(session, document_id, version) if row else None
    if row is None or found is None:
        raise HTTPException(status_code=404, detail="No such version")
    return Version(
        **_version_summary(found, row.version.version).model_dump(),
        blocks=found.blocks,
        fields=found.fields,
    )
