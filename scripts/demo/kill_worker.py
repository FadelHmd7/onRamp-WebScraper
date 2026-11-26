"""Demo 1: kill a worker in the middle of a job.

    uv run python scripts/demo/kill_worker.py

A worker takes a slow page (15 s), and we `docker kill` its container while
it waits. Expected:

- RabbitMQ sees the connection drop and gives the unacknowledged job to
  another worker (acks_late): at once if the dead connection closed, or
  within about 15 s through missed AMQP heartbeats if it vanished silently;
- the page is stored exactly once, and the crawl finishes;
- the dead worker's heartbeat expires, so "active workers" drops by one.

The killed container is started again at the end.
"""

import sys
import time

import _demo as demo

URL = f"{demo.TESTSITE}/slow/1?delay=15"


def main() -> int:
    demo.title("Kill a worker mid-task")
    before = demo.ensure_workers(2)
    try:
        return run()
    finally:
        demo.step("starting the killed container again")
        demo.compose("up", "-d", "--scale", f"worker={max(before, 2)}", "--no-recreate", "worker")


def run() -> int:
    demo.reset_testsite()
    alive_before = len(demo.workers())
    demo.step(f"{alive_before} workers have a live heartbeat")

    crawl_id = demo.start_crawl(URL)
    demo.step(f"started crawl {crawl_id} of {URL}")

    def holder(other_than: str = "") -> dict[str, str] | None:
        """A worker whose heartbeat says it is fetching URL.

        A killed worker's heartbeat stays visible, still showing the URL, until
        its TTL runs out; ``other_than`` skips it so the rescuer can be seen.
        """
        return next(
            (w for w in demo.workers() if w["current_url"] == URL and w["name"] != other_than),
            None,
        )

    demo.wait_for(lambda: holder() is not None, 30, "a worker to take the job")
    victim = holder()
    if victim is None:
        sys.exit("the job finished before it could be killed; run the demo again")
    demo.step(f"worker {victim['name']} is fetching the page; killing its container now")
    demo.docker("kill", victim["name"])  # the hostname is the container id
    killed_at = time.monotonic()

    demo.wait_for(
        lambda: holder(other_than=victim["name"]) is not None,
        90,
        "another worker to take the job over",
    )
    taken_over = time.monotonic() - killed_at
    rescuer = holder(other_than=victim["name"])
    if rescuer is None:
        sys.exit("the job finished before it could be observed; run the demo again")
    demo.step(f"worker {rescuer['name']} took the job over {taken_over:.1f}s after the kill")

    state = demo.wait_crawl(crawl_id, timeout=120)
    row = demo.url_row(crawl_id, URL)
    demo.step(f"crawl {state['status']}: {row}")
    versions = demo.sql(
        "SELECT count(*) FROM document_versions v JOIN pages p ON p.id = v.page_id "
        "WHERE p.url = :'url'",
        url=URL,
    )[0][0]
    fetches = demo.sql(
        "SELECT count(*) FROM fetches f JOIN pages p ON p.id = f.page_id "
        "WHERE f.crawl_id = :'crawl' AND p.url = :'url'",
        crawl=crawl_id,
        url=URL,
    )[0][0]

    gone = demo.wait_for(
        lambda: all(w["name"] != victim["name"] for w in demo.workers()), 30, "the heartbeat"
    )
    demo.step(f"the killed worker's heartbeat expired {gone:.0f}s later")
    alive_after = len(demo.workers())

    return demo.verdict(
        {
            "another worker took the job over within 30 s": taken_over < 30,
            "the crawl finished": state["status"] == "finished",
            "the page was stored (once)": row.get("status") in ("fetched", "unchanged")
            and versions == "1"
            and fetches == "1",
            "active workers dropped by one": alive_after == alive_before - 1,
        }
    )


if __name__ == "__main__":
    sys.exit(main())
