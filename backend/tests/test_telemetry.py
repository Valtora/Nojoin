"""Guards for anonymous opt-out telemetry.

The tests here lock the three invariants the feature's credibility rests on:
the payload carries nothing identifying, the environment kill switch cannot be
overridden from the UI, and nothing is sent without consent. See
docs/TELEMETRY.md for the disclosure these assertions keep honest.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.utils import telemetry
from backend.utils.config_manager import ConfigManager

# Imported at module scope deliberately, not inside the tests that use it.
# Importing the worker package runs load_dotenv(), which repopulates os.environ
# from the developer's own .env. Done inside a test that would put
# NOJOIN_TELEMETRY_ENABLED back *after* _no_env_override cleared it, so a
# machine whose .env disables telemetry would silently turn every send test into
# a no-op that still passes. At module scope the load happens during collection,
# and the per-test delenv wins.
from backend.worker.tasks.system import send_telemetry_ping_task


class _Scalar:
    def __init__(self, value):
        self._value = value

    def one(self):
        return self._value


class FakeSession:
    """Stands in for a SQLModel session, returning a fixed scalar per query.

    build_payload only ever reads scalars, so a counter is enough to exercise
    the payload shape without a database.
    """

    def __init__(self, value=1):
        self.value = value

    def exec(self, _statement):
        return _Scalar(self.value)


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Point telemetry at an isolated config file rather than the real one."""
    manager = ConfigManager(config_path=str(tmp_path / "config.json"))
    monkeypatch.setattr(telemetry, "config_manager", manager)
    return manager


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the install-id file at a temporary user data directory."""
    monkeypatch.setattr(
        telemetry.path_manager, "_user_data_directory", tmp_path, raising=False
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    monkeypatch.delenv(telemetry.TELEMETRY_ENABLED_ENV_KEY, raising=False)
    monkeypatch.delenv(telemetry.TELEMETRY_ENDPOINT_ENV_KEY, raising=False)


class FakeRedis:
    """The two operations the send markers use, backed by a dict."""

    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


@pytest.fixture
def redis_stub(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(telemetry, "_redis_client", lambda: client)
    return client


# --- Install identity -------------------------------------------------------


def test_install_id_is_minted_once_and_stays_stable(data_dir) -> None:
    first_id, first_created = telemetry.load_install_identity()
    second_id, second_created = telemetry.load_install_identity()

    assert first_id == second_id
    assert first_created == second_created


def test_install_id_file_is_owner_only(data_dir) -> None:
    telemetry.load_install_identity()
    path = data_dir / telemetry.INSTALL_ID_FILENAME

    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600

    stored = json.loads(path.read_text())
    assert set(stored) == {"install_id", "created_at"}


def test_corrupt_install_id_file_mints_a_new_one_instead_of_failing(data_dir) -> None:
    path = data_dir / telemetry.INSTALL_ID_FILENAME
    path.write_text("not json at all")

    install_id, _ = telemetry.load_install_identity()

    assert install_id
    assert json.loads(path.read_text())["install_id"] == install_id


# --- Environment kill switch ------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("false", False), ("0", False), ("off", False), ("true", True), ("1", True)],
)
def test_env_override_outranks_the_stored_setting(
    config, monkeypatch, raw, expected
) -> None:
    # Store the opposite of the env value; the environment must still win.
    config.set(telemetry.ENABLED_CONFIG_KEY, not expected)
    monkeypatch.setenv(telemetry.TELEMETRY_ENABLED_ENV_KEY, raw)

    assert telemetry.is_telemetry_enabled() is expected
    assert telemetry.is_env_managed() is True


def test_unset_env_leaves_the_setting_to_config(config, monkeypatch) -> None:
    assert telemetry.is_env_managed() is False

    config.set(telemetry.ENABLED_CONFIG_KEY, False)
    assert telemetry.is_telemetry_enabled() is False

    config.set(telemetry.ENABLED_CONFIG_KEY, True)
    assert telemetry.is_telemetry_enabled() is True


def test_telemetry_is_enabled_by_default(config) -> None:
    assert telemetry.is_telemetry_enabled() is True


@pytest.mark.parametrize("raw", ["false", "true"])
def test_notice_is_never_shown_when_the_environment_pins_the_value(
    config, monkeypatch, raw
) -> None:
    monkeypatch.setenv(telemetry.TELEMETRY_ENABLED_ENV_KEY, raw)

    # Nothing the admin could do about it, so there is nothing to tell them.
    assert telemetry.notice_pending() is False


def test_env_true_is_itself_consent_on_an_upgraded_install(config, monkeypatch) -> None:
    # No acknowledgement and no shown-notice stamp: the upgraded-install state.
    # The banner that would write either is suppressed while the environment
    # pins the value, so waiting for it would leave an operator who explicitly
    # opted in silent forever.
    monkeypatch.setenv(telemetry.TELEMETRY_ENABLED_ENV_KEY, "true")

    assert telemetry.consent_granted() is True
    assert telemetry.should_send() is True


def test_env_false_still_blocks_sending_whatever_consent_says(
    config, monkeypatch
) -> None:
    config.set(telemetry.ACKNOWLEDGED_CONFIG_KEY, True)
    monkeypatch.setenv(telemetry.TELEMETRY_ENABLED_ENV_KEY, "false")

    assert telemetry.should_send() is False


# --- Consent ----------------------------------------------------------------


def test_upgraded_install_sends_nothing_until_the_notice_is_shown(config) -> None:
    # No acknowledgement and no shown-notice stamp: the state every install
    # upgraded into this feature starts in.
    assert telemetry.consent_granted() is False
    assert telemetry.should_send() is False


def test_install_nobody_signs_into_never_consents(config) -> None:
    # Deliberate, documented undercount: without a rendered notice the clock
    # never starts, however long the install runs.
    far_future = datetime.now(timezone.utc) + timedelta(days=3650)

    assert telemetry.consent_granted(now=far_future) is False


def test_silence_becomes_consent_only_after_the_grace_period(config) -> None:
    shown_at = datetime.now(timezone.utc)
    config.set(telemetry.NOTICE_SHOWN_CONFIG_KEY, shown_at.isoformat())

    just_before = shown_at + timedelta(days=telemetry.GRACE_PERIOD_DAYS, seconds=-1)
    just_after = shown_at + timedelta(days=telemetry.GRACE_PERIOD_DAYS, seconds=1)

    assert telemetry.consent_granted(now=just_before) is False
    assert telemetry.consent_granted(now=just_after) is True


def test_acknowledgement_grants_consent_immediately(config) -> None:
    config.set(telemetry.ACKNOWLEDGED_CONFIG_KEY, True)

    assert telemetry.consent_granted() is True


def test_disabling_still_blocks_sending_even_once_consent_is_granted(config) -> None:
    config.set(telemetry.ACKNOWLEDGED_CONFIG_KEY, True)
    config.set(telemetry.ENABLED_CONFIG_KEY, False)

    assert telemetry.consent_granted() is True
    assert telemetry.should_send() is False


def test_turning_telemetry_off_also_retires_the_notice(config) -> None:
    telemetry.set_enabled(False)

    assert telemetry.is_telemetry_enabled() is False
    assert telemetry.notice_pending() is False


def test_notice_stamp_is_write_once(config) -> None:
    telemetry.mark_notice_shown()
    first = config.get(telemetry.NOTICE_SHOWN_CONFIG_KEY)

    telemetry.mark_notice_shown()

    # A later render must not push the clock forward, or the grace period could
    # be extended indefinitely by simply reloading the page.
    assert config.get(telemetry.NOTICE_SHOWN_CONFIG_KEY) == first


# --- Payload ----------------------------------------------------------------

EXPECTED_PAYLOAD_FIELDS = {
    "schema",
    "install_id",
    "version",
    "install_age_days",
    "local_origin",
    "users_total",
    "users_recording_28d",
    "recordings_total",
    "recordings_28d",
    "recording_hours_28d",
    "llm_provider",
    "secondary_configured",
    "cli_oauth_in_use",
    "meeting_edge_enabled",
    "asr_engine",
    "whisper_model_size",
    "gpu",
    "calendar_connected",
    "mcp_in_use",
    "chat_used_28d",
    "documents_used",
    "tasks_used",
    "people_library_used",
}


def test_payload_contains_exactly_the_documented_fields(config, data_dir) -> None:
    # The lock on the disclosure: adding a field to the ping fails here until
    # docs/TELEMETRY.md is updated in the same change.
    payload = telemetry.build_payload(FakeSession())

    assert set(payload) == EXPECTED_PAYLOAD_FIELDS


def test_payload_carries_nothing_identifying(config, data_dir, monkeypatch) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://meetings.example.com")

    serialised = json.dumps(telemetry.build_payload(FakeSession()))

    for forbidden in [
        "meetings.example.com",
        "example.com",
        "https://",
        "/app/",
        "postgres",
        "redis",
    ]:
        assert forbidden not in serialised


def test_payload_reports_but_does_not_suppress_a_local_origin(
    config, data_dir, monkeypatch
) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://localhost:14443")
    assert telemetry.build_payload(FakeSession())["local_origin"] is True

    monkeypatch.setenv("WEB_APP_URL", "https://meetings.example.com")
    assert telemetry.build_payload(FakeSession())["local_origin"] is False


def test_payload_declares_the_schema_version(config, data_dir) -> None:
    assert telemetry.build_payload(FakeSession())["schema"] == (
        telemetry.TELEMETRY_SCHEMA_VERSION
    )


def test_payload_has_no_client_timestamp(config, data_dir) -> None:
    # The ingest derives the day bucket from its own clock, so a skewed client
    # cannot land a row in the wrong day or the future.
    payload = telemetry.build_payload(FakeSession())

    assert not any("time" in key or key.endswith("_at") for key in payload)


# --- Sending ----------------------------------------------------------------


def test_send_failure_is_swallowed_rather_than_raised(monkeypatch) -> None:
    import httpx

    def explode(*_args, **_kwargs):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "post", explode)

    result = telemetry.send_payload({"schema": 1})

    assert result.ok is False
    assert "could not be reached" in result.detail


def test_a_timeout_is_reported_differently_from_an_unreachable_host(
    monkeypatch,
) -> None:
    import httpx

    def stall(*_args, **_kwargs):
        raise httpx.ReadTimeout("too slow")

    monkeypatch.setattr(httpx, "post", stall)

    # The remedies differ, so the two must not collapse into one message.
    assert "did not respond" in telemetry.send_payload({"schema": 1}).detail


def test_an_unexpected_transport_failure_is_named_rather_than_swallowed(
    monkeypatch,
) -> None:
    import httpx

    def explode(*_args, **_kwargs):
        raise httpx.TooManyRedirects("looping")

    monkeypatch.setattr(httpx, "post", explode)

    result = telemetry.send_payload({"schema": 1})

    assert result.ok is False
    assert "TooManyRedirects" in result.detail


def test_rejected_ping_reports_failure(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(400, request=httpx.Request("POST", "https://x")),
    )

    result = telemetry.send_payload({"schema": 1})

    assert result.ok is False
    assert "400" in result.detail


def test_accepted_ping_reports_success(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(204, request=httpx.Request("POST", "https://x")),
    )

    result = telemetry.send_payload({"schema": 1})

    assert result.ok is True
    assert result.detail is None


# --- Send bookkeeping -------------------------------------------------------


def test_a_failed_attempt_is_recorded_without_advancing_last_sent(redis_stub) -> None:
    moment = datetime.now(timezone.utc)

    telemetry.record_attempt(moment, telemetry.SendResult(False, "Nope."))

    # The whole point: an install that has been failing must not read as one
    # that has never tried, and must not claim to have sent anything either.
    assert telemetry.get_last_attempt() == {
        "at": moment.isoformat(),
        "ok": False,
        "detail": "Nope.",
    }
    assert telemetry.get_last_sent_at() is None


def test_a_successful_attempt_advances_both_markers(redis_stub) -> None:
    moment = datetime.now(timezone.utc)

    telemetry.record_attempt(moment, telemetry.SendResult(True))

    assert telemetry.get_last_attempt() == {
        "at": moment.isoformat(),
        "ok": True,
        "detail": None,
    }
    assert telemetry.get_last_sent_at() == moment


def test_an_untried_install_reports_no_attempt(redis_stub) -> None:
    assert telemetry.get_last_attempt() is None


def test_a_corrupt_attempt_marker_reads_as_untried(redis_stub) -> None:
    redis_stub.values[telemetry.LAST_ATTEMPT_REDIS_KEY] = "not json"

    # Falls back to what the consent state alone can say, rather than raising
    # on a display-only path.
    assert telemetry.get_last_attempt() is None


def test_status_reports_the_failed_attempt_alongside_the_silent_last_sent(
    config, data_dir, redis_stub
) -> None:
    moment = datetime.now(timezone.utc)
    telemetry.record_attempt(
        moment, telemetry.SendResult(False, "The collector could not be reached.")
    )

    status = telemetry.telemetry_status()

    assert status["last_sent_at"] is None
    assert status["last_attempt_at"] == moment.isoformat()
    assert status["last_attempt_ok"] is False
    assert status["last_attempt_detail"] == "The collector could not be reached."


def test_the_task_records_an_attempt_that_the_collector_rejected(
    config, data_dir, redis_stub, monkeypatch
) -> None:
    import httpx

    config.set(telemetry.ACKNOWLEDGED_CONFIG_KEY, True)
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(400, request=httpx.Request("POST", "https://x")),
    )
    # The task reaches the database only to assemble the payload, which is
    # covered on its own elsewhere. Stand both in so this exercises the path
    # from a rejected response to the marker an admin reads.
    monkeypatch.setattr(send_telemetry_ping_task, "_session", FakeSession())
    monkeypatch.setattr(telemetry, "build_payload", lambda _session: {"schema": 1})

    send_telemetry_ping_task.run()

    attempt = telemetry.get_last_attempt()
    assert attempt is not None
    assert attempt["ok"] is False
    assert "400" in attempt["detail"]
    assert telemetry.get_last_sent_at() is None


def test_endpoint_is_overridable_by_environment(monkeypatch) -> None:
    assert telemetry.telemetry_endpoint() == telemetry.DEFAULT_TELEMETRY_ENDPOINT

    monkeypatch.setenv(
        telemetry.TELEMETRY_ENDPOINT_ENV_KEY, "https://example.invalid/p"
    )
    assert telemetry.telemetry_endpoint() == "https://example.invalid/p"


def test_task_sends_nothing_when_consent_is_absent(
    config, data_dir, monkeypatch
) -> None:
    import httpx

    def explode(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("telemetry must not be sent without consent")

    monkeypatch.setattr(httpx, "post", explode)

    # No acknowledgement and no shown notice: the upgraded-install state.
    send_telemetry_ping_task.run()


# --- Beat wiring ------------------------------------------------------------


def test_telemetry_ping_is_scheduled_six_hourly_on_the_io_lane() -> None:
    from backend.celery_app import TASK_ROUTES, celery_app

    entry = celery_app.conf.beat_schedule["send-telemetry-ping-every-6h"]
    assert entry["task"] == "backend.worker.tasks.send_telemetry_ping_task"
    # Six hours, not a day: beat re-anchors an interval on every worker restart,
    # so a daily interval can skip a calendar day outright and read downstream
    # as the install having gone quiet.
    assert entry["schedule"] == 21600.0

    # Must stay off the single-slot GPU lane: a network call has no business
    # occupying the worker that holds the card.
    assert TASK_ROUTES["backend.worker.tasks.send_telemetry_ping_task"] == {
        "queue": "io"
    }
