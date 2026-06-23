from __future__ import annotations

import logging

from backend.utils.deployment_warnings import (
    get_deployment_warnings,
    log_deployment_warnings,
)


def test_get_deployment_warnings_detects_placeholder_env_secrets(monkeypatch) -> None:
    monkeypatch.setenv("FIRST_RUN_PASSWORD", "change_this_before_first_start")
    monkeypatch.setenv(
        "DATA_ENCRYPTION_KEY",
        "change_this_to_a_long_random_secret",
    )
    monkeypatch.setenv(
        "REDIS_URL",
        "redis://:change_this_before_remote_access@redis:6379/0",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@db:5432/nojoin",
    )

    warnings = get_deployment_warnings()

    assert warnings == [
        {
            "code": "placeholder_first_run_password",
            "key": "FIRST_RUN_PASSWORD",
            "title": "Placeholder bootstrap password configured",
            "message": (
                "FIRST_RUN_PASSWORD still matches the tracked deployment-template "
                "placeholder. Update .env and restart or redeploy Nojoin."
            ),
        },
        {
            "code": "placeholder_data_encryption_key",
            "key": "DATA_ENCRYPTION_KEY",
            "title": "Placeholder data encryption key configured",
            "message": (
                "DATA_ENCRYPTION_KEY still matches the tracked deployment-template "
                "placeholder. Update .env and restart or redeploy Nojoin."
            ),
        },
        {
            "code": "placeholder_redis_password",
            "key": "REDIS_PASSWORD",
            "title": "Placeholder Redis password configured",
            "message": (
                "REDIS_PASSWORD still matches a tracked deployment-template "
                "placeholder. Update .env and restart or redeploy Nojoin."
            ),
        },
        {
            "code": "placeholder_postgres_password",
            "key": "POSTGRES_PASSWORD",
            "title": "Placeholder PostgreSQL password configured",
            "message": (
                "POSTGRES_PASSWORD still matches the tracked deployment-template "
                "placeholder. Update .env and restart or redeploy Nojoin."
            ),
        },
    ]


def test_get_deployment_warnings_detects_alternate_redis_placeholder(
    monkeypatch,
) -> None:
    monkeypatch.delenv("FIRST_RUN_PASSWORD", raising=False)
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://:change_to_secure_string@redis:6379/0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:custom-secret@db:5432/nojoin")

    warnings = get_deployment_warnings()

    assert warnings == [
        {
            "code": "placeholder_redis_password",
            "key": "REDIS_PASSWORD",
            "title": "Placeholder Redis password configured",
            "message": (
                "REDIS_PASSWORD still matches a tracked deployment-template "
                "placeholder. Update .env and restart or redeploy Nojoin."
            ),
        }
    ]


def test_get_deployment_warnings_ignores_custom_secrets(monkeypatch) -> None:
    monkeypatch.setenv("FIRST_RUN_PASSWORD", "super-secret-bootstrap")
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "long-random-custom-value")
    monkeypatch.setenv("REDIS_URL", "redis://:redis-secret@redis:6379/0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:db-secret@db:5432/nojoin")

    assert get_deployment_warnings() == []


def test_log_deployment_warnings_redacts_secret_values(monkeypatch, caplog) -> None:
    monkeypatch.setenv("FIRST_RUN_PASSWORD", "change_this_before_first_start")
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "change_this_to_a_long_random_secret")
    monkeypatch.setenv("REDIS_URL", "redis://:change_to_secure_string@redis:6379/0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/nojoin")

    with caplog.at_level(logging.WARNING):
        findings = log_deployment_warnings(startup_path="API startup")

    assert len(findings) == 4
    assert "FIRST_RUN_PASSWORD" in caplog.text
    assert "DATA_ENCRYPTION_KEY" in caplog.text
    assert "REDIS_PASSWORD" in caplog.text
    assert "POSTGRES_PASSWORD" in caplog.text
    assert "change_this_before_first_start" not in caplog.text
    assert "change_this_to_a_long_random_secret" not in caplog.text
    assert "change_to_secure_string" not in caplog.text
    assert "postgresql://postgres:postgres@db:5432/nojoin" not in caplog.text
