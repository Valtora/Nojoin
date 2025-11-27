from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.core.security import get_password_hash
from backend.models.user import User, UserCreate

router = APIRouter()

@router.get("/status")
async def get_system_status(
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Check if the system is initialized (has at least one user).
    """
    query = select(User).limit(1)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    return {"initialized": user is not None}

@router.post("/setup")
async def setup_system(
    *,
    db: AsyncSession = Depends(get_db),
    user_in: UserCreate,
) -> Any:
    """
    Initialize the system with the first admin user.
    Only works if no users exist.
    """
    query = select(User).limit(1)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="System is already initialized.",
        )
    
    user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        is_superuser=True,
        force_password_change=False, # First user sets their own password, so no need to force change
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {"message": "System initialized successfully"}
