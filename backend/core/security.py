from datetime import datetime, timedelta
from typing import Any, Union, Optional
from jose import jwt
from passlib.context import CryptContext
import os
import secrets
from pathlib import Path

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


from backend.utils.path_manager import path_manager

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
# Set expiration to 30 days (43200 minutes) to prevent auth issues during long recordings
ACCESS_TOKEN_EXPIRE_MINUTES = 43200

def create_access_token(subject: Union[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)
