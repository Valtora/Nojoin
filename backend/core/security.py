import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Optional, Union

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from jose import jwt

from backend.utils.path_manager import path_manager
from backend.utils.time import utc_now

password_hasher = PasswordHasher()

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 8


SESSION_TOKEN_TYPE = "session"
API_TOKEN_TYPE = "api"

WEB_SESSION_SCOPE = "session:web"
API_ACCESS_SCOPE = "api:full"

ALGORITHM = "HS256"
DEFAULT_LEGACY_KID = "legacy"
_KEYRING_FILENAME = ".secret_keys.json"
_LEGACY_KEY_FILENAME = ".secret_key"
_keyring_lock = Lock()


def _keyring_path() -> Path:
    return path_manager.user_data_directory / _KEYRING_FILENAME


def _legacy_key_path() -> Path:
    return path_manager.user_data_directory / _LEGACY_KEY_FILENAME


def _new_kid() -> str:
    return f"k_{secrets.token_hex(4)}"


def _read_keyring_file() -> Optional[dict[str, Any]]:
    keyring_file = _keyring_path()
    if not keyring_file.exists():
        return None
    try:
        data = json.loads(keyring_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Unable to load JWT keyring at {keyring_file}: {exc}"
        ) from exc
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("keys"), dict)
        or not isinstance(data.get("active"), str)
        or data["active"] not in data["keys"]
    ):
        raise RuntimeError(f"JWT keyring at {keyring_file} is malformed.")
    return data


def _write_keyring_file(data: dict[str, Any]) -> None:
    keyring_file = _keyring_path()
    keyring_file.parent.mkdir(parents=True, exist_ok=True)
    keyring_file.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    try:
        keyring_file.chmod(0o600)
    except OSError as e:
        logger.warning("Could not set owner-only permissions on keyring file %s: %s", keyring_file, e)


def _bootstrap_keyring() -> dict[str, Any]:
    legacy_file = _legacy_key_path()
    _migrate_legacy_secret_file(legacy_file)

    if legacy_file.exists():
        legacy_value = legacy_file.read_text(encoding="utf-8").strip()
        data = {
            "active": DEFAULT_LEGACY_KID,
            "keys": {DEFAULT_LEGACY_KID: legacy_value},
        }
        _write_keyring_file(data)
        try:
            legacy_file.replace(legacy_file.with_name(f"{legacy_file.name}.migrated"))
        except OSError:
            logger.warning(
                "Loaded legacy SECRET_KEY into keyring but could not move legacy file %s.",
                legacy_file,
            )
        else:
            logger.info("Migrated legacy SECRET_KEY into JWT keyring at %s.", _keyring_path())
        return data

    kid = _new_kid()
    data = {"active": kid, "keys": {kid: secrets.token_hex(32)}}
    _write_keyring_file(data)
    return data


def _load_keyring() -> dict[str, Any]:
    """Load the JWT signing keyring.

    Priority:
    1. SECRET_KEY environment variable (treated as a single static key with kid='env').
    2. Persistent keyring file at <user_data>/.secret_keys.json.
    3. Migrate legacy <user_data>/.secret_key into a new keyring, or auto-generate.
    """
    if env_key := os.getenv("SECRET_KEY"):
        return {"active": "env", "keys": {"env": env_key}}

    with _keyring_lock:
        existing = _read_keyring_file()
        if existing is not None:
            return existing
        return _bootstrap_keyring()


def get_signing_keyring() -> dict[str, Any]:
    return _load_keyring()


def get_active_signing_key() -> tuple[str, str]:
    keyring = _load_keyring()
    active_kid = keyring["active"]
    return active_kid, keyring["keys"][active_kid]


def get_signing_key_for_kid(kid: Optional[str]) -> Optional[str]:
    keyring = _load_keyring()
    if not kid:
        # Tokens issued before kid was introduced: fall back to the active key.
        return keyring["keys"].get(keyring["active"])
    return keyring["keys"].get(kid)


def rotate_signing_key(*, retire_others: bool = False) -> str:
    """Generate a new active signing key. Returns the new kid.

    If ``retire_others`` is True, all previously stored keys are removed at
    once (use only when you are certain no live tokens reference them).
    Otherwise old keys are kept so existing tokens keep verifying until
    they expire and you call :func:`prune_signing_keys`.
    """
    if os.getenv("SECRET_KEY"):
        raise RuntimeError(
            "Cannot rotate keyring while SECRET_KEY environment variable is set."
        )
    with _keyring_lock:
        existing = _read_keyring_file() or _bootstrap_keyring()
        new_kid = _new_kid()
        while new_kid in existing["keys"]:
            new_kid = _new_kid()
        if retire_others:
            existing["keys"] = {}
        existing["keys"][new_kid] = secrets.token_hex(32)
        existing["active"] = new_kid
        _write_keyring_file(existing)
        logger.info("Rotated JWT signing key. New active kid=%s.", new_kid)
        return new_kid


