// The yard: a live picture of the crawl.
//
// Every yellow robot at the top is a real worker container (from /health).
// A worker that is fetching a page stands at the door of that page's site; an
// idle one waits at the dock, where the queue's jobs are stacked. When a worker
// changes place it walks there (a FLIP animation between the two positions).
//
// The lanes below show the watched crawl's pages, newest first: finished pages
// carry a book home, pages in the dead-letter queue wait with their letter, and
// failed pages lie where they fell. Pages that are retrying stand dizzy at the
// dock until their next attempt.

import { type RefObject, useCallback, useLayoutEffect, useRef } from "react";

import { api, type Crawl, type CrawlDetail, type CrawlUrl, type Health, type Page, type Site } from "../api";
import { ago, percent } from "../format";
import { usePolling } from "../usePolling";
import { type Mood, Robot } from "./Robot";

const REFRESH_MS = 3000;
const LANE_SIZE = 16;
const MAX_PARCELS = 30;

type Worker = NonNullable<Health["workers"]>["list"][number];

interface Feed {
  id: string;
  detail: CrawlDetail;
  done: Page<CrawlUrl>;
  dead: Page<CrawlUrl>;
  failed: Page<CrawlUrl>;
}

export function RobotYard({
  health,
  sites,
  crawls,
  watchId,
  onWatch,
}: {
  health: Health | null;
  sites: Site[];
  crawls: Crawl[];
  watchId: string | null;
  onWatch: (id: string) => void;
}) {
  const workers = health?.workers?.list ?? [];
  const bySite = new Map<string, Worker[]>();
  const atDock: Worker[] = [];
  for (const worker of [...workers].sort((a, b) => a.name.localeCompare(b.name))) {
    const site = siteOf(worker.current_url, sites);
    if (site) bySite.set(site.key, [...(bySite.get(site.key) ?? []), worker]);
    else atDock.push(worker);
  }

  const ground = useRef<HTMLDivElement>(null);
  const placement = workers
    .map((w) => `${w.name}@${siteOf(w.current_url, sites)?.key ?? "dock"}`)
    .sort()
    .join("|");
  useWalk(ground, placement);

  const feed = useFeed(watchId);
  const watched = crawls.find((c) => c.id === watchId) ?? null;
  const backlog = health?.queue?.backlog ?? 0;
  const retrying = (feed?.detail.recent_errors ?? []).filter((e) => e.status === "queued");

  return (
    <section className="yard" aria-labelledby="yard-title">
      <div className="yard-head">
        <div>
          <h2 id="yard-title">The yard</h2>
          <p className="muted">
            Each yellow robot is one worker container. It walks to the site it is reading and back
            to the dock when it has nothing to do.
          </p>
        </div>
        {crawls.length > 0 && (
          <label className="watch">
            Watching
            <select value={watchId ?? ""} onChange={(e) => onWatch(e.target.value)}>
              {crawls.map((c) => (
                <option key={c.id} value={c.id}>
                  {siteName(c.site, sites)}, {c.mode}, {c.status} ({ago(c.created_at)})
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="yard-ground" ref={ground}>
        <div className="dock">
          <div className="dock-sign">
            <h3>Dock</h3>
            <p>
              {health?.queue ? (
                <>
                  <strong>{backlog}</strong> {backlog === 1 ? "job waits" : "jobs wait"} in the queue
                </>
              ) : health ? (
                "The queue is not answering"
              ) : (
                "Checking the queue…"
              )}
            </p>
          </div>
          <div className="parcels" aria-hidden="true">
            {Array.from({ length: Math.min(backlog, MAX_PARCELS) }, (_, i) => (
              <span key={i} className="parcel" />
            ))}
            {backlog > MAX_PARCELS && <span className="parcel-more">+{backlog - MAX_PARCELS}</span>}
          </div>
          <ul className="crew" aria-label="Workers at the dock">
            {atDock.map((w) => (
              <WorkerBot key={w.name} worker={w} />
            ))}
            {retrying.map((e) => (
              <li
                key={e.url}
                className="bot"
                title={`Trying again after attempt ${e.attempts}: ${e.last_error ?? ""}\n${e.url}`}
              >
                <Robot mood="retrying" size={30} />
                <span className="bot-tag">retry</span>
              </li>
            ))}
            {health && workers.length === 0 && (
              <li className="crew-empty">No workers are running. Start one with docker compose.</li>
            )}
          </ul>
        </div>

        <div className="buildings">
          {sites.map((site) => (
            <Building
              key={site.key}
              site={site}
              crew={bySite.get(site.key) ?? []}
              crawl={crawls.find((c) => c.site === site.key && isActive(c)) ?? null}
            />
          ))}
        </div>
      </div>

      <Lanes feed={feed} crawl={watched} sites={sites} />
    </section>
  );
}

function Building({ site, crew, crawl }: { site: Site; crew: Worker[]; crawl: Crawl | null }) {
  const done = crawl ? percent(crawl.done, crawl.total) : 0;
  return (
    <article className={`building ${crew.length > 0 ? "busy" : ""}`} aria-label={site.name}>
      <div className="roof" aria-hidden="true" />
      <div className="facade">
        <h3>{site.name}</h3>
        <p className="muted">
          {site.fetch_mode === "browser" ? "Opens a real browser" : "Reads plain HTML"},{" "}
          {rate(site.requests_per_second)}
        </p>
        {crawl ? (
          <div className="building-crawl">
            <div className="progress slim" aria-hidden="true">
              <span style={{ width: `${done}%` }} />
            </div>
            <span className="muted">
              {crawl.done} of {crawl.total} pages
            </span>
          </div>
        ) : (
          <p className="muted building-quiet">No crawl running</p>
        )}
        <ul className="crew door" aria-label={`Workers at ${site.name}`}>
          {crew.map((w) => (
            <WorkerBot key={w.name} worker={w} />
          ))}
        </ul>
      </div>
    </article>
  );
}

function WorkerBot({ worker }: { worker: Worker }) {
  const busy = worker.current_url !== null;
  return (
    <li
      className="bot worker"
      data-walker={worker.name}
      title={`${worker.name}\n${worker.current_url ?? "Idle"}\n${worker.processed} jobs done`}
    >
      <Robot mood={busy ? "working" : "idle"} size={34} />
      <span className="bot-tag">{shortName(worker.name)}</span>
    </li>
  );
}

// ---------------------------------------------------------------- lanes

function Lanes({ feed, crawl, sites }: { feed: Feed | null; crawl: Crawl | null; sites: Site[] }) {
  if (!crawl) {
    return (
      <div className="lanes-empty">
        <Robot mood="idle" size={36} />
        <p>No crawl yet. When one starts, its pages line up here as they finish, wait or fail.</p>
      </div>
    );
  }
  const done = percent(crawl.done, crawl.total);
  return (
    <div className="lanes">
      <div className="lanes-head">
        <p>
          <strong>{siteName(crawl.site, sites)}</strong>, {crawl.mode} crawl, {crawl.status}
          {crawl.whole_site === false && " (custom seeds)"}
        </p>
        <div
          className="progress"
          role="progressbar"
          aria-valuenow={done}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Watched crawl progress"
        >
          <span style={{ width: `${done}%` }} />
        </div>
        <p className="muted">
          {crawl.done} of {crawl.total} pages done ({done}%), {crawl.counts.queued} still queued
        </p>
      </div>
      <Lane
        tone="done"
        title="Finished"
        mood="done"
        page={feed?.done ?? null}
        note={crawl.counts.skipped > 0 ? `${crawl.counts.skipped} more skipped by robots.txt or rules` : null}
        empty="No page has finished yet."
      />
      <Lane
        tone="dlq"
        title="Dead-letter queue"
        mood="waiting"
        page={feed?.dead ?? null}
        note="Failed every attempt. An admin can send them again."
        empty="Nobody is waiting here."
      />
      <Lane
        tone="fail"
        title="Failed"
        mood="dead"
        page={feed?.failed ?? null}
        note="Permanent errors, such as a missing page."
        empty="No page has failed."
      />
    </div>
  );
}

function Lane({
  tone,
  title,
  mood,
  page,
  note,
  empty,
}: {
  tone: "done" | "dlq" | "fail";
  title: string;
  mood: Mood;
  page: Page<CrawlUrl> | null;
  note: string | null;
  empty: string;
}) {
  const more = page ? page.total - page.items.length : 0;
  return (
    <div className={`lane lane-${tone}`}>
      <div className="lane-label">
        <h3>{title}</h3>
        <span className="lane-count" aria-label={`${title}: ${page ? page.total : "loading"}`}>
          {page ? page.total : "–"}
        </span>
        {note && <p className="muted">{note}</p>}
      </div>
      {page === null ? (
        <p className="muted lane-empty">Loading…</p>
      ) : page.items.length === 0 ? (
        <p className="muted lane-empty">{empty}</p>
      ) : (
        <ul className="lane-bots" aria-label={title}>
          {page.items.map((u) => (
            <li key={u.url} className="bot" title={describe(u)}>
              <Robot mood={mood} size={28} />
              <span className="visually-hidden">{describe(u)}</span>
            </li>
          ))}
          {more > 0 && <li className="lane-more">+{more}</li>}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- data

function useFeed(id: string | null): Feed | null {
  const load = useCallback(async (): Promise<Feed | null> => {
    if (!id) return null;
    const [detail, done, dead, failed] = await Promise.all([
      api.crawl(id),
      api.crawlUrls(id, ["fetched", "unchanged"], LANE_SIZE),
      api.crawlUrls(id, ["dead"], LANE_SIZE),
      api.crawlUrls(id, ["failed"], LANE_SIZE),
    ]);
    return { id, detail, done, dead, failed };
  }, [id]);
  const { data } = usePolling(load, REFRESH_MS);
  return data && data.id === id ? data : null; // never show another crawl's pages
}

/**
 * Walk: when an element marked data-walker changes place between renders, it
 * slides from the old place to the new one instead of jumping (FLIP). Skipped
 * when the reader prefers reduced motion.
 */
function useWalk(container: RefObject<HTMLElement | null>, placement: string) {
  const last = useRef(new Map<string, DOMRect>());
  useLayoutEffect(() => {
    const root = container.current;
    if (!root) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
    const now = new Map<string, DOMRect>();
    root.querySelectorAll<HTMLElement>("[data-walker]").forEach((el) => {
      const key = el.dataset.walker ?? "";
      const rect = el.getBoundingClientRect();
      now.set(key, rect);
      const before = last.current.get(key);
      if (still || !before || typeof el.animate !== "function") return;
      const dx = before.left - rect.left;
      const dy = before.top - rect.top;
      if (Math.abs(dx) + Math.abs(dy) < 4) return;
      el.classList.add("walking");
      const walk = el.animate(
        [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "translate(0, 0)" }],
        { duration: 1300, easing: "cubic-bezier(.45, 0, .25, 1)" },
      );
      const stop = () => el.classList.remove("walking");
      walk.onfinish = stop;
      walk.oncancel = stop;
    });
    last.current = now;
  }, [container, placement]);
}

// ---------------------------------------------------------------- helpers

function siteOf(url: string | null, sites: Site[]): Site | undefined {
  if (!url) return undefined;
  const host = hostOf(url);
  return sites.find((s) => hostOf(s.base_url) === host);
}

function hostOf(url: string): string | null {
  try {
    return new URL(url).host;
  } catch {
    return null;
  }
}

function siteName(key: string, sites: Site[]): string {
  return sites.find((s) => s.key === key)?.name ?? key;
}

function isActive(crawl: Crawl): boolean {
  return crawl.status === "running" || crawl.status === "queued";
}

function rate(perSecond: number): string {
  if (perSecond >= 1) return `${perSecond} requests a second`;
  return `one request every ${Math.round(1 / perSecond)} s`;
}

/** "celery@4f2a9c1b77e0" -> "4f2a9c". */
function shortName(name: string): string {
  return (name.split("@").pop() ?? name).slice(0, 6);
}

function describe(u: CrawlUrl): string {
  const error = u.last_error ? `: ${u.last_error}` : "";
  const tries = u.attempts > 1 ? ` after ${u.attempts} attempts` : "";
  return `${u.url}${error}${tries}`;
}
