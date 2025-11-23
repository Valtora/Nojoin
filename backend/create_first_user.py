import asyncio
from backend.core.db import async_session_maker
from backend.models.user import User
from backend.core.security import get_password_hash
from sqlmodel import select

async def create_first_user():
    async with async_session_maker() as session:
        query = select(User).where(User.username == "admin")
        result = await session.execute(query)
        user = result.scalar_one_or_none()
        
        if user:
            print("User admin already exists")
            return
            
        user = User(
            username="admin",
            hashed_password=get_password_hash("admin"),
            is_superuser=True,
            email="admin@example.com"
        )
        session.add(user)
        await session.commit()
        print("User admin created")

if __name__ == "__main__":
    asyncio.run(create_first_user())
