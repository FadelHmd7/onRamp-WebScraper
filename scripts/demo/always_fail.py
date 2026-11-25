"""Demo 4: a page that always fails, the dead-letter queue, and an admin retry.

    uv run python scripts/demo/always_fail.py

The page answers 503 forever. Expected: MAX_FETCH_ATTEMPTS attempts with
growing, jittered delays, then a dead-letter row with the URL, last error and
attempt count, while the crawl still finishes. An admin retry sends it again;
it still fails, so it returns to the dead-letter queue. Takes about a minute.
"""

import sys

import _demo as demo

URL = f"{demo.TESTSITE}/broken/4"


def dead_row() -> dict[str, str]:
    rows = demo.sql(
        "SELECT id, status, attempts, coalesce(last_status::text, ''), last_error "
        "FROM dead_letters WHERE url = :'url' ORDER BY failed_at DESC LIMIT 1",
        url=URL,
    )
    if not rows:
        return {}
    keys = ("id", "status", "attempts", "last_status", "last_error")
    return dict(zip(keys, rows[0], strict=True))


def main() -> int:
    demo.title("A page that always fails")
    demo.ensure_workers(1)
    started = demo.now()
    crawl_id = demo.start_crawl(URL)
    demo.step(f"started crawl {crawl_id} of {URL}")

    state = demo.wait_crawl(crawl_id, timeout=300)
    for line in demo.worker_log_lines("/broken/4", since=started):
        if "attempt" in line or "dead after" in line:
            demo.step(line.split("] ", 1)[-1])
    dead = dead_row()
    demo.step(f"crawl {state['status']}; dead-letter row: {dead}")
    first = {
        "the crawl finished anyway": state["status"] == "finished",
        "the URL is in the dead-letter queue": dead.get("status") == "dead",
        "with its last error and status": dead.get("last_error") == "HTTP 503"
        and dead.get("last_status") == "503",
    }

    demo.step(f"admin retry of dead letter {dead.get('id')}")
    demo.cli("retry", dead["id"])
    demo.step(f"while it runs again the row is '{dead_row().get('status')}'")
    again = demo.wait_crawl(crawl_id, timeout=300)
    back = dead_row()
    demo.step(f"crawl {again['status']} again; dead-letter row: {back}")

    return demo.verdict(
        {
            **first,
            "after the retry fails it is back in the queue": back.get("status") == "dead",
        }
    )


if __name__ == "__main__":
    sys.exit(main())
