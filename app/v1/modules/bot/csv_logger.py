import logging
import sys


def init() -> None:
    """
    Ensure the bot logger is configured for terminal-only output.
    Prevents duplicate StreamHandler accumulation. All log messages
    go to stdout — no files are ever created.
    """
    bot_logger = logging.getLogger("app.v1.modules.bot")
    bot_logger.setLevel(logging.INFO)

    # Ensure exactly one terminal StreamHandler exists
    has_terminal_handler = any(
        isinstance(h, logging.StreamHandler)
        and getattr(h, "stream", None) in (sys.stderr, sys.stdout)
        for h in bot_logger.handlers
    )
    if not has_terminal_handler:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        bot_logger.addHandler(console_handler)


def shutdown() -> None:
    """Flush all bot logger handlers. Kept for backward compatibility."""
    bot_logger = logging.getLogger("app.v1.modules.bot")
    for handler in list(bot_logger.handlers):
        try:
            handler.flush()
        except Exception:
            pass


def clear_handlers() -> None:
    """Remove all handlers from the bot logger to release memory."""
    bot_logger = logging.getLogger("app.v1.modules.bot")
    for handler in list(bot_logger.handlers):
        bot_logger.removeHandler(handler)
        try:
            handler.flush()
        except Exception:
            pass
        try:
            handler.close()
        except Exception:
            pass
