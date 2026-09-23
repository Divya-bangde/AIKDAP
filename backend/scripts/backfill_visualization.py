"""Backfill chunk positions, paper OpenAlex data and source mix.

Runs `app.workers.backfill.run_backfill` in-process (no worker needed).
Idempotent and resumable: re-running only touches records still missing
data. To run it on a worker instead, enqueue
`workers.backfill_visualization_data`.

Usage (from backend/):
    python -m scripts.backfill_visualization [--batch-size 50]
        [--skip-positions] [--skip-papers] [--skip-source-mix]
"""

import argparse
import asyncio
import json
import sys

if sys.platform == "win32":
    # psycopg's async driver needs the selector loop (see database/session.py).
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import app.workers.celery_app  # noqa: E402,F401 - registers every model
from app.workers.backfill import run_backfill  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--skip-positions", action="store_true")
    parser.add_argument("--skip-papers", action="store_true")
    parser.add_argument("--skip-source-mix", action="store_true")
    args = parser.parse_args()

    report = asyncio.run(
        run_backfill(
            batch_size=args.batch_size,
            positions=not args.skip_positions,
            papers=not args.skip_papers,
            source_mix=not args.skip_source_mix,
        )
    )
    print(json.dumps(report.as_dict(), indent=2))


if __name__ == "__main__":
    main()
