from __future__ import annotations

import logging

from fastapi import HTTPException


def sanitized_http_exception(
    *,
    logger: logging.Logger,
    status_code: int,
    client_message: str,
    log_message: str,
    exc: Exception | None = None,
) -> HTTPException:
    if exc is None:
        logger.error(log_message)
    else:
        logger.exception(log_message)

    return HTTPException(status_code=status_code, detail=client_message)