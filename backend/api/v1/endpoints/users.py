from typing import List, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlmodel import select
from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import os
import logging

from backend.api.deps import get_db, get_current_active_superuser, get_current_user
from backend.core.security import get_password_hash, verify_password
from backend.models.user import User, UserCreate, UserRead, UserUpdate, UserPasswordUpdate, UserRole, UserList
from backend.models.invitation import Invitation
from backend.models.recording import Recording
from backend.seed_demo import seed_demo_data

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/register", response_model=UserRead)
async def register_user(
    *,
    db: AsyncSession = Depends(get_db),
    user_in: UserCreate,
) -> Any:
    """
    Register a new user with an invitation code.
    """
    if not user_in.invite_code:
        raise HTTPException(status_code=400, detail="Invitation code required")
        
    # Validate invite
    query = select(Invitation).where(Invitation.code == user_in.invite_code)
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
        
    # Check if user exists
    query = select(User).where(User.username == user_in.username)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="The user with this username already exists in the system.",
        )
    
    user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        role=invitation.role,
        invitation_id=invitation.id,
        is_superuser=False,
        force_password_change=False,
    )
    db.add(user)
    
    # Update invitation usage
    invitation.used_count += 1
    db.add(invitation)
    
    await db.commit()
    await db.refresh(user)
    
    # Seed demo data
    try:
        await seed_demo_data(user.id)
    except Exception as e:
        print(f"Failed to seed demo data: {e}")
    
    return user

