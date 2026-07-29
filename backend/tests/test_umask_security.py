import os

from backend import setup_secure_umask
from backend.utils.path_manager import PathManager


def _reset_path_manager_singleton() -> None:
    PathManager._instance = None


def test_setup_secure_umask_parsing(monkeypatch):
    # Store actual umask to restore later
    original_umask = os.umask(0)
    os.umask(original_umask)

    try:
        # Case 1: Default umask (no env var)
        monkeypatch.delenv("NOJOIN_UMASK", raising=False)
        setup_secure_umask()
        current_umask = os.umask(0)
        os.umask(current_umask)
        assert current_umask == 0o077

        # Case 2: Standard octal umask
        monkeypatch.setenv("NOJOIN_UMASK", "0022")
        setup_secure_umask()
        current_umask = os.umask(0)
        os.umask(current_umask)
        assert current_umask == 0o022

        # Case 3: Octal with 0o prefix
        monkeypatch.setenv("NOJOIN_UMASK", "0o027")
        setup_secure_umask()
        current_umask = os.umask(0)
        os.umask(current_umask)
        assert current_umask == 0o027

        # Case 4: Decimal representation
        monkeypatch.setenv("NOJOIN_UMASK", "18")  # 18 in decimal is 0o022 in octal
        setup_secure_umask()
        current_umask = os.umask(0)
        os.umask(current_umask)
        assert current_umask == 0o022

        # Case 5: Invalid umask fallback to 0077
        monkeypatch.setenv("NOJOIN_UMASK", "invalid_umask")
        setup_secure_umask()
        current_umask = os.umask(0)
        os.umask(current_umask)
        assert current_umask == 0o077

    finally:
        os.umask(original_umask)


def test_repair_data_permissions(tmp_path, monkeypatch):
    # Prepare mock path manager using tmp_path as user data directory
    _reset_path_manager_singleton()
    monkeypatch.setattr(PathManager, "_get_project_root", lambda self: tmp_path)
    monkeypatch.setattr(PathManager, "_is_containerized_runtime", lambda self: True)

    manager = PathManager()
    # Override user data directory to a temp path we control
    user_data_dir = tmp_path / "data"
    user_data_dir.mkdir(parents=True)
    monkeypatch.setattr(manager, "_user_data_directory", user_data_dir)

    # Create subdirectories and files with wide open permissions (0o777 / 0o666 or similar)
    sub_dir = user_data_dir / "logs"
    sub_dir.mkdir()
    sub_dir.chmod(0o755)

    test_file = sub_dir / "app.log"
    test_file.write_text("log data", encoding="utf-8")
    test_file.chmod(0o644)

    secret_file = user_data_dir / ".secret_key"
    secret_file.write_text("secret keyring", encoding="utf-8")
    secret_file.chmod(0o644)

    # Execute repair pass
    manager.repair_data_permissions()

    # Check that directories are 0o700
    assert (user_data_dir.stat().st_mode & 0o777) == 0o700
    assert (sub_dir.stat().st_mode & 0o777) == 0o700

    # Check that files are 0o600
    assert (test_file.stat().st_mode & 0o777) == 0o600
    assert (secret_file.stat().st_mode & 0o777) == 0o600

    _reset_path_manager_singleton()


def test_repair_data_permissions_does_not_follow_symlinks(tmp_path, monkeypatch):
    """A link inside the data directory must not re-permission its target.

    Regression test for issue #164: the Codex CLI persists argv[0] dispatch
    links under the data volume that point at its own binary inside the image,
    and the repair pass chmod-ed the binary to 0600 through them, leaving it
    unexecutable for the worker.
    """
    _reset_path_manager_singleton()
    monkeypatch.setattr(PathManager, "_get_project_root", lambda self: tmp_path)
    monkeypatch.setattr(PathManager, "_is_containerized_runtime", lambda self: True)

    manager = PathManager()
    user_data_dir = tmp_path / "data"
    user_data_dir.mkdir(parents=True)
    monkeypatch.setattr(manager, "_user_data_directory", user_data_dir)

    # Targets outside the data directory, standing in for files in the image.
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_binary = outside / "codex"
    outside_binary.write_text("#!/bin/sh\n", encoding="utf-8")
    outside_binary.chmod(0o755)
    outside_dir = outside / "vendor"
    outside_dir.mkdir()
    outside_dir.chmod(0o755)

    # Links persisted inside the data directory, as the Codex CLI leaves them.
    arg0_dir = user_data_dir / "cli-oauth" / "1" / "codex" / "tmp" / "arg0"
    arg0_dir.mkdir(parents=True)
    file_link = arg0_dir / "apply_patch"
    file_link.symlink_to(outside_binary)
    dir_link = arg0_dir / "vendor"
    dir_link.symlink_to(outside_dir, target_is_directory=True)

    manager.repair_data_permissions()

    # Link targets are untouched, so the binary stays executable.
    assert (outside_binary.stat().st_mode & 0o777) == 0o755
    assert (outside_dir.stat().st_mode & 0o777) == 0o755

    # Real entries in the same tree are still repaired.
    assert (arg0_dir.stat().st_mode & 0o777) == 0o700

    _reset_path_manager_singleton()
