"""Anonymous, opt-out telemetry.

Nojoin sends one small anonymous ping per day describing how many deployments
exist, how they are configured, and how much they are used. See
``docs/TELEMETRY.md`` for the user-facing disclosure this module must remain
true to.

Three invariants are load-bearing and are locked by tests:

1. **The payload carries nothing identifying.** No hostname, URL, IP, username,
   meeting title, transcript text, key, or model name. Only the fields built by
   :func:`build_payload` are ever sent.
2. **The environment kill switch outranks everything.** An operator who sets
   ``NOJOIN_TELEMETRY_ENABLED=false`` cannot have that overridden from the UI.
3. **Nothing is sent without consent.** A new install consents through the
   first-run wizard; an upgraded install sends nothing at all until the admin
   notice has actually been shown, and then only after acknowledgement or the
   grace period. An install nobody signs into therefore never pings.

State ownership is deliberately split so no two processes write the same thing:

===========================  ====================  =========================
State                        Written by            Read by
===========================  ====================  =========================
``config.json`` keys         API only              API, worker
``.install_id``              whoever mints it      API, worker (write-once)
Redis ``last_sent_at``       worker only           API (display only)
===========================  ====================  =========================

Making the worker a second writer of ``config.json`` would let a stale worker
map revert an admin's opt-out, so it never writes there.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.utils.config_manager import config_manager, get_configured_web_origin
from backend.utils.path_manager import path_manager

logger = logging.getLogger(__name__)

TELEMETRY_SCHEMA_VERSION = 1
DEFAULT_TELEMETRY_ENDPOINT = "https://telemetry.nojoin.co.uk/v1/ping"

TELEMETRY_ENABLED_ENV_KEY = "NOJOIN_TELEMETRY_ENABLED"
TELEMETRY_ENDPOINT_ENV_KEY = "NOJOIN_TELEMETRY_ENDPOINT"

ENABLED_CONFIG_KEY = "telemetry_enabled"
ACKNOWLEDGED_CONFIG_KEY = "telemetry_notice_acknowledged"
NOTICE_SHOWN_CONFIG_KEY = "telemetry_notice_first_shown_at"

LAST_SENT_REDIS_KEY = "nojoin:telemetry:last_sent_at"

INSTALL_ID_FILENAME = ".install_id"

#: Silence is treated as consent only after the admin notice has been on screen
#: for this long. The clock starts when the banner reports itself rendered, not
#: when the container started, so it cannot elapse unseen.
GRACE_PERIOD_DAYS = 7

#: Rolling activity window for the "is this deployment actually used" metrics.
ACTIVITY_WINDOW_DAYS = 28

SEND_TIMEOUT_SECONDS = 10.0

_FALSY = {"0", "false", "no", "off"}
_TRUTHY = {"1", "true", "yes", "on"}

_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


# --- Install identity -------------------------------------------------------


def _install_id_path() -> Path:
    return path_manager.user_data_directory / INSTALL_ID_FILENAME


def load_install_identity() -> tuple[str, datetime]:
    """Return this install's random id and the moment it was minted.

    Write-once: the file is created on first use and never rewritten, so the id
    is stable for the lifetime of the deployment. It is deliberately **not**
    included in backup archives, so restoring onto a second host mints a fresh
    identity rather than cloning the original and double-counting it.
    """
    path = _install_id_path()

    try:
        if path.exists():
            stored = json.loads(path.read_text(encoding="utf-8"))
            install_id = str(uuid.UUID(str(stored["install_id"])))
            created_at = datetime.fromisoformat(str(stored["created_at"]))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            return install_id, created_at
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # A corrupt file must not wedge the install. Mint a new identity, which
        # costs at most one deployment appearing as two across the rewrite.
        logger.warning("Unreadable install id file, minting a new one: %s", exc)

    install_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"install_id": install_id, "created_at": created_at.isoformat()}
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
    except OSError as exc:
        logger.warning("Could not persist install id: %s", exc)

    return install_id, created_at


def get_install_id() -> str:
    return load_install_identity()[0]


# --- Configuration ----------------------------------------------------------


def _env_flag(raw: str | None) -> bool | None:
    if raw is None or not raw.strip():
        return None
    value = raw.strip().lower()
    if value in _FALSY:
        return False
    if value in _TRUTHY:
        return True
    return None


def telemetry_endpoint() -> str:
    configured = os.environ.get(TELEMETRY_ENDPOINT_ENV_KEY, "").strip()
    return configured or DEFAULT_TELEMETRY_ENDPOINT


def env_override() -> bool | None:
    """The operator's ``NOJOIN_TELEMETRY_ENABLED`` value, if it is set and valid."""
    return _env_flag(os.environ.get(TELEMETRY_ENABLED_ENV_KEY))