@router.get("", response_model=UserList)
async def read_users_root(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Retrieve users (root path). Only for admins and owners.
    """
    if current_user.role not in [UserRole.ADMIN, UserRole.OWNER] and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Not authorized")
    return await read_users(skip=skip, limit=limit, search=search, current_user=current_user, db=db)

@router.get("/", response_model=UserList)
async def read_users(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Retrieve users. Only for admins and owners.
    """
    if current_user.role not in [UserRole.ADMIN, UserRole.OWNER] and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    query = select(User)
    if search:
        query = query.where(
            or_(
                User.username.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%")
            )
        )
    
    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar_one()

    # Pagination
    query = query.offset(skip).limit(limit).order_by(User.id)
    result = await db.execute(query)
    users = result.scalars().all()
    
    return UserList(items=users, total=total)

@router.post("/", response_model=UserRead)
async def create_user(
    *,
    db: AsyncSession = Depends(get_db),
    user_in: UserCreate,
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Create new user manually. Only for admins and owners.
    """
    if current_user.role not in [UserRole.ADMIN, UserRole.OWNER] and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Check if user exists
    query = select(User).where(User.username == user_in.username)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="The user with this username already exists in the system.",
        )
    
    user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        is_superuser=user_in.is_superuser,
        role=user_in.role,
        force_password_change=True, # Force password change for new users created by admin
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    # Seed demo data
    try:
        await seed_demo_data(user.id)
    except Exception as e:
        print(f"Failed to seed demo data: {e}")

    return user

@router.delete("/{user_id}", response_model=UserRead)
async def delete_user(
    *,
    db: AsyncSession = Depends(get_db),
    user_id: int,
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Delete a user. Only Admins and Owners.
    """
    if current_user.role not in [UserRole.ADMIN, UserRole.OWNER] and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # Prevent deleting self
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
        
    # Prevent deleting owner (unless you are owner?)
    if user.role == UserRole.OWNER and current_user.role != UserRole.OWNER:
        raise HTTPException(status_code=403, detail="Cannot delete the owner")
        
    try:
        logger.info(f"User {current_user.id} deleting user {user_id}")
        await db.delete(user)
        await db.commit()
        logger.info(f"Successfully deleted user {user_id}")
        return user
    except Exception as e:
        logger.error(f"Failed to delete user {user_id}: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete user: {str(e)}")

@router.patch("/{user_id}/role", response_model=UserRead)
async def update_user_role(
    *,
    db: AsyncSession = Depends(get_db),
    user_id: int,
    role: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Update user role. Only Admins and Owners.
    """
    if current_user.role not in [UserRole.ADMIN, UserRole.OWNER] and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # Prevent modifying owner
    if user.role == UserRole.OWNER and current_user.role != UserRole.OWNER:
        raise HTTPException(status_code=403, detail="Cannot modify the owner")
        
    # Prevent promoting to owner (only one owner usually, or manual DB change)
    if role == UserRole.OWNER and current_user.role != UserRole.OWNER:
        raise HTTPException(status_code=403, detail="Cannot promote to owner")

    user.role = role
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/me", response_model=UserRead)
async def read_user_me(
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Get current user.
    """
    # print(f"DEBUG: read_user_me hit for {current_user.username}")
    return current_user

@router.put("/me", response_model=UserRead)
async def update_user_me(
    *,
    db: AsyncSession = Depends(get_db),
    user_in: UserUpdate,
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Update own user.
    """
    if user_in.username:
        # Check uniqueness
        query = select(User).where(User.username == user_in.username)
        result = await db.execute(query)
        existing_user = result.scalar_one_or_none()
        if existing_user and existing_user.id != current_user.id:
             raise HTTPException(
                status_code=400,
                detail="The user with this username already exists in the system.",
            )
        current_user.username = user_in.username
        
    if user_in.email is not None:
        current_user.email = user_in.email
        
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return current_user

@router.put("/me/password", response_model=Any)
async def update_password_me(
    *,
    db: AsyncSession = Depends(get_db),
    body: UserPasswordUpdate,
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Update own password.
    """
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect password")
    
    current_user.hashed_password = get_password_hash(body.new_password)
    current_user.force_password_change = False
    db.add(current_user)
    await db.commit()
    return {"message": "Password updated successfully"}

@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    *,
    db: AsyncSession = Depends(get_db),
    user_id: int,
    user_in: UserUpdate,
    current_user: User = Depends(get_current_active_superuser),
) -> Any:
    """
    Update a user. Only for superusers.
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=404,
            detail="The user with this id does not exist in the system",
        )
    
    if user_in.username:
        query = select(User).where(User.username == user_in.username)
        result = await db.execute(query)
        existing_user = result.scalar_one_or_none()
        if existing_user and existing_user.id != user_id:
             raise HTTPException(
                status_code=400,
                detail="The user with this username already exists in the system.",
            )
        user.username = user_in.username

    if user_in.password:
        user.hashed_password = get_password_hash(user_in.password)
        user.force_password_change = True # Force change if admin resets it
        
    if user_in.is_active is not None:
        user.is_active = user_in.is_active
    if user_in.is_superuser is not None:
        user.is_superuser = user_in.is_superuser
    if user_in.email is not None:
        user.email = user_in.email
    if user_in.role is not None:
        # Prevent modifying owner if not owner
        if user.role == UserRole.OWNER and current_user.role != UserRole.OWNER:
            raise HTTPException(status_code=403, detail="Cannot modify the owner")
        # Prevent promoting to owner if not owner
        if user_in.role == UserRole.OWNER and current_user.role != UserRole.OWNER:
            raise HTTPException(status_code=403, detail="Cannot promote to owner")
        user.role = user_in.role

    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@router.patch("/{user_id}/role", response_model=UserRead)
async def update_user_role(
    *,
    db: AsyncSession = Depends(get_db),
    user_id: int,
    role: str = Body(..., embed=True),
    current_user: User = Depends(get_current_active_superuser),
) -> Any:
    """
    Update a user's role. Only for superusers.
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=404,
            detail="The user with this id does not exist in the system",
        )
    
    if role not in [UserRole.ADMIN, UserRole.USER, UserRole.OWNER]:
         raise HTTPException(
            status_code=400,
            detail="Invalid role",
        )
        
    user.role = role
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@router.delete("/{user_id}", response_model=UserRead)
async def delete_user(
    *,
    db: AsyncSession = Depends(get_db),
    user_id: int,
    current_user: User = Depends(get_current_active_superuser),
) -> Any:
    """
    Delete a user. Only for superusers.
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=404,
            detail="The user with this id does not exist in the system",
        )
    if user.id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="Users cannot delete themselves",
        )
        
    if user.is_superuser:
        # Check if this is the last superuser
        query = select(func.count(User.id)).where(User.is_superuser == True)
        result = await db.execute(query)
        admin_count = result.scalar_one()
        if admin_count <= 1:
             raise HTTPException(
                status_code=400,
                detail="Cannot delete the last admin account",
            )

    # Cleanup user files (recordings)
    stmt = select(Recording).where(Recording.user_id == user_id)
    result = await db.execute(stmt)
    recordings = result.scalars().all()
    
    for recording in recordings:
        if recording.audio_path and os.path.exists(recording.audio_path):
            try:
                os.remove(recording.audio_path)
            except OSError:
                pass
        if recording.proxy_path and os.path.exists(recording.proxy_path):
            try:
                os.remove(recording.proxy_path)
            except OSError:
                pass

    await db.delete(user)
    await db.commit()
    return user
