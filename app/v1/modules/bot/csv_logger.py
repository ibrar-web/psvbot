import logging


def init() -> None:
    """
    Ensure the bot logger is configured for terminal-only output.
    No file logging is performed; all logger.info() calls go to the
    console via the root StreamHandler.
    """
    bot_logger = logging.getLogger("app.v1.modules.bot")
    bot_logger.setLevel(logging.INFO)


def get_log_path() -> None:
    """File logging is disabled — always returns None."""
    return None


def shutdown() -> None:
    """No-op. Kept for backward compatibility; no file handlers to clean up."""
    pass
