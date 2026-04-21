from datetime import datetime, timedelta
from typing import Any, Optional, Union
from jose import jwt
from passlib.context import CryptContext
import os
import secrets
from pathlib import Path

from backend.utils.time import utc_now

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


from backend.utils.path_manager import path_manager


SESSION_TOKEN_TYPE = "session"
API_TOKEN_TYPE = "api"
COMPANION_TOKEN_TYPE = "companion"

WEB_SESSION_SCOPE = "session:web"
API_ACCESS_SCOPE = "api:full"
COMPANION_BOOTSTRAP_SCOPE = "companion:init"
COMPANION_RECORDING_SCOPE = "recordings:companion"
COMPANION_PAIRING_ID_CLAIM = "companion_pairing_id"

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
    if key_file.exists():
        return key_file.read_text().strip()
    
    new_key = secrets.token_hex(32)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(new_key)
    return new_key


SECRET_KEY = _get_secret_key()
ALGORITHM = "HS256"

SESSION_TOKEN_EXPIRE_MINUTES = 12 * 60
API_TOKEN_EXPIRE_MINUTES = 60
COMPANION_BOOTSTRAP_TOKEN_EXPIRE_MINUTES = 12 * 60
COMPANION_RECORDING_TOKEN_EXPIRE_MINUTES = 12 * 60

TOKEN_EXPIRY_MINUTES = {
    SESSION_TOKEN_TYPE: SESSION_TOKEN_EXPIRE_MINUTES,
    API_TOKEN_TYPE: API_TOKEN_EXPIRE_MINUTES,
    COMPANION_TOKEN_TYPE: COMPANION_BOOTSTRAP_TOKEN_EXPIRE_MINUTES,
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

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)