def prune_signing_keys(keep_kids: set[str]) -> list[str]:
    """Remove all stored keys whose kid is not in ``keep_kids`` (active kid
    is always kept). Returns the list of removed kids."""
    if os.getenv("SECRET_KEY"):
        return []
    with _keyring_lock:
        existing = _read_keyring_file()
        if existing is None:
            return []
        kept = set(keep_kids) | {existing["active"]}
        removed = [kid for kid in list(existing["keys"]) if kid not in kept]
        for kid in removed:
            existing["keys"].pop(kid, None)
        if removed:
            _write_keyring_file(existing)
            logger.info("Pruned retired JWT signing keys: %s", removed)
        return removed


def _legacy_documents_secret_file(file_name: str) -> Path:
    return Path.home() / "Documents" / "Nojoin" / file_name


def _migrate_legacy_secret_file(
    current_key_file: Path,
    legacy_key_file: Optional[Path] = None,
) -> None:
    legacy_key_file = legacy_key_file or _legacy_documents_secret_file(current_key_file.name)
    if legacy_key_file == current_key_file or not legacy_key_file.exists():
        return

    legacy_value = legacy_key_file.read_text(encoding="utf-8").strip()
    current_value = None
    if current_key_file.exists():
        current_value = current_key_file.read_text(encoding="utf-8").strip()

    if current_value != legacy_value:
        current_key_file.parent.mkdir(parents=True, exist_ok=True)
        current_key_file.write_text(legacy_value, encoding="utf-8")
        try:
            current_key_file.chmod(0o600)
        except OSError as e:
            logger.warning("Could not set owner-only permissions on migrated secret file %s: %s", current_key_file, e)
        logger.warning(
            "Migrated legacy SECRET_KEY from %s to %s so tokens survive container restarts.",
            legacy_key_file,
            current_key_file,
        )

    migrated_key_file = legacy_key_file.with_name(f"{legacy_key_file.name}.migrated")
    try:
        legacy_key_file.replace(migrated_key_file)
    except OSError:
        logger.warning(
            "Unable to move legacy SECRET_KEY file %s out of the legacy path.",
            legacy_key_file,
        )


SESSION_TOKEN_EXPIRE_MINUTES = 12 * 60
API_TOKEN_EXPIRE_MINUTES = 60

TOKEN_EXPIRY_MINUTES = {
    SESSION_TOKEN_TYPE: SESSION_TOKEN_EXPIRE_MINUTES,
    API_TOKEN_TYPE: API_TOKEN_EXPIRE_MINUTES,
}

_REVOCABLE_TOKEN_TYPES = frozenset({SESSION_TOKEN_TYPE, API_TOKEN_TYPE})


def _new_jti() -> str:
    return uuid.uuid4().hex


def create_access_token(
    subject: Union[str, Any],
    *,
    token_type: str,
    scopes: Optional[list[str]] = None,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[dict[str, Any]] = None,
    token_version: Optional[int] = None,
) -> str:
    if expires_delta:
        expire = utc_now() + expires_delta
    else:
        minutes = TOKEN_EXPIRY_MINUTES.get(token_type)
        if minutes is None:
            raise ValueError(f"Unsupported token type: {token_type}")
        expire = utc_now() + timedelta(minutes=minutes)

    issued_at = utc_now()
    to_encode: dict[str, Any] = {
        "exp": expire,
        "iat": issued_at,
        "sub": str(subject),
        "token_type": token_type,
        "scopes": sorted(set(scopes or [])),
    }
    if token_type in _REVOCABLE_TOKEN_TYPES:
        to_encode["jti"] = _new_jti()
        if token_version is None:
            raise ValueError(
                f"token_version is required when minting {token_type} tokens."
            )
        to_encode["tv"] = int(token_version)
    if extra_claims:
        to_encode.update(extra_claims)

    active_kid, signing_key = get_active_signing_key()
    return jwt.encode(
        to_encode,
        signing_key,
        algorithm=ALGORITHM,
        headers={"kid": active_kid},
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT using the keyring entry indicated by its ``kid``.

    Raises :class:`jose.JWTError` if the token cannot be verified with any
    known key.
    """
    try:
        unverified_header = jwt.get_unverified_header(token)
    except Exception:  # pragma: no cover - defensive: jose raises subclass of Exception
        unverified_header = {}
    kid = unverified_header.get("kid") if isinstance(unverified_header, dict) else None
    signing_key = get_signing_key_for_kid(kid)
    if signing_key is None:
        from jose import JWTError

        raise JWTError("Unknown signing key id")
    return jwt.decode(token, signing_key, algorithms=[ALGORITHM])


def validate_password_policy(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )
    if password.isspace():
        raise ValueError("Password cannot be all whitespace.")
    return password


def hash_user_password(password: str) -> str:
    return get_password_hash(validate_password_policy(password))

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return password_hasher.verify(hashed_password, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False

def get_password_hash(password: str) -> str:
    return password_hasher.hash(password)