def is_env_managed() -> bool:
    """Whether the environment pins the setting, making the UI toggle read-only."""
    return env_override() is not None


def is_telemetry_enabled() -> bool:
    """Whether telemetry is switched on for this install.

    The config is reloaded from disk first because the worker's ConfigManager is
    a singleton populated at process start: without this, an admin's opt-out
    would not take effect until the container restarted, which is the one
    failure this feature cannot afford.
    """
    override = env_override()
    if override is not None:
        return override

    config_manager.reload()
    return bool(config_manager.get(ENABLED_CONFIG_KEY, True))


def _config_datetime(key: str) -> datetime | None:
    raw = config_manager.get(key)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def consent_granted(now: datetime | None = None) -> bool:
    """Whether this install may send, ignoring the enabled switch itself.

    A new install acknowledges through the first-run wizard. An upgraded install
    has no acknowledgement and no shown-notice timestamp, so it sends nothing
    until the banner reports itself rendered to an admin; after that, silence
    for the grace period counts as consent. This is why a deployment nobody
    signs into never pings — a documented, accepted undercount.
    """
    if bool(config_manager.get(ACKNOWLEDGED_CONFIG_KEY, False)):
        return True

    shown_at = _config_datetime(NOTICE_SHOWN_CONFIG_KEY)
    if shown_at is None:
        return False

    reference = now or datetime.now(timezone.utc)
    return reference >= shown_at + timedelta(days=GRACE_PERIOD_DAYS)


def should_send(now: datetime | None = None) -> bool:
    return is_telemetry_enabled() and consent_granted(now=now)


def notice_pending() -> bool:
    """Whether the admin telemetry notice still needs to be shown.

    Stops as soon as the admin makes any explicit choice, in either direction.
    An install pinned by the environment never shows it, since there would be
    nothing the admin could do about it.
    """
    if is_env_managed():
        return False
    config_manager.reload()
    return not bool(config_manager.get(ACKNOWLEDGED_CONFIG_KEY, False))


# --- Config writes (API process only) ---------------------------------------


def _write_config(updates: dict[str, Any]) -> None:
    """Persist telemetry keys to config.json.

    Called only from the API. The worker deliberately never writes here: it
    holds a ConfigManager populated at process start, and saving its whole map
    could revert an admin's opt-out made moments earlier.
    """
    config_manager.reload()
    data = config_manager.get_all()
    data.update(updates)
    config_manager.save_config(data)
    # Forced: this process just wrote the file, so it must not be talked out of
    # re-reading by a same-instant mtime matching what it last parsed.
    config_manager.reload(force=True)


def set_enabled(enabled: bool, *, acknowledge: bool = True) -> None:
    """Record the operator's choice. Any explicit choice also retires the notice."""
    updates: dict[str, Any] = {ENABLED_CONFIG_KEY: bool(enabled)}
    if acknowledge:
        updates[ACKNOWLEDGED_CONFIG_KEY] = True
    _write_config(updates)


def mark_notice_shown() -> None:
    """Stamp the moment the admin notice first reached a screen.

    Write-once: the grace period is measured from the *first* time the banner
    rendered, so a later render cannot push the clock forward and delay sending
    indefinitely.
    """
    config_manager.reload()
    if config_manager.get(NOTICE_SHOWN_CONFIG_KEY):
        return
    _write_config({NOTICE_SHOWN_CONFIG_KEY: datetime.now(timezone.utc).isoformat()})


