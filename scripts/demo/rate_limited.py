"""Demo 3: the site answers 429 Too Many Requests with Retry-After.

    uv run python scripts/demo/rate_limited.py

The page answers 429 with ``Retry-After: 10`` until 10 seconds have passed.
Expected: the job is retried after 10 seconds, not sooner (the backoff alone
would be about 2 s), and the second attempt stores the page. The rate limiter
also pauses the whole host for those 10 s, so no other worker hits it either.
"""

import sys

import _demo as demo

WAIT = 10
URL = f"{demo.TESTSITE}/throttle/3?retry_after={WAIT}"


def main() -> int:
    demo.title("429 with Retry-After")
    demo.ensure_workers(1)
    demo.reset_testsite()
    started = demo.now()
    crawl_id = demo.start_crawl(URL)
    demo.step(f"started crawl {crawl_id} of {URL}")

    state = demo.wait_crawl(crawl_id, timeout=120)
    row = demo.url_row(crawl_id, URL)
    lines = [line for line in demo.worker_log_lines("/throttle/3", since=started)]
    for line in lines:
        if "attempt" in line or "fetched" in line:
            demo.step(line.split("] ", 1)[-1])
    demo.step(f"crawl {state['status']} after {state['seconds']} s: {row}")

    return demo.verdict(
        {
            "the server's 429 was seen": any("HTTP 429" in line for line in lines),
            f"the retry waited {WAIT} s as asked": any(
                f"retry in {WAIT}.0s" in line for line in lines
            ),
            f"the crawl took at least {WAIT} s": (state["seconds"] or 0) >= WAIT,
            "the second attempt stored the page": row.get("status") in ("fetched", "unchanged")
            and row.get("attempts") == "2",
        }
    )


if __name__ == "__main__":
    sys.exit(main())
