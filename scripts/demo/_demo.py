"""Shared helpers for the failure demos. Standard library only; runs on the host.

Each demo drives the running compose stack (``docker compose up``): it starts
a small crawl of the test site through the worker CLI, causes one failure,
watches what happens, and ends with PASS or FAIL and the evidence.
"""

import json
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TESTSITE_HOST = "http://127.0.0.1:9050"  # published port of the test site
TESTSITE = "http://testsite:8000"  # the same site as workers see it
CLI = ["python", "-m", "onramp_scraper.worker.cli"]


def compose(*args: str, check: bool = True, stdin: str | None = None, strip: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 (fixed docker command, no user input)
        ["docker", "compose", *args],  # noqa: S607
        cwd=ROOT,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if check and result.returncode != 0:
        sys.exit(f"docker compose {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip() if strip else result.stdout


def docker(*args: str) -> str:
    result = subprocess.run(  # noqa: S603
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def cli(*args: str) -> str:
    """Run the crawl CLI inside a running worker container (any one: a demo may kill some)."""
    running = compose("ps", "-q", "worker").splitlines()
    if not running:
        sys.exit("no worker container is running: start the stack with docker compose up")
    return docker("exec", running[0], *CLI, *args)


def sql(query: str, **params: str) -> list[list[str]]:
    """Rows of a query. Values go in as psql variables (``:'url'``), quoted by psql."""
    variables = [arg for name, value in params.items() for arg in ("-v", f"{name}={value}")]
    psql = ["psql", "-U", "scraper", "-tA", "-F", "\t", *variables]
    # Not stripped: an empty last column is a trailing tab that must stay.
    out = compose("exec", "-T", "postgres", *psql, stdin=query, strip=False)
    return [line.split("\t") for line in out.splitlines() if line]


def workers() -> list[dict[str, Any]]:
    return [json.loads(line) for line in cli("workers").splitlines() if line]


def reset_testsite() -> None:
    url = f"{TESTSITE_HOST}/_admin/reset"  # a fixed local http URL
    request = urllib.request.Request(url, method="POST")  # noqa: S310
    with urllib.request.urlopen(request, timeout=5):  # noqa: S310
        pass


def ensure_workers(minimum: int) -> int:
    """At least ``minimum`` worker containers. Returns how many there were before."""
    running = len(compose("ps", "-q", "worker").splitlines())
    if running < minimum:
        step(f"scaling workers from {running} to {minimum}")
        compose("up", "-d", "--scale", f"worker={minimum}", "--no-recreate", "worker")
        wait_for(lambda: len(workers()) >= minimum, 60, "workers to report a heartbeat")
    return running


def now() -> str:
    """A timestamp for ``docker compose logs --since``."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def start_crawl(*seeds: str, max_depth: int = 0) -> str:
    args = ["start", "testsite", "--max-depth", str(max_depth)]
    for seed in seeds:
        args += ["--seed", seed]
    return cli(*args)


def wait_crawl(crawl_id: str, timeout: float = 600) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        state = json.loads(cli("status", crawl_id))
        if state["status"] != "running" or time.monotonic() > deadline:
            return state
        time.sleep(2)


def wait_for(condition: Any, timeout: float, what: str) -> float:
    """Poll until ``condition()`` is true. Returns the seconds it took."""
    started = time.monotonic()
    while not condition():
        if time.monotonic() - started > timeout:
            sys.exit(f"timed out after {timeout:.0f}s waiting for {what}")
        time.sleep(0.5)
    return time.monotonic() - started


def url_row(crawl_id: str, url: str) -> dict[str, str]:
    rows = sql(
        "SELECT status, attempts, coalesce(last_error, '') FROM crawl_urls "
        "WHERE crawl_id = :'crawl' AND url = :'url'",
        crawl=crawl_id,
        url=url,
    )
    if not rows:
        return {}
    status, attempts, error = rows[0]
    return {"status": status, "attempts": attempts, "last_error": error}


def worker_log_lines(pattern: str, since: str) -> list[str]:
    """Worker log lines containing ``pattern``, in time order across all workers.

    ``docker compose logs`` groups lines by container; each Celery line starts
    with its timestamp after the "worker-N  | " prefix, so sorting on that
    text puts them back in the order they happened.
    """
    logs = compose("logs", "--since", since, "worker", check=False)
    found = [line for line in logs.splitlines() if pattern in line]
    return sorted(found, key=lambda line: line.split("| ", 1)[-1])


_steps = 0


def title(text: str) -> None:
    print(f"\n=== {text} ===\n")


def step(text: str) -> None:
    global _steps
    _steps += 1
    print(f"[{_steps}] {text}", flush=True)


def verdict(checks: dict[str, bool]) -> int:
    print()
    for name, ok in checks.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    passed = all(checks.values())
    print(f"\n{'PASS' if passed else 'FAIL'}\n")
    return 0 if passed else 1
