from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE_ENV_KEY = "DEFAULT_TIMEZONE"
UTC_TIMEZONE = "UTC"


def normalise_timezone_name(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    if cleaned.upper() == UTC_TIMEZONE:
        return UTC_TIMEZONE

    try:
        ZoneInfo(cleaned)
    except ZoneInfoNotFoundError:
        return None

    return cleaned


def validate_timezone_name(value: str) -> str:
    normalised = normalise_timezone_name(value)
    if normalised is None:
        raise ValueError(
            "Invalid timezone. Use a valid IANA timezone such as Europe/London.",
        )
    return normalised


def get_default_timezone_name() -> str:
    return normalise_timezone_name(os.getenv(DEFAULT_TIMEZONE_ENV_KEY)) or UTC_TIMEZONE


def get_user_timezone_name(
    user_settings: Mapping[str, Any] | None = None,
    *,
    fallback: str | None = None,
) -> str:
    if user_settings:
        user_timezone = normalise_timezone_name(str(user_settings.get("timezone") or ""))
        if user_timezone is not None:
            return user_timezone

    fallback_timezone = normalise_timezone_name(fallback)
    if fallback_timezone is not None:
        return fallback_timezone

    return get_default_timezone_name()


def get_timezone(value: str | None = None) -> ZoneInfo:
    return ZoneInfo(normalise_timezone_name(value) or UTC_TIMEZONE)


def convert_datetime_to_utc_naive(
    value: datetime | None,
    *,
    timezone_name: str | None = None,
) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=get_timezone(timezone_name))

    return value.astimezone(UTC).replace(tzinfo=None)


def utc_naive_to_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def utc_naive_to_timezone(value: datetime | None, timezone_name: str) -> datetime | None:
    aware_value = utc_naive_to_aware(value)
    if aware_value is None:
        return None

    return aware_value.astimezone(get_timezone(timezone_name))


def today_in_timezone(timezone_name: str) -> date:
    return datetime.now(get_timezone(timezone_name)).date()