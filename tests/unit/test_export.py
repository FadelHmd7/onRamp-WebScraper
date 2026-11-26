"""scripts/export.py: which documents go into the sample."""

import importlib.util
from pathlib import Path
from typing import Any

spec = importlib.util.spec_from_file_location(
    "export", Path(__file__).parents[2] / "scripts" / "export.py"
)
assert spec and spec.loader
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def doc(url: str, versions: int = 1, fields: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"url": url, "versions": versions, "fields": fields or {}}


def test_multi_version_documents_come_first_then_detail_pages() -> None:
    docs = [
        doc("https://x/listing-2"),
        doc("https://x/book-b", fields={"price": 1.0}),
        doc("https://x/edited", versions=3),
        doc("https://x/book-a", fields={"price": 2.0}),
        doc("https://x/listing-1"),
    ]
    assert [d["url"] for d in export.choose(docs, 4)] == [
        "https://x/edited",
        "https://x/book-a",
        "https://x/book-b",
        "https://x/listing-1",
    ]


def test_every_multi_version_document_is_kept_even_past_the_limit() -> None:
    docs = [doc(f"https://x/{n}", versions=2) for n in range(3)] + [doc("https://x/one")]
    assert len(export.choose(docs, 2)) == 3
