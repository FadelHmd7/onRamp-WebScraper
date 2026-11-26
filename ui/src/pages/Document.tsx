// One document: its cleaned content, source URL, fields, links and every
// version. ?v=N shows an older version and what changed since the one
// before it.

import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import {
  api,
  type Block,
  type DocumentDetail,
  type RawPage,
  type Version,
  type VersionHistory,
} from "../api";
import { Blocks } from "../components/Blocks";
import { diffBlocks } from "../diff";
import { ago } from "../format";

interface Loaded {
  id: number;
  doc: DocumentDetail;
  history: VersionHistory;
}

export function DocumentPage() {
  const id = Number(useParams().id);
  const [params, setParams] = useSearchParams();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<{ id: number; message: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.document(id), api.versions(id)])
      .then(([doc, history]) => {
        if (!cancelled) setLoaded({ id, doc, history });
      })
      .catch((err: unknown) => {
        if (!cancelled) setError({ id, message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (error?.id === id) {
    return (
      <section>
        <h1>Document {id}</h1>
        <p className="error" role="alert">
          {error.message}
        </p>
        <Link to="/search">Back to search</Link>
      </section>
    );
  }
  if (!loaded || loaded.id !== id) return <p className="muted">Loading…</p>;

  const { doc, history } = loaded;
  const requested = Number(params.get("v") ?? doc.version);
  const viewing = history.versions.some((v) => v.version === requested) ? requested : doc.version;
  const select = (version: number) =>
    setParams(version === doc.version ? {} : { v: String(version) });

  return (
    <section>
      <p className="muted">
        <Link to="/search">Search</Link> / {doc.site}
      </p>
      <h1>{doc.title}</h1>
      <p>
        <a className="url" href={doc.url} target="_blank" rel="noopener noreferrer nofollow">
          {doc.url}
        </a>
      </p>
      <p className="row">
        <span className="badge">{doc.site}</span>
        {doc.status === "removed" ? (
          <span className="badge bad">
            removed from the site{doc.removed_at ? ` ${ago(doc.removed_at)}` : ""}
          </span>
        ) : (
          <span className="badge good">active</span>
        )}
        <span className="muted">
          version {doc.version} of {history.versions.length} · first seen {ago(doc.first_seen_at)} ·
          last seen {ago(doc.last_seen_at)}
        </span>
      </p>

      <div className="doc-layout">
        <div>
          {viewing === doc.version ? (
            <Content blocks={doc.blocks} fields={doc.fields} />
          ) : (
            <OldVersion id={doc.id} version={viewing} current={doc.version} />
          )}
          <Links links={doc.links} />
          <Raw pageId={doc.id} />
        </div>
        <aside className="panel versions" aria-label="Versions">
          <h2>Versions</h2>
          <ol reversed>
            {history.versions.map((v) => (
              <li key={v.version}>
                <button
                  type="button"
                  className={v.version === viewing ? "" : "secondary"}
                  aria-pressed={v.version === viewing}
                  onClick={() => select(v.version)}
                >
                  Version {v.version}
                  {v.current ? " (current)" : ""}
                </button>
                <div className="muted" style={{ fontSize: "0.8rem" }}>
                  {new Date(v.crawled_at).toLocaleString()}
                  <br />
                  <span className="url" title={v.content_hash}>
                    {v.content_hash.slice(0, 12)}
                  </span>
                </div>
              </li>
            ))}
          </ol>
        </aside>
      </div>
    </section>
  );
}

function Content({ blocks, fields }: { blocks: Block[]; fields: Record<string, unknown> }) {
  const entries = Object.entries(fields);
  return (
    <>
      {entries.length > 0 && (
        <div className="panel">
          <h2>Fields</h2>
          <table>
            <tbody>
              {entries.map(([name, value]) => (
                <tr key={name}>
                  <th scope="row">{name}</th>
                  <td>{formatField(value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <article className="panel">
        <Blocks blocks={blocks} />
      </article>
    </>
  );
}

function formatField(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${k}: ${String(v)}`)
      .join("; ");
  }
  return String(value);
}

function OldVersion({ id, version, current }: { id: number; version: number; current: number }) {
  const [state, setState] = useState<{
    version: number;
    data: Version;
    previous: Version | null;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.version(id, version), version > 1 ? api.version(id, version - 1) : null])
      .then(([data, previous]) => {
        if (!cancelled) setState({ version, data, previous });
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [id, version]);

  if (error) return <p className="error">{error}</p>;
  if (!state || state.version !== version) return <p className="muted">Loading version {version}…</p>;
  const changes = state.previous ? diffBlocks(state.previous.blocks, state.data.blocks) : null;
  return (
    <>
      <p className="panel" role="status">
        Viewing <strong>version {version}</strong> from{" "}
        {new Date(state.data.crawled_at).toLocaleString()}. The current version is {current}.
      </p>
      {changes && (
        <div className="panel">
          <h2>Changed since version {version - 1}</h2>
          {changes.length === 0 ? (
            <p className="muted">Only the fields changed.</p>
          ) : (
            <ul className="changes">
              {changes.map((c, i) => (
                <li key={i} className={c.kind}>
                  <span className={`badge ${c.kind === "added" ? "good" : "bad"}`}>{c.kind}</span>{" "}
                  {c.text}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <Content blocks={state.data.blocks} fields={state.data.fields} />
    </>
  );
}

function Links({ links }: { links: DocumentDetail["links"] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="panel">
      <h2>
        Links on this page <span className="badge">{links.length}</span>
      </h2>
      <button type="button" className="secondary" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "Hide links" : "Show links"}
      </button>
      {open && (
        <ul>
          {links.map((link, i) => (
            <li key={i}>
              <span className="url">{link.url}</span> {link.text && <span>— {link.text}</span>}
              {link.nofollow && <span className="badge">nofollow</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Raw({ pageId }: { pageId: number }) {
  const [raw, setRaw] = useState<RawPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setBusy(true);
    try {
      setRaw(await api.raw(pageId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <h2>Raw HTML</h2>
      {!raw && (
        <button type="button" className="secondary" onClick={() => void load()} disabled={busy}>
          {busy ? "Loading…" : "Show the HTML as fetched"}
        </button>
      )}
      {error && <p className="error">{error}</p>}
      {raw && (
        <>
          <p className="muted">
            HTTP {raw.fetch.status_code} · {raw.fetch.outcome} · {raw.elapsed_ms} ms ·{" "}
            {new Date(raw.fetch.fetched_at).toLocaleString()} · {raw.history.length} fetches
          </p>
          {/* Text, not markup: React escapes it, so the site's HTML is shown, never run. */}
          <pre className="raw">{raw.html}</pre>
        </>
      )}
    </div>
  );
}
