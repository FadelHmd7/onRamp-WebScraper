"""Run the four failure demos in order and summarise. About three minutes.

    docker compose up -d --build --scale worker=4
    uv run python scripts/demo/run_all.py

Each demo can also be run on its own:

    kill_worker.py   a worker is killed mid-task; another one finishes the job
    timeout.py       a request times out; the retry succeeds
    rate_limited.py  a 429 with Retry-After is waited out exactly
    always_fail.py   a page fails every attempt; dead-letter queue, admin retry
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMOS = ["kill_worker.py", "timeout.py", "rate_limited.py", "always_fail.py"]


def main() -> int:
    results = {}
    for demo in DEMOS:
        code = subprocess.run([sys.executable, str(HERE / demo)], check=False).returncode  # noqa: S603
        results[demo] = code == 0
    print("=== Summary ===\n")
    for demo, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {demo}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
