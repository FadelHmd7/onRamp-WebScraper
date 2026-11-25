// Everyone: system status, queue, workers, crawl progress and errors, live.
// Admins also: start a crawl, and retry jobs from the dead-letter queue.

import { type FormEvent, useCallback, useState } from "react";

import {
  api,
  type Crawl,
  type CrawlDetail,
  type DeadLetter,
  type Health,
  type Site,
} from "../api";
import { AdminOnly } from "../auth";
import { ago, duration, percent } from "../format";
import { usePolling } from "../usePolling";

const REFRESH_MS = 3000;

export function Dashboard() {
  const health = usePolling(api.health, REFRESH_MS);
  const loadCrawls = useCallback(() => api.crawls({ limit: 10 }), []);
  const crawls = usePolling(loadCrawls, REFRESH_MS);

  return (
    <section>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <h1>Dashboard</h1>
        <span className="muted">Updates every {REFRESH_MS / 1000} s</span>
      </div>
      <SystemPanel health={health.data} error={health.error} />
      <AdminOnly>
        <StartCrawl onStarted={crawls.refresh} />
      </AdminOnly>
      <CrawlsPanel crawls={crawls.data?.items ?? null} error={crawls.error} />
      <AdminOnly>
        <DeadLetters onRetried={crawls.refresh} />
      </AdminOnly>
    </section>
  );
}

// ---------------------------------------------------------------- system