def telemetry_status() -> dict[str, Any]:
    """Admin-facing view of the current telemetry state."""
    config_manager.reload()
    install_id, _ = load_install_identity()
    last_sent = get_last_sent_at()
    shown_at = _config_datetime(NOTICE_SHOWN_CONFIG_KEY)

    return {
        "enabled": is_telemetry_enabled(),
        "managed_by_env": is_env_managed(),
        "notice_acknowledged": bool(config_manager.get(ACKNOWLEDGED_CONFIG_KEY, False)),
        "notice_pending": notice_pending(),
        "notice_first_shown_at": shown_at.isoformat() if shown_at else None,
        "consent_granted": consent_granted(),
        "install_id": install_id,
        "endpoint": telemetry_endpoint(),
        "last_sent_at": last_sent.isoformat() if last_sent else None,
        "grace_period_days": GRACE_PERIOD_DAYS,
    }


def is_local_origin() -> bool:
    """Whether this install is reached on a localhost origin.

    Reported rather than acted on: development stacks ping exactly like real
    deployments, and this flag lets them be segmented at query time instead of
    being deleted at source where the judgement could never be revised.
    """
    hostname = urlparse(get_configured_web_origin()).hostname
    return (hostname or "").lower() in _LOCAL_HOSTNAMES


# --- Last-sent bookkeeping (worker-owned, Redis) ----------------------------


def _redis_client():
    import redis

    from backend.core.redis import REDIS_URL

    return redis.from_url(REDIS_URL)


def get_last_sent_at() -> datetime | None:
    try:
        raw = _redis_client().get(LAST_SENT_REDIS_KEY)
    except Exception as exc:  # noqa: BLE001 -- boundary: display-only, never fail a request
        logger.debug("Could not read telemetry last-sent marker: %s", exc)
        return None

    if not raw:
        return None
    try:
        return datetime.fromisoformat(
            raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        )
    except ValueError:
        return None


def record_sent_at(moment: datetime) -> None:
    """Only the worker calls this. Losing it costs one duplicate ping, which the
    ingest deduplicates on ``(install_id, day)`` anyway."""
    try:
        _redis_client().set(LAST_SENT_REDIS_KEY, moment.isoformat())
    except Exception as exc:  # noqa: BLE001 -- boundary: bookkeeping must not fail the task
        logger.debug("Could not persist telemetry last-sent marker: %s", exc)


# --- Payload ----------------------------------------------------------------


def _scalar(session: Any, statement: Any) -> Any:
    return session.exec(statement).one()


def _usage_metrics(session: Any, cutoff: datetime) -> dict[str, Any]:
    from sqlalchemy import distinct, func
    from sqlmodel import select

    from backend.models.recording import Recording
    from backend.models.user import User

    live = Recording.is_deleted == False  # noqa: E712
    recent = Recording.created_at >= cutoff

    hours = _scalar(
        session, select(func.sum(Recording.duration_seconds)).where(live, recent)
    )

    return {
        "users_total": _scalar(
            session,
            select(func.count()).select_from(User).where(User.is_active == True),  # noqa: E712
        ),
        "users_recording_28d": _scalar(
            session,
            select(func.count(distinct(Recording.user_id))).where(live, recent),
        ),
        "recordings_total": _scalar(
            session, select(func.count()).select_from(Recording).where(live)
        ),
        "recordings_28d": _scalar(
            session, select(func.count()).select_from(Recording).where(live, recent)
        ),
        "recording_hours_28d": round((hours or 0) / 3600.0, 1),
    }


def _ai_shape(session: Any) -> dict[str, Any]:
    """Provider *family* only. Never keys, endpoints, or model names."""
    from sqlalchemy import func
    from sqlmodel import select

    from backend.models.cli_oauth import CliOAuthCredential
    from backend.utils.config_manager import is_meeting_edge_enabled

    secondary = config_manager.get("secondary_llm_provider")
    cli_credentials = _scalar(
        session, select(func.count()).select_from(CliOAuthCredential)
    )

    return {
        "llm_provider": config_manager.get("llm_provider") or None,
        "secondary_configured": bool(secondary),
        "cli_oauth_in_use": bool(cli_credentials),
        "meeting_edge_enabled": is_meeting_edge_enabled(None),
    }


