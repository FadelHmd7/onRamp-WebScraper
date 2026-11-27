// Keyword search with snippets and a site filter. The query lives in the URL
// (?q=&site=&page=), so a search can be bookmarked and Back works. With no
// query, the page lists the most recently changed documents instead.

import { type FormEvent, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { api, type DocumentSummary, type Page, type SearchHit, type Site } from "../api";
import { Snippet } from "../components/Snippet";
import { Loading, Spinner } from "../components/Spinner";
import { ago } from "../format";

const PER_PAGE = 20;

type Results =
  | { kind: "search"; page: Page<SearchHit> }
  | { kind: "browse"; page: Page<DocumentSummary> };

export function Search() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const site = params.get("site") ?? "";
  const pageNumber = Math.max(1, Number(params.get("page") ?? "1") || 1);

  // The search box follows the URL (Back/Forward), adjusted during render
  // rather than in an effect: https://react.dev/learn/you-might-not-need-an-effect
  const [draft, setDraft] = useState(q);
  const [draftFor, setDraftFor] = useState(q);
  if (draftFor !== q) {
    setDraftFor(q);
    setDraft(q);
  }

  const [sites, setSites] = useState<Site[]>([]);
  useEffect(() => {
    api.sites().then(setSites).catch(() => setSites([]));
  }, []);

  // Each answer is stored with the request it answers. "Loading" is derived:
  // the stored answer belongs to another request. A slow old answer can never
  // replace a newer one.
  const requestKey = JSON.stringify([q, site, pageNumber]);
  const [answer, setAnswer] = useState<{
    key: string;
    results: Results | null;
    error: string | null;
  } | null>(null);
  useEffect(() => {
    let cancelled = false;
    const query = { site: site || undefined, limit: PER_PAGE, offset: (pageNumber - 1) * PER_PAGE };
    const request: Promise<Results> = q
      ? api.search({ ...query, q }).then((page) => ({ kind: "search", page }))
      : api.documents(query).then((page) => ({ kind: "browse", page }));
    request
      .then((results) => {
        if (!cancelled) setAnswer({ key: requestKey, results, error: null });
      })
      .catch((err: unknown) => {
        const error = err instanceof Error ? err.message : String(err);
        if (!cancelled) setAnswer({ key: requestKey, results: null, error });
      });
    return () => {
      cancelled = true;
    };
  }, [requestKey, q, site, pageNumber]);
  const loading = answer?.key !== requestKey;
  const results = answer?.results ?? null;
  const error = loading ? null : (answer?.error ?? null);

  function update(changes: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    update({ q: draft.trim(), page: "" });
  }

  const total = results?.page.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PER_PAGE));

  return (
    <section>
      <h1>Search</h1>
      <form className="row panel" onSubmit={submit} role="search">
        <label style={{ flex: "1 1 280px" }}>
          Words
          <input
            type="search"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={'pride prejudice, "exact phrase", -exclude'}
            aria-label="Search words"
          />
        </label>
        <label>
          Site
          <select
            value={site}
            onChange={(e) => update({ site: e.target.value, page: "" })}
            aria-label="Site"
          >
            <option value="">All sites</option>
            {sites.map((s) => (
              <option key={s.key} value={s.key}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="primary">
          Search
        </button>
      </form>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!results && loading && <Loading>Searching…</Loading>}
      {results && (
        <p className="muted results-count" aria-live="polite">
          {results.kind === "search"
            ? `${total} result${total === 1 ? "" : "s"} for “${q}”`
            : `${total} documents, most recently changed first`}
          {loading && (
            <>
              {" "}
              <Spinner label="Updating" />
            </>
          )}
        </p>
      )}

      {results?.kind === "search" &&
        results.page.items.map((hit) => (
          <article className="panel hit" key={hit.id}>
            <h2>
              <Link to={`/documents/${hit.id}`}>{hit.title}</Link>
            </h2>
            <div className="url muted">{hit.url}</div>
            <Snippet html={hit.snippet} />
            <p className="hit-meta">
              <span className="badge">{hit.site}</span>
              <span className="muted small">
                Version {hit.version}, crawled {ago(hit.crawled_at)}
              </span>
            </p>
          </article>
        ))}

      {results?.kind === "browse" && (
        <div className="panel scroll">
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Site</th>
                <th>Version</th>
                <th>Changed</th>
              </tr>
            </thead>
            <tbody>
              {results.page.items.map((doc) => (
                <tr key={doc.id}>
                  <td>
                    <Link to={`/documents/${doc.id}`}>{doc.title}</Link>
                    <div className="url muted">{doc.url}</div>
                  </td>
                  <td>{doc.site}</td>
                  <td>{doc.version}</td>
                  <td>{ago(doc.crawled_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {total > PER_PAGE && (
        <nav className="row" aria-label="Pages">
          <button
            type="button"
            className="secondary"
            disabled={pageNumber <= 1}
            onClick={() => update({ page: String(pageNumber - 1) })}
          >
            Previous
          </button>
          <span className="muted">
            Page {pageNumber} of {pages}
          </span>
          <button
            type="button"
            className="secondary"
            disabled={pageNumber >= pages}
            onClick={() => update({ page: String(pageNumber + 1) })}
          >
            Next
          </button>
        </nav>
      )}
    </section>
  );
}
