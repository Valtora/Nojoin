from __future__ import annotations

from fastapi import HTTPException

INVITATION_ROLE_USER = "user"
INVITATION_ROLE_ADMIN = "admin"
INVITATION_ROLE_OWNER = "owner"
INVITATION_ALLOWED_ROLE_VALUES = frozenset(
    (INVITATION_ROLE_USER, INVITATION_ROLE_ADMIN)
)


def resolve_invitation_role(
    role: object,
    *,
    invalid_detail: str,
    owner_detail: str,
) -> str:
    if not isinstance(role, str):
        raise HTTPException(status_code=400, detail=invalid_detail)

    normalized_role = role.strip().lower()
    if normalized_role == INVITATION_ROLE_OWNER:
        raise HTTPException(status_code=400, detail=owner_detail)
    if normalized_role not in INVITATION_ALLOWED_ROLE_VALUES:
        raise HTTPException(status_code=400, detail=invalid_detail)

    return normalized_role
