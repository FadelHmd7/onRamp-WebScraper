"""Export a sample of cleaned documents per site through the API.

    uv run python scripts/export.py                       # 120 per site into exports/
    uv run python scripts/export.py --per-site 200 --sites books-toscrape

Standard library only: it logs in as the viewer and reads the same endpoints
the UI uses (GET /documents, /documents/{id}, /documents/{id}/versions/{n}).

Writes, for each site:

- ``exports/<site>.jsonl``: one document per line: its current version (title,
  blocks, fields, links, hash, times) and, when it has more than one version,
  every older version in full under ``"versions"``.
- ``exports/manifest.json``: what was exported, from where and when.

Which documents: detail pages first (those with site-specific fields, such as
a book's price), then the rest, by URL; every document with several versions
is always included.
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
Json = dict[str, Any]


class Api:
    def __init__(self, base: str, username: str, password: str) -> None:
        self.base = base.rstrip("/")
        form = urllib.parse.urlencode({"username": username, "password": password}).encode()
        token = self._call("/auth/token", data=form)["access_token"]
        self.headers = {"Authorization": f"Bearer {token}"}

    def _call(
        self, path: str, data: bytes | None = None, headers: dict[str, str] | None = None
    ) -> Any:
        if not self.base.startswith(("http://", "https://")):
            raise ValueError("the API URL must be http(s)")
        request = urllib.request.Request(self.base + path, data=data, headers=headers or {})  # noqa: S310
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.load(response)

    def get(self, path: str, **query: object) -> Any:
        if query:
            path += "?" + urllib.parse.urlencode(query)
        return self._call(path, headers=self.headers)

    def summaries(self, site: str) -> list[Json]:
        """Every active document of a site (the API pages by 100)."""
        found: list[Json] = []
        while True:
            page = self.get("/documents", site=site, status="active", limit=100, offset=len(found))
            found += page["items"]
            if len(found) >= page["total"] or not page["items"]:
                return found


def choose(details: list[Json], per_site: int) -> list[Json]:
    """Multi-version documents always; then detail pages (with fields); then the rest."""

    def priority(doc: Json) -> tuple[int, str]:
        if doc["versions"] > 1:
            return (0, doc["url"])
        return (1 if doc["fields"] else 2, doc["url"])

    ordered = sorted(details, key=priority)
    multi = [d for d in ordered if d["versions"] > 1]
    return multi + [d for d in ordered if d["versions"] == 1][: max(per_site - len(multi), 0)]


def record(api: Api, doc: Json) -> Json:
    """One exported document; older versions in full when there are any."""
    out = {key: doc[key] for key in (
        "id", "url", "site", "title", "status", "version", "content_hash",
        "crawled_at", "first_seen_at", "last_seen_at", "fields", "blocks", "links",
    )}  # fmt: skip
    if doc["versions"] > 1:
        out["versions"] = [
            {
                k: v
                for k, v in api.get(f"/documents/{doc['id']}/versions/{n}").items()
                if k != "current"
            }
            for n in range(doc["version"] - 1, 0, -1)
        ]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--api", default="http://localhost:9060")
    parser.add_argument("--user", default="viewer")
    parser.add_argument("--password", default="viewer-dev-password")
    parser.add_argument("--per-site", type=int, default=120)
    parser.add_argument("--sites", nargs="+", help="default: every configured site")
    parser.add_argument("--out", type=Path, default=ROOT / "exports")
    args = parser.parse_args()

    api = Api(args.api, args.user, args.password)
    sites = args.sites or [s["key"] for s in api.get("/sites")]
    args.out.mkdir(parents=True, exist_ok=True)
    manifest: Json = {
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "api": args.api,
        "per_site": args.per_site,
        "sites": {},
    }
    for site in sites:
        summaries = api.summaries(site)
        details = [api.get(f"/documents/{s['id']}") for s in summaries]
        chosen = choose(details, args.per_site)
        path = args.out / f"{site}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for doc in chosen:
                handle.write(
                    json.dumps(record(api, doc), ensure_ascii=False, sort_keys=True) + "\n"
                )
        manifest["sites"][site] = {
            "file": path.name,
            "documents": len(chosen),
            "available": len(summaries),
            "with_fields": sum(1 for d in chosen if d["fields"]),
            "with_several_versions": [d["url"] for d in chosen if d["versions"] > 1],
        }
        print(f"{site}: {len(chosen)} of {len(summaries)} documents -> {path}")
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    short = [s for s, m in manifest["sites"].items() if m["documents"] < min(100, args.per_site)]
    if short:
        print(f"fewer than 100 documents for: {', '.join(short)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
