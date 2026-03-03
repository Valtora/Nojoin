from datetime import timedelta
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_db, get_current_user
from backend.core import security
from backend.models.user import User

router = APIRouter()

@router.post("/login/access-token")
async def login_access_token(
    response: Response,
    db: AsyncSession = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends()
) -> Any:
    """
    OAuth2 compatible token login, get an access token for future requests
    """
    query = select(User).where(User.username == form_data.username)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    
    if not user or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect username or password"
        )
    elif not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
        
    access_token_expires = timedelta(minutes=security.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = security.create_access_token(
        user.username, expires_delta=access_token_expires
    )
    
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=security.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "force_password_change": user.force_password_change,
        "is_superuser": user.is_superuser,
        "username": user.username,
    }

@router.post("/login/logout")
async def logout_user(response: Response) -> Any:
    """
    Endpoint to clear the HttpOnly access token on logout.
    """
    response.delete_cookie(
        key="access_token",
        httponly=True,
        samesite="lax"
    )
    return {"message": "Logged out successfully"}

@router.get("/login/companion-token")
async def get_companion_token(
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Issues a fresh JWT for companion app pairing.
    Authenticated via HttpOnly cookie so the token never resides in localStorage.
    """
    access_token_expires = timedelta(minutes=security.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = security.create_access_token(
        current_user.username, expires_delta=access_token_expires
    )
    return {"token": token}
