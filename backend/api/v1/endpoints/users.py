from typing import List, Any
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db, get_current_active_superuser, get_current_user
from backend.core.security import get_password_hash, verify_password
from backend.models.user import User, UserCreate, UserRead, UserUpdate, UserPasswordUpdate

router = APIRouter()

@router.get("", response_model=List[UserRead])
async def read_users_root(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_active_superuser),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Retrieve users (root path). Only for superusers.
    """
    return await read_users(skip=skip, limit=limit, current_user=current_user, db=db)

@router.get("/", response_model=List[UserRead])
async def read_users(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_active_superuser),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Retrieve users. Only for superusers.
    """
    query = select(User).offset(skip).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()
    return users

@router.post("/", response_model=UserRead)
async def create_user(
    *,
    db: AsyncSession = Depends(get_db),
    user_in: UserCreate,
    current_user: User = Depends(get_current_active_superuser),
) -> Any:
    """
    Create new user. Only for superusers.
    """
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
        force_password_change=True, # Force password change for new users created by admin
    )
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

@router.put("/{user_id}", response_model=UserRead)
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
        
    await db.delete(user)
    await db.commit()
    return user
