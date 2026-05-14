from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from backend.utils.path_manager import path_manager

_IDENTITY_KEY_FILENAME = ".companion_backend_identity_ed25519.pem"


@dataclass(frozen=True)
class BackendIdentity:
    key_id: str
    public_key: str


def _identity_key_path() -> Path:
    return path_manager.user_data_directory / _IDENTITY_KEY_FILENAME


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def _canonical_signature_message(fields: dict[str, str]) -> bytes:
    parts = [f"{key}={fields[key]}" for key in sorted(fields)]
    return "\n".join(parts).encode("utf-8")


def _load_or_create_private_key() -> Ed25519PrivateKey:
    key_path = _identity_key_path()
    if key_path.exists():
        return serialization.load_pem_private_key(
            key_path.read_bytes(),
            password=None,
        )

    key_path.parent.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return private_key


def get_backend_identity() -> BackendIdentity:
    private_key = _load_or_create_private_key()
    public_key_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_key = _b64_encode(public_key_bytes)
    key_id = hashlib.sha256(public_key_bytes).hexdigest()[:16]
    return BackendIdentity(key_id=key_id, public_key=public_key)


def sign_backend_identity_fields(fields: dict[str, str]) -> str:
    private_key = _load_or_create_private_key()
    signature = private_key.sign(_canonical_signature_message(fields))
    return _b64_encode(signature)


def verify_backend_identity_fields(
    *,
    public_key: str,
    fields: dict[str, str],
    signature: str,
) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(_b64_decode(public_key)).verify(
            _b64_decode(signature),
            _canonical_signature_message(fields),
        )
    except Exception:
        return False
    return True