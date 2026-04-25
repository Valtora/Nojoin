from datetime import datetime, timedelta
import hashlib
import logging
from typing import Any, Optional, Union
from jose import jwt
from passlib.context import CryptContext
import os
import secrets
from pathlib import Path

from backend.utils.time import utc_now

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

logger = logging.getLogger(__name__)


from backend.utils.path_manager import path_manager


SESSION_TOKEN_TYPE = "session"
API_TOKEN_TYPE = "api"
COMPANION_TOKEN_TYPE = "companion"
COMPANION_LOCAL_CONTROL_TOKEN_TYPE = "companion_local_control"

WEB_SESSION_SCOPE = "session:web"
API_ACCESS_SCOPE = "api:full"
COMPANION_BOOTSTRAP_SCOPE = "companion:init"
COMPANION_RECORDING_SCOPE = "recordings:companion"
COMPANION_PAIRING_ID_CLAIM = "companion_pairing_id"
COMPANION_LOCAL_CONTROL_AUDIENCE = "nojoin-companion-local"
COMPANION_LOCAL_CONTROL_TOKEN_EXPIRE_SECONDS = 120
LOCAL_CONTROL_STATUS_READ_ACTION = "status:read"
LOCAL_CONTROL_SETTINGS_READ_ACTION = "settings:read"
LOCAL_CONTROL_SETTINGS_WRITE_ACTION = "settings:write"
LOCAL_CONTROL_DEVICES_READ_ACTION = "devices:read"
LOCAL_CONTROL_WAVEFORM_READ_ACTION = "waveform:read"
LOCAL_CONTROL_RECORDING_START_ACTION = "recording:start"
LOCAL_CONTROL_RECORDING_STOP_ACTION = "recording:stop"
LOCAL_CONTROL_RECORDING_PAUSE_ACTION = "recording:pause"
LOCAL_CONTROL_RECORDING_RESUME_ACTION = "recording:resume"
LOCAL_CONTROL_UPDATE_TRIGGER_ACTION = "update:trigger"
LOCAL_CONTROL_ALLOWED_ACTIONS = frozenset(
    {
        LOCAL_CONTROL_STATUS_READ_ACTION,
        LOCAL_CONTROL_SETTINGS_READ_ACTION,
        LOCAL_CONTROL_SETTINGS_WRITE_ACTION,
        LOCAL_CONTROL_DEVICES_READ_ACTION,
        LOCAL_CONTROL_WAVEFORM_READ_ACTION,
        LOCAL_CONTROL_RECORDING_START_ACTION,
        LOCAL_CONTROL_RECORDING_STOP_ACTION,
        LOCAL_CONTROL_RECORDING_PAUSE_ACTION,
        LOCAL_CONTROL_RECORDING_RESUME_ACTION,
        LOCAL_CONTROL_UPDATE_TRIGGER_ACTION,
    }
)

def _get_secret_key() -> str:
    """Get or generate a persistent SECRET_KEY.
    
    Priority:
    1. SECRET_KEY environment variable (for advanced deployments)
    2. Persistent key file at /app/data/.secret_key
    3. Auto-generate and persist a new secure key
    """
    if env_key := os.getenv("SECRET_KEY"):
        return env_key
    
    key_file = path_manager.user_data_directory / ".secret_key"
    _migrate_legacy_secret_file(key_file)
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    
    new_key = secrets.token_hex(32)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(new_key, encoding="utf-8")
    return new_key


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


SECRET_KEY = _get_secret_key()
ALGORITHM = "HS256"

SESSION_TOKEN_EXPIRE_MINUTES = 12 * 60
API_TOKEN_EXPIRE_MINUTES = 60
COMPANION_ACCESS_TOKEN_EXPIRE_MINUTES = 5
COMPANION_ACCESS_TOKEN_EXPIRE_SECONDS = COMPANION_ACCESS_TOKEN_EXPIRE_MINUTES * 60
COMPANION_RECORDING_TOKEN_EXPIRE_MINUTES = 12 * 60

TOKEN_EXPIRY_MINUTES = {
    SESSION_TOKEN_TYPE: SESSION_TOKEN_EXPIRE_MINUTES,
    API_TOKEN_TYPE: API_TOKEN_EXPIRE_MINUTES,
    COMPANION_TOKEN_TYPE: COMPANION_ACCESS_TOKEN_EXPIRE_MINUTES,
}

def create_access_token(
    subject: Union[str, Any],
    *,
    token_type: str,
    scopes: Optional[list[str]] = None,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[dict[str, Any]] = None,
) -> str:
    if expires_delta:
        expire = utc_now() + expires_delta
    else:
        minutes = TOKEN_EXPIRY_MINUTES.get(token_type)
        if minutes is None:
            raise ValueError(f"Unsupported token type: {token_type}")
        expire = utc_now() + timedelta(minutes=minutes)
    
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "token_type": token_type,
        "scopes": sorted(set(scopes or [])),
    }
    if extra_claims:
        to_encode.update(extra_claims)

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def generate_local_control_secret() -> str:
    return secrets.token_urlsafe(48)


def generate_companion_credential_secret() -> str:
    return secrets.token_urlsafe(48)


def hash_companion_credential_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_companion_credential_secret(secret: str, expected_hash: str) -> bool:
    candidate_hash = hash_companion_credential_secret(secret)
    return secrets.compare_digest(candidate_hash, expected_hash)


def create_local_control_token(
    *,
    secret_key: str,
    subject: Union[str, Any],
    user_id: int,
    username: str,
    origin: str,
    actions: list[str],
    pairing_session_id: str,
    secret_version: int,
    expires_delta: Optional[timedelta] = None,
) -> str:
    expire = utc_now() + (
        expires_delta or timedelta(seconds=COMPANION_LOCAL_CONTROL_TOKEN_EXPIRE_SECONDS)
    )
    issued_at = utc_now()
    payload = {
        "aud": COMPANION_LOCAL_CONTROL_AUDIENCE,
        "exp": expire,
        "iat": issued_at,
        "sub": str(subject),
        "token_type": COMPANION_LOCAL_CONTROL_TOKEN_TYPE,
        "user_id": user_id,
        "username": username,
        "origin": origin,
        "actions": sorted(set(actions)),
        COMPANION_PAIRING_ID_CLAIM: pairing_session_id,
        "secret_version": secret_version,
    }
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)
