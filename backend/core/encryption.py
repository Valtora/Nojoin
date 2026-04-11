import base64
import hashlib
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken

from backend.utils.path_manager import path_manager


def _get_encryption_seed() -> str:
    if env_seed := os.getenv("DATA_ENCRYPTION_KEY"):
        return env_seed

    key_file = path_manager.user_data_directory / ".data_encryption_key"
    if key_file.exists():
        return key_file.read_text().strip()

    new_seed = secrets.token_urlsafe(48)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(new_seed)
    return new_seed


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