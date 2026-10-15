"""
Windows-friendly queue worker — runs the real Mongo task queue without uvicorn.

Listens to the same Mongo "tasks" collection, claims tasks, runs the same
handlers (create estimate, invoice lookup, pricing, ...) and sends the same
callbacks to the main server as the FastAPI app does. It just calls
queue_service.start_queue_workers() directly instead of through the app's
startup event.

Why: uvicorn (with --reload) installs WindowsSelectorEventLoopPolicy on
Windows. sync_playwright() then creates a Selector loop in the worker thread,
which cannot spawn the browser subprocess and fails with NotImplementedError.
This script forces the Proactor loop policy, which supports subprocesses.

Usage:
    python test_queue_worker.py
    python test_queue_worker.py --env-file .env.staging
    python test_queue_worker.py --concurrency 1 --headed

Stop with Ctrl+C. A task that is mid-run when you stop keeps running until its
browser flow finishes; if the process is killed instead, the task is picked up
again by stale-task recovery (QUEUE_PROCESSING_STALE_SECONDS).

Note: this does not serve the HTTP API (/enqueue-task). New tasks must be
inserted into Mongo by the main server or another running instance; this
worker polls Mongo on QUEUE_POLL_INTERVAL_SECONDS.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Mongo task queue workers (Windows-friendly).")
    parser.add_argument(
        "--env-file",
        default=".env.local",
        help="Env file to load before the app settings (default: .env.local, same as run.sh)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Override QUEUE_WORKER_CONCURRENCY (e.g. 1 to run one task at a time)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser (sets PRINTSMITH_HEADLESS=false)",
    )
    return parser.parse_args()


def _load_environment(args: argparse.Namespace) -> None:
    # Must run before any app import: app.v1.core.settings and bot.config read
    # os.environ at import time. Mirrors uvicorn's --env-file in run.sh.
    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = PROJECT_ROOT / env_path
    if env_path.is_file():
        load_dotenv(env_path, override=True)
        print(f"[QueueWorker] Loaded env file: {env_path}")
    else:
        print(f"[QueueWorker] Env file not found, using .env / OS environment only: {env_path}")

    if args.concurrency is not None:
        os.environ["QUEUE_WORKER_CONCURRENCY"] = str(max(args.concurrency, 1))
    if args.headed:
        os.environ["PRINTSMITH_HEADLESS"] = "false"


def _configure_logging() -> None:
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


async def _run_workers() -> None:
    from app.db.mongo import get_client
    from app.v1.core.settings import MACHINE_NAME, MONGO_DB, QUEUE_WORKER_CONCURRENCY
    from app.v1.modules.bot.services.queue_service import (
        WORKER_ID,
        start_queue_workers,
        stop_queue_workers,
    )

    mongo_client = get_client()
    print(
        f"[QueueWorker] Starting worker_id={WORKER_ID} machine={MACHINE_NAME or '-'} "
        f"db={MONGO_DB} concurrency={QUEUE_WORKER_CONCURRENCY}"
    )
    await start_queue_workers(mongo_client)
    print("[QueueWorker] Listening for tasks. Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    finally:
        print("[QueueWorker] Stopping workers...")
        await stop_queue_workers()
        mongo_client.close()
        print("[QueueWorker] Stopped.")


def main() -> None:
    args = _parse_args()
    _load_environment(args)
    _configure_logging()

    if sys.platform == "win32":
        # Proactor is the only Windows loop that supports subprocesses, which
        # Playwright needs to launch the browser. sync_playwright() creates
        # its own loop in the worker thread from this same policy.
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    try:
        asyncio.run(_run_workers())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
