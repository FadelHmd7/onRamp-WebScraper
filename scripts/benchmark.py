"""Benchmark: the same 630 test-site pages with 1, 2, 4 (and 8) workers.

    docker compose up -d --build
    uv run python scripts/benchmark.py              # 1, 2, 4, 8 workers, 3 runs each
    uv run python scripts/benchmark.py --workers 1 2 4 --runs 1

Before every run the test site's pages, versions and crawls are deleted, so
each run does the same work: fetch, clean, validate and store 630 new pages.
The real sites' data is never touched. After each scale change a small
warm-up crawl runs first (not recorded). While a run goes, CPU use of the
test site, PostgreSQL and the workers is sampled about once a second.

Run it on an otherwise idle machine: Docker Desktop shares the host's CPUs,
so anything else running shows up as noise in the timings.

Writes docs/phase1/benchmark/results.csv (every run) and chart.svg (median
pages per minute against the ideal linear speedup). The analysis is in
docs/phase1/BENCHMARK.md.
"""

import argparse
import csv
import json
import statistics
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "demo"))
import _demo as demo

OUT = demo.ROOT / "docs" / "phase1" / "benchmark"
PAGES = 630

FORGET_TESTSITE = """
DELETE FROM document_versions WHERE page_id IN (SELECT id FROM pages WHERE site_key = 'testsite');
DELETE FROM fetches WHERE page_id IN (SELECT id FROM pages WHERE site_key = 'testsite');
DELETE FROM crawls WHERE site_key = 'testsite';
DELETE FROM pages WHERE site_key = 'testsite';
"""


def scale(workers: int) -> None:
    demo.compose("up", "-d", "--scale", f"worker={workers}", "--no-recreate", "worker")
    demo.wait_for(lambda: len(demo.workers()) == workers, 120, f"{workers} heartbeats")
    # Warm-up, not recorded: new containers import code, open connections and
    # load robots.txt on their first jobs. A small crawl does that for each one.
    warmup = demo.cli("start", "testsite", "--max-depth", "1")
    demo.wait_crawl(warmup, timeout=300)


class CpuSampler(threading.Thread):
    """Average CPU % per service while a run goes (docker stats, once a second)."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.samples: dict[str, list[float]] = {}
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.is_set():
            out = demo.docker("stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}")
            for line in out.splitlines():
                name, cpu = line.split("\t")
                service = name.removeprefix("onramp-scraper-").rsplit("-", 1)[0]
                self.samples.setdefault(service, []).append(float(cpu.rstrip("%")))

    def average(self, service: str) -> float:
        values = self.samples.get(service, [])
        return round(statistics.mean(values), 1) if values else 0.0


def one_run(workers: int) -> dict[str, object]:
    demo.sql(FORGET_TESTSITE)
    sampler = CpuSampler()
    sampler.start()
    crawl_id = demo.cli("start", "testsite")
    state = demo.wait_crawl(crawl_id, timeout=1200)
    sampler.stop.set()
    sampler.join()
    seconds = float(state["seconds"])
    done = int(state["done"])
    # docker stats samples each worker container separately: sum them per sample.
    worker_cpu = sampler.average("worker") * workers
    return {
        "workers": workers,
        "pages": done,
        "fetched": state["fetched"],
        "seconds": round(seconds, 1),
        "pages_per_minute": round(done / seconds * 60, 1),
        "testsite_cpu": sampler.average("testsite"),
        "postgres_cpu": sampler.average("postgres"),
        "workers_cpu": round(worker_cpu, 1),
        "status": state["status"],
    }


def chart(rows: list[dict[str, object]]) -> str:
    """A bar chart of median pages/minute, with the ideal linear speedup as a line."""
    by_workers: dict[int, list[float]] = {}
    for row in rows:
        by_workers.setdefault(int(row["workers"]), []).append(float(row["pages_per_minute"]))
    counts = sorted(by_workers)
    medians = [statistics.median(by_workers[n]) for n in counts]
    ideal = [medians[0] * n / counts[0] for n in counts]
    top = max([*medians, *ideal]) * 1.15
    width, height, left, bottom, top_pad = 640, 380, 70, 50, 40
    plot_w, plot_h = width - left - 30, height - bottom - top_pad
    slot = plot_w / len(counts)

    def y(value: float) -> float:
        return top_pad + plot_h - value / top * plot_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        'font-family="system-ui, sans-serif" font-size="13">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width / 2}" y="22" text-anchor="middle" font-size="15" font-weight="600">'
        f"Crawl throughput: {PAGES} test-site pages (median of runs)</text>",
    ]
    for tick in range(6):
        value = top / 5 * tick
        parts.append(
            f'<line x1="{left}" x2="{width - 30}" y1="{y(value):.1f}" y2="{y(value):.1f}" '
            'stroke="#e5e7eb"/>'
            f'<text x="{left - 8}" y="{y(value) + 4:.1f}" text-anchor="end" fill="#6b7280">'
            f"{value:.0f}</text>"
        )
    points = []
    for i, (n, med, ide) in enumerate(zip(counts, medians, ideal, strict=True)):
        cx = left + slot * i + slot / 2
        bar_w = slot * 0.5
        parts.append(
            f'<rect x="{cx - bar_w / 2:.1f}" y="{y(med):.1f}" width="{bar_w:.1f}" '
            f'height="{y(0) - y(med):.1f}" fill="#2563eb"/>'
            f'<text x="{cx:.1f}" y="{y(med) - 6:.1f}" text-anchor="middle" font-weight="600">'
            f"{med:.0f}</text>"
            f'<text x="{cx:.1f}" y="{height - bottom + 20}" text-anchor="middle">'
            f"{n} worker{'s' if n > 1 else ''}</text>"
        )
        points.append(f"{cx:.1f},{y(ide):.1f}")
    parts.append(
        f'<polyline points="{" ".join(points)}" fill="none" stroke="#9ca3af" '
        'stroke-width="2" stroke-dasharray="6 4"/>'
    )
    parts.append(
        f'<text x="{left}" y="{height - 8}" fill="#6b7280">Bars: pages per minute. '
        "Dashed line: ideal linear speedup from 1 worker.</text>"
    )
    parts.append(
        f'<text transform="translate(18 {top_pad + plot_h / 2}) rotate(-90)" '
        'text-anchor="middle" fill="#6b7280">pages per minute</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()

    demo.title(f"Benchmark: {PAGES} pages, workers {args.workers}, {args.runs} runs each")
    before = len(demo.compose("ps", "-q", "worker").splitlines())
    rows: list[dict[str, object]] = []
    try:
        for workers in args.workers:
            scale(workers)
            for run in range(1, args.runs + 1):
                row = {"run": run, **one_run(workers)}
                rows.append(row)
                demo.step(json.dumps(row))
    finally:
        demo.step(f"scaling back to {max(before, 1)} workers")
        demo.compose("up", "-d", "--scale", f"worker={max(before, 1)}", "--no-recreate", "worker")

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (OUT / "chart.svg").write_text(chart(rows), encoding="utf-8", newline="\n")
    demo.step(f"wrote {OUT / 'results.csv'} and {OUT / 'chart.svg'}")
    incomplete = [r for r in rows if r["status"] != "finished" or r["pages"] != PAGES]
    return 1 if incomplete else 0


if __name__ == "__main__":
    sys.exit(main())
