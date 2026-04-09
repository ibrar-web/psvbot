import os


def _to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


HEADLESS = _to_bool(os.getenv("PRINTSMITH_HEADLESS", "true"))
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("PRINTSMITH_TIMEOUT_SECONDS", "200"))
KEEP_BROWSER_OPEN = _to_bool(os.getenv("PRINTSMITH_KEEP_BROWSER_OPEN", "false"))
DEBUG = _to_bool(os.getenv("PRINTSMITH_DEBUG", "true"))
QUOTE_SUMMARY_STORAGE_ROOT = (
    os.getenv("PRINTSMITH_QUOTE_SUMMARY_STORAGE_ROOT", "estimates").strip()
    or "estimates"
)
