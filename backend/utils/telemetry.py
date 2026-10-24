"""Anonymous telemetry: a daily install heartbeat, and optional diagnostics.

Nojoin sends two things to the collector, and they follow different rules. See
``docs/TELEMETRY.md`` for the user-facing disclosure this module must remain
true to.

**The heartbeat** carries the install id and the version and nothing else. It
goes out within fifteen minutes of first start, on the first start of each new
version, and once per UTC day, so the number of live deployments can be
counted. It needs no consent and the app has no switch for it.

**The diagnostics ping** carries counts and configuration shape, and is sent
every six hours once the install consents. The ingest keys on ``(install_id,
day)`` and upserts, so the four sends a day collapse to a single stored row.
That cadence exists to survive worker restarts, not to record more: a daily
interval re-anchors on every restart and can skip a calendar day.

Four invariants are load-bearing and are locked by tests:

1. **Neither payload carries anything identifying.** No hostname, URL, IP,
   username, meeting title, transcript text, key, or model name. Only the
   fields built by :func:`build_heartbeat` and :func:`build_payload` are sent.
2. **The environment outranks everything.** An operator who sets
   ``NOJOIN_TELEMETRY_ENABLED`` cannot have it overridden from the UI, in
   either direction: ``false`` stops the heartbeat and the diagnostics alike,
   and ``true`` is an explicit opt-in to both that needs no further consent.
3. **Diagnostics are never sent without consent.** A new install consents
   through the first-run wizard; an upgraded install sends no diagnostics until
   the admin notice has actually been shown, and then only after
   acknowledgement or the grace period.
4. **A "no" given before the heartbeat existed still stands.** An install that
   turned telemetry off before the heartbeat shipped turned off what was then
   all of it, so it sends no heartbeat either until diagnostics are turned back
   on. A choice made after that covers the diagnostics only.

State ownership is deliberately split so no two processes write the same thing:

===========================  ====================  =========================
State                        Written by            Read by
===========================  ====================  =========================
``config.json`` keys         API only              API, worker
``.install_id``              whoever mints it      API, worker (write-once)
Redis ``last_sent_at``       worker only           API (display only)
Redis ``last_attempt``       worker only           API (display only)
Redis ``heartbeat_*``        worker only           worker, API (display)
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
from typing import Any, NamedTuple
from urllib.parse import urljoin, urlparse

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

#: Written with every telemetry choice made once the heartbeat exists. An
#: opt-out recorded without it predates the heartbeat and keeps the install
#: exempt from it; see :func:`opted_out_before_heartbeat`.
POLICY_CONFIG_KEY = "telemetry_policy_version"
HEARTBEAT_POLICY_VERSION = 2

LAST_SENT_REDIS_KEY = "nojoin:telemetry:last_sent_at"

#: Every attempt, successful or not. Kept separate from ``last_sent_at`` because
#: the two answer different questions: an admin looking at a "Last sent: Never"
#: needs to know whether nothing was ever tried or everything tried has failed,
#: and one timestamp cannot say both.
LAST_ATTEMPT_REDIS_KEY = "nojoin:telemetry:last_attempt"

#: The last heartbeat the collector accepted, as ``{"at", "day", "version"}``,
#: and the last attempt whatever its outcome. Worker-owned, like the ping's.
HEARTBEAT_SENT_REDIS_KEY = "nojoin:telemetry:heartbeat_sent"
HEARTBEAT_ATTEMPT_REDIS_KEY = "nojoin:telemetry:heartbeat_attempt"

HEARTBEAT_SCHEMA_VERSION = 1

#: After a failed heartbeat, wait this long before the next try. The task runs
#: every fifteen minutes, and an operator who blocks the collector should see
#: one failed lookup an hour rather than four.
HEARTBEAT_RETRY_AFTER = timedelta(hours=1)

INSTALL_ID_FILENAME = ".install_id"

#: Silence is treated as consent only after the admin notice has been on screen
#: for this long. The clock starts when the banner reports itself rendered, not
#: when the container started, so it cannot elapse unseen.
GRACE_PERIOD_DAYS = 7

#: Rolling activity window for the "is this deployment actually used" metrics.
ACTIVITY_WINDOW_DAYS = 28

#: Window for the daily-use figure. Rolling rather than a calendar day, because
#: the ingest keeps each day's last ping and a calendar-day count taken in the
#: evening would miss whatever was recorded after it.
DAILY_WINDOW = timedelta(hours=24)

SEND_TIMEOUT_SECONDS = 10.0


class SendResult(NamedTuple):
    """Outcome of one attempt, with a reason an admin can act on.

    ``detail`` is a short sentence written here rather than a raw exception
    string: it is rendered verbatim in Settings, and an exception repr is both
    poor copy and an uncontrolled channel for whatever a library chose to put in
    it. It is ``None`` when the ping succeeded.
    """

    ok: bool
    detail: str | None = None


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


def heartbeat_endpoint() -> str:
    """The heartbeat route on the same collector as the diagnostics ping.

    Derived rather than configured on its own, so ``NOJOIN_TELEMETRY_ENDPOINT``
    still points both at a test collector with one setting.
    """
    return urljoin(telemetry_endpoint(), "heartbeat")


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
    # An operator who set the environment variable to true opted in explicitly,
    # in the one place that outranks the UI. Waiting for the notice as well
    # would be a deadlock rather than a safeguard: notice_pending() suppresses
    # the banner precisely because the environment is pinned, so the
    # acknowledgement this would wait for could never be written.
    #
    # This early return is also what keeps the worker's view of consent fresh.
    # should_send() reaches here through is_telemetry_enabled(), which returns
    # before its reload() whenever the environment pins the value, and the
    # worker's config map is otherwise frozen at process start.
    if env_override() is True:
        return True

    if bool(config_manager.get(ACKNOWLEDGED_CONFIG_KEY, False)):
        return True

    shown_at = _config_datetime(NOTICE_SHOWN_CONFIG_KEY)
    if shown_at is None:
        return False

    reference = now or datetime.now(timezone.utc)
    return reference >= shown_at + timedelta(days=GRACE_PERIOD_DAYS)


def should_send(now: datetime | None = None) -> bool:
    return is_telemetry_enabled() and consent_granted(now=now)


def opted_out_before_heartbeat() -> bool:
    """Whether this install said no to telemetry before the heartbeat existed.

    That answer switched off what was then the whole of Nojoin's telemetry, so
    sending a heartbeat afterwards would go back on it. The policy marker
    written with every later choice is what tells the two apart: an opt-out
    stored without it was made before the heartbeat shipped.
    """
    config_manager.reload()
    return (
        config_manager.get(ENABLED_CONFIG_KEY, True) is False
        and config_manager.get(POLICY_CONFIG_KEY) is None
    )


def heartbeat_blocked_by() -> str | None:
    """Why the heartbeat is not sent, or ``None`` when it is.

    ``"environment"`` when ``NOJOIN_TELEMETRY_ENABLED=false``, and
    ``"earlier_opt_out"`` for an install covered by
    :func:`opted_out_before_heartbeat`. These are the only two exemptions.
    """
    override = env_override()
    if override is not None:
        return None if override else "environment"
    if opted_out_before_heartbeat():
        return "earlier_opt_out"
    return None


def heartbeat_allowed() -> bool:
    return heartbeat_blocked_by() is None


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
    """Record the operator's choice. Any explicit choice also retires the notice.

    The choice is stamped with the heartbeat policy, so a later opt-out covers
    the diagnostics only. The one exception is an install that opted out before
    the heartbeat existed and is saying no again: stamping that would quietly
    end the exemption it was promised, so its earlier answer is left standing
    until it turns diagnostics back on.
    """
    updates: dict[str, Any] = {ENABLED_CONFIG_KEY: bool(enabled)}
    if acknowledge:
        updates[ACKNOWLEDGED_CONFIG_KEY] = True
    if enabled or not opted_out_before_heartbeat():
        updates[POLICY_CONFIG_KEY] = HEARTBEAT_POLICY_VERSION
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
    last_attempt = get_last_attempt()
    shown_at = _config_datetime(NOTICE_SHOWN_CONFIG_KEY)
    heartbeat_blocked = heartbeat_blocked_by()
    heartbeat_sent = get_heartbeat_sent() or {}
    heartbeat_attempt = get_heartbeat_attempt() or {}

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
        # Reported alongside last_sent_at rather than folded into it. "Last
        # sent: Never" is ambiguous on its own -- never tried, or tried and
        # failed every time -- and only the second is a problem an admin can do
        # something about.
        "last_attempt_at": last_attempt["at"] if last_attempt else None,
        "last_attempt_ok": last_attempt["ok"] if last_attempt else None,
        "last_attempt_detail": last_attempt["detail"] if last_attempt else None,
        "grace_period_days": GRACE_PERIOD_DAYS,
        "heartbeat_enabled": heartbeat_blocked is None,
        "heartbeat_blocked_by": heartbeat_blocked,
        "heartbeat_endpoint": heartbeat_endpoint(),
        "heartbeat_last_sent_at": heartbeat_sent.get("at"),
        "heartbeat_last_attempt_at": heartbeat_attempt.get("at"),
        "heartbeat_last_attempt_ok": heartbeat_attempt.get("ok"),
        "heartbeat_last_attempt_detail": heartbeat_attempt.get("detail"),
    }


def is_local_origin() -> bool:
    """Whether this install is reached on a localhost origin.

    Reported rather than acted on: development stacks ping exactly like real
    deployments, and this flag lets them be segmented at query time instead of
    being deleted at source where the judgement could never be revised.
    """
    hostname = urlparse(get_configured_web_origin()).hostname
    return (hostname or "").lower() in _LOCAL_HOSTNAMES


# --- Send bookkeeping (worker-owned, Redis) ---------------------------------


def _redis_client():
    import redis

    from backend.core.redis import REDIS_URL

    return redis.from_url(REDIS_URL)


def _redis_text(key: str) -> str | None:
    try:
        raw = _redis_client().get(key)
    except Exception as exc:  # noqa: BLE001 -- boundary: display-only, never fail a request
        logger.debug("Could not read telemetry marker %s: %s", key, exc)
        return None

    if not raw:
        return None
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


def get_last_sent_at() -> datetime | None:
    raw = _redis_text(LAST_SENT_REDIS_KEY)
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _redis_json(key: str) -> dict[str, Any] | None:
    """A JSON object stored under ``key`` with an ``at`` stamp, or ``None``."""
    raw = _redis_text(key)
    if raw is None:
        return None
    try:
        stored = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(stored, dict) or not stored.get("at"):
        return None
    return stored


def _read_attempt(key: str) -> dict[str, Any] | None:
    stored = _redis_json(key)
    if stored is None:
        return None
    return {
        "at": str(stored["at"]),
        "ok": bool(stored.get("ok")),
        "detail": str(stored["detail"]) if stored.get("detail") else None,
    }


def get_last_attempt() -> dict[str, Any] | None:
    """The most recent send attempt, successful or not, or ``None`` if untried.

    Read for display only. A missing or unparseable value is indistinguishable
    from never having tried, which is the safe reading: the panel then falls
    back to what the consent state alone can say.
    """
    return _read_attempt(LAST_ATTEMPT_REDIS_KEY)


def get_heartbeat_attempt() -> dict[str, Any] | None:
    """The most recent heartbeat attempt, successful or not, or ``None``."""
    return _read_attempt(HEARTBEAT_ATTEMPT_REDIS_KEY)


def get_heartbeat_sent() -> dict[str, Any] | None:
    """The last heartbeat the collector accepted: when, which UTC day, which version.

    Unlike the ping's markers this one is not display-only, since it decides
    whether a heartbeat is due. Losing it costs one extra heartbeat, which the
    ingest folds into the same day's row.
    """
    stored = _redis_json(HEARTBEAT_SENT_REDIS_KEY)
    if stored is None:
        return None
    return {
        "at": str(stored["at"]),
        "day": stored.get("day"),
        "version": stored.get("version"),
    }


def record_attempt(moment: datetime, result: SendResult) -> None:
    """Record one send attempt. Only the worker calls this.

    Writes the attempt unconditionally and the last-sent marker only on success,
    because the two answer different questions and collapsing them would lose
    exactly the case worth reporting: an install that consented and has been
    failing ever since looks identical to one that never tried.

    Losing either costs nothing but the display. A missing last-sent marker at
    most causes one duplicate ping, which the ingest deduplicates on
    ``(install_id, day)`` anyway.
    """
    stamp = moment.isoformat()
    try:
        client = _redis_client()
        client.set(
            LAST_ATTEMPT_REDIS_KEY,
            json.dumps({"at": stamp, "ok": result.ok, "detail": result.detail}),
        )
        if result.ok:
            client.set(LAST_SENT_REDIS_KEY, stamp)
    except Exception as exc:  # noqa: BLE001 -- boundary: bookkeeping must not fail the task
        logger.debug("Could not persist telemetry send markers: %s", exc)


def record_heartbeat_attempt(
    moment: datetime, result: SendResult, version: str | None
) -> None:
    """Record one heartbeat attempt. Only the worker calls this.

    The sent marker carries the UTC day and the version it was sent for, which
    is what :func:`heartbeat_due` compares against.
    """
    stamp = moment.isoformat()
    try:
        client = _redis_client()
        client.set(
            HEARTBEAT_ATTEMPT_REDIS_KEY,
            json.dumps({"at": stamp, "ok": result.ok, "detail": result.detail}),
        )
        if result.ok:
            client.set(
                HEARTBEAT_SENT_REDIS_KEY,
                json.dumps({"at": stamp, "day": _utc_day(moment), "version": version}),
            )
    except Exception as exc:  # noqa: BLE001 -- boundary: bookkeeping must not fail the task
        logger.debug("Could not persist telemetry heartbeat markers: %s", exc)


def _utc_day(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).date().isoformat()


def heartbeat_due(now: datetime | None = None) -> bool:
    """Whether the heartbeat should go out on this tick.

    Due on an install's first tick, on the first tick after it starts on a new
    version, and on the first tick of each UTC day: the three moments the
    heartbeat is documented to cover. A failed attempt holds the next one back
    for :data:`HEARTBEAT_RETRY_AFTER`.
    """
    if not heartbeat_allowed():
        return False

    moment = now or datetime.now(timezone.utc)
    sent = get_heartbeat_sent()
    if (
        sent is not None
        and sent["day"] == _utc_day(moment)
        and sent["version"] == _installed_version()
    ):
        return False

    attempt = get_heartbeat_attempt()
    if attempt is not None and not attempt["ok"]:
        try:
            attempted_at = datetime.fromisoformat(attempt["at"])
        except ValueError:
            return True
        if attempted_at.tzinfo is None:
            attempted_at = attempted_at.replace(tzinfo=timezone.utc)
        if moment - attempted_at < HEARTBEAT_RETRY_AFTER:
            return False

    return True


# --- Payload ----------------------------------------------------------------


def build_heartbeat() -> dict[str, Any]:
    """Assemble the heartbeat: the install id and the version, and nothing else.

    This is the part of telemetry the app cannot switch off, so its contents are
    fixed at these fields and locked by a test, and the ingest discards anything
    else it is sent. Anything worth knowing beyond whether an install exists and
    which version it runs belongs in :func:`build_payload`, behind consent.
    """
    return {
        "schema": HEARTBEAT_SCHEMA_VERSION,
        "install_id": get_install_id(),
        "version": _installed_version(),
    }


def _scalar(session: Any, statement: Any) -> Any:
    return session.exec(statement).one()


def _usage_metrics(
    session: Any, cutoff: datetime, day_cutoff: datetime
) -> dict[str, Any]:
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
        # How often an install is used, as distinct from whether it is running:
        # the heartbeat already says the second. One count, not a timeline.
        "recordings_24h": _scalar(
            session,
            select(func.count())
            .select_from(Recording)
            .where(live, Recording.created_at >= day_cutoff),
        ),
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
    from sqlmodel import col, select

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
        # Documents of a meeting; a note's file is a note feature.
        "documents_used": any_row(Document, col(Document.note_id).is_(None)),
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
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ACTIVITY_WINDOW_DAYS)

    payload: dict[str, Any] = {
        "schema": TELEMETRY_SCHEMA_VERSION,
        "install_id": install_id,
        "version": _installed_version(),
        "install_age_days": _install_age_days(session, minted_at),
        "local_origin": is_local_origin(),
    }
    payload.update(_usage_metrics(session, cutoff, now - DAILY_WINDOW))
    payload.update(_ai_shape(session))
    payload.update(_transcription_shape())
    payload.update(_feature_flags(session, cutoff))
    return payload


def _installed_version() -> str | None:
    from backend.utils.version import get_installed_version

    return get_installed_version() or None


def send_heartbeat(payload: dict[str, Any]) -> SendResult:
    """POST the heartbeat, and say what happened. Same contract as the ping."""
    return _post(heartbeat_endpoint(), payload)


def send_payload(payload: dict[str, Any]) -> SendResult:
    """POST the diagnostics ping. See :func:`_post` for the contract."""
    return _post(telemetry_endpoint(), payload)


def _post(url: str, payload: dict[str, Any]) -> SendResult:
    """POST one telemetry payload, and say what happened.

    Best-effort by contract: an unreachable endpoint, a DNS failure, or a
    non-2xx response returns rather than raises, and the cycle is simply
    skipped. Nothing here retries and nothing is backfilled: the ping's next
    try is the next six-hourly tick and the heartbeat's is an hour later, so a
    flaky network can never turn into a storm against our own endpoint.

    Failures stay at debug rather than warning. Blocking the endpoint at the
    network layer is a documented, supported way to opt out (docs/TELEMETRY.md),
    and an operator who did that deliberately should not have their logs filled
    for it. The outcome reaches the admin through Settings instead, which is
    where someone actually asking the question will be looking.
    """
    import httpx

    try:
        response = httpx.post(
            url,
            json=payload,
            timeout=SEND_TIMEOUT_SECONDS,
            headers={"content-type": "application/json"},
        )
    except Exception as exc:  # noqa: BLE001 -- boundary: telemetry must never disrupt the worker
        logger.debug("Telemetry ping failed: %s", exc)
        return SendResult(False, _failure_detail(exc))

    if response.status_code >= 400:
        logger.debug("Telemetry ping rejected with status %s", response.status_code)
        return SendResult(
            False,
            f"The collector rejected the ping with HTTP {response.status_code}.",
        )

    return SendResult(True)


def _failure_detail(exc: Exception) -> str:
    """Turn a transport failure into something an admin can act on.

    The three cases are separated because the remedies differ: a timeout is
    usually transient, an unreachable host is usually a firewall or a
    deliberate DNS block, and anything else is worth naming so it can be
    searched for.
    """
    import httpx

    if isinstance(exc, httpx.TimeoutException):
        return (
            f"The collector did not respond within {SEND_TIMEOUT_SECONDS:.0f} seconds."
        )
    if isinstance(exc, httpx.ConnectError):
        return "The collector could not be reached. It may be blocked by a firewall or DNS."
    return f"The ping failed: {type(exc).__name__}."
