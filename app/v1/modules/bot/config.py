import os
from pathlib import Path


def _to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


HEADLESS = _to_bool(os.getenv("PRINTSMITH_HEADLESS", "true"))
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("PRINTSMITH_TIMEOUT_SECONDS", "120"))
PAGE_LOAD_TIMEOUT_SECONDS = int(os.getenv("PRINTSMITH_PAGE_LOAD_TIMEOUT_SECONDS", "60"))
RECOVERY_HOME_LOAD_TIMEOUT_SECONDS = int(
    os.getenv("PRINTSMITH_RECOVERY_HOME_LOAD_TIMEOUT_SECONDS", "120")
)
KEEP_BROWSER_OPEN = _to_bool(os.getenv("PRINTSMITH_KEEP_BROWSER_OPEN", "false"))
DEBUG = _to_bool(os.getenv("PRINTSMITH_DEBUG", "true"))
QUOTE_SUMMARY_STORAGE_ROOT = (
    os.getenv("PRINTSMITH_QUOTE_SUMMARY_STORAGE_ROOT", "estimates").strip()
    or "estimates"
)
ESTIMATE_HISTORY_STORAGE_ROOT = (
    os.getenv("PRINTSMITH_ESTIMATE_HISTORY_STORAGE_ROOT", "estimate-history").strip()
    or "estimate-history"
)
ESTIMATE_DETAIL_STORAGE_ROOT = (
    os.getenv("PRINTSMITH_ESTIMATE_DETAIL_STORAGE_ROOT", "estimate-detail").strip()
    or "estimate-detail"
)
INVOICE_DETAIL_STORAGE_ROOT = (
    os.getenv("PRINTSMITH_INVOICE_DETAIL_STORAGE_ROOT", "invoice-detail").strip()
    or "invoice-detail"
)
WANTED_DATE_DEFAULT_WORKING_DAYS = int(
    os.getenv("PRINTSMITH_WANTED_DATE_WORKING_DAYS", "5")
)

# Served statically at /public by the FastAPI app (see app/__init__.py). Files
# written here are reachable by anyone with the link, no auth required.
# Lives at app/v1/public, not nested under the bot module.
BOT_PUBLIC_DIR = Path(__file__).resolve().parents[2] / "public"
