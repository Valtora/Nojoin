from fastapi import APIRouter
from pydantic import BaseModel
import httpx
from datetime import datetime, timedelta
import asyncio
import os
import logging

router = APIRouter()

logger = logging.getLogger(__name__)

class VersionInfo(BaseModel):
    current_version: str
    latest_version: str | None = None
    is_update_available: bool = False
    release_url: str | None = None

# Simple in-memory cache
_version_cache = {
    "data": None,
    "expires_at": datetime.min
}
_cache_lock = asyncio.Lock()

GITHUB_RELEASE_URL = "https://api.github.com/repos/Valtora/Nojoin/releases/latest"
# Try strict paths first, then relative
VERSION_FILE_PATHS = [
    "/app/docs/VERSION",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))), "docs/VERSION"),
]

def get_local_version() -> str:
    for path in VERSION_FILE_PATHS:
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    return f.read().strip()
        except Exception:
            continue
    return "0.0.0"

@router.get("", response_model=VersionInfo)
async def get_version():
    global _version_cache
    
    current_version = get_local_version()
    
    async with _cache_lock:
        if _version_cache["data"] and datetime.now() < _version_cache["expires_at"]:
            cached_data = _version_cache["data"]
            # Update current version in cache in case it changed
            cached_data.current_version = current_version
            
            # Re-evaluate update available
            if cached_data.latest_version:
                 cur = current_version.lstrip('v')
                 lat = cached_data.latest_version.lstrip('v')
                 # Simple check: if strings differ, update is available (assuming we don't run newer-than-release often in prod)
                 cached_data.is_update_available = cur != lat
            
            return cached_data

    # Fetch from GitHub
    latest_version = None
    release_url = None
    is_update_available = False
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(GITHUB_RELEASE_URL)
            if resp.status_code == 200:
                data = resp.json()
                latest_version = data.get("tag_name", "").lstrip('v')
                release_url = data.get("html_url")
                
                clean_current = current_version.lstrip('v')
                if clean_current and latest_version and clean_current != latest_version:
                    is_update_available = True
            else:
                logger.warning(f"Failed to fetch version from GitHub: {resp.status_code}")
    except Exception as e:
        logger.error(f"Error checking version: {e}")
        
    info = VersionInfo(
        current_version=current_version,
        latest_version=latest_version,
        is_update_available=is_update_available,
        release_url=release_url
    )
    
    async with _cache_lock:
        _version_cache["data"] = info
        _version_cache["expires_at"] = datetime.now() + timedelta(minutes=30)
        
    return info
