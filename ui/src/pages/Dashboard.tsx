// Everyone: system status, the live yard of workers and pages, crawl progress
// and errors. Admins also: start a crawl, and retry jobs from the dead-letter
// queue. Everything refreshes every 3 seconds.

import { type FormEvent, useCallback, useState } from "react";

import {
  api,
  ApiError,
  type Crawl,
  type CrawlDetail,
  type DeadLetter,
  type Health,
  type Site,
} from "../api";
import { AdminOnly } from "../auth";
import { RobotYard } from "../components/RobotYard";
import { BusyButton, Loading, Spinner } from "../components/Spinner";
import { ago, duration, percent } from "../format";
import { useToast } from "../toast";
import { usePolling } from "../usePolling";

const REFRESH_MS = 3000;

export function Dashboard() {
  const health = usePolling(api.health, REFRESH_MS);
  const loadCrawls = useCallback(() => api.crawls({ limit: 10 }), []);
  const crawls = usePolling(loadCrawls, REFRESH_MS);
  const sites = usePolling<Site[]>(api.sites, 60_000);
  const [picked, setPicked] = useState<string | null>(null);

  const list = crawls.data?.items ?? [];
  // Watch the crawl the user picked, else the newest one still running, else the newest.
  const watchId =
    (picked && list.some((c) => c.id === picked) ? picked : null) ??
    (list.find(isActive) ?? list[0])?.id ??
    null;

  return (
    <section className="dashboard">
      <div className="page-head">
        <h1>Dashboard</h1>
        <p className="muted">Live: refreshed every {REFRESH_MS / 1000} seconds.</p>
      </div>
      <SystemStats health={health.data} error={health.error} />
      <AdminOnly>
        <StartCrawl
          sites={sites.data}
          health={health.data}
          crawls={crawls.data ? list : null}
          onStarted={(crawl) => {
            if (crawl) setPicked(crawl.id);
            crawls.refresh();
            health.refresh();
          }}
        />
      </AdminOnly>
      <RobotYard
        health={health.data}
        sites={sites.data ?? []}
        crawls={list}
        watchId={watchId}
        onWatch={setPicked}
      />
      <CrawlsPanel
        crawls={crawls.data ? list : null}
        error={crawls.error}
        sites={sites.data ?? []}
        watchId={watchId}
        onWatch={setPicked}
      />
      <WorkersPanel health={health.data} />
      <AdminOnly>
        <DeadLetters
          health={health.data}
          onRetried={() => {
            crawls.refresh();
            health.refresh();
          }}
        />
      </AdminOnly>
    </section>
  );
}

function isActive(crawl: Crawl): boolean {
  return crawl.status === "running" || crawl.status === "queued";
}

