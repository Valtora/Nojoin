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


def test_notice_is_never_shown_when_the_environment_pins_the_value(
    config, monkeypatch
) -> None:
    monkeypatch.setenv(telemetry.TELEMETRY_ENABLED_ENV_KEY, "false")

    # Nothing the admin could do about it, so there is nothing to tell them.
    assert telemetry.notice_pending() is False


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

    assert telemetry.send_payload({"schema": 1}) is False


def test_rejected_ping_reports_failure(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(400, request=httpx.Request("POST", "https://x")),
    )

    assert telemetry.send_payload({"schema": 1}) is False


def test_accepted_ping_reports_success(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(204, request=httpx.Request("POST", "https://x")),
    )

    assert telemetry.send_payload({"schema": 1}) is True


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

    from backend.worker.tasks.system import send_telemetry_ping_task

    def explode(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("telemetry must not be sent without consent")

    monkeypatch.setattr(httpx, "post", explode)

    # No acknowledgement and no shown notice: the upgraded-install state.
    send_telemetry_ping_task.run()


# --- Beat wiring ------------------------------------------------------------


def test_telemetry_ping_is_scheduled_daily_on_the_io_lane() -> None:
    from backend.celery_app import TASK_ROUTES, celery_app

    entry = celery_app.conf.beat_schedule["send-telemetry-ping-every-24h"]
    assert entry["task"] == "backend.worker.tasks.send_telemetry_ping_task"
    assert entry["schedule"] == 86400.0

    # Must stay off the single-slot GPU lane: a network call has no business
    # occupying the worker that holds the card.
    assert TASK_ROUTES["backend.worker.tasks.send_telemetry_ping_task"] == {
        "queue": "io"
    }
