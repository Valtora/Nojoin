"""ChatGPT (Codex) device-login in the io lane.

The connect endpoint dispatches here because only ``worker-io`` has the codex
binary and a pty. This drives ``codex login --device-auth`` (see
:mod:`backend.processing.cli.codex_login`), publishes the verification URL + code
to Redis for the API to relay to the browser, and on success stores the resulting
``auth.json`` encrypted, then wipes the plaintext copy (encrypted-at-rest).

Long-lived: the task blocks a worker-io slot until the user approves or the code
lapses (~15 min). Acceptable for an infrequent connect action.
"""

from backend.processing.cli.codex_login import (
    CodexLoginError,
    codex_home_for,
    run_device_login,
)
from backend.services.cli_oauth import codex_oauth
from backend.services.cli_oauth.persistence import store_codex_auth_blob_sync

from .constants import *  # noqa: F401,F403 - shared task imports (celery_app, logger)


@celery_app.task(  # noqa: F405
    name="backend.worker.tasks.codex_device_login_task",
    bind=True,  # noqa: F405
)
def codex_device_login_task(self, user_id: int) -> None:
    def _publish_code(verification_uri: str, user_code: str) -> None:
        codex_oauth.publish_login_state(
            user_id,
            {
                "status": codex_oauth.STATUS_AWAITING,
                "verification_uri": verification_uri,
                "user_code": user_code,
            },
        )

    try:
        auth_blob = run_device_login(user_id, _publish_code)
        store_codex_auth_blob_sync(user_id, auth_blob)
        # No plaintext credential left at rest: the driver re-materialises
        # auth.json per inference from the encrypted store.
        _wipe_auth_json(user_id)
        codex_oauth.publish_login_state(
            user_id, {"status": codex_oauth.STATUS_CONNECTED}
        )
        logger.info("Codex device login connected for user %s.", user_id)  # noqa: F405
    except CodexLoginError as exc:
        _wipe_auth_json(user_id)
        codex_oauth.publish_login_state(
            user_id, {"status": codex_oauth.STATUS_FAILED, "detail": str(exc)[:200]}
        )
        logger.warning(  # noqa: F405
            "Codex device login failed for user %s: %s", user_id, exc
        )
    except Exception as exc:  # noqa: BLE001 - report any failure to the client
        _wipe_auth_json(user_id)
        codex_oauth.publish_login_state(
            user_id,
            {"status": codex_oauth.STATUS_FAILED, "detail": "Sign-in failed."},
        )
        logger.exception(  # noqa: F405
            "Codex device login errored for user %s: %s", user_id, exc
        )


def _wipe_auth_json(user_id: int) -> None:
    try:
        (codex_home_for(user_id) / "auth.json").unlink(missing_ok=True)
    except OSError:
        pass
