import asyncio
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List

from fastapi.concurrency import run_in_threadpool

from app.v1.common.storage_service import upload_bytes_to_storage
from app.v1.core.settings import (
    BOT_LOG_ARCHIVE_RUN_HOUR,
    BOT_LOG_ARCHIVE_RUN_MINUTE,
    BOT_LOG_LOCAL_DIR,
    BOT_LOG_STORAGE_ROOT,
)

logger = logging.getLogger(__name__)


def _target_log_date(today: date | None = None) -> date:
    return (today or datetime.now().date()) - timedelta(days=1)


def _parse_log_date(path: Path) -> date | None:
    stem = path.stem
    if not stem.startswith("bot_log_"):
        return None

    parts = stem.split("_")
    if len(parts) < 3:
        return None

    raw_date = parts[2]
    if len(raw_date) != 8 or not raw_date.isdigit():
        return None

    try:
        return datetime.strptime(raw_date, "%Y%m%d").date()
    except ValueError:
        return None


def _list_logs_for_date(target_date: date) -> List[Path]:
    log_dir = Path(BOT_LOG_LOCAL_DIR)
    if not log_dir.exists():
        return []

    return sorted(
        path
        for path in log_dir.glob("bot_log_*.csv")
        if path.is_file() and _parse_log_date(path) == target_date
    )


def archive_logs_for_date(target_date: date | None = None) -> Dict[str, Any]:
    archive_date = target_date or _target_log_date()
    candidates = _list_logs_for_date(archive_date)
    uploaded: List[str] = []
    deleted: List[str] = []
    failed: List[Dict[str, str]] = []

    if not candidates:
        logger.info("Log archive found no files for date=%s", archive_date.isoformat())
        return {
            "status": "success",
            "date": archive_date.isoformat(),
            "uploaded_count": 0,
            "deleted_count": 0,
            "failed_count": 0,
            "uploaded_files": uploaded,
            "deleted_files": deleted,
            "failed_files": failed,
        }

    for path in candidates:
        storage_key = f"{BOT_LOG_STORAGE_ROOT.rstrip('/')}/{path.name}"
        try:
            upload_bytes_to_storage(
                key=storage_key,
                content=path.read_bytes(),
                content_type="text/csv",
                metadata={
                    "archive_date": archive_date.isoformat(),
                    "source": "psvbot",
                },
            )
            uploaded.append(storage_key)
            path.unlink()
            deleted.append(str(path))
            logger.info("Archived bot log to GCS and deleted local file path=%s key=%s", path, storage_key)
        except Exception as exc:
            logger.exception("Failed to archive bot log path=%s key=%s", path, storage_key)
            failed.append(
                {
                    "path": str(path),
                    "key": storage_key,
                    "error": str(exc),
                }
            )

    return {
        "status": "success" if not failed else "partial_success",
        "date": archive_date.isoformat(),
        "uploaded_count": len(uploaded),
        "deleted_count": len(deleted),
        "failed_count": len(failed),
        "uploaded_files": uploaded,
        "deleted_files": deleted,
        "failed_files": failed,
    }


def archive_previous_day_logs() -> Dict[str, Any]:
    return archive_logs_for_date(_target_log_date())


def seconds_until_next_archive_run(now: datetime | None = None) -> float:
    current = now or datetime.now()
    next_run = datetime.combine(
        current.date(),
        time(hour=BOT_LOG_ARCHIVE_RUN_HOUR, minute=BOT_LOG_ARCHIVE_RUN_MINUTE),
    )
    if next_run <= current:
        next_run += timedelta(days=1)
    return max((next_run - current).total_seconds(), 1.0)


async def run_daily_log_archive_forever() -> None:
    while True:
        sleep_seconds = seconds_until_next_archive_run()
        logger.info("Next bot log archive run scheduled in %.0fs", sleep_seconds)
        await asyncio.sleep(sleep_seconds)
        try:
            result = await run_in_threadpool(archive_previous_day_logs)
            logger.info("Bot log archive completed result=%s", result)
        except Exception:
            logger.exception("Bot log archive task failed")
