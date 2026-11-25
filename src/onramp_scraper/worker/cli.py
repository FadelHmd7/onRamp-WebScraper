"""Command line for crawls, run inside a worker container.

    docker compose exec worker python -m onramp_scraper.worker.cli start testsite
    docker compose exec worker python -m onramp_scraper.worker.cli start testsite \\
        --seed http://testsite:8000/flaky/1
    docker compose exec worker python -m onramp_scraper.worker.cli status <crawl-id>
    docker compose exec worker python -m onramp_scraper.worker.cli wait <crawl-id>
    docker compose exec worker python -m onramp_scraper.worker.cli dead
    docker compose exec worker python -m onramp_scraper.worker.cli workers
    docker compose exec worker python -m onramp_scraper.worker.cli retry <dead-letter-id>
"""

import argparse
import json
import sys
import time
import uuid
from datetime import UTC, datetime

from onramp_scraper.storage.crawls import CrawlProgress, progress
from onramp_scraper.storage.dead_letters import list_dead
from onramp_scraper.worker.app import context
from onramp_scraper.worker.heartbeat import active_workers
from onramp_scraper.worker.runs import MODES, retry_dead_letter, start_crawl


def describe(p: CrawlProgress) -> dict[str, object]:
    elapsed = None
    if p.started_at is not None:
        end = p.finished_at or datetime.now(UTC)
        elapsed = round((end - p.started_at).total_seconds(), 1)
    return {
        "crawl": str(p.crawl_id),
        "site": p.site_key,
        "mode": p.mode,
        "status": p.status,
        "done": p.done,
        "total": p.total,
        "seconds": elapsed,
        **p.counts,
    }


def _progress(crawl_id: str) -> CrawlProgress:
    with context().sessions() as session:
        found = progress(session, uuid.UUID(crawl_id))
    if found is None:
        raise SystemExit(f"no crawl {crawl_id}")
    return found


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m onramp_scraper.worker.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="start a crawl and print its id")
    start.add_argument("site")
    start.add_argument("--mode", choices=MODES, default="full")
    start.add_argument("--seed", action="append", help="crawl these URLs instead of the seeds")
    start.add_argument(
        "--max-depth", type=int, help="follow links at most this deep (0: the seeds only)"
    )
    status = commands.add_parser("status", help="print a crawl's progress as JSON")
    status.add_argument("crawl_id")
    wait = commands.add_parser("wait", help="print progress until the crawl finishes")
    wait.add_argument("crawl_id")
    wait.add_argument("--every", type=float, default=2.0, help="seconds between lines")
    wait.add_argument("--timeout", type=float, default=3600.0)
    commands.add_parser("dead", help="list the dead-letter queue as JSON lines")
    commands.add_parser("workers", help="list workers with a live heartbeat as JSON lines")
    retry = commands.add_parser("retry", help="send a dead-letter URL again")
    retry.add_argument("dead_letter_id", type=int)
    args = parser.parse_args(argv)

    if args.command == "start":
        print(start_crawl(context(), args.site, args.mode, args.seed, max_depth=args.max_depth))
    elif args.command == "status":
        print(json.dumps(describe(_progress(args.crawl_id))))
    elif args.command == "wait":
        deadline = time.monotonic() + args.timeout
        while True:
            current = _progress(args.crawl_id)
            print(json.dumps(describe(current)), flush=True)
            if current.status != "running":
                return 0
            if time.monotonic() > deadline:
                return 1
            time.sleep(args.every)
    elif args.command == "dead":
        with context().sessions() as session:
            rows, _ = list_dead(session, limit=1000)
        for row in rows:
            print(
                json.dumps(
                    {
                        "id": row.id,
                        "url": row.url,
                        "attempts": row.attempts,
                        "last_status": row.last_status,
                        "last_error": row.last_error,
                        "crawl": str(row.crawl_id),
                    }
                )
            )
    elif args.command == "workers":
        for worker in active_workers(context().frontier.redis):
            print(json.dumps(worker))
    elif args.command == "retry":
        if not retry_dead_letter(context(), args.dead_letter_id):
            print(f"dead letter {args.dead_letter_id} is not in the queue", file=sys.stderr)
            return 1
        print(f"retried {args.dead_letter_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