function siteName(key: string, sites: Site[] | null): string {
  return sites?.find((s) => s.key === key)?.name ?? key;
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// ---------------------------------------------------------------- system

function SystemStats({ health, error }: { health: Health | null; error: string | null }) {
  if (!health) {
    return (
      <div className="stats">{error ? <p className="error">{error}</p> : <Loading>Checking the system…</Loading>}</div>
    );
  }
  const workers = health.workers;
  return (
    <div className="stats">
      <div className="stat-item">
        <h2>Queue length</h2>
        <div className="stat" aria-label="Queue length">
          {health.queue ? health.queue.backlog : "–"}
        </div>
        <p className="muted">
          {health.queue
            ? `${health.queue.ready} waiting, ${health.queue.unacked} being worked on`
            : "RabbitMQ is not answering"}
        </p>
      </div>
      <div className="stat-item">
        <h2>Workers</h2>
        <div className="stat" aria-label="Active workers">
          {workers ? workers.active : "–"}
        </div>
        <p className="muted">{workers ? `${workers.busy} busy right now` : "Redis is not answering"}</p>
      </div>
      <div className="stat-item">
        <h2>Running crawls</h2>
        <div className="stat">{health.running_crawls ?? "–"}</div>
        <p className="muted">across all sites</p>
      </div>
      <div className="stat-item">
        <h2>Services</h2>
        <ul className="services">
          {Object.entries(health.checks).map(([name, check]) => (
            <li key={name} className={check.ok ? "up" : "down"} title={check.error ?? `${check.ms} ms`}>
              {name} {check.ok ? "up" : "down"}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function WorkersPanel({ health }: { health: Health | null }) {
  const list = health?.workers?.list ?? [];
  if (list.length === 0) return null;
  return (
    <div className="panel">
      <h2>What each worker is doing</h2>
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
            {list.map((w) => (
              <tr key={w.name}>
                <td className="url">{w.name}</td>
                <td className="url">{w.current_url ?? <span className="muted">idle</span>}</td>
                <td className="num">{w.processed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- crawls

const STATUS_TONE: Record<Crawl["status"], string> = {
  queued: "",
  running: "warn live",
  finished: "good",
  failed: "bad",
};

function CrawlsPanel({
  crawls,
  error,
  sites,
  watchId,
  onWatch,
}: {
  crawls: Crawl[] | null;
  error: string | null;
  sites: Site[];
  watchId: string | null;
  onWatch: (id: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="panel">
      <h2>Recent crawls</h2>
      {error && <p className="error">{error}</p>}
      {crawls === null ? (
        <Loading />
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
                <th>
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {crawls.map((c) => (
                <CrawlRow
                  key={c.id}
                  crawl={c}
                  name={siteName(c.site, sites)}
                  open={open === c.id}
                  watched={watchId === c.id}
                  onToggle={() => setOpen(open === c.id ? null : c.id)}
                  onWatch={() => onWatch(c.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CrawlRow({
  crawl,
  name,
  open,
  watched,
  onToggle,
  onWatch,
}: {
  crawl: Crawl;
  name: string;
  open: boolean;
  watched: boolean;
  onToggle: () => void;
  onWatch: () => void;
}) {
  const done = percent(crawl.done, crawl.total);
  return (
    <>
      <tr className={watched ? "watched" : undefined}>
        <td>
          {name}
          <div className="muted small">
            {crawl.mode}
            {crawl.whole_site === false ? ", custom seeds" : ""}, {ago(crawl.created_at)}
          </div>
        </td>
        <td>
          <span className={`badge ${STATUS_TONE[crawl.status]}`}>{crawl.status}</span>
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
          <span className="muted small">
            {crawl.done} / {crawl.total} ({done}%), {crawl.counts.queued} queued
          </span>
        </td>
        <td className="num">
          {crawl.counts.fetched} / {crawl.counts.unchanged}
        </td>
        <td>
          {crawl.errors > 0 ? <span className="badge bad">{crawl.errors}</span> : <span className="muted">0</span>}
        </td>
        <td className="num">
          {duration(crawl.seconds)}
          {crawl.pages_per_minute !== null && (
            <div className="muted small">{Math.round(crawl.pages_per_minute)} pages/min</div>
          )}
        </td>
        <td className="actions">
          <button type="button" className="secondary" onClick={onWatch} aria-pressed={watched} disabled={watched}>
            {watched ? "Watching" : "Watch"}
          </button>
          <button type="button" className="secondary" onClick={onToggle} aria-expanded={open}>
            {open ? "Hide" : "Details"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="details-row">
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
  if (!data) return <Loading />;
  return (
    <div>
      <ul className="counts">
        {Object.entries(data.counts).map(([status, n]) => (
          <li key={status}>
            <span className="muted">{status}</span> <strong>{n}</strong>
          </li>
        ))}
      </ul>
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
                <td className="num">{e.attempts}</td>
                <td>{e.last_error}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- admin: start a crawl

function StartCrawl({
  sites,
  health,
  crawls,
  onStarted,
}: {
  sites: Site[] | null;
  health: Health | null;
  crawls: Crawl[] | null;
  onStarted: (crawl: Crawl | null) => void;
}) {
  const notify = useToast();
  const [site, setSite] = useState("");
  const [mode, setMode] = useState<"full" | "incremental">("full");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chosen = site || sites?.[0]?.key || "";
  const name = siteName(chosen, sites);

  // The API refuses these too; the page says why before anyone clicks.
  const running = crawls?.find((c) => c.site === chosen && isActive(c) && c.whole_site !== false);
  const queueDown = health !== null && health.queue === null;
  const blocked = running
    ? `${name} is already being crawled (started ${ago(running.created_at)}). Start another when it finishes.`
    : queueDown
      ? "The job queue is not answering, so a crawl cannot be sent. Check that RabbitMQ is running."
      : null;
  const noWorkers = health?.workers?.active === 0;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (blocked || busy) return;
    setBusy(true);
    setError(null);
    try {
      const crawl = await api.startCrawl({ site: chosen, mode });
      notify("good", `Crawl started: ${siteName(crawl.site, sites)}, ${crawl.mode}.`);
      onStarted(crawl);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(`${name} is already being crawled. Start another when it finishes.`);
        onStarted(null); // show the crawl that is running
      } else {
        setError(`The crawl did not start: ${message(err)}`);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel start">
      <h2>Start a crawl</h2>
      <form className="row" onSubmit={submit}>
        <label>
          Site
          <select
            value={chosen}
            onChange={(e) => {
              setSite(e.target.value);
              setError(null);
            }}
            disabled={!sites || busy}
          >
            {sites?.map((s) => (
              <option key={s.key} value={s.key}>
                {s.name} ({s.key})
              </option>
            ))}
          </select>
        </label>
        <label>
          Mode
          <select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)} disabled={busy}>
            <option value="full">Full: download every page</option>
            <option value="incremental">Incremental: only what changed</option>
          </select>
        </label>
        <button
          type="submit"
          className="primary"
          disabled={busy || !chosen || blocked !== null || crawls === null}
          aria-describedby={blocked ? "start-blocked" : undefined}
          aria-busy={busy}
        >
          {busy ? (
            <>
              <Spinner /> Starting…
            </>
          ) : (
            "Start crawl"
          )}
        </button>
      </form>
      {blocked && (
        <p id="start-blocked" className="note warn">
          {blocked}
        </p>
      )}
      {!blocked && noWorkers && (
        <p className="note warn">No workers are running. The crawl will wait in the queue until one starts.</p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- admin: dead-letter queue

interface Letters {
  dead: DeadLetter[];
  total: number;
  retried: DeadLetter[];
  resolved: DeadLetter[];
}

function DeadLetters({ health, onRetried }: { health: Health | null; onRetried: () => void }) {
  const notify = useToast();
  const load = useCallback(async (): Promise<Letters> => {
    const [dead, retried, resolved] = await Promise.all([
      api.deadLetters({ status: "dead", limit: 20 }),
      api.deadLetters({ status: "retried", limit: 10 }),
      api.deadLetters({ status: "resolved", limit: 5 }),
    ]);
    return { dead: dead.items, total: dead.total, retried: retried.items, resolved: resolved.items };
  }, []);
  const dlq = usePolling(load, REFRESH_MS);
  const [sending, setSending] = useState<ReadonlySet<number>>(new Set());
  const [sent, setSent] = useState<ReadonlySet<number>>(new Set()); // retried from this page

  const queueDown = health !== null && health.queue === null;
  const noWorkers = health?.workers?.active === 0;

  async function retry(letter: DeadLetter) {
    if (sending.has(letter.id)) return;
    setSending((s) => new Set(s).add(letter.id));
    try {
      await api.retryDeadLetter(letter.id);
      setSent((s) => new Set(s).add(letter.id));
      notify("info", `Sent again: ${letter.url}`);
      onRetried();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        notify("info", "That job was already sent again.");
      } else {
        notify("bad", `The retry did not go out: ${message(err)}`);
      }
    } finally {
      setSending((s) => {
        const next = new Set(s);
        next.delete(letter.id);
        return next;
      });
      dlq.refresh();
    }
  }

  const data = dlq.data;
  const recent = data ? [...data.retried, ...data.resolved.filter((r) => sent.has(r.id) || isRecent(r))] : [];

  return (
    <div className="panel dlq">
      <h2>
        Dead-letter queue {data && <span className="badge violet">{data.total}</span>}
      </h2>
      <p className="muted">Jobs that failed every attempt. Retrying sends a job again with fresh attempts.</p>
      {dlq.error && <p className="error">{dlq.error}</p>}
      {queueDown && <p className="note warn">The job queue is not answering, so retries are paused.</p>}
      {!queueDown && noWorkers && (
        <p className="note warn">No workers are running. A retried job waits in the queue until one starts.</p>
      )}
      {!data ? (
        <Loading />
      ) : data.dead.length === 0 ? (
        <p className="muted">Empty: no job is waiting for a retry.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>URL</th>
                <th>Last error</th>
                <th>Attempts</th>
                <th>Failed</th>
                <th>
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.dead.map((d) => (
                <tr key={d.id}>
                  <td className="url">
                    {d.url}
                    {sent.has(d.id) && <span className="badge bad">failed again</span>}
                  </td>
                  <td>{d.last_error}</td>
                  <td className="num">{d.attempts}</td>
                  <td>{ago(d.failed_at)}</td>
                  <td className="actions">
                    <BusyButton
                      busy={sending.has(d.id)}
                      busyText="Sending…"
                      disabled={queueDown}
                      title={queueDown ? "The job queue is not answering" : undefined}
                      onClick={() => void retry(d)}
                    >
                      Retry
                    </BusyButton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {recent.length > 0 && (
        <>
          <h3>Sent again</h3>
          <ul className="retries">
            {recent.map((r) => (
              <li key={r.id}>
                {r.status === "retried" ? (
                  <span className="badge warn live">
                    <Spinner /> running again
                  </span>
                ) : (
                  <span className="badge good">recovered</span>
                )}{" "}
                <span className="url">{r.url}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/** Retried in the last 15 minutes. */
function isRecent(letter: DeadLetter): boolean {
  return letter.retried_at ? Date.now() - new Date(letter.retried_at).getTime() < 15 * 60_000 : false;
}
