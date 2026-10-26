"""The default test environment keeps the data directory out of the checkout."""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.api.v1.endpoints import documents as documents_endpoint
from backend.core import encryption, security
from backend.utils import config_manager, logging_config, telemetry
from backend.utils.path_manager import PathManager, path_manager

REPOSITORY = Path(__file__).resolve().parents[2]


def test_every_data_path_resolves_under_the_suite_data_dir(
    suite_data_dir: Path,
) -> None:
    """Take no data fixture: ordinary backend tests must already be isolated.

    The config path, the log path and the documents directory are fixed when
    their modules import, so this also catches an import that ran before
    conftest.py set NOJOIN_DATA_DIR.
    """
    assert not suite_data_dir.is_relative_to(REPOSITORY)
    assert path_manager.user_data_directory == suite_data_dir
    assert PathManager().user_data_directory == suite_data_dir

    paths = {
        "config_manager.CONFIG_PATH": Path(config_manager.CONFIG_PATH),
        "config_manager.config_path": Path(config_manager.config_manager.config_path),
        "path_manager.config_path": path_manager.config_path,
        "JWT keyring": security._keyring_path(),
        "legacy JWT key": security._legacy_key_path(),
        "install id": telemetry._install_id_path(),
        "log": Path(logging_config.LOG_PATH),
        "documents": Path(documents_endpoint.DOCUMENTS_DIR),
        "recordings": path_manager.recordings_directory,
    }
    outside = {
        name: path
        for name, path in paths.items()
        if not path.is_relative_to(suite_data_dir)
    }
    assert outside == {}
    # config_manager wrote its defaults on import, into the temporary directory.
    assert Path(config_manager.CONFIG_PATH).is_file()


def test_generated_keys_are_written_to_the_suite_data_dir(
    suite_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)

    security.get_active_signing_key()
    secret = encryption.encrypt_secret("value")

    assert (suite_data_dir / ".secret_keys.json").is_file()
    assert (suite_data_dir / ".data_encryption_key").is_file()
    assert encryption.decrypt_secret(secret) == "value"


def test_a_subprocess_resolves_the_same_data_dir(suite_data_dir: Path) -> None:
    """Tests that import the backend in a subprocess inherit the variable."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from backend.utils.path_manager import path_manager; "
            "print(path_manager.user_data_directory)",
        ],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )

    assert Path(result.stdout.strip()) == suite_data_dir


def test_a_second_load_of_conftest_keeps_the_data_dir(suite_data_dir: Path) -> None:
    """test_single_flight.py imports backend.tests.conftest, which runs it again."""
    importlib.import_module("backend.tests.conftest")

    assert os.environ["NOJOIN_DATA_DIR"] == str(suite_data_dir)
