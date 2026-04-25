import logging
import os
import re
from logging.handlers import RotatingFileHandler
from .path_manager import path_manager
from .config_manager import config_manager

LOG_PATH = str(path_manager.log_path)

# Delete log if >100KB before logging is initialized
if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 100 * 1024:
    try:
        os.remove(LOG_PATH)
    except Exception:
        pass

class CheckpointFilter(logging.Filter):
    def __init__(self, name=''):
        super().__init__(name)
        self.unwanted_patterns = [
            "Registered checkpoint save hook for _speechbrain_save",
            "Registered checkpoint load hook for _speechbrain_load",
            "Registered checkpoint save hook for save",
            "Registered checkpoint load hook for load",
            "Registered checkpoint save hook for _save",
            "Registered checkpoint load hook for _recover"
        ]

    def filter(self, record):
        # Filters out unwanted log messages by pattern. Also verifies the record
        # originates from speechbrain.utils.checkpoints at DEBUG level as an
        # additional guard, though the string match should be sufficient.
        is_speechbrain_debug = record.name == 'speechbrain.utils.checkpoints' and record.levelno == logging.DEBUG
        
        if is_speechbrain_debug:
            for pattern in self.unwanted_patterns:
                if pattern in record.getMessage():
                    return False  # Don't log this message
        return True # Log this message


class SensitiveDataFilter(logging.Filter):
    _SENSITIVE_FIELD_NAMES = {
        "access_token",
        "anthropic_api_key",
        "api_key",
        "authorization",
        "companion_credential_secret",
        "cookie",
        "current_password",
        "gemini_api_key",
        "hf_token",
        "local_control_secret",
        "new_password",
        "openai_api_key",
        "password",
        "set-cookie",
        "token",
        "x-first-run-password",
    }
    _KEY_VALUE_PATTERNS = [
        re.compile(r'(?i)("authorization"\s*:\s*")([^"]+)(")'),
        re.compile(r"(?i)('authorization'\s*:\s*')([^']+)(')"),
        re.compile(r'(?i)("cookie"\s*:\s*")([^"]+)(")'),
        re.compile(r"(?i)('cookie'\s*:\s*')([^']+)(')"),
        re.compile(r'(?i)("set-cookie"\s*:\s*")([^"]+)(")'),
        re.compile(r"(?i)('set-cookie'\s*:\s*')([^']+)(')"),
        re.compile(r'(?i)("x-first-run-password"\s*:\s*")([^"]+)(")'),
        re.compile(r"(?i)('x-first-run-password'\s*:\s*')([^']+)(')"),
        re.compile(r'(?i)("(?:access_token|anthropic_api_key|api_key|companion_credential_secret|current_password|gemini_api_key|hf_token|local_control_secret|new_password|openai_api_key|password|token)"\s*:\s*")([^"]+)(")'),
        re.compile(r"(?i)('(?:access_token|anthropic_api_key|api_key|companion_credential_secret|current_password|gemini_api_key|hf_token|local_control_secret|new_password|openai_api_key|password|token)'\s*:\s*')([^']+)(')"),
    ]
    _AUTH_SCHEME_PATTERN = re.compile(r'(?i)\b(authorization\s*[:=]\s*)(bearer|bootstrap)\s+([^\s,;]+)')

    def _sanitize_string(self, value):
        if not isinstance(value, str):
            return value

        sanitized = value
        for pattern in self._KEY_VALUE_PATTERNS:
            sanitized = pattern.sub(r'\1[REDACTED]\3', sanitized)
        sanitized = self._AUTH_SCHEME_PATTERN.sub(r'\1\2 [REDACTED]', sanitized)
        return sanitized

    def _sanitize_value(self, key, value):
        if isinstance(key, str) and key.lower() in self._SENSITIVE_FIELD_NAMES:
            return "[REDACTED]"

        if isinstance(value, dict):
            return {
                nested_key: self._sanitize_value(nested_key, nested_value)
                for nested_key, nested_value in value.items()
            }

        if isinstance(value, tuple):
            return tuple(self._sanitize_value(None, item) for item in value)

        if isinstance(value, list):
            return [self._sanitize_value(None, item) for item in value]

        if isinstance(value, BaseException):
            return self._sanitize_string(str(value))

        return self._sanitize_string(value)

    def filter(self, record):
        record.msg = self._sanitize_value(None, record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: self._sanitize_value(key, value)
                    for key, value in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(self._sanitize_value(None, arg) for arg in record.args)
            else:
                record.args = self._sanitize_value(None, record.args)

        return True

def setup_logging(log_level=None):
    """Configures application-wide logging."""
    log_formatter = logging.Formatter(
        '(%(levelname)s) %(name)s: %(message)s'
    )

    # Determine log level from config if not provided
    if log_level is None:
        cfg = config_manager.get_all()
        log_verbosity = cfg.get('advanced', {}).get('log_verbosity', 'INFO')
        log_level = getattr(logging, log_verbosity.upper(), logging.INFO)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers to prevent duplication
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Create the filter instance
    checkpoint_filter = CheckpointFilter()
    sensitive_data_filter = SensitiveDataFilter()

    # File Handler (Rotating)
    # Rotate logs after 5MB, keep 3 backup logs
    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'
    )
    file_handler.setFormatter(log_formatter)
    file_handler.addFilter(checkpoint_filter) # Add filter
    file_handler.addFilter(sensitive_data_filter)
    root_logger.addHandler(file_handler)

    # Console Handler (for displaying logs during development/debugging)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    console_handler.addFilter(checkpoint_filter) # Add filter
    console_handler.addFilter(sensitive_data_filter)
    root_logger.addHandler(console_handler)

    # Silence verbose loggers from dependencies
    sb_logger = logging.getLogger('speechbrain.utils.checkpoints')
    sb_logger.setLevel(logging.DEBUG)
    
    # Silence SQLAlchemy engine logs (redundant if echo=False, but good practice)
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
    
    # Silence other potentially noisy libraries
    logging.getLogger('multipart').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('huggingface_hub').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)

    # Ensure Celery loggers are explicitly set to the correct level
    logging.getLogger('celery').setLevel(log_level)
    logging.getLogger('celery.task').setLevel(log_level)
    logging.getLogger('celery.worker').setLevel(log_level)

    # Silence Celery's default task logging to allow custom pretty logs
    # logging.getLogger('celery.worker.strategy').setLevel(logging.WARNING)
    # logging.getLogger('celery.app.trace').setLevel(logging.WARNING)
    # logging.getLogger('celery.worker.job').setLevel(logging.WARNING)

    logging.info("Logging configured.")

 