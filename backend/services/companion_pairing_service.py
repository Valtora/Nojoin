from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.core import security
from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.companion_pairing import (
    CompanionPairing,
    CompanionPairingRevocationReason,
    CompanionPairingStatus,
)
from backend.models.user import User
from backend.utils.config_manager import get_trusted_web_origin
from backend.utils.time import utc_now


class CompanionPairingStateError(Exception):
    def __init__(self, detail: str, *, status_code: int = 409):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class PreparedCompanionPairingPayload:
    pairing_code: str
    bootstrap_token: str
    expires_in: int
    api_protocol: str
    api_host: str
    api_port: int
    tls_fingerprint: str | None
    local_control_secret: str
    local_control_secret_version: int
    backend_pairing_id: str


@dataclass(frozen=True)
class ActiveCompanionPairingAuth:
    pairing_session_id: str
    paired_web_origin: str
    local_control_secret: str
    local_control_secret_version: int


def _normalize_origin(origin: str | None) -> str | None:
    if not origin:
        return None

    parsed = urlparse(origin.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None

    default_port = 443 if parsed.scheme == "https" else 80
    if parsed.port and parsed.port != default_port:
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    return f"{parsed.scheme}://{parsed.hostname}"


def normalize_origin(origin: str | None) -> str | None:
    return _normalize_origin(origin)


def _parse_api_origin(origin: str) -> tuple[str, str, int]:
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CompanionPairingStateError(
            "Backend pairing origin is not configured correctly.",
            status_code=500,
        )

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname, port


def _ensure_complete_pairing(row: CompanionPairing) -> None:
    if (
        not row.paired_web_origin
        or not row.api_protocol
        or not row.api_host
        or row.api_port <= 0
        or not row.local_control_secret_encrypted
        or row.local_control_secret_version < 1
    ):
        raise CompanionPairingStateError(
            "Companion pairing state is incomplete. Revoke the pairing and pair again."
        )


def _ensure_revoked_pairing_is_clean(row: CompanionPairing) -> None:
    if row.local_control_secret_encrypted is not None:
        raise CompanionPairingStateError(
            "Companion pairing cleanup is incomplete. Revoke the pairing and pair again."
        )
    if row.revoked_at is None or not row.revocation_reason:
        raise CompanionPairingStateError(
            "Companion pairing revocation metadata is incomplete. Revoke the pairing and pair again."
        )


def _group_pairings(
    rows: Iterable[CompanionPairing],
) -> tuple[list[CompanionPairing], list[CompanionPairing], list[CompanionPairing]]:
    active_rows: list[CompanionPairing] = []
    pending_rows: list[CompanionPairing] = []
    revoked_rows: list[CompanionPairing] = []

    for row in rows:
        if row.status == CompanionPairingStatus.ACTIVE.value:
            _ensure_complete_pairing(row)
            if row.revoked_at is not None or row.revocation_reason:
                raise CompanionPairingStateError(
                    "Companion pairing state is conflicting. Revoke the pairing and pair again."
                )
            active_rows.append(row)
        elif row.status == CompanionPairingStatus.PENDING.value:
            _ensure_complete_pairing(row)
            if row.revoked_at is not None or row.revocation_reason:
                raise CompanionPairingStateError(
                    "Companion pairing state is conflicting. Revoke the pairing and pair again."
                )
            pending_rows.append(row)
        elif row.status == CompanionPairingStatus.REVOKED.value:
            _ensure_revoked_pairing_is_clean(row)
            revoked_rows.append(row)
        else:
            raise CompanionPairingStateError(
                "Companion pairing state is invalid. Revoke the pairing and pair again."
            )

    if len(active_rows) > 1 or len(pending_rows) > 1:
        raise CompanionPairingStateError(
            "Companion pairing state is conflicting. Revoke the pairing and pair again."
        )

    return active_rows, pending_rows, revoked_rows


async def _load_pairings_for_user(db: AsyncSession, user_id: int) -> list[CompanionPairing]:
    result = await db.execute(
        select(CompanionPairing)
        .where(CompanionPairing.user_id == user_id)
        .order_by(
            CompanionPairing.local_control_secret_version.desc(),
            CompanionPairing.created_at.desc(),
        )
    )
    return list(result.scalars().all())


def _next_secret_version(rows: Iterable[CompanionPairing]) -> int:
    current = max((row.local_control_secret_version for row in rows), default=0)
    return current + 1


def _latest_live_secret_version(rows: Iterable[CompanionPairing]) -> int:
    current = max(
        (
            row.local_control_secret_version
            for row in rows
            if row.status != CompanionPairingStatus.REVOKED.value
        ),
        default=0,
    )
    return current


def _revoke_pairing_row(
    row: CompanionPairing,
    *,
    reason: CompanionPairingRevocationReason,
) -> None:
    row.status = CompanionPairingStatus.REVOKED.value
    row.local_control_secret_encrypted = None
    row.revoked_at = utc_now()
    row.revocation_reason = reason.value
    row.supersedes_pairing_session_id = None


async def prepare_companion_pairing(
    db: AsyncSession,
    *,
    current_user: User,
    pairing_code: str,
    paired_web_origin: str,
    tls_fingerprint: str | None,
) -> PreparedCompanionPairingPayload:
    normalized_origin = _normalize_origin(paired_web_origin)
    if not normalized_origin:
        raise CompanionPairingStateError(
            "Pairing requests must include a valid Origin header.",
            status_code=400,
        )

    rows = await _load_pairings_for_user(db, current_user.id)
    active_rows, pending_rows, _ = _group_pairings(rows)

    if pending_rows:
        raise CompanionPairingStateError(
            "A previous Companion pairing attempt is still pending. Revoke the pairing and try again."
        )

    active_row = active_rows[0] if active_rows else None
    latest_live_version = _latest_live_secret_version(rows)
    if active_row and active_row.local_control_secret_version != latest_live_version:
        raise CompanionPairingStateError(
            "Companion pairing state is stale. Revoke the pairing and pair again."
        )

    api_protocol, api_host, api_port = _parse_api_origin(get_trusted_web_origin())
    local_control_secret = security.generate_local_control_secret()
    pairing_session_id = uuid4().hex
    local_control_secret_version = _next_secret_version(rows)

    bootstrap_token = security.create_access_token(
        current_user.username,
        token_type=security.COMPANION_TOKEN_TYPE,
        scopes=[security.COMPANION_BOOTSTRAP_SCOPE],
        extra_claims={
            security.COMPANION_PAIRING_ID_CLAIM: pairing_session_id,
        },
    )

    pairing = CompanionPairing(
        user_id=current_user.id,
        pairing_session_id=pairing_session_id,
        status=CompanionPairingStatus.PENDING.value,
        api_protocol=api_protocol,
        api_host=api_host,
        api_port=api_port,
        paired_web_origin=normalized_origin,
        tls_fingerprint=tls_fingerprint,
        local_control_secret_encrypted=encrypt_secret(local_control_secret),
        local_control_secret_version=local_control_secret_version,
        supersedes_pairing_session_id=(
            active_row.pairing_session_id if active_row is not None else None
        ),
    )

    db.add(pairing)
    await db.commit()

    return PreparedCompanionPairingPayload(
        pairing_code=pairing_code,
        bootstrap_token=bootstrap_token,
        expires_in=security.COMPANION_BOOTSTRAP_TOKEN_EXPIRE_MINUTES * 60,
        api_protocol=api_protocol,
        api_host=api_host,
        api_port=api_port,
        tls_fingerprint=tls_fingerprint,
        local_control_secret=local_control_secret,
        local_control_secret_version=local_control_secret_version,
        backend_pairing_id=pairing_session_id,
    )


async def finalize_companion_pairing(
    db: AsyncSession,
    *,
    current_user: User,
    pairing_session_id: str,
) -> CompanionPairing:
    rows = await _load_pairings_for_user(db, current_user.id)
    active_rows, pending_rows, _ = _group_pairings(rows)
    row_by_id = {row.pairing_session_id: row for row in rows}

    pairing = row_by_id.get(pairing_session_id)
    if pairing is None:
        raise CompanionPairingStateError(
            "Companion pairing state is stale or revoked. Pair again from Nojoin."
        )

    if pairing.status == CompanionPairingStatus.REVOKED.value:
        raise CompanionPairingStateError(
            "Companion pairing was revoked. Pair again from Nojoin."
        )

    latest_live_version = _latest_live_secret_version(rows)
    if pairing.local_control_secret_version != latest_live_version:
        raise CompanionPairingStateError(
            "Companion pairing state is stale or rotated. Pair again from Nojoin."
        )

    if pairing.status == CompanionPairingStatus.ACTIVE.value:
        if pending_rows:
            raise CompanionPairingStateError(
                "A newer Companion pairing is pending. Pair again from Nojoin."
            )
        return pairing

    if pairing.status != CompanionPairingStatus.PENDING.value:
        raise CompanionPairingStateError(
            "Companion pairing state is invalid. Pair again from Nojoin."
        )

    if len(pending_rows) != 1 or pending_rows[0].pairing_session_id != pairing_session_id:
        raise CompanionPairingStateError(
            "Companion pairing state is conflicting. Revoke the pairing and pair again."
        )

    active_row = active_rows[0] if active_rows else None
    if pairing.supersedes_pairing_session_id:
        if active_row is None or active_row.pairing_session_id != pairing.supersedes_pairing_session_id:
            raise CompanionPairingStateError(
                "Companion pairing replacement state is incomplete. Revoke the pairing and pair again."
            )
        active_row.status = CompanionPairingStatus.REVOKED.value
        active_row.local_control_secret_encrypted = None
        active_row.revoked_at = utc_now()
        active_row.revocation_reason = CompanionPairingRevocationReason.REPLACED.value
        db.add(active_row)
    elif active_row is not None:
        raise CompanionPairingStateError(
            "Companion pairing state is conflicting. Revoke the pairing and pair again."
        )

    pairing.status = CompanionPairingStatus.ACTIVE.value
    pairing.supersedes_pairing_session_id = None
    db.add(pairing)
    await db.commit()
    return pairing


async def get_active_companion_pairing_auth(
    db: AsyncSession,
    *,
    current_user: User,
) -> ActiveCompanionPairingAuth:
    rows = await _load_pairings_for_user(db, current_user.id)
    active_rows, pending_rows, _ = _group_pairings(rows)

    if pending_rows:
        raise CompanionPairingStateError(
            "A newer Companion pairing is pending. Pair again from Nojoin."
        )

    active_row = active_rows[0] if active_rows else None
    if active_row is None:
        raise CompanionPairingStateError(
            "Companion pairing is not active. Pair again from Nojoin."
        )

    latest_live_version = _latest_live_secret_version(rows)
    if active_row.local_control_secret_version != latest_live_version:
        raise CompanionPairingStateError(
            "Companion pairing state is stale or rotated. Pair again from Nojoin."
        )

    if not active_row.local_control_secret_encrypted:
        raise CompanionPairingStateError(
            "Companion pairing state is incomplete. Revoke the pairing and pair again."
        )

    return ActiveCompanionPairingAuth(
        pairing_session_id=active_row.pairing_session_id,
        paired_web_origin=active_row.paired_web_origin,
        local_control_secret=decrypt_secret(active_row.local_control_secret_encrypted),
        local_control_secret_version=active_row.local_control_secret_version,
    )


async def revoke_companion_pairings(
    db: AsyncSession,
    *,
    current_user: User,
) -> int:
    rows = await _load_pairings_for_user(db, current_user.id)
    revoked_count = 0
    changed = False

    for row in rows:
        if row.status == CompanionPairingStatus.REVOKED.value:
            if row.local_control_secret_encrypted is not None:
                row.local_control_secret_encrypted = None
                db.add(row)
                changed = True
            continue

        _revoke_pairing_row(
            row,
            reason=CompanionPairingRevocationReason.MANUAL_UNPAIR,
        )
        db.add(row)
        revoked_count += 1
        changed = True

    if changed:
        await db.commit()

    return revoked_count


async def cancel_pending_companion_pairings(
    db: AsyncSession,
    *,
    current_user: User,
) -> int:
    rows = await _load_pairings_for_user(db, current_user.id)
    cancelled_count = 0
    changed = False

    for row in rows:
        if row.status == CompanionPairingStatus.REVOKED.value:
            if row.local_control_secret_encrypted is not None:
                row.local_control_secret_encrypted = None
                db.add(row)
                changed = True
            continue

        if row.status != CompanionPairingStatus.PENDING.value:
            continue

        _revoke_pairing_row(
            row,
            reason=CompanionPairingRevocationReason.PENDING_CANCELLED,
        )
        db.add(row)
        cancelled_count += 1
        changed = True

    if changed:
        await db.commit()

    return cancelled_count