from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta
from sqlalchemy.orm import selectinload

from backend.api.deps import get_db, get_current_user
from backend.models.user import User, UserRole
from backend.models.invitation import Invitation
from backend.utils.config_manager import config_manager
from pydantic import BaseModel

router = APIRouter()

class InvitationCreate(BaseModel):
    role: str = "user"
    expires_in_days: int = 7
    max_uses: int = 1

class InvitationRead(BaseModel):
    id: int
    code: str
    role: str
    expires_at: datetime | None
    max_uses: int | None
    used_count: int
    is_revoked: bool
    created_by_id: int
    link: str
    users: List[str] = []

import os
from urllib.parse import urlparse

def get_invite_base_url(request: Request) -> str:
    system_config = config_manager.config
    web_app_url = system_config.get("web_app_url", "https://localhost:14443")
    
    # If the configured URL is the default localhost, try to detect the actual domain
    if "localhost" in web_app_url:
        # Check for X-Forwarded headers (common in reverse proxies)
        forwarded_host = request.headers.get("x-forwarded-host")
        forwarded_proto = request.headers.get("x-forwarded-proto", "https")
        
        allowed_origins = os.environ.get("ALLOWED_ORIGINS", "").split(",")
        allowed_origins = [origin.strip() for origin in allowed_origins if origin.strip()]
        
        def is_host_allowed(host: str) -> bool:
            if not host:
                return False
            host_without_port = host.split(":")[0]
            if host_without_port in ["localhost", "127.0.0.1"]:
                return True
            for origin in allowed_origins:
                try:
                    parsed = urlparse(origin)
                    if parsed.hostname == host_without_port or parsed.netloc == host:
                        return True
                except:
                    pass
            return False
        
        if forwarded_host and is_host_allowed(forwarded_host):
            return f"{forwarded_proto}://{forwarded_host}"
            
        # Fallback to the request's base URL if not behind a proxy or headers missing
        if is_host_allowed(request.base_url.hostname):
            # request.base_url returns a URL object, convert to string
            return str(request.base_url).rstrip("/")
        
    return web_app_url

@router.post("/", response_model=InvitationRead)
async def create_invitation(
    *,
    request: Request,
    db: AsyncSession = Depends(get_db),
    invitation_in: InvitationCreate,
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Create a new invitation. Only Admins and Owners can create invites.
    """
    if current_user.role not in [UserRole.ADMIN, UserRole.OWNER] and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Not authorized")

    expires_at = None
    if invitation_in.expires_in_days:
        expires_at = datetime.utcnow() + timedelta(days=invitation_in.expires_in_days)

    invitation = Invitation(
        role=invitation_in.role,
        expires_at=expires_at,
        max_uses=invitation_in.max_uses,
        created_by_id=current_user.id
    )
    db.add(invitation)
    await db.commit()
    await db.refresh(invitation)
    
    # Construct link
    web_app_url = get_invite_base_url(request)
    link = f"{web_app_url}/register?invite={invitation.code}"
    
    return InvitationRead(
        **invitation.dict(),
        link=link,
        users=[]
    )

@router.get("/", response_model=List[InvitationRead])
async def read_invitations(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Retrieve invitations. Only Admins and Owners.
    """
    if current_user.role not in [UserRole.ADMIN, UserRole.OWNER] and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Not authorized")

    query = select(Invitation).options(selectinload(Invitation.users)).offset(skip).limit(limit).order_by(Invitation.id.desc())
    result = await db.execute(query)
    invitations = result.scalars().all()
    
    web_app_url = get_invite_base_url(request)
    
    res = []
    for inv in invitations:
        link = f"{web_app_url}/register?invite={inv.code}"
        usernames = [u.username for u in inv.users]
        res.append(InvitationRead(
            **inv.dict(),
            link=link,
            users=usernames
        ))
    return res

@router.post("/{id}/revoke", response_model=InvitationRead)
async def revoke_invitation(
    *,
    db: AsyncSession = Depends(get_db),
    id: int,
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Revoke an invitation.
    """
    if current_user.role not in [UserRole.ADMIN, UserRole.OWNER] and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    invitation = await db.get(Invitation, id)
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")
        
    invitation.is_revoked = True
    db.add(invitation)
    await db.commit()
    await db.refresh(invitation)
    
    system_config = config_manager.config
    web_app_url = system_config.get("web_app_url", "https://localhost:14443")
    link = f"{web_app_url}/register?invite={invitation.code}"
    
    return InvitationRead(
        **invitation.dict(),
        link=link,
        users=[] 
    )

@router.delete("/{id}", response_model=InvitationRead)
async def delete_invitation(
    *,
    db: AsyncSession = Depends(get_db),
    id: int,
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Permanently delete an invitation.
    """
    if current_user.role not in [UserRole.ADMIN, UserRole.OWNER] and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    invitation = await db.get(Invitation, id)
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")
        
    await db.delete(invitation)
    await db.commit()
    
    system_config = config_manager.config
    web_app_url = system_config.get("web_app_url", "https://localhost:14443")
    link = f"{web_app_url}/register?invite={invitation.code}"
    
    return InvitationRead(
        **invitation.dict(),
        link=link,
        users=[] 
    )

@router.get("/validate/{code}")
async def validate_invitation(
    code: str,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Check if an invitation code is valid. Public endpoint.
    """
    query = select(Invitation).where(Invitation.code == code).options(selectinload(Invitation.created_by))
    result = await db.execute(query)
    invitation = result.scalar_one_or_none()
    
    if not invitation:
        raise HTTPException(status_code=404, detail="Invalid invitation code")
        
    if invitation.is_revoked:
        raise HTTPException(status_code=400, detail="Invitation has been revoked")
        
    if invitation.expires_at and invitation.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invitation has expired")
        
    if invitation.max_uses and invitation.used_count >= invitation.max_uses:
        raise HTTPException(status_code=400, detail="Invitation usage limit reached")
        
    inviter_username = invitation.created_by.username if invitation.created_by else "System"
        
    return {"valid": True, "role": invitation.role, "inviter": inviter_username}
