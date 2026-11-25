"""Command line for crawls, run inside a container.

    docker compose run --rm worker python -m onramp_scraper.worker.cli start testsite
    docker compose run --rm worker python -m onramp_scraper.worker.cli start testsite \\
        --seed http://testsite:8000/flaky/1
"""

import argparse
import sys

from onramp_scraper.worker.app import context
from onramp_scraper.worker.runs import MODES, start_crawl


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m onramp_scraper.worker.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="start a crawl and print its id")
    start.add_argument("site")
    start.add_argument("--mode", choices=MODES, default="full")
    start.add_argument("--seed", action="append", help="crawl these URLs instead of the seeds")
    args = parser.parse_args(argv)

    if args.command == "start":
        print(start_crawl(context(), args.site, args.mode, args.seed))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
