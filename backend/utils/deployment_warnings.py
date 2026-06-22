from __future__ import annotations

import logging
import os
from typing import TypedDict
from urllib.parse import unquote, urlparse


class DeploymentWarning(TypedDict):
    code: str
    key: str
    title: str
    message: str


logger = logging.getLogger(__name__)

_FIRST_RUN_PASSWORD_PLACEHOLDER = "change_this_before_first_start"
_DATA_ENCRYPTION_KEY_PLACEHOLDER = "change_this_to_a_long_random_secret"
_REDIS_PASSWORD_PLACEHOLDERS = frozenset(
    {
        "change_this_before_remote_access",
        "change_to_secure_string",
    }
)
_POSTGRES_PASSWORD_PLACEHOLDER = "postgres"


def _password_from_url(url: str | None) -> str | None:
    if not url:
        return None

    parsed = urlparse(url)
    if parsed.password is None:
        return None

    return unquote(parsed.password)


def get_deployment_warnings() -> list[DeploymentWarning]:
    warnings: list[DeploymentWarning] = []

    first_run_password = os.getenv("FIRST_RUN_PASSWORD")
    if first_run_password == _FIRST_RUN_PASSWORD_PLACEHOLDER:
        warnings.append(
            {
                "code": "placeholder_first_run_password",
                "key": "FIRST_RUN_PASSWORD",
                "title": "Placeholder bootstrap password configured",
                "message": (
                    "FIRST_RUN_PASSWORD still matches the tracked deployment-template "
                    "placeholder. Update .env and restart or redeploy Nojoin."
                ),
            }
        )

    data_encryption_key = os.getenv("DATA_ENCRYPTION_KEY")
    if data_encryption_key == _DATA_ENCRYPTION_KEY_PLACEHOLDER:
        warnings.append(
            {
                "code": "placeholder_data_encryption_key",
                "key": "DATA_ENCRYPTION_KEY",
                "title": "Placeholder data encryption key configured",
                "message": (
                    "DATA_ENCRYPTION_KEY still matches the tracked deployment-template "
                    "placeholder. Update .env and restart or redeploy Nojoin."
                ),
            }
        )

    redis_password = _password_from_url(os.getenv("REDIS_URL"))
    if redis_password in _REDIS_PASSWORD_PLACEHOLDERS:
        warnings.append(
            {
                "code": "placeholder_redis_password",
                "key": "REDIS_PASSWORD",
                "title": "Placeholder Redis password configured",
                "message": (
                    "REDIS_PASSWORD still matches a tracked deployment-template "
                    "placeholder. Update .env and restart or redeploy Nojoin."
                ),
            }
        )

    postgres_password = _password_from_url(os.getenv("DATABASE_URL"))
    if postgres_password == _POSTGRES_PASSWORD_PLACEHOLDER:
        warnings.append(
            {
                "code": "placeholder_postgres_password",
                "key": "POSTGRES_PASSWORD",
                "title": "Placeholder PostgreSQL password configured",
                "message": (
                    "POSTGRES_PASSWORD still matches the tracked deployment-template "
                    "placeholder. Update .env and restart or redeploy Nojoin."
                ),
            }
        )

    return warnings


def log_deployment_warnings(
    *, startup_path: str, logger_instance: logging.Logger | None = None
) -> list[DeploymentWarning]:
    findings = get_deployment_warnings()
    if not findings:
        return findings

    active_logger = logger_instance or logger
    affected_keys = ", ".join(sorted(finding["key"] for finding in findings))
    active_logger.warning(
        (
            "Nojoin %s is running with known placeholder secrets from the deployment "
            "templates: %s. Update .env and restart or redeploy Nojoin."
        ),
        startup_path,
        affected_keys,
    )
    return findings