def _transcription_shape() -> dict[str, Any]:
    device = str(config_manager.get("processing_device", "auto")).lower()
    if device == "auto":
        gpu = _cuda_available()
    else:
        gpu = device != "cpu"

    return {
        "asr_engine": config_manager.get("transcription_backend") or None,
        "whisper_model_size": config_manager.get("whisper_model_size") or None,
        "gpu": gpu,
    }


def _cuda_available() -> bool:
    """Report GPU availability without importing torch.

    The API process must stay light, and this runs on the IO worker lane which
    has no GPU of its own, so the presence of an NVIDIA device node is the
    honest signal available here.
    """
    return Path("/dev/nvidiactl").exists() or Path("/proc/driver/nvidia").exists()


def _feature_flags(session: Any, cutoff: datetime) -> dict[str, Any]:
    from sqlalchemy import func
    from sqlmodel import select

    from backend.models.calendar import CalendarConnection
    from backend.models.chat import ChatMessage
    from backend.models.document import Document
    from backend.models.oauth import OAuthRefreshToken
    from backend.models.speaker import GlobalSpeaker
    from backend.models.task import UserTask

    def any_row(model: Any, *where: Any) -> bool:
        return bool(
            _scalar(session, select(func.count()).select_from(model).where(*where))
        )

    return {
        "calendar_connected": any_row(CalendarConnection),
        "mcp_in_use": any_row(OAuthRefreshToken),
        "chat_used_28d": any_row(ChatMessage, ChatMessage.created_at >= cutoff),
        "documents_used": any_row(Document),
        "tasks_used": any_row(UserTask),
        "people_library_used": any_row(GlobalSpeaker),
    }


def _install_age_days(session: Any, minted_at: datetime) -> int:
    """Age of the deployment, preferring the first account's creation date.

    That is the real first-run date and survives an upgrade, whereas the install
    id file is only created when this feature first runs.
    """
    from sqlalchemy import func
    from sqlmodel import select

    from backend.models.user import User

    earliest = _scalar(session, select(func.min(User.created_at)))
    # Fall back rather than trust the column's type: an unexpected value here
    # would raise inside build_payload and silently cost the whole ping, for a
    # field that is only ever approximate anyway.
    origin = earliest if isinstance(earliest, datetime) else minted_at
    if origin.tzinfo is None:
        origin = origin.replace(tzinfo=timezone.utc)

    return max(0, (datetime.now(timezone.utc) - origin).days)


def build_payload(session: Any) -> dict[str, Any]:
    """Assemble the ping.

    Everything sent is assembled here and nowhere else, so the disclosure in
    ``docs/TELEMETRY.md`` has exactly one place to stay in step with. There is
    deliberately no timestamp field: the ingest derives the day bucket from its
    own clock, so a skewed client clock cannot land a row in the wrong day.
    """
    install_id, minted_at = load_install_identity()
    cutoff = datetime.now(timezone.utc) - timedelta(days=ACTIVITY_WINDOW_DAYS)

    payload: dict[str, Any] = {
        "schema": TELEMETRY_SCHEMA_VERSION,
        "install_id": install_id,
        "version": _installed_version(),
        "install_age_days": _install_age_days(session, minted_at),
        "local_origin": is_local_origin(),
    }
    payload.update(_usage_metrics(session, cutoff))
    payload.update(_ai_shape(session))
    payload.update(_transcription_shape())
    payload.update(_feature_flags(session, cutoff))
    return payload


def _installed_version() -> str | None:
    from backend.utils.version import get_installed_version

    return get_installed_version() or None


def send_payload(payload: dict[str, Any]) -> bool:
    """POST the ping. Returns whether the ingest accepted it.

    Best-effort by contract: an unreachable endpoint, a DNS failure, or a
    non-2xx response is logged at debug and the day is simply skipped. There is
    no retry and no backfill, so a flaky network can never turn into a storm
    against our own endpoint.
    """
    import httpx

    try:
        response = httpx.post(
            telemetry_endpoint(),
            json=payload,
            timeout=SEND_TIMEOUT_SECONDS,
            headers={"content-type": "application/json"},
        )
    except Exception as exc:  # noqa: BLE001 -- boundary: telemetry must never disrupt the worker
        logger.debug("Telemetry ping failed: %s", exc)
        return False

    if response.status_code >= 400:
        logger.debug("Telemetry ping rejected with status %s", response.status_code)
        return False

    return True
