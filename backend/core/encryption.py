import base64
import hashlib
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from backend.utils.path_manager import path_manager


def _get_encryption_seed() -> str:
    if env_seed := os.getenv("DATA_ENCRYPTION_KEY"):
        return env_seed

    key_file = path_manager.user_data_directory / ".data_encryption_key"
    _migrate_legacy_encryption_key_file(key_file)
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()

    new_seed = secrets.token_urlsafe(48)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(new_seed, encoding="utf-8")
    return new_seed


def _legacy_documents_key_file(file_name: str) -> Path:
    return Path.home() / "Documents" / "Nojoin" / file_name


def _migrate_legacy_encryption_key_file(current_key_file: Path) -> None:
    legacy_key_file = _legacy_documents_key_file(current_key_file.name)
    if legacy_key_file == current_key_file or not legacy_key_file.exists():
        return

    legacy_value = legacy_key_file.read_text(encoding="utf-8").strip()
    current_value = None
    if current_key_file.exists():
        current_value = current_key_file.read_text(encoding="utf-8").strip()

    if current_value != legacy_value:
        current_key_file.parent.mkdir(parents=True, exist_ok=True)
        current_key_file.write_text(legacy_value, encoding="utf-8")

    migrated_key_file = legacy_key_file.with_name(f"{legacy_key_file.name}.migrated")
    try:
        legacy_key_file.replace(migrated_key_file)
    except OSError:
        pass


def _build_fernet() -> Fernet:
    seed = _get_encryption_seed().encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    return _build_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _build_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Stored secret could not be decrypted") from exc