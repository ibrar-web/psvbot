import contextlib
import csv
import logging
import threading
from datetime import datetime
from pathlib import Path


class CSVHandler(logging.Handler):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._lock = threading.Lock()
        with self._path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["timestamp", "source", "message"])

    def emit(self, record: logging.LogRecord) -> None:
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        source = record.name.split(".")[-1]  # last segment of the logger name
        message = self.format(record)
        with self._lock:
            with self._path.open("a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([timestamp, source, message])


_csv_path: Path | None = None
_csv_handler: CSVHandler | None = None


def init(log_dir: str = "logs") -> Path:
    """
    Create a timestamped CSV log file and attach a CSVHandler to the bot's
    root logger. Call once at startup — all logger.info() calls across every
    bot module will flow here automatically.
    """
    global _csv_path, _csv_handler
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    filename = datetime.now().strftime("bot_log_%Y%m%d_%H%M%S.csv")
    _csv_path = Path(log_dir) / filename

    shutdown()
    handler = CSVHandler(_csv_path)
    handler.setLevel(logging.INFO)

    bot_logger = logging.getLogger("app.v1.modules.bot")
    bot_logger.addHandler(handler)
    bot_logger.setLevel(logging.INFO)
    _csv_handler = handler

    return _csv_path


def get_log_path() -> Path | None:
    return _csv_path


def shutdown() -> None:
    global _csv_handler
    if _csv_handler is None:
        return

    bot_logger = logging.getLogger("app.v1.modules.bot")
    with contextlib.suppress(Exception):
        bot_logger.removeHandler(_csv_handler)
    with contextlib.suppress(Exception):
        _csv_handler.flush()
    with contextlib.suppress(Exception):
        _csv_handler.close()
    _csv_handler = None
