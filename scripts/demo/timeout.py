"""Demo 2: a request that times out.

    uv run python scripts/demo/timeout.py

The page takes 40 s to answer the first time; the fetcher gives up after its
30 s read timeout. Expected: attempt 1 fails with a ReadTimeout, the job is
sent again after a backoff, attempt 2 gets a fast answer and stores the page.
Takes about 35 seconds.
"""

import sys

import _demo as demo

URL = f"{demo.TESTSITE}/slow/2?delay=40&times=1"


def main() -> int:
    demo.title("A request that times out")
    demo.ensure_workers(1)
    demo.reset_testsite()
    started = demo.now()
    crawl_id = demo.start_crawl(URL)
    demo.step(f"started crawl {crawl_id} of {URL} (40 s the first time only)")
    demo.step("waiting: the fetcher's read timeout is 30 s")

    state = demo.wait_crawl(crawl_id, timeout=180)
    row = demo.url_row(crawl_id, URL)
    retries = demo.worker_log_lines("/slow/2", since=started)
    for line in retries:
        if "attempt" in line or "fetched" in line:
            demo.step(line.split("] ", 1)[-1])
    demo.step(f"crawl {state['status']}: {row}")

    return demo.verdict(
        {
            "attempt 1 timed out": any("timeout: ReadTimeout" in line for line in retries),
            "the retry stored the page": row.get("status") in ("fetched", "unchanged"),
            "it took exactly two attempts": row.get("attempts") == "2",
        }
    )


if __name__ == "__main__":
    sys.exit(main())