function SystemPanel({ health, error }: { health: Health | null; error: string | null }) {
  if (!health) {
    return <div className="panel">{error ? <p className="error">{error}</p> : "Loading…"}</div>;
  }
  const workers = health.workers;
  return (
    <div className="grid">
      <div className="panel">
        <h2>Queue</h2>
        <div className="stat" aria-label="Queue length">
          {health.queue ? health.queue.backlog : "–"}
        </div>
        <p className="muted">
          {health.queue
            ? `${health.queue.ready} waiting, ${health.queue.unacked} in hand`
            : "RabbitMQ unreachable"}
        </p>
      </div>
      <div className="panel">
        <h2>Workers</h2>
        <div className="stat" aria-label="Active workers">
          {workers ? workers.active : "–"}
        </div>
        <p className="muted">{workers ? `${workers.busy} busy` : "Redis unreachable"}</p>
      </div>
      <div className="panel">
        <h2>Running crawls</h2>
        <div className="stat">{health.running_crawls ?? "–"}</div>
        <p className="muted">
          {Object.entries(health.checks).map(([name, check]) => (
            <span
              key={name}
              className={`badge ${check.ok ? "good" : "bad"}`}
              title={check.error ?? `${check.ms} ms`}
              style={{ marginRight: 4 }}
            >
              {name}
            </span>
          ))}
        </p>
      </div>
      {workers && workers.list.length > 0 && (
        <div className="panel" style={{ gridColumn: "1 / -1" }}>
          <h2>What the workers are doing</h2>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>Worker</th>
                  <th>Now</th>
                  <th>Jobs done</th>
                </tr>
              </thead>
              <tbody>
                {workers.list.map((w) => (
                  <tr key={w.name}>
                    <td className="url">{w.name}</td>
                    <td className="url">{w.current_url ?? <span className="muted">idle</span>}</td>
                    <td>{w.processed}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- crawls

const STATUS_BADGE: Record<Crawl["status"], string> = {
  queued: "",
  running: "warn",
  finished: "good",
  failed: "bad",
};

function CrawlsPanel({ crawls, error }: { crawls: Crawl[] | null; error: string | null }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="panel">
      <h2>Recent crawls</h2>
      {error && <p className="error">{error}</p>}
      {crawls === null ? (
        <p className="muted">Loading…</p>
      ) : crawls.length === 0 ? (
        <p className="muted">No crawls yet.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Site</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Stored / unchanged</th>
                <th>Errors</th>
                <th>Time</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {crawls.map((c) => (
                <CrawlRow
                  key={c.id}
                  crawl={c}
                  open={open === c.id}
                  onToggle={() => setOpen(open === c.id ? null : c.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CrawlRow({ crawl, open, onToggle }: { crawl: Crawl; open: boolean; onToggle: () => void }) {
  const done = percent(crawl.done, crawl.total);
  return (
    <>
      <tr>
        <td>
          {crawl.site}
          <div className="muted" style={{ fontSize: "0.8rem" }}>
            {crawl.mode} · {ago(crawl.created_at)}
          </div>
        </td>
        <td>
          <span className={`badge ${STATUS_BADGE[crawl.status]}`}>{crawl.status}</span>
        </td>
        <td>
          <div
            className="progress"
            role="progressbar"
            aria-valuenow={done}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`${crawl.site} progress`}
          >
            <span style={{ width: `${done}%` }} />
          </div>
          <span className="muted" style={{ fontSize: "0.8rem" }}>
            {crawl.done} / {crawl.total} ({done}%) · {crawl.counts.queued} queued
          </span>
        </td>
        <td>
          {crawl.counts.fetched} / {crawl.counts.unchanged}
        </td>
        <td>
          {crawl.errors > 0 ? (
            <span className="badge bad">{crawl.errors}</span>
          ) : (
            <span className="muted">0</span>
          )}
        </td>
        <td>
          {duration(crawl.seconds)}
          {crawl.pages_per_minute !== null && (
            <div className="muted" style={{ fontSize: "0.8rem" }}>
              {Math.round(crawl.pages_per_minute)}/min
            </div>
          )}
        </td>
        <td>
          <button type="button" className="secondary" onClick={onToggle} aria-expanded={open}>
            {open ? "Hide" : "Details"}
          </button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7}>
            <CrawlDetails id={crawl.id} />
          </td>
        </tr>
      )}
    </>
  );
}

function CrawlDetails({ id }: { id: string }) {
  const load = useCallback(() => api.crawl(id), [id]);
  const { data, error } = usePolling<CrawlDetail>(load, REFRESH_MS);
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <div>
      <p className="muted">
        {Object.entries(data.counts)
          .map(([status, n]) => `${status}: ${n}`)
          .join(" · ")}
      </p>
      {data.recent_errors.length === 0 ? (
        <p className="muted">No errors.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>URL</th>
              <th>Status</th>
              <th>Attempts</th>
              <th>Last error</th>
            </tr>
          </thead>
          <tbody>
            {data.recent_errors.map((e) => (
              <tr key={e.url}>
                <td className="url">{e.url}</td>
                <td>{e.status === "queued" ? "retrying" : e.status}</td>
                <td>{e.attempts}</td>
                <td>{e.last_error}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- admin actions

function StartCrawl({ onStarted }: { onStarted: () => void }) {
  const sites = usePolling<Site[]>(api.sites, 60_000);
  const [site, setSite] = useState("");
  const [mode, setMode] = useState<"full" | "incremental">("full");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const chosen = site || sites.data?.[0]?.key || "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const crawl = await api.startCrawl({ site: chosen, mode });
      setMessage({ ok: true, text: `Started a ${crawl.mode} crawl of ${crawl.site}.` });
      onStarted();
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <h2>Start a crawl</h2>
      <form className="row" onSubmit={submit}>
        <label>
          Site
          <select value={chosen} onChange={(e) => setSite(e.target.value)} disabled={!sites.data}>
            {sites.data?.map((s) => (
              <option key={s.key} value={s.key}>
                {s.name} ({s.key})
              </option>
            ))}
          </select>
        </label>
        <label>
          Mode
          <select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
            <option value="full">Full: download every page</option>
            <option value="incremental">Incremental: only what changed</option>
          </select>
        </label>
        <button type="submit" disabled={busy || !chosen}>
          {busy ? "Starting…" : "Start crawl"}
        </button>
      </form>
      {message && (
        <p className={message.ok ? "muted" : "error"} role="status">
          {message.text}
        </p>
      )}
    </div>
  );
}

function DeadLetters({ onRetried }: { onRetried: () => void }) {
  const load = useCallback(() => api.deadLetters({ limit: 20 }), []);
  const dlq = usePolling(load, REFRESH_MS);
  const [retrying, setRetrying] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function retry(letter: DeadLetter) {
    setRetrying(letter.id);
    setMessage(null);
    try {
      await api.retryDeadLetter(letter.id);
      setMessage(`Sent ${letter.url} again.`);
      dlq.refresh();
      onRetried();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setRetrying(null);
    }
  }

  const items = dlq.data?.items ?? [];
  return (
    <div className="panel">
      <h2>Dead-letter queue {dlq.data ? <span className="badge">{dlq.data.total}</span> : null}</h2>
      {dlq.error && <p className="error">{dlq.error}</p>}
      {message && (
        <p className="muted" role="status">
          {message}
        </p>
      )}
      {dlq.data && items.length === 0 ? (
        <p className="muted">Empty: no job has failed every attempt.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>URL</th>
                <th>Last error</th>
                <th>Attempts</th>
                <th>Failed</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((d) => (
                <tr key={d.id}>
                  <td className="url">{d.url}</td>
                  <td>{d.last_error}</td>
                  <td>{d.attempts}</td>
                  <td>{ago(d.failed_at)}</td>
                  <td>
                    <button
                      type="button"
                      onClick={() => void retry(d)}
                      disabled={retrying !== null}
                    >
                      {retrying === d.id ? "Sending…" : "Retry"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
