import logging
import os
from logging.handlers import RotatingFileHandler
from .config_manager import config_manager, get_log_path

LOG_PATH = get_log_path()

# Delete log if >100KB before logging is initialized
if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 100 * 1024:
    try:
        os.remove(LOG_PATH)
    except Exception:
        pass

def setup_logging(log_level=None):
    """Configures application-wide logging."""
    log_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Determine log level from config if not provided
    if log_level is None:
        cfg = config_manager.get_all()
        log_verbosity = cfg.get('advanced', {}).get('log_verbosity', 'INFO')
        log_level = getattr(logging, log_verbosity.upper(), logging.INFO)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # File Handler (Rotating)
    # Rotate logs after 5MB, keep 3 backup logs
    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'
    )
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)

    # Console Handler (for displaying logs during development/debugging)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

    logging.info("Logging configured.")

# Example usage (typically called once at application startup)
# if __name__ == '__main__':
#     setup_logging()
#     logging.info("This is an info message.")
#     logging.warning("This is a warning message.")
#     logging.error("This is an error message.") 